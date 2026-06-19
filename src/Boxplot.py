import csv
import json
import os
import random
import re
import sys
from pathlib import Path

import pandas as pd

_NO_LIMIT    = 1e100
_SPEC_LABELS = {"Tests#", "Patterns", "Unit", "HighL", "LowL"}

# Hierarchy of grouping columns inside the data CSV
_GROUP_COLS = ["LOT_ID", "SITE", "M_Handler-ArmNo"]

# Colour palette per LOT_ID (cycles if more lots than colours)
_LOT_COLOURS = [
    "#66BB6A", "#42A5F5", "#AB47BC",
    "#FFA726", "#EF5350", "#26C6DA",
    "#FF7043", "#26A69A",
]

# Tester palette (matches LinePlot)
_TESTER_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf",
]
_LOW_COLOUR  = "#d62728"
_HIGH_COLOUR = "#2ca02c"

# ── Paths used by main() ───────────────────────────────────────────────────────
_BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RESULT_DIR  = os.path.join(_BASE_DIR, "result")
_SUMMARY_CSV = os.path.join(_RESULT_DIR, "GuLog_FailedSummary.csv")
_CF_CSV      = os.path.join(_RESULT_DIR, "GuCorrFactor_ALL_CONCAT.csv")
_VE_CSV      = os.path.join(_RESULT_DIR, "GuVrfyError_ALL_CONCAT.csv")
_CR_CSV      = os.path.join(_RESULT_DIR, "Corr_GuCorrRawData_ALL_CONCAT.csv")
_VR_CSV      = os.path.join(_RESULT_DIR, "Vry_GuRawData_ALL_CONCAT.csv")
_BOXPLOT_DIR = os.path.join(_RESULT_DIR, "Plot")


class BoxplotGenerator:
    """
    Generates a box plot for a single parameter from the concatenated dataset.

    Grouping hierarchy (left → right on x-axis):
      Handler ID  (from per-lot JSON header)
        └─ LOT_ID
             └─ SITE
                  └─ M_Handler-ArmNo   ← one box per unique combination

    Spec limits are drawn as red horizontal lines across the full plot width.
    Title is the full parameter name.
    Bottom table shows all four grouping levels for each box.
    """

    def __init__(
        self,
        concat_csv: str,
        output_folder: str = "output/plots",
        header_folder: str = "header",
    ):
        self.concat_csv    = Path(concat_csv)
        self.output_folder = Path(output_folder)
        self.header_folder = Path(header_folder)

    # ------------------------------------------------------------------
    # Header / JSON helpers
    # ------------------------------------------------------------------

    def _load_handler_ids(self, lot_ids: list[str]) -> dict[str, str]:
        """
        Returns {lot_id: handler_id} by searching JSON files in header_folder
        whose filename contains the lot_id as a substring.
        """
        mapping: dict[str, str] = {}
        for lot_id in lot_ids:
            for json_path in self.header_folder.glob("*.json"):
                if lot_id in json_path.stem:
                    with open(json_path, "r", encoding="utf-8") as f:
                        header = json.load(f)
                    mapping[lot_id] = (
                        header.get("Site details", {}).get("Handler ID", "N/A")
                    )
                    break
            if lot_id not in mapping:
                mapping[lot_id] = "N/A"
        return mapping

    # ------------------------------------------------------------------
    # Parameter discovery
    # ------------------------------------------------------------------

    def first_parameter(self, prefix: str = "PR_") -> str:
        """Returns the first parameter column whose name starts with prefix."""
        with open(self.concat_csv, "r", encoding="utf-8-sig", errors="replace") as f:
            for row in csv.reader(f):
                if row and row[0].strip() == "Parameter":
                    for col in row[1:]:
                        if col.strip().startswith(prefix):
                            return col.strip()
        raise ValueError(f"No parameter with prefix '{prefix}' found in {self.concat_csv}")

    # ------------------------------------------------------------------
    # Data reading
    # ------------------------------------------------------------------

    def _read_data(self, param_name: str) -> dict:
        """
        Reads the concat CSV and returns:
          groups      : {(lot_id, site, arm_no): [float, ...]}
          high_limit  : float | None
          low_limit   : float | None
          lot_ids     : sorted list of distinct lot_ids in data
        """
        col_names:   list[str]                        = []
        param_idx:   int | None                       = None
        grp_idx:     dict[str, int | None]            = {c: None for c in _GROUP_COLS}
        high_limit:  float | None                     = None
        low_limit:   float | None                     = None
        groups:      dict[tuple, list[float]]         = {}

        with open(self.concat_csv, "r", encoding="utf-8-sig", errors="replace") as f:
            for row in csv.reader(f):
                if not row:
                    continue
                label = row[0].strip()

                # ── Parameter header row ─────────────────────────────────
                if label == "Parameter":
                    col_names = [c.strip() for c in row]
                    try:
                        param_idx = col_names.index(param_name)
                    except ValueError:
                        raise ValueError(f"Parameter '{param_name}' not found in CSV")
                    for gcol in _GROUP_COLS:
                        try:
                            grp_idx[gcol] = col_names.index(gcol)
                        except ValueError:
                            grp_idx[gcol] = None   # column absent — use placeholder
                    continue

                # ── Spec rows ────────────────────────────────────────────
                if label == "HighL" and param_idx is not None:
                    try:
                        v = float(row[param_idx])
                        if abs(v) < _NO_LIMIT:
                            high_limit = v
                    except (ValueError, IndexError):
                        pass
                    continue

                if label == "LowL" and param_idx is not None:
                    try:
                        v = float(row[param_idx])
                        if abs(v) < _NO_LIMIT:
                            low_limit = v
                    except (ValueError, IndexError):
                        pass
                    continue

                if label in _SPEC_LABELS:
                    continue

                # ── Data rows ────────────────────────────────────────────
                if param_idx is None:
                    continue
                try:
                    val = float(row[param_idx])
                except (ValueError, IndexError):
                    continue

                def _get(col: str, default: str) -> str:
                    idx = grp_idx.get(col)
                    if idx is None or idx >= len(row):
                        return default
                    return row[idx].strip() or default

                lot_id = _get("LOT_ID",          "Unknown")
                site   = _get("SITE",             "0")
                arm_no = _get("M_Handler-ArmNo",  "0")

                # Normalise arm_no: "1.0" → "1"
                try:
                    arm_no = str(int(float(arm_no)))
                except ValueError:
                    pass

                groups.setdefault((lot_id, site, arm_no), []).append(val)

        lot_ids = sorted({k[0] for k in groups})
        return {
            "groups":     groups,
            "high_limit": high_limit,
            "low_limit":  low_limit,
            "lot_ids":    lot_ids,
        }

    # ------------------------------------------------------------------
    # Table helper
    # ------------------------------------------------------------------

    @staticmethod
    def _draw_merged_table(
        ax_tbl,
        table_rows:   list,
        table_labels: list,
        plt,
        lot_sep_cols: list[int] | None = None,
        n_boxes:      int               = 0,
    ) -> None:
        """
        Draw a table on ax_tbl using the SAME x-coordinate system as the
        boxplot (box i centred at i, spanning i-0.5 … i+0.5).  This
        guarantees that LOT-boundary cell edges align exactly with the
        dashed separator lines in the plot above.

        Row labels are drawn to the left of x = -0.5 with clip_on=False.
        lot_sep_cols: column indices where a LOT boundary occurs — merging
                      is suppressed at these positions.
        """
        n_rows = len(table_rows)
        n_cols = len(table_rows[0]) if table_rows else 0
        if n_rows == 0 or n_cols == 0:
            return

        # Mirror the boxplot x-axis exactly
        x_min   = -0.5
        x_max   = n_boxes - 0.5
        # Label column sits left of x_min (width ≈ 18 % of data span)
        label_w = max(0.5, n_cols * 0.22)

        row_h = 1.0 / n_rows

        LABEL_BG = "#F0F0F0"
        EVEN_BG  = "#FAFAFA"
        ODD_BG   = "white"
        BORDER   = "#CCCCCC"

        ax_tbl.set_xlim(x_min, x_max)
        ax_tbl.set_ylim(0.0, 1.0)

        sep_set = set(lot_sep_cols) if lot_sep_cols else set()

        for row_i, (label, row_data) in enumerate(zip(table_labels, table_rows)):
            y0 = 1.0 - (row_i + 1) * row_h
            yc = y0 + row_h / 2

            # ── Row label cell (left of axes boundary) ───────────────────
            ax_tbl.add_patch(plt.Rectangle(
                (x_min - label_w, y0), label_w, row_h,
                fc=LABEL_BG, ec=BORDER, lw=0.5, zorder=1,
                transform=ax_tbl.transData, clip_on=False,
            ))
            ax_tbl.text(
                x_min - label_w / 2, yc, label,
                ha="center", va="center", fontsize=8, fontweight="bold",
                transform=ax_tbl.transData, clip_on=False,
            )

            # ── Group consecutive identical values (never across LOT boundary) ─
            groups: list[tuple[int, int, str]] = []
            start = 0
            for j in range(1, len(row_data)):
                if str(row_data[j]) != str(row_data[j - 1]) or j in sep_set:
                    groups.append((start, j - 1, str(row_data[start])))
                    start = j
            groups.append((start, len(row_data) - 1, str(row_data[start])))

            bg = EVEN_BG if row_i % 2 == 0 else ODD_BG

            for sc, ec, val in groups:
                cell_x0 = sc - 0.5
                cell_w  = float(ec - sc + 1)
                cell_xc = (sc + ec) / 2.0

                ax_tbl.add_patch(plt.Rectangle(
                    (cell_x0, y0), cell_w, row_h,
                    fc=bg, ec=BORDER, lw=0.5, zorder=1,
                    transform=ax_tbl.transData, clip_on=False,
                ))
                ax_tbl.text(
                    cell_xc, yc, val,
                    ha="center", va="center", fontsize=8,
                    transform=ax_tbl.transData,
                )


    # ------------------------------------------------------------------
    # Plot
    # ------------------------------------------------------------------

    def plot(
        self,
        param_name:      str | None = None,
        output_filename: str | None = None,
        prefix:          str        = "PR_",
    ) -> str:
        """
        Generates and saves a box plot PNG.
        If param_name is None the first parameter matching prefix is used.
        Returns the path to the saved file.
        """
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec

        self.output_folder.mkdir(parents=True, exist_ok=True)
        random.seed(42)

        if param_name is None:
            param_name = self.first_parameter(prefix)

        print(f"  Parameter : {param_name}")
        result     = self._read_data(param_name)
        groups     = result["groups"]
        high_limit = result["high_limit"]
        low_limit  = result["low_limit"]
        lot_ids    = result["lot_ids"]

        if not groups:
            print("  No data found — skipping.")
            return ""

        # Load Handler IDs from JSON headers
        handler_map = self._load_handler_ids(lot_ids)

        # ── Sort groups: M_Handler-ArmNo → SITE → Handler ID → LOT_ID ──────
        def _sort_key(key: tuple) -> tuple:
            lot, site, arm = key
            try:
                arm_n = float(arm)
            except ValueError:
                arm_n = 0
            return (arm_n, site, handler_map.get(lot, ""), lot)

        sorted_keys = sorted(groups.keys(), key=_sort_key)
        values      = [groups[k] for k in sorted_keys]
        n_boxes     = len(sorted_keys)
        positions   = list(range(n_boxes))

        # ── Colour per LOT_ID ────────────────────────────────────────────
        lot_colour = {
            lid: _LOT_COLOURS[i % len(_LOT_COLOURS)]
            for i, lid in enumerate(lot_ids)
        }

        # ── Build bottom-table data ──────────────────────────────────────
        # Row order top→bottom: M_Handler-ArmNo, SITE, Handler ID, LOT_ID
        arm_row     = [k[2] for k in sorted_keys]
        site_row    = [k[1] for k in sorted_keys]
        handler_row = [handler_map.get(k[0], "N/A") for k in sorted_keys]
        lot_row     = [k[0] for k in sorted_keys]

        table_rows   = [arm_row, site_row, handler_row, lot_row]
        table_labels = ["M_Handler-ArmNo", "Site", "Handler ID", "LOT_ID"]
        n_tbl_rows   = len(table_rows)

        # ── Figure layout ────────────────────────────────────────────────
        fig_w  = max(10, n_boxes * 1.8 + 3)
        tbl_h  = n_tbl_rows * 0.35          # height fraction for table
        fig    = plt.figure(figsize=(fig_w, 9), facecolor="white")
        gs     = gridspec.GridSpec(
            2, 1,
            figure=fig,
            height_ratios=[5, tbl_h],
            hspace=0,
        )
        ax     = fig.add_subplot(gs[0])
        ax_tbl = fig.add_subplot(gs[1])
        ax_tbl.axis("off")

        # ── Box plots (red, white-filled) ────────────────────────────────
        ax.boxplot(
            values,
            positions=positions,
            widths=0.55,
            patch_artist=True,
            boxprops=dict(facecolor="white", color="red", linewidth=1.5),
            medianprops=dict(color="red", linewidth=2),
            whiskerprops=dict(color="red", linewidth=1.5),
            capprops=dict(color="red", linewidth=1.5),
            showfliers=False,
        )

        # ── Scatter overlay (jittered, coloured by LOT_ID) ───────────────
        for i, (key, vals) in enumerate(zip(sorted_keys, values)):
            colour   = lot_colour[key[0]]
            jittered = [i + random.uniform(-0.15, 0.15) for _ in vals]
            ax.scatter(jittered, vals, alpha=0.45, s=12, color=colour, zorder=3)

        # ── Spec limits — red horizontal lines ───────────────────────────
        spec_kw = dict(color="red", linewidth=2, linestyle="-", zorder=5)
        if high_limit is not None:
            ax.axhline(high_limit, **spec_kw, label=f"HighL = {high_limit:.4g}")
        if low_limit is not None:
            ax.axhline(low_limit,  **spec_kw, label=f"LowL  = {low_limit:.4g}")

        # ── Legend: LOT_ID colours + Site info + spec limits ────────────────
        for lot_id, colour in lot_colour.items():
            sites = sorted({k[1] for k in sorted_keys if k[0] == lot_id})
            site_str = ", ".join(sites)
            ax.scatter([], [], color=colour, s=30, alpha=0.8,
                       label=f"LOT: {lot_id}  |  Site: {site_str}")

        ax.legend(fontsize=8, loc="upper right", framealpha=0.8)

        # ── Group separators: vertical dashed lines between LOT_IDs ──────
        lot_sep_cols: list[int] = []
        for i in range(1, n_boxes):
            if sorted_keys[i][0] != sorted_keys[i - 1][0]:
                ax.axvline(x=i - 0.5, color="#AAAAAA", linewidth=1.2,
                           linestyle="--", zorder=2)
                lot_sep_cols.append(i)

        # ── Axis styling ─────────────────────────────────────────────────
        ax.set_title(
            param_name,
            fontsize=9,
            fontweight="bold",
            pad=10,
            wrap=True,
            loc="center",
        )
        ax.set_ylabel("Value", fontsize=11)
        ax.set_xlabel("Measurement Index", fontsize=10)
        ax.set_xlim(-0.5, n_boxes - 0.5)
        ax.set_xticks(positions)
        ax.set_xticklabels([str(i + 1) for i in positions], fontsize=7)
        ax.tick_params(axis="x", which="both", bottom=True)
        ax.grid(axis="y", linestyle="--", alpha=0.35)
        ax.spines["bottom"].set_visible(True)

        # ── Bottom metadata table (merged cells for identical adjacent values) ─
        self._draw_merged_table(ax_tbl, table_rows, table_labels, plt, lot_sep_cols, n_boxes)

        # ── Save ─────────────────────────────────────────────────────────
        if output_filename is None:
            safe = (
                param_name[:80]
                .replace("/", "_")
                .replace("\\", "_")
                .replace(":", "_")
            )
            output_filename = f"{safe}.png"

        out_path = self.output_folder / output_filename
        plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig)

        counts = {f"{k[0]}|s{k[1]}|arm{k[2]}": len(groups[k]) for k in sorted_keys}
        print(f"  Groups    : {counts}")
        print(f"  HighL     : {high_limit}   LowL : {low_limit}")
        print(f"  Plot saved -> {out_path}")
        return str(out_path)


# ── Module-level helpers for failed-parameter box plots ───────────────────────

def _load_concat_csv(path: str) -> pd.DataFrame:
    """Load a GuCorrFactor / GuVrfyError ALL_CONCAT CSV (rows 5+ are data)."""
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


def _find_col(df: pd.DataFrame, param_name: str) -> str | None:
    """Exact match first, then partial match."""
    if param_name in df.columns:
        return param_name
    matches = [c for c in df.columns if param_name in c]
    return matches[0] if matches else None


def _build_box_plot(
    param_name: str,
    low_l: float,
    high_l: float,
    src_df: pd.DataFrame,
    out_dir: str,
    fail_label: str,
) -> str:
    """
    Generate an interactive Plotly HTML box plot for one failed parameter.
    Groups data by TesterName (one box per tester).
    Each individual point shows PID (Verify) or ZipFile (CorrFactor) on hover.
    TesterName is shown on the x-axis; legend shows spec limits only.
    Returns the saved file path, or "" if no data.
    """
    import plotly.graph_objects as go

    col = _find_col(src_df, param_name)
    if col is None:
        print(f"  SKIP (column not found): {param_name[:70]}")
        return ""

    # Pull only the columns we need
    extra_cols = [c for c in ("Parameter", "ZipFile", "M_Handler-ArmNo") if c in src_df.columns]
    sub = src_df[["TesterName", col] + extra_cols].copy()
    sub[col] = pd.to_numeric(sub[col], errors="coerce")
    sub = sub.dropna(subset=[col])
    if "M_Handler-ArmNo" not in sub.columns:
        sub["M_Handler-ArmNo"] = "N/A"
    if sub.empty:
        print(f"  SKIP (no data): {param_name[:70]}")
        return ""

    tester_names  = sorted(sub["TesterName"].unique())
    tester_colour = {
        t: _TESTER_PALETTE[i % len(_TESTER_PALETTE)]
        for i, t in enumerate(tester_names)
    }

    fig = go.Figure()

    for tester in tester_names:
        td     = sub[sub["TesterName"] == tester].reset_index(drop=True)
        colour = tester_colour[tester]

        # Hover label: PID for Verify rows, ZipFile for CorrFactor rows
        if ("Parameter" in td.columns
                and td["Parameter"].astype(str).str.startswith("PID-").any()):
            point_ids  = td["Parameter"].str.replace("PID-", "", regex=False)
            id_label   = "PID"
        elif "ZipFile" in td.columns:
            point_ids = td["ZipFile"]
            id_label  = "ZipFile"
        else:
            point_ids = pd.Series([str(i + 1) for i in range(len(td))])
            id_label  = "Index"

        hover_texts = [
            f"<b>Tester:</b> {tester}<br>"
            f"<b>{id_label}:</b> {pid}<br>"
            f"<b>ArmNo:</b> {arm}<br>"
            f"<b>Value:</b> {val:.6g}"
            for pid, arm, val in zip(point_ids, td["M_Handler-ArmNo"], td[col])
        ]

        fig.add_trace(go.Box(
            y=td[col].tolist(),
            name=tester,
            boxpoints="all",        # show every individual point
            jitter=0.4,
            pointpos=0,
            marker=dict(color=colour, size=6, opacity=0.6),
            line=dict(color=colour),
            fillcolor="rgba(255,255,255,0.6)",
            text=hover_texts,
            hovertemplate="%{text}<extra></extra>",
            showlegend=False,       # tester name already on x-axis
        ))

    # Spec limit lines + legend entries
    if low_l is not None:
        fig.add_hline(
            y=low_l,
            line_dash="dash", line_color=_LOW_COLOUR, line_width=1.5,
            annotation_text=f"LowL = {low_l:g}",
            annotation_font_color=_LOW_COLOUR,
            annotation_position="bottom right",
        )
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="lines",
            name=f"--- LowL = {low_l:g}",
            line=dict(color=_LOW_COLOUR, dash="dash", width=1.5),
            showlegend=True,
        ))

    if high_l is not None:
        fig.add_hline(
            y=high_l,
            line_dash="dash", line_color=_HIGH_COLOUR, line_width=1.5,
            annotation_text=f"HighL = {high_l:g}",
            annotation_font_color=_HIGH_COLOUR,
            annotation_position="top right",
        )
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="lines",
            name=f"--- HighL = {high_l:g}",
            line=dict(color=_HIGH_COLOUR, dash="dash", width=1.5),
            showlegend=True,
        ))

    fig.update_layout(
        title=dict(
            text=f"<b>[{fail_label}]  {param_name}</b>",
            x=0.5, xanchor="center",
            font=dict(size=16),
        ),
        yaxis_title="Measured Value",
        xaxis_title="TesterName",
        template="plotly_white",
        height=600,
        legend=dict(
            title=dict(text="<b>Spec Limits</b>"),
            orientation="v",
            x=1.01, y=1.0,
            xanchor="left", yanchor="top",
            bordercolor="#cccccc", borderwidth=1,
            font=dict(size=12),
        ),
        hovermode="closest",
        margin=dict(l=80, r=200, t=80, b=80),
    )

    # _build_box_plot is kept for single-param use; paginated flow uses _build_box_page below.
    safe     = param_name[:80].replace("/", "_").replace("\\", "_").replace(":", "_")
    out_path = os.path.join(out_dir, f"{fail_label}_{safe}.html")
    fig.write_html(out_path, include_plotlyjs="cdn")

    print(f"  OK  {len(sub)} point(s)  {len(tester_names)} tester(s)  ->  {out_path}")
    return out_path


_PARAMS_PER_PAGE = 20


def _chunked(df: pd.DataFrame, size: int):
    """Yield successive slices of a DataFrame."""
    for i in range(0, len(df), size):
        yield df.iloc[i : i + size].reset_index(drop=True)


def _safe_id(name: str) -> str:
    """Return a valid HTML element ID from a parameter name."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def _build_box_html_with_anchors(
    param_specs: pd.DataFrame,
    src_df: pd.DataFrame,
    fail_label: str,
    page_title: str,
    show_spec: bool = True,
    cf_device_map: "dict | None" = None,
) -> "str | None":
    """
    Build a standalone HTML page with one Plotly box-plot figure per param.
    Each section gets <div id="{safe_id}"> for direct URL-fragment navigation.
    Returns None if no params are plottable.
    """
    import plotly.graph_objects as go

    if param_specs.empty:
        return None

    N        = len(param_specs)
    sections: list = []
    tester_colour: dict = {}
    colour_idx = 0

    for row_i, (_, spec) in enumerate(param_specs.iterrows(), start=1):
        param_name = spec["ParamName"]
        low_l      = float(spec["LowL"])
        high_l     = float(spec["HighL"])

        if src_df.empty:
            print(f"  [{row_i}/{N}] SKIP (empty source): {param_name[:70]}")
            continue
        col = _find_col(src_df, param_name)
        if col is None:
            print(f"  [{row_i}/{N}] SKIP (column not found): {param_name[:70]}")
            continue

        extra_cols = [c for c in ("Parameter", "ZipFile", "M_Handler-ArmNo") if c in src_df.columns]
        sub = src_df[["TesterName", col] + extra_cols].copy()
        sub[col] = pd.to_numeric(sub[col], errors="coerce")
        sub = sub.dropna(subset=[col])
        if "M_Handler-ArmNo" not in sub.columns:
            sub["M_Handler-ArmNo"] = "N/A"
        if sub.empty:
            print(f"  [{row_i}/{N}] SKIP (no data): {param_name[:70]}")
            continue

        tester_names = sorted(sub["TesterName"].unique())
        for t in tester_names:
            if t not in tester_colour:
                tester_colour[t] = _TESTER_PALETTE[colour_idx % len(_TESTER_PALETTE)]
                colour_idx += 1

        fig = go.Figure()

        for tester in tester_names:
            colour = tester_colour[tester]
            td     = sub[sub["TesterName"] == tester].reset_index(drop=True)

            if ("Parameter" in td.columns
                    and td["Parameter"].astype(str).str.startswith("PID-").any()):
                point_ids = td["Parameter"].str.replace("PID-", "", regex=False)
                id_label  = "PID"
            elif "ZipFile" in td.columns:
                _dm = cf_device_map or {}
                point_ids = [_dm.get(str(zf), f"*{i + 1}")
                             for i, zf in enumerate(td["ZipFile"])]
                id_label  = "PID"
            else:
                point_ids = pd.Series([str(i + 1) for i in range(len(td))])
                id_label  = "Index"

            hover_texts = [
                f"<b>PID:</b> {pid}<br>"
                f"<b>ArmNo:</b> {arm}<br>"
                f"<b>Value:</b> {val:.6g}"
                for pid, arm, val in zip(point_ids, td["M_Handler-ArmNo"], td[col])
            ]
            fig.add_trace(go.Box(
                y=td[col].tolist(), name=tester,
                boxpoints="all", jitter=0.4, pointpos=0,
                marker=dict(color=colour, size=6, opacity=0.6),
                line=dict(color=colour),
                fillcolor="rgba(255,255,255,0.6)",
                text=hover_texts,
                hovertemplate="%{text}<extra></extra>",
                showlegend=False,
            ))

        if show_spec:
            fig.add_hline(y=low_l, line_dash="dash", line_color=_LOW_COLOUR, line_width=1.5,
                          annotation_text=f"LowL = {low_l:g}",
                          annotation_font_color=_LOW_COLOUR,
                          annotation_position="bottom right")
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                                      name=f"--- LowL = {low_l:g}",
                                      line=dict(color=_LOW_COLOUR, dash="dash", width=1.5),
                                      showlegend=True))
            fig.add_hline(y=high_l, line_dash="dash", line_color=_HIGH_COLOUR, line_width=1.5,
                          annotation_text=f"HighL = {high_l:g}",
                          annotation_font_color=_HIGH_COLOUR,
                          annotation_position="top right")
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                                      name=f"--- HighL = {high_l:g}",
                                      line=dict(color=_HIGH_COLOUR, dash="dash", width=1.5),
                                      showlegend=True))

        fig.update_layout(
            title=dict(
                text=f"<b>[{fail_label}]  {param_name}</b>",
                x=0.5, xanchor="center", font=dict(size=16),
            ),
            height=600,
            yaxis_title="Measured Value",
            xaxis_title="TesterName",
            template="plotly_white",
            legend=dict(
                title=dict(text="<b>Spec Limits</b>"),
                orientation="v", x=1.01, y=1.0,
                xanchor="left", yanchor="top",
                bordercolor="#cccccc", borderwidth=1,
                font=dict(size=12),
            ),
            hovermode="closest",
            margin=dict(l=80, r=200, t=80, b=80),
        )

        safe_id    = _safe_id(param_name)
        include_js = "cdn" if not sections else False
        fig_div    = fig.to_html(include_plotlyjs=include_js, full_html=False,
                                  div_id=f"bp-{safe_id}")
        print(f"  [{row_i}/{N}] OK  {len(tester_names)} tester(s)  {len(sub)} point(s)   {param_name[:70]}")
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


def _build_box_page(
    param_specs: pd.DataFrame,
    src_df: pd.DataFrame,
    fail_label: str,
    page_title: str,
) -> "go.Figure | None":
    """
    Build one Plotly figure with up to _PARAMS_PER_PAGE box-plot subplots.
    One subplot per ParamName; one go.Box trace per TesterName.
    TesterName shown on each subplot's x-axis; legend shows spec limits only.
    Individual points show PID (Verify) or ZipFile (CorrFactor) on hover.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    if param_specs.empty:
        return None

    N = len(param_specs)
    subplot_titles = [
        f"[{fail_label}]  {r['ParamName']}"
        for _, r in param_specs.iterrows()
    ]

    v_spacing = max(0.06, min(0.10, 0.8 / max(N, 1)))
    fig = make_subplots(
        rows=N, cols=1,
        subplot_titles=subplot_titles,
        vertical_spacing=v_spacing,
        shared_xaxes=False,
    )

    tester_colour: dict[str, str] = {}
    colour_idx = 0

    for row_i, (_, spec) in enumerate(param_specs.iterrows(), start=1):
        param_name = spec["ParamName"]
        low_l      = float(spec["LowL"])
        high_l     = float(spec["HighL"])

        if src_df.empty:
            print(f"  [{row_i}/{N}] SKIP (empty source): {param_name[:70]}")
            continue

        col = _find_col(src_df, param_name)
        if col is None:
            print(f"  [{row_i}/{N}] SKIP (column not found): {param_name[:70]}")
            continue

        extra_cols = [c for c in ("Parameter", "ZipFile", "M_Handler-ArmNo") if c in src_df.columns]
        sub = src_df[["TesterName", col] + extra_cols].copy()
        sub[col] = pd.to_numeric(sub[col], errors="coerce")
        sub = sub.dropna(subset=[col])
        if "M_Handler-ArmNo" not in sub.columns:
            sub["M_Handler-ArmNo"] = "N/A"

        if sub.empty:
            print(f"  [{row_i}/{N}] SKIP (no data): {param_name[:70]}")
            continue

        tester_names = sorted(sub["TesterName"].unique())

        for tester in tester_names:
            if tester not in tester_colour:
                tester_colour[tester] = _TESTER_PALETTE[colour_idx % len(_TESTER_PALETTE)]
                colour_idx += 1
            colour = tester_colour[tester]

            td = sub[sub["TesterName"] == tester].reset_index(drop=True)

            if ("Parameter" in td.columns
                    and td["Parameter"].astype(str).str.startswith("PID-").any()):
                point_ids = td["Parameter"].str.replace("PID-", "", regex=False)
                id_label  = "PID"
            elif "ZipFile" in td.columns:
                point_ids = td["ZipFile"]
                id_label  = "ZipFile"
            else:
                point_ids = pd.Series([str(i + 1) for i in range(len(td))])
                id_label  = "Index"

            hover_texts = [
                f"<b>PID:</b> {pid}<br>"
                f"<b>ArmNo:</b> {arm}<br>"
                f"<b>Value:</b> {val:.6g}"
                for pid, arm, val in zip(point_ids, td["M_Handler-ArmNo"], td[col])
            ]

            fig.add_trace(
                go.Box(
                    y=td[col].tolist(),
                    name=tester,
                    boxpoints="all",
                    jitter=0.4,
                    pointpos=0,
                    marker=dict(color=colour, size=6, opacity=0.6),
                    line=dict(color=colour),
                    fillcolor="rgba(255,255,255,0.6)",
                    text=hover_texts,
                    hovertemplate="%{text}<extra></extra>",
                    showlegend=False,
                ),
                row=row_i, col=1,
            )

        # Spec limit lines
        fig.add_hline(
            y=low_l,
            line_dash="dash", line_color=_LOW_COLOUR, line_width=1.5,
            annotation_text=f"LowL = {low_l:g}",
            annotation_font_color=_LOW_COLOUR,
            annotation_position="bottom right",
            row=row_i, col=1,
        )
        fig.add_hline(
            y=high_l,
            line_dash="dash", line_color=_HIGH_COLOUR, line_width=1.5,
            annotation_text=f"HighL = {high_l:g}",
            annotation_font_color=_HIGH_COLOUR,
            annotation_position="top right",
            row=row_i, col=1,
        )

        fig.update_xaxes(title_text="TesterName",     row=row_i, col=1)
        fig.update_yaxes(title_text="Measured Value", row=row_i, col=1)

        print(f"  [{row_i}/{N}] OK  {len(tester_names)} tester(s)  {len(sub)} point(s)   {param_name[:70]}")

    # Spec-limit dummy legend entries (same style as LinePlot)
    fig.add_trace(
        go.Scatter(x=[None], y=[None], mode="lines",
                   name="--- LowL spec", legendgroup="__lowl__", showlegend=True,
                   line=dict(color=_LOW_COLOUR, dash="dash", width=1.5)),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=[None], y=[None], mode="lines",
                   name="--- HighL spec", legendgroup="__highl__", showlegend=True,
                   line=dict(color=_HIGH_COLOUR, dash="dash", width=1.5)),
        row=1, col=1,
    )

    fig.update_layout(
        title=dict(
            text=f"<b>{page_title}</b>",
            x=0.5, xanchor="center",
            font=dict(size=20),
        ),
        height=max(700, N * 450),
        template="plotly_white",
        legend=dict(
            title=dict(text="<b>Spec Limits</b>"),
            orientation="v",
            x=1.01, y=1.0,
            xanchor="left", yanchor="top",
            bordercolor="#cccccc", borderwidth=1,
            font=dict(size=12),
        ),
        hovermode="closest",
        margin=dict(l=80, r=200, t=100, b=60),
    )

    return fig


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(_SUMMARY_CSV):
        print(f"ERROR: {_SUMMARY_CSV} not found.\nRun main.py first to generate result files.")
        sys.exit(1)

    with open(_SUMMARY_CSV, encoding="utf-8") as f:
        peek = f.readline().strip()

    if peek.startswith("All Pass"):
        print("GuLog_FailedSummary.csv reports All Pass -- nothing to plot.")
        sys.exit(0)

    summary_df = pd.read_csv(_SUMMARY_CSV)

    param_specs = (
        summary_df
        .groupby(["ParamName", "FailType"], sort=False)
        .agg(LowL=("LowL", "first"), HighL=("HighL", "first"))
        .reset_index()
    )

    N = len(param_specs)
    print(f"GuLog_FailedSummary: {len(summary_df)} failure rows -> {N} unique parameter(s) to plot\n")

    cf_df = _load_concat_csv(_CF_CSV)
    ve_df = _load_concat_csv(_VE_CSV)
    cr_df = _load_concat_csv(_CR_CSV)
    vr_df = _load_concat_csv(_VR_CSV)

    # Build ZipFile -> "#PID1,#PID2,..." from CorrRawData for CF labeling
    cf_device_map: dict = {}
    if not cr_df.empty and all(c in cr_df.columns for c in ("ZipFile", "Parameter")):
        for _zf, _grp in cr_df.groupby("ZipFile"):
            _pids = sorted({
                str(p).replace("PID-", "")
                for p in _grp["Parameter"].unique()
                if str(p).startswith("PID-")
            })
            if _pids:
                cf_device_map[str(_zf)] = ",".join("#" + p for p in _pids)

    if not cf_df.empty:
        print(f"  GuCorrFactor    : {len(cf_df):,} data rows")
    if not ve_df.empty:
        print(f"  GuVrfyError     : {len(ve_df):,} data rows")
    if not cr_df.empty:
        print(f"  GuCorrRawData   : {len(cr_df):,} data rows")
    if not vr_df.empty:
        print(f"  GuRawData       : {len(vr_df):,} data rows")

    os.makedirs(_BOXPLOT_DIR, exist_ok=True)

    outputs = []

    def _run_pages(specs, src_df, fail_label, tag, show_spec=True):
        """Paginate _build_box_html_with_anchors and write HTML files for one data set."""
        if specs.empty:
            print(f"No parameters to plot for {tag}.\n")
            return
        n_pages = -(-len(specs) // _PARAMS_PER_PAGE)
        print(f"\n=== {tag}: {len(specs)} parameter(s), {n_pages} page(s) ===")
        for page_i, chunk in enumerate(_chunked(specs, _PARAMS_PER_PAGE), start=1):
            title = (f"Failed GU Parameters — {tag} Data — Box Plot"
                     f"  (Page {page_i} / {n_pages})")
            html = _build_box_html_with_anchors(chunk, src_df, fail_label, title, show_spec=show_spec,
                                                 cf_device_map=cf_device_map)
            if html is not None:
                out_path = os.path.join(_BOXPLOT_DIR,
                                        f"FailedParams_{tag}_BoxPlot_p{page_i:02d}.html")
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(html)
                print(f"  Saved -> {out_path}\n")
                outputs.append(out_path)

    # All 4 plot sets include every failed parameter regardless of FailType
    _run_pages(param_specs, cf_df, "CorrFactor", "CorrFactor")
    _run_pages(param_specs, ve_df, "Verify",     "Verify")
    if not cr_df.empty:
        _run_pages(param_specs, cr_df, "CorrRaw", "CorrRaw", show_spec=False)
    else:
        print("No CorrRaw data to plot.\n")
    if not vr_df.empty:
        _run_pages(param_specs, vr_df, "VryRaw", "VryRaw", show_spec=False)
    else:
        print("No VryRaw data to plot.\n")

    print(f"\nDone.  {len(outputs)} HTML file(s) saved to {_BOXPLOT_DIR}")


if __name__ == "__main__":
    main()
