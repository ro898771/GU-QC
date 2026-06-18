"""
plot.py
=======
CLI entry point for plot generation.
Run this script after main.py has produced the result/ folder.

Usage:
  py src/plot.py
"""

import os
import sys


def _run_lineplot():
    from LinePlot import main
    main()
    generate_summary_html("LinePlot")


def _run_boxplot():
    from Boxplot import main
    main()
    generate_summary_html("BoxPlot")


def generate_summary_html(plot_type: str) -> None:
    """
    Build result/Plot/Summary.html — a styled table of every row in
    GuLog_FailedSummary.csv with clickable links into the paginated plot files.

    Columns:
      TesterName | Product | Sublot | Device | FailType | ParamName |
      LowL | MeasureError | HighL |
      Corr_Factor | Verify_Error | Raw_B4Final | Raw_B4VryError |
      Date | FinishTime
    """
    import json
    import pandas as pd

    BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    RESULT_DIR  = os.path.join(BASE_DIR, "result")
    SUMMARY_CSV = os.path.join(RESULT_DIR, "GuLog_FailedSummary.csv")
    PLOT_DIR    = os.path.join(RESULT_DIR, "Plot")
    OUT_HTML    = os.path.join(PLOT_DIR, "Summary.html")
    PER_PAGE    = 20

    if not os.path.exists(SUMMARY_CSV):
        print("  [SKIP] GuLog_FailedSummary.csv not found — no summary table.")
        return

    with open(SUMMARY_CSV, encoding="utf-8") as f:
        if f.readline().strip().startswith("All Pass"):
            print("  All Pass — no summary table to generate.")
            return

    df = pd.read_csv(SUMMARY_CSV)
    if df.empty:
        return

    # Unique (ParamName, FailType) in first-seen order — same ordering used
    # by LinePlot/Boxplot generators for all 4 plot sets.
    param_specs = (
        df.groupby(["ParamName", "FailType"], sort=False)
        .first()
        .reset_index()
    )

    # Single unified page map keyed on (ParamName, FailType)
    all_pages = {
        (row["ParamName"], row["FailType"]): (i // PER_PAGE) + 1
        for i, (_, row) in enumerate(param_specs.iterrows())
    }

    # Modal data: unique (ParamName, FailType) pairs as JSON for JS modal
    modal_json = json.dumps(
        [{"p": r["ParamName"], "f": r["FailType"]}
         for _, r in param_specs.iterrows()],
        ensure_ascii=False,
    )

    import re

    def _safe_id(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "_", str(name))

    # Summary.html lives inside Plot/ — links are same-directory
    def _link(tag: str, page: int, label: str, param: str) -> str:
        fname  = f"FailedParams_{tag}_{plot_type}_p{page:02d}.html"
        anchor = _safe_id(param)
        if not os.path.exists(os.path.join(PLOT_DIR, fname)):
            return '<span class="na">—</span>'
        return f'<a href="{fname}#{anchor}" target="_blank">{label} p{page}</a>'

    def _esc(v) -> str:
        return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _dev(v) -> str:
        """Return device as a whole number string (strip trailing .0)."""
        try:
            return str(int(float(str(v))))
        except (ValueError, TypeError):
            return _esc(v)

    rows_html = []
    for _, row in df.iterrows():
        param     = row.get("ParamName", "")
        fail_type = str(row.get("FailType", ""))
        page      = all_pages.get((param, fail_type), 1)

        # All 4 link columns shown for every row; os.path.exists check handles missing files
        col_corr_factor  = _link("CorrFactor", page, "CorrFactor", param)
        col_verify_error = _link("Verify",     page, "Verify",     param)
        col_raw_b4final  = _link("CorrRaw",    page, "CorrRaw",    param)
        col_raw_b4vry    = _link("VryRaw",     page, "VryRaw",     param)

        rows_html.append(
            f"<tr>"
            f"<td>{_esc(row.get('TesterName',''))}</td>"
            f"<td>{_esc(row.get('Product',''))}</td>"
            f"<td>{_esc(row.get('Sublot',''))}</td>"
            f"<td>{_dev(row.get('Device',''))}</td>"
            f"<td>{_esc(fail_type)}</td>"
            f"<td class='param'>{_esc(param)}</td>"
            f"<td>{_esc(row.get('LowL',''))}</td>"
            f"<td>{_esc(row.get('MeasureError',''))}</td>"
            f"<td>{_esc(row.get('HighL',''))}</td>"
            f"<td>{col_corr_factor}</td>"
            f"<td>{col_verify_error}</td>"
            f"<td>{col_raw_b4final}</td>"
            f"<td>{col_raw_b4vry}</td>"
            f"<td>{_esc(row.get('Date',''))}</td>"
            f"<td>{_esc(row.get('FinishTime',''))}</td>"
            f"</tr>"
        )

    details_path = "../../Info/TzerMingCalculation.png"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>GU-QC Failure Summary</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:"Segoe UI",Arial,sans-serif;font-size:12px;background:#f0f2f5;padding:20px}}
  .top-bar{{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;gap:8px}}
  h1{{font-size:20px;color:#2c3e50;flex:1}}
  .top-btns{{display:flex;gap:8px;white-space:nowrap}}
  .btn{{background:#2c3e50;color:#fff;border:none;padding:6px 14px;border-radius:4px;
        cursor:pointer;font-size:12px;text-decoration:none;white-space:nowrap}}
  .btn:hover{{background:#1a252f}}
  .btn-blue{{background:#1a6b9a}}.btn-blue:hover{{background:#135580}}
  .meta{{color:#666;margin-bottom:16px;font-size:11px}}
  .wrap{{overflow-x:auto}}
  table{{border-collapse:collapse;background:#fff;min-width:100%;
         box-shadow:0 1px 4px rgba(0,0,0,.12);border-radius:6px;overflow:hidden}}
  thead th{{background:#2c3e50;color:#fff;padding:6px 10px;text-align:left;
             white-space:nowrap;position:sticky;top:0;z-index:1;
             font-size:11px;letter-spacing:.4px;vertical-align:top}}
  thead th.lk{{background:#1a6b9a}}
  thead th input{{display:block;margin-top:4px;width:100%;padding:3px 5px;
                  font-size:11px;border:1px solid #5a7fa0;border-radius:3px;
                  background:#eaf3fb;color:#1a252f;outline:none}}
  td{{padding:6px 10px;border-bottom:1px solid #eee;white-space:nowrap}}
  td.param{{white-space:normal;max-width:300px;word-break:break-word}}
  tr:last-child td{{border-bottom:none}}
  tr:nth-child(even) td{{background:#fafafa}}
  tr:hover td{{background:#e8f4fd}}
  a{{color:#2980b9;text-decoration:none;font-weight:600}}
  a:hover{{text-decoration:underline}}
  .na{{color:#bbb}}
  /* Modal */
  #modal{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);
          z-index:999;align-items:center;justify-content:center}}
  #modal.open{{display:flex}}
  #modal-box{{background:#fff;padding:28px;border-radius:8px;
              width:90vw;max-width:1100px;max-height:88vh;overflow:auto;min-width:500px;
              box-shadow:0 4px 24px rgba(0,0,0,.25)}}
  #modal-box h2{{font-size:16px;color:#2c3e50;margin-bottom:14px}}
  #modal-box table{{border-collapse:collapse;width:100%;font-size:13px}}
  #modal-box th{{background:#2c3e50;color:#fff;padding:8px 14px;text-align:left}}
  #modal-box td{{padding:7px 14px;border-bottom:1px solid #eee}}
  #modal-box tr:last-child td{{border-bottom:none}}
  #modal-box tr:nth-child(even) td{{background:#fafafa}}
  .modal-close{{float:right;background:#888;color:#fff;border:none;
                padding:4px 10px;border-radius:3px;cursor:pointer;font-size:12px}}
  .modal-close:hover{{background:#555}}
</style>
</head>
<body>
<div class="top-bar">
  <h1>GU-QC Failure Summary</h1>
  <div class="top-btns">
    <button class="btn btn-blue" onclick="document.getElementById('modal').classList.add('open')">Unique Params</button>
    <a class="btn" href="{details_path}" target="_blank">Flow Reference</a>
  </div>
</div>
<p class="meta">
  Plot type: <b>{plot_type}</b> &nbsp;|&nbsp;
  {len(df)} failure rows &nbsp;|&nbsp;
  {len(param_specs)} unique parameter(s)
</p>
<div class="wrap">
<table id="tbl">
<thead>
<tr>
  <th><div>TesterName</div><input class="col-filter" data-col="0" type="text" placeholder="e.g. F_RL*" oninput="filterTable()"></th>
  <th><div>Product</div><input class="col-filter" data-col="1" type="text" placeholder="Filter..." oninput="filterTable()"></th>
  <th><div>Sublot</div><input class="col-filter" data-col="2" type="text" placeholder="Filter..." oninput="filterTable()"></th>
  <th><div>Device</div><input class="col-filter" data-col="3" type="text" placeholder="Filter..." oninput="filterTable()"></th>
  <th><div>FailType</div><input class="col-filter" data-col="4" type="text" placeholder="Filter..." oninput="filterTable()"></th>
  <th><div>ParamName</div><input class="col-filter" data-col="5" type="text" placeholder="e.g. F_RL*" oninput="filterTable()"></th>
  <th>LowL</th><th>MeasureError</th><th>HighL</th>
  <th class="lk">Corr_Factor</th>
  <th class="lk">Verify_Error</th>
  <th class="lk">Raw_B4Final</th>
  <th class="lk">Raw_B4VryError</th>
  <th>Date</th><th>FinishTime</th>
</tr>
</thead>
<tbody>
{''.join(rows_html)}
</tbody>
</table>
</div>

<!-- Unique Params modal -->
<div id="modal" onclick="if(event.target===this)this.classList.remove('open')">
  <div id="modal-box">
    <button class="modal-close" onclick="document.getElementById('modal').classList.remove('open')">&#10005; Close</button>
    <h2>Unique Failed Parameters ({len(param_specs)})</h2>
    <table><thead><tr><th>#</th><th>ParamName</th><th>FailType</th></tr></thead>
    <tbody id="modal-tbody"></tbody></table>
  </div>
</div>

<script>
var UNIQUE_PARAMS = {modal_json};
(function(){{
  var tbody = document.getElementById('modal-tbody');
  tbody.innerHTML = UNIQUE_PARAMS.map(function(r, i){{
    return '<tr><td>'+(i+1)+'</td><td>'+r.p+'</td><td>'+r.f+'</td></tr>';
  }}).join('');
}})();

function globToRegex(pat) {{
  if (!pat) return null;
  if (pat.indexOf('*') === -1 && pat.indexOf('?') === -1) {{
    return new RegExp(pat.replace(/[.+^${{}}()|[\]\\\\]/g, '\\\\$&'), 'i');
  }}
  var esc = pat.replace(/[.+^${{}}()|[\]\\\\]/g, '\\\\$&')
               .replace(/\\*/g, '.*')
               .replace(/\\?/g, '.');
  return new RegExp('^' + esc + '$', 'i');
}}
function filterTable() {{
  var filters = [];
  document.querySelectorAll('.col-filter').forEach(function(inp) {{
    var re = globToRegex(inp.value.trim());
    if (re) filters.push({{col: parseInt(inp.dataset.col, 10), re: re}});
  }});
  document.querySelectorAll('#tbl tbody tr').forEach(function(row) {{
    var cells = row.querySelectorAll('td');
    var show = filters.every(function(f) {{
      return f.re.test(cells[f.col].textContent.trim());
    }});
    row.style.display = show ? '' : 'none';
  }});
}}
</script>
</body>
</html>"""

    os.makedirs(PLOT_DIR, exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n  Summary table -> {OUT_HTML}")
    try:
        os.startfile(OUT_HTML)
    except Exception:
        pass


def main():
    print("=" * 50)
    print("       GU-QC Plot Generator")
    print("=" * 50)
    print()
    print("Select plot type:")
    print("  1) Line Plot for failed parameters")
    print("  2) (Default) Box Plot for failed parameters")
    print()

    choice = input("Enter choice [1/2, default=Enter]: ").strip() or "2"

    if choice == "1":
        _run_lineplot()
    elif choice == "2":
        _run_boxplot()
    else:
        print("Invalid choice. Please enter 1 or 2.")
        sys.exit(1)


if __name__ == "__main__":
    main()
