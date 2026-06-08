"""
plot_failures.py
================
Reads  result/GuLog_FailedSummary.csv  and generates two interactive Plotly HTML files:

  result/FailedParams_CorrFactor_Plot.html   -- Failed GU Corr-factor limits
  result/FailedParams_Verify_Plot.html       -- Failed GU Verification limits

Each unique ParamName gets one subplot:
  - One coloured line per TesterName
  - Red  dashed horizontal line = LowL spec limit
  - Green dashed horizontal line = HighL spec limit

CorrFactor x-axis note
  GuCorrFactor_ALL_CONCAT.csv uses Parameter=999 for every row (site-level aggregate).
  The actual device IDs tested in each session are resolved from GuVrfyError_ALL_CONCAT.csv
  (same ZipFile -> unique PID rows).  Each CorrFactor session is therefore expanded to
  one point per device so the x-axis shows individual device measurements.
"""

from __future__ import annotations

import csv
import os
import sys

import pandas as pd
import plotly.colors as pc
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ── Paths ───────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT_DIR = os.path.join(BASE_DIR, "result")

SUMMARY_CSV  = os.path.join(RESULT_DIR, "GuLog_FailedSummary.csv")
CF_CSV       = os.path.join(RESULT_DIR, "GuCorrFactor_ALL_CONCAT.csv")
VE_CSV       = os.path.join(RESULT_DIR, "GuVrfyError_ALL_CONCAT.csv")
OUT_CF_HTML  = os.path.join(RESULT_DIR, "FailedParams_CorrFactor_Plot.html")
OUT_VE_HTML  = os.path.join(RESULT_DIR, "FailedParams_Verify_Plot.html")

# ── Visual constants ─────────────────────────────────────────────────────────────
TESTER_PALETTE  = pc.qualitative.Plotly   # 10 distinct colours
LOW_COLOUR      = "#d62728"               # red
HIGH_COLOUR     = "#2ca02c"              # green
HEIGHT_PER_PLOT = 500                     # px per subplot


# ── CSV loader ───────────────────────────────────────────────────────────────────
def load_concat_csv(path: str) -> pd.DataFrame:
    """
    Load a GuCorrFactor / GuVrfyError ALL_CONCAT CSV.

    File layout:
      row 0 : column-name header  (TesterName, ZipFile, Parameter, <test cols...>)
      row 1 : Test#
      row 2 : Unit
      row 3 : HighL
      row 4 : LowL
      row 5+: data rows
    Returns DataFrame using row-0 as column names, rows 5+ as data.
    """
    if not os.path.exists(path):
        print(f"  [WARN] File not found: {os.path.basename(path)}")
        return pd.DataFrame()
    with open(path, encoding="utf-8", errors="replace") as f:
        rows = list(csv.reader(f))
    if len(rows) < 6:
        print(f"  [WARN] Too few rows in {os.path.basename(path)}")
        return pd.DataFrame()
    header    = rows[0]
    data_rows = rows[5:]
    if not data_rows:
        return pd.DataFrame(columns=header)
    n = len(header)
    normalised = [r[:n] + [""] * max(0, n - len(r)) for r in data_rows]
    return pd.DataFrame(normalised, columns=header)


def find_column(df: pd.DataFrame, param_name: str) -> str | None:
    """Exact match first, then partial match."""
    if param_name in df.columns:
        return param_name
    matches = [c for c in df.columns if param_name in c]
    return matches[0] if matches else None


# ── Device-ID mapping: ZipFile -> sorted list of device IDs ─────────────────────
def build_zip_device_map(ve_df: pd.DataFrame) -> dict[str, list[str]]:
    """
    Build {ZipFile: ['7000018', '7000019', ...]} from the Parameter column of
    GuVrfyError_ALL_CONCAT.csv (values are like 'PID-7000018').
    This is used to expand CorrFactor rows (which have Parameter=999) to
    the actual devices that were run in that session.
    """
    mapping: dict[str, list[str]] = {}
    if ve_df.empty or "ZipFile" not in ve_df.columns:
        return mapping
    for zipfile, grp in ve_df.groupby("ZipFile"):
        pids = sorted({
            str(p).replace("PID-", "")
            for p in grp["Parameter"].unique()
            if str(p).startswith("PID-")
        })
        if pids:
            mapping[str(zipfile)] = pids
    return mapping


# ── Data extraction helpers ───────────────────────────────────────────────────────
def get_corr_data(cf_df: pd.DataFrame, col: str,
                  zip_device_map: dict[str, list[str]]) -> pd.DataFrame:
    """
    Extract CorrFactor rows for *col*.  One point per session (ZipFile).
    The Device column contains all device IDs for that session as a
    comma-separated string (e.g. '7000018, 7000019, 7000020, 7000026'),
    shown in the hover tooltip.
    Returns DataFrame with columns: TesterName, ZipFile, Device, <col>.
    """
    sub = cf_df[["TesterName", "ZipFile", col]].copy()
    sub[col] = pd.to_numeric(sub[col], errors="coerce")
    sub = sub.dropna(subset=[col]).reset_index(drop=True)
    if sub.empty:
        return pd.DataFrame(columns=["TesterName", "ZipFile", "Device", col])

    sub["Device"] = sub["ZipFile"].apply(
        lambda zf: ", ".join(zip_device_map.get(str(zf), ["N/A"]))
    )
    return sub


def get_verify_data(ve_df: pd.DataFrame, col: str) -> pd.DataFrame:
    """
    Extract Verify rows for *col*, stripping 'PID-' from the Parameter column.
    Returns DataFrame with columns: TesterName, ZipFile, Device, <col>.
    """
    sub = ve_df[["TesterName", "ZipFile", "Parameter", col]].copy()
    sub[col] = pd.to_numeric(sub[col], errors="coerce")
    sub = sub.dropna(subset=[col])
    sub["Device"] = sub["Parameter"].str.replace("PID-", "", regex=False)
    return sub.drop(columns=["Parameter"]).reset_index(drop=True)


# ── Figure builder ────────────────────────────────────────────────────────────────
def build_figure(param_specs: pd.DataFrame,
                 cf_df: pd.DataFrame,
                 ve_df: pd.DataFrame,
                 zip_device_map: dict[str, list[str]],
                 page_title: str,
                 is_corr: bool) -> go.Figure | None:
    """
    Build a Plotly figure (one subplot per ParamName) for either CorrFactor or Verify.
    Returns None if no plottable parameters are found.
    """
    if param_specs.empty:
        return None

    N = len(param_specs)

    # Full parameter name in subtitle — no truncation
    subplot_titles = [
        f"[{'CorrFactor' if is_corr else 'Verify'}]  {r['ParamName']}"
        for _, r in param_specs.iterrows()
    ]

    # Give enough gap between subplots so x-axis labels don't overlap subplot titles
    v_spacing = max(0.06, min(0.10, 0.8 / max(N, 1)))
    fig = make_subplots(
        rows=N, cols=1,
        subplot_titles=subplot_titles,
        vertical_spacing=v_spacing,
        shared_xaxes=False,
    )

    tester_colors: dict[str, str] = {}
    color_idx     = 0
    shown_testers : set[str] = set()

    src_df = cf_df if is_corr else ve_df

    for row_i, (_, spec) in enumerate(param_specs.iterrows(), start=1):
        param_name = spec["ParamName"]
        low_l      = float(spec["LowL"])
        high_l     = float(spec["HighL"])

        if src_df.empty:
            print(f"  [{row_i}/{N}] SKIP (empty source): {param_name[:70]}")
            continue

        col = find_column(src_df, param_name)
        if col is None:
            print(f"  [{row_i}/{N}] SKIP (column not found): {param_name[:70]}")
            continue

        sub = (get_corr_data(cf_df, col, zip_device_map) if is_corr
               else get_verify_data(ve_df, col))

        if sub.empty:
            print(f"  [{row_i}/{N}] SKIP (no data): {param_name[:70]}")
            continue

        testers = sub["TesterName"].unique()
        for tester in testers:
            if tester not in tester_colors:
                tester_colors[tester] = TESTER_PALETTE[color_idx % len(TESTER_PALETTE)]
                color_idx += 1
            colour = tester_colors[tester]

            td    = sub[sub["TesterName"] == tester].reset_index(drop=True)
            x_idx = list(range(1, len(td) + 1))

            hover = [
                (
                    f"<b>Tester:</b> {tester}<br>"
                    f"<b>ZipFile:</b> {zf}<br>"
                    f"<b>Device:</b> {dev}<br>"
                    f"<b>Value:</b> {val:.6g}"
                )
                for zf, dev, val in zip(td["ZipFile"], td["Device"], td[col])
            ]

            fig.add_trace(
                go.Scatter(
                    x=x_idx,
                    y=td[col].tolist(),
                    mode="lines+markers",
                    name=tester,
                    legendgroup=tester,
                    showlegend=(tester not in shown_testers),
                    line=dict(color=colour, width=2),
                    marker=dict(size=7, color=colour),
                    hovertemplate="%{customdata}<extra></extra>",
                    customdata=hover,
                ),
                row=row_i, col=1,
            )
            shown_testers.add(tester)

        # Spec limit lines
        fig.add_hline(
            y=low_l,
            line_dash="dash", line_color=LOW_COLOUR, line_width=1.5,
            annotation_text=f"LowL = {low_l:g}",
            annotation_font_color=LOW_COLOUR,
            annotation_position="bottom right",
            row=row_i, col=1,
        )
        fig.add_hline(
            y=high_l,
            line_dash="dash", line_color=HIGH_COLOUR, line_width=1.5,
            annotation_text=f"HighL = {high_l:g}",
            annotation_font_color=HIGH_COLOUR,
            annotation_position="top right",
            row=row_i, col=1,
        )

        x_label = "Session Index" if is_corr else "Measurement Index"
        fig.update_xaxes(title_text=x_label,         row=row_i, col=1)
        fig.update_yaxes(title_text="Measured Value", row=row_i, col=1)

        print(f"  [{row_i}/{N}] OK  {len(testers)} tester(s)  {len(sub)} point(s)   {param_name[:70]}")

    # Legend-only dummy traces for spec lines
    fig.add_trace(
        go.Scatter(x=[None], y=[None], mode="lines",
                   name="--- LowL spec", legendgroup="__lowl__", showlegend=True,
                   line=dict(color=LOW_COLOUR, dash="dash", width=1.5)),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=[None], y=[None], mode="lines",
                   name="--- HighL spec", legendgroup="__highl__", showlegend=True,
                   line=dict(color=HIGH_COLOUR, dash="dash", width=1.5)),
        row=1, col=1,
    )

    fig.update_layout(
        title=dict(
            text=f"<b>{page_title}</b>",
            x=0.5, xanchor="center",
            font=dict(size=20),
        ),
        height=max(700, N * HEIGHT_PER_PLOT),
        template="plotly_white",
        legend=dict(
            title=dict(text="<b>Tester / Spec</b>"),
            orientation="v",
            x=1.01, y=1.0,
            xanchor="left", yanchor="top",
            bordercolor="#cccccc", borderwidth=1,
            font=dict(size=12),
        ),
        hovermode="closest",
        margin=dict(l=80, r=240, t=100, b=60),
    )

    return fig


# ── Main ─────────────────────────────────────────────────────────────────────────
if not os.path.exists(SUMMARY_CSV):
    print(f"ERROR: {SUMMARY_CSV} not found.\nRun main.py first to generate result files.")
    sys.exit(1)

with open(SUMMARY_CSV, encoding="utf-8") as _f:
    _peek = _f.readline().strip()

if _peek.startswith("All Pass"):
    print("GuLog_FailedSummary.csv reports All Pass -- nothing to plot.")
    sys.exit(0)

summary_df = pd.read_csv(SUMMARY_CSV)

# One spec entry per unique (ParamName, FailType) -- first LowL / HighL wins
param_specs = (
    summary_df
    .groupby(["ParamName", "FailType"], sort=False)
    .agg(LowL=("LowL", "first"), HighL=("HighL", "first"))
    .reset_index()
)

N = len(param_specs)
print(f"GuLog_FailedSummary: {len(summary_df)} failure rows -> {N} unique parameter(s) to plot\n")

# Load concat data files
cf_df = load_concat_csv(CF_CSV)
ve_df = load_concat_csv(VE_CSV)

if not cf_df.empty:
    print(f"  GuCorrFactor : {len(cf_df):,} data rows")
if not ve_df.empty:
    print(f"  GuVrfyError  : {len(ve_df):,} data rows")

# Build ZipFile -> device ID mapping from GuVrfyError
zip_device_map = build_zip_device_map(ve_df)
print(f"  ZipFile->Device map : {len(zip_device_map)} session(s)\n")

# Split parameters by FailType
cf_specs = param_specs[param_specs["FailType"].str.contains("Corr")].reset_index(drop=True)
ve_specs  = param_specs[~param_specs["FailType"].str.contains("Corr")].reset_index(drop=True)

outputs_opened = []

# ── CorrFactor HTML ───────────────────────────────────────────────────────────────
if not cf_specs.empty:
    print(f"=== CorrFactor: {len(cf_specs)} parameter(s) ===")
    cf_fig = build_figure(
        param_specs=cf_specs,
        cf_df=cf_df, ve_df=ve_df,
        zip_device_map=zip_device_map,
        page_title="Failed GU Corr-factor Parameters -- Measurement Trace",
        is_corr=True,
    )
    if cf_fig is not None:
        cf_fig.write_html(OUT_CF_HTML, include_plotlyjs="cdn")
        print(f"  Saved -> {OUT_CF_HTML}\n")
        outputs_opened.append(OUT_CF_HTML)
    else:
        print("  No plottable CorrFactor parameters.\n")
else:
    print("No CorrFactor failures in summary.\n")

# ── Verify HTML ───────────────────────────────────────────────────────────────────
if not ve_specs.empty:
    print(f"=== Verify: {len(ve_specs)} parameter(s) ===")
    ve_fig = build_figure(
        param_specs=ve_specs,
        cf_df=cf_df, ve_df=ve_df,
        zip_device_map=zip_device_map,
        page_title="Failed GU Verification Parameters -- Measurement Trace",
        is_corr=False,
    )
    if ve_fig is not None:
        ve_fig.write_html(OUT_VE_HTML, include_plotlyjs="cdn")
        print(f"  Saved -> {OUT_VE_HTML}\n")
        outputs_opened.append(OUT_VE_HTML)
    else:
        print("  No plottable Verify parameters.\n")
else:
    print("No Verify failures in summary.\n")

# Open all generated HTML files
for path in outputs_opened:
    os.startfile(path)

print("Done.")
