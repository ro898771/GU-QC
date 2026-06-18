"""
LinePlot.py
===========
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
import re
import sys

import pandas as pd
import plotly.colors as pc
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ── Paths ───────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT_DIR = os.path.join(BASE_DIR, "result")

SUMMARY_CSV     = os.path.join(RESULT_DIR, "GuLog_FailedSummary.csv")
CF_CSV          = os.path.join(RESULT_DIR, "GuCorrFactor_ALL_CONCAT.csv")
VE_CSV          = os.path.join(RESULT_DIR, "GuVrfyError_ALL_CONCAT.csv")
CR_CSV          = os.path.join(RESULT_DIR, "Corr_GuCorrRawData_ALL_CONCAT.csv")
VR_CSV          = os.path.join(RESULT_DIR, "Vry_GuRawData_ALL_CONCAT.csv")
PLOT_DIR        = os.path.join(RESULT_DIR, "Plot")
PARAMS_PER_PAGE = 20

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
    Returns DataFrame with columns: TesterName, ZipFile, Device, M_Handler-ArmNo, <col>.
    """
    arm_present = "M_Handler-ArmNo" in cf_df.columns
    keep = ["TesterName", "ZipFile"] + (["M_Handler-ArmNo"] if arm_present else []) + [col]
    sub = cf_df[keep].copy()
    sub[col] = pd.to_numeric(sub[col], errors="coerce")
    sub = sub.dropna(subset=[col]).reset_index(drop=True)
    if sub.empty:
        return pd.DataFrame(columns=["TesterName", "ZipFile", "Device", "M_Handler-ArmNo", col])

    sub["Device"] = sub["ZipFile"].apply(
        lambda zf: ", ".join(zip_device_map.get(str(zf), ["N/A"]))
    )
    if not arm_present:
        sub["M_Handler-ArmNo"] = "N/A"
    return sub


def get_verify_data(ve_df: pd.DataFrame, col: str) -> pd.DataFrame:
    """
    Extract Verify rows for *col*, stripping 'PID-' from the Parameter column.
    Returns DataFrame with columns: TesterName, ZipFile, Device, M_Handler-ArmNo, <col>.
    """
    arm_present = "M_Handler-ArmNo" in ve_df.columns
    keep = ["TesterName", "ZipFile", "Parameter"] + (["M_Handler-ArmNo"] if arm_present else []) + [col]
    sub = ve_df[keep].copy()
    sub[col] = pd.to_numeric(sub[col], errors="coerce")
    sub = sub.dropna(subset=[col])
    sub["Device"] = sub["Parameter"].str.replace("PID-", "", regex=False)
    if not arm_present:
        sub["M_Handler-ArmNo"] = "N/A"
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
                    f"<b>ArmNo:</b> {arm}<br>"
                    f"<b>Value:</b> {val:.6g}"
                )
                for zf, dev, arm, val in zip(
                    td["ZipFile"], td["Device"], td["M_Handler-ArmNo"], td[col]
                )
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


# ── Helpers ──────────────────────────────────────────────────────────────────────
def _chunked_df(df: pd.DataFrame, size: int):
    """Yield successive DataFrame slices of `size` rows."""
    for i in range(0, len(df), size):
        yield df.iloc[i : i + size].reset_index(drop=True)


def _safe_id(name: str) -> str:
    """Return a valid HTML element ID from a parameter name."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def _build_html_with_anchors(
    param_specs: pd.DataFrame,
    cf_df: pd.DataFrame,
    ve_df: pd.DataFrame,
    zip_device_map: dict,
    page_title: str,
    is_corr: bool,
) -> "str | None":
    """
    Build a standalone HTML page with one Plotly figure per param.
    Each section gets <div id="{safe_id}"> so that linking to
    file.html#safe_id scrolls directly to that parameter's chart.
    Returns None if no params are plottable.
    """
    if param_specs.empty:
        return None

    src_df   = cf_df if is_corr else ve_df
    N        = len(param_specs)
    sections: list = []
    tester_colors: dict = {}
    color_idx = 0

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

        fig: go.Figure = go.Figure()
        shown_testers: set = set()

        for tester in sub["TesterName"].unique():
            if tester not in tester_colors:
                tester_colors[tester] = TESTER_PALETTE[color_idx % len(TESTER_PALETTE)]
                color_idx += 1
            colour = tester_colors[tester]
            td     = sub[sub["TesterName"] == tester].reset_index(drop=True)
            x_idx  = list(range(1, len(td) + 1))
            hover  = [
                (
                    f"<b>Tester:</b> {tester}<br>"
                    f"<b>ZipFile:</b> {zf}<br>"
                    f"<b>Device:</b> {dev}<br>"
                    f"<b>ArmNo:</b> {arm}<br>"
                    f"<b>Value:</b> {val:.6g}"
                )
                for zf, dev, arm, val in zip(
                    td["ZipFile"], td["Device"], td["M_Handler-ArmNo"], td[col]
                )
            ]
            fig.add_trace(go.Scatter(
                x=x_idx, y=td[col].tolist(),
                mode="lines+markers",
                name=tester, legendgroup=tester,
                showlegend=(tester not in shown_testers),
                line=dict(color=colour, width=2),
                marker=dict(size=7, color=colour),
                hovertemplate="%{customdata}<extra></extra>",
                customdata=hover,
            ))
            shown_testers.add(tester)

        fig.add_hline(y=low_l, line_dash="dash", line_color=LOW_COLOUR, line_width=1.5,
                      annotation_text=f"LowL = {low_l:g}",
                      annotation_font_color=LOW_COLOUR,
                      annotation_position="bottom right")
        fig.add_hline(y=high_l, line_dash="dash", line_color=HIGH_COLOUR, line_width=1.5,
                      annotation_text=f"HighL = {high_l:g}",
                      annotation_font_color=HIGH_COLOUR,
                      annotation_position="top right")
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                                  name="--- LowL spec", legendgroup="__lowl__",
                                  showlegend=True,
                                  line=dict(color=LOW_COLOUR, dash="dash", width=1.5)))
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                                  name="--- HighL spec", legendgroup="__highl__",
                                  showlegend=True,
                                  line=dict(color=HIGH_COLOUR, dash="dash", width=1.5)))

        x_label = "Session Index" if is_corr else "Measurement Index"
        fig.update_layout(
            title=dict(
                text=f"<b>[{'CorrFactor' if is_corr else 'Verify'}]  {param_name}</b>",
                x=0.5, xanchor="center", font=dict(size=16),
            ),
            height=HEIGHT_PER_PLOT,
            xaxis_title=x_label,
            yaxis_title="Measured Value",
            template="plotly_white",
            legend=dict(
                title=dict(text="<b>Tester / Spec</b>"),
                orientation="v", x=1.01, y=1.0,
                xanchor="left", yanchor="top",
                bordercolor="#cccccc", borderwidth=1,
                font=dict(size=12),
            ),
            hovermode="closest",
            margin=dict(l=80, r=240, t=80, b=60),
        )

        safe_id    = _safe_id(param_name)
        include_js = "cdn" if not sections else False
        fig_div    = fig.to_html(include_plotlyjs=include_js, full_html=False,
                                  div_id=f"lp-{safe_id}")
        print(f"  [{row_i}/{N}] OK  {len(shown_testers)} tester(s)  {len(sub)} point(s)   {param_name[:70]}")
        sections.append((safe_id, param_name, fig_div))

    if not sections:
        return None

    modal_rows = "".join(
        f'<tr><td>{i}</td><td><a href="#{s}" onclick="closeModal()">{n}</a></td></tr>'
        for i, (s, n, _) in enumerate(sections, start=1)
    )
    body = "".join(
        f'<div id="{s}" class="ps">'
        f'<div class="bk"><a href="#top">&#8593; Top</a></div>'
        f'{h}</div>'
        for s, _, h in sections
    )
    n_params = len(sections)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{page_title}</title>
<style>
  body{{font-family:"Segoe UI",Arial,sans-serif;background:#f0f2f5;margin:0;padding:16px}}
  .top-bar{{display:flex;align-items:center;gap:12px;margin-bottom:14px;
            background:#2c3e50;padding:8px 14px;border-radius:6px;position:sticky;top:0;z-index:99}}
  h1{{font-size:15px;color:#fff;flex:1;margin:0}}
  .btn-nav{{background:#1a6b9a;color:#fff;border:none;padding:5px 14px;
            border-radius:4px;cursor:pointer;font-size:12px;white-space:nowrap}}
  .btn-nav:hover{{background:#135580}}
  .ps{{background:#fff;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.1);
       margin-bottom:20px;padding:8px 8px 4px}}
  .bk{{text-align:right;font-size:11px;padding:2px 6px 4px}}
  .bk a{{color:#aaa;text-decoration:none}}.bk a:hover{{color:#2c3e50}}
  /* Modal */
  #pmodal{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);
           z-index:999;align-items:center;justify-content:center}}
  #pmodal.open{{display:flex}}
  #pmodal-box{{background:#fff;padding:28px;border-radius:8px;
               width:80vw;max-width:900px;max-height:85vh;overflow:auto;
               box-shadow:0 4px 24px rgba(0,0,0,.25)}}
  #pmodal-box h2{{font-size:15px;color:#2c3e50;margin-bottom:12px}}
  #pmodal-box table{{border-collapse:collapse;width:100%;font-size:13px}}
  #pmodal-box th{{background:#2c3e50;color:#fff;padding:7px 14px;text-align:left}}
  #pmodal-box td{{padding:6px 14px;border-bottom:1px solid #eee}}
  #pmodal-box tr:last-child td{{border-bottom:none}}
  #pmodal-box tr:nth-child(even) td{{background:#fafafa}}
  #pmodal-box a{{color:#2980b9;text-decoration:none;font-weight:600}}
  #pmodal-box a:hover{{text-decoration:underline}}
  .modal-close{{float:right;background:#888;color:#fff;border:none;
                padding:4px 10px;border-radius:3px;cursor:pointer;font-size:12px}}
  .modal-close:hover{{background:#555}}
</style>
</head>
<body id="top">
<div class="top-bar">
  <h1>{page_title}</h1>
  <button class="btn-nav" onclick="document.getElementById('pmodal').classList.add('open')">
    &#9776; Parameters ({n_params})
  </button>
</div>

<!-- Parameters modal -->
<div id="pmodal" onclick="if(event.target===this)closeModal()">
  <div id="pmodal-box">
    <button class="modal-close" onclick="closeModal()">&#10005; Close</button>
    <h2>Parameters on this page ({n_params})</h2>
    <table>
      <thead><tr><th>#</th><th>Parameter Name</th></tr></thead>
      <tbody>{modal_rows}</tbody>
    </table>
  </div>
</div>

{body}
<script>
function closeModal(){{document.getElementById('pmodal').classList.remove('open');}}
// Auto-open modal if no anchor is targeted on load
window.addEventListener('load', function(){{
  if (!window.location.hash) {{
    document.getElementById('pmodal').classList.add('open');
  }}
}});
</script>
</body>
</html>"""


def main():
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
    cr_df = load_concat_csv(CR_CSV)
    vr_df = load_concat_csv(VR_CSV)

    if not cf_df.empty:
        print(f"  GuCorrFactor    : {len(cf_df):,} data rows")
    if not ve_df.empty:
        print(f"  GuVrfyError     : {len(ve_df):,} data rows")
    if not cr_df.empty:
        print(f"  GuCorrRawData   : {len(cr_df):,} data rows")
    if not vr_df.empty:
        print(f"  GuRawData       : {len(vr_df):,} data rows")

    # Build ZipFile -> device ID mapping from GuVrfyError
    zip_device_map = build_zip_device_map(ve_df)
    print(f"  ZipFile->Device map : {len(zip_device_map)} session(s)\n")

    os.makedirs(PLOT_DIR, exist_ok=True)
    outputs_opened = []

    def _save_pages(specs, src_cf, src_ve, is_corr, tag, title_prefix):
        """Paginate and write one HTML-with-anchors file per page into PLOT_DIR."""
        if specs.empty:
            print(f"No parameters to plot for {tag}.\n")
            return
        n_pages = -(-len(specs) // PARAMS_PER_PAGE)
        print(f"=== {tag}: {len(specs)} parameter(s), {n_pages} page(s) ===")
        for page_i, chunk in enumerate(_chunked_df(specs, PARAMS_PER_PAGE), start=1):
            html = _build_html_with_anchors(
                param_specs=chunk,
                cf_df=src_cf, ve_df=src_ve,
                zip_device_map=zip_device_map,
                page_title=f"{title_prefix}  (Page {page_i} / {n_pages})",
                is_corr=is_corr,
            )
            if html is not None:
                fname = f"FailedParams_{tag}_LinePlot_p{page_i:02d}.html"
                out   = os.path.join(PLOT_DIR, fname)
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write(html)
                print(f"  Saved -> {out}")
                outputs_opened.append(out)
        print()

    # All 4 plot sets include every failed parameter regardless of FailType
    _save_pages(param_specs, cf_df,          ve_df, True,  "CorrFactor",
                "Failed GU Parameters — Corr-factor Data — Line Plot")
    _save_pages(param_specs, cf_df,          ve_df, False, "Verify",
                "Failed GU Parameters — Verification Data — Line Plot")

    if not cr_df.empty:
        _save_pages(param_specs, pd.DataFrame(), cr_df, False, "CorrRaw",
                    "Failed GU Parameters — CorrRaw Data — Line Plot")
    else:
        print("No CorrRaw data available.\n")

    if not vr_df.empty:
        _save_pages(param_specs, pd.DataFrame(), vr_df, False, "VryRaw",
                    "Failed GU Parameters — VryRaw Data — Line Plot")
    else:
        print("No VryRaw data available.\n")

    print(f"Done.  {len(outputs_opened)} HTML file(s) saved to {PLOT_DIR}")


if __name__ == "__main__":
    main()
