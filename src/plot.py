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
    GuLog_FailedSummary.csv with clickable links into the paginated plot files,
    plus a Quick Plot bar that renders 4 inline Plotly charts for any failed param.

    Columns:
      TesterName | Product | Sublot | Device | FailType | ParamName |
      LowL | MeasureError | HighL |
      Corr_Factor | Verify_Error | Raw_B4Final | Raw_B4VryError |
      Date | FinishTime
    """
    import csv as csv_mod
    import json
    import re
    import pandas as pd

    BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    RESULT_DIR  = os.path.join(BASE_DIR, "result")
    SUMMARY_CSV = os.path.join(RESULT_DIR, "GuLog_FailedSummary.csv")
    PLOT_DIR    = os.path.join(RESULT_DIR, "Plot")
    OUT_HTML    = os.path.join(PLOT_DIR, "Summary.html")
    PER_PAGE    = 20

    CF_CSV = os.path.join(RESULT_DIR, "GuCorrFactor_ALL_CONCAT.csv")
    VE_CSV = os.path.join(RESULT_DIR, "GuVrfyError_ALL_CONCAT.csv")
    CR_CSV = os.path.join(RESULT_DIR, "Corr_GuCorrRawData_ALL_CONCAT.csv")
    VR_CSV = os.path.join(RESULT_DIR, "Vry_GuRawData_ALL_CONCAT.csv")
    RF_CSV = os.path.join(RESULT_DIR, "GuRefFinalData_ALL_CONCAT.csv")
    VD_CSV = os.path.join(RESULT_DIR, "GuVrfyData_ALL_CONCAT.csv")
    CC_CSV = os.path.join(RESULT_DIR, "GuCorrCoeff_ALL_CONCAT.csv")

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

    # ── Quick-plot: load concat CSVs and extract per-param data ─────────────
    def _load_csv(path):
        """Load concat CSV (header=row[0], data=rows[5+])."""
        if not os.path.exists(path):
            return pd.DataFrame()
        with open(path, encoding="utf-8", errors="replace") as fh:
            rows = list(csv_mod.reader(fh))
        if len(rows) < 6:
            return pd.DataFrame()
        header    = rows[0]
        data_rows = rows[5:]
        n = len(header)
        normalised = [r[:n] + [""] * max(0, n - len(r)) for r in data_rows]
        return pd.DataFrame(normalised, columns=header)

    # Columns that are metadata, not parameters
    _SKIP = {
        "TesterName", "Parameter", "ZipFile", "", " ",
        "SBIN", "HBIN", "DIE_X", "DIE_Y", "SITE", "TIME",
        "TOTAL_TESTS", "LOT_ID", "WAFER_ID",
        "PaTestTime", "ServoFail", "SpecFail", "SocketCounter",
    }

    def _param_cols(src_df):
        """Return all non-metadata column names from a concat DataFrame."""
        if src_df.empty:
            return []
        return [c for c in src_df.columns
                if c.strip() and c.strip() not in _SKIP and not c.strip().startswith("M_")]

    def _load_limits(path):
        """Read HighL (row 3) and LowL (row 4) from a concat CSV.
        Returns {col_name: (lowL, highL)} with float or None values.
        Sentinel values (|limit| >= 1e5, e.g. 999999 / 9999999) are treated as None."""
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8", errors="replace") as fh:
            rows = list(csv_mod.reader(fh))
        if len(rows) < 5:
            return {}
        header   = rows[0]
        high_row = rows[3]
        low_row  = rows[4]
        _SENTINEL = 1e5
        lims = {}
        for i in range(3, len(header)):
            col = header[i].strip()
            if not col or col in _SKIP or col.startswith("M_"):
                continue
            try:
                h = float(high_row[i]) if i < len(high_row) and high_row[i].strip() else None
                l = float(low_row[i])  if i < len(low_row)  and low_row[i].strip()  else None
            except ValueError:
                continue
            if h is not None and abs(h) >= _SENTINEL:
                h = None
            if l is not None and abs(l) >= _SENTINEL:
                l = None
            if h is not None or l is not None:
                lims[col] = (l, h)
        return lims

    # cf_device_map kept for backward compat but superseded by cf_pid_map below
    cf_device_map: dict = {}

    import time as _time

    def _pbar(done, total, width=32):
        pct    = done * 100 // total if total else 100
        filled = done * width // total if total else width
        bar    = "=" * filled + (">" if filled < width else "") + " " * max(0, width - filled - 1)
        return f"  [{bar}] {pct:3d}%  ({done}/{total})"

    print("  Loading concat CSVs for quick-plot data...")
    for _label, _path, _slot in [
        ("GuCorrFactor   ", CF_CSV, "cf"),
        ("GuVrfyError    ", VE_CSV, "ve"),
        ("GuCorrRawData  ", CR_CSV, "cr"),
        ("Vry_GuRawData  ", VR_CSV, "vr"),
        ("GuRefFinalData ", RF_CSV, "rf"),
        ("GuVrfyData     ", VD_CSV, "vd"),
        ("GuCorrCoeff    ", CC_CSV, "cc"),
    ]:
        print(f"    [{_label}] loading ...", end="", flush=True)
        _df = _load_csv(_path)
        if _df.empty:
            print("  (not found / empty)")
        else:
            print(f"  {len(_df):,} rows  x  {len(_df.columns)} columns")
        if _slot == "cf":   cf_df = _df
        elif _slot == "ve": ve_df = _df
        elif _slot == "cr": cr_df = _df
        elif _slot == "rf": rf_df = _df
        elif _slot == "vd": vd_df = _df
        elif _slot == "cc": cc_df = _df
        else:               vr_df = _df

    # Build ZipFile -> "#PID1,#PID2,..." from CorrRawData and patch cf_df
    # so Parameter=PID-999 rows carry the real device labels into _build_lookup.
    if not cr_df.empty and all(c in cr_df.columns for c in ("ZipFile", "Parameter")):
        _cf_pid_map: dict = {}
        for _zf, _grp in cr_df.groupby("ZipFile"):
            _pids = sorted({
                str(p).replace("PID-", "")
                for p in _grp["Parameter"].unique()
                if str(p).startswith("PID-")
            })
            if _pids:
                _cf_pid_map[str(_zf)] = ",".join("#" + p for p in _pids)
        if _cf_pid_map and not cf_df.empty and "Parameter" in cf_df.columns and "ZipFile" in cf_df.columns:
            _999_mask = cf_df["Parameter"].astype(str).str.contains("999", na=False)
            if _999_mask.any():
                cf_df.loc[_999_mask, "Parameter"] = cf_df.loc[_999_mask, "ZipFile"].map(
                    lambda z: "PID-" + _cf_pid_map.get(str(z), "999")
                )

    # Per-source limits from concat CSV rows 3 (HighL) and 4 (LowL)
    cf_limits = _load_limits(CF_CSV)
    ve_limits = _load_limits(VE_CSV)
    cc_limits = _load_limits(CC_CSV)

    # Collect every parameter column across all 6 CSVs
    all_cols = set()
    for src_df in (cf_df, ve_df, cr_df, vr_df, rf_df, vd_df):
        all_cols.update(_param_cols(src_df))

    # Pre-build grouped lookup per DataFrame in one pass.
    # Result: {col: {tester: {"v": [values], "p": [pids], "a": [arms]}}}
    def _build_lookup(src_df, device_map=None):
        if src_df.empty or "TesterName" not in src_df.columns:
            return {}
        param_cols = _param_cols(src_df)
        if not param_cols:
            return {}
        # Detect PID column (Parameter col with "PID-" prefix values)
        has_pid  = ("Parameter" in src_df.columns and
                    src_df["Parameter"].astype(str).str.startswith("PID-").any())
        has_arm  = "M_Handler-ArmNo" in src_df.columns
        has_zipf = "ZipFile" in src_df.columns
        has_zip  = device_map is not None and has_zipf
        meta     = ["TesterName"]
        if has_pid:
            meta.append("Parameter")
        if has_arm:
            meta.append("M_Handler-ArmNo")
        if has_zipf:
            meta.append("ZipFile")
        numeric = src_df[meta + param_cols].copy()
        for c in param_cols:
            numeric[c] = pd.to_numeric(numeric[c], errors="coerce")
        lookup = {}
        for tester, grp in numeric.groupby("TesterName"):
            grp = grp.reset_index(drop=True)
            for c in param_cols:
                mask = grp[c].notna()
                if not mask.any():
                    continue
                valid = grp[mask]
                vals  = [round(float(v), 6) for v in valid[c]]
                if has_pid:
                    pids = [str(p).replace("PID-", "") for p in valid["Parameter"]]
                elif has_zip:
                    pids = [device_map.get(str(z), f"*{i + 1}")
                            for i, z in enumerate(valid["ZipFile"])]
                else:
                    pids = [str(i + 1) for i in range(len(vals))]
                arms  = ([str(a) for a in valid["M_Handler-ArmNo"]]
                         if has_arm else ["N/A"] * len(vals))
                zips  = ([str(z) for z in valid["ZipFile"]]
                         if has_zipf else [""] * len(vals))
                if c not in lookup:
                    lookup[c] = {}
                lookup[c][str(tester)] = {"v": vals, "p": pids, "a": arms, "z": zips}
        return lookup

    from concurrent.futures import ThreadPoolExecutor, as_completed

    sorted_cols = sorted(all_cols)
    total_cols  = len(sorted_cols)

    _tasks = [
        ("CorrFactor",   cf_df, cf_device_map),
        ("VrfyError",    ve_df, None),
        ("CorrRawData",  cr_df, None),
        ("VryRawData",   vr_df, None),
        ("RefFinalData", rf_df, None),
        ("VrfyData",     vd_df, None),
        ("CorrCoeff",    cc_df, None),
    ]
    print(f"\n  Pre-processing {total_cols} parameter(s) across {len(_tasks)} DataFrames (parallel)...")
    _t0 = _time.time()
    _lk_results = {}
    with ThreadPoolExecutor(max_workers=len(_tasks)) as _pool:
        _futures = {
            _pool.submit(_build_lookup, df, dm): label
            for label, df, dm in _tasks
        }
        for _fut in as_completed(_futures):
            _label = _futures[_fut]
            _lk = _fut.result()
            _lk_results[_label] = _lk
            print(f"    {_label:14s} done  {len(_lk)} param(s)", flush=True)
    print(f"  All {len(_tasks)} lookups built in {_time.time() - _t0:.1f}s")

    cf_lk = _lk_results["CorrFactor"]
    ve_lk = _lk_results["VrfyError"]
    cr_lk = _lk_results["CorrRawData"]
    vr_lk = _lk_results["VryRawData"]
    rf_lk = _lk_results["RefFinalData"]
    vd_lk = _lk_results["VrfyData"]
    cc_lk = _lk_results["CorrCoeff"]
    del cf_df, ve_df, cr_df, vr_df, rf_df, vd_df, cc_df

    print(f"\n  Assembling JSON for {total_cols} parameter(s)...")
    plot_data   = {}
    _last_print = _time.time()
    for _i, p in enumerate(sorted_cols, start=1):
        _cfl = cf_limits.get(p)
        _vel = ve_limits.get(p)
        _ccl = cc_limits.get(p)
        entry = {
            "cfLowL":  _cfl[0] if _cfl else None,
            "cfHighL": _cfl[1] if _cfl else None,
            "veLowL":  _vel[0] if _vel else None,
            "veHighL": _vel[1] if _vel else None,
            "ccLowL":  _ccl[0] if _ccl else None,
            "ccHighL": _ccl[1] if _ccl else None,
            "cf": cf_lk.get(p, {}),
            "ve": ve_lk.get(p, {}),
            "cr": cr_lk.get(p, {}),
            "vr": vr_lk.get(p, {}),
            "rf": rf_lk.get(p, {}),
            "vd": vd_lk.get(p, {}),
            "cc": cc_lk.get(p, {}),
        }
        if any(entry[k] for k in ("cf", "ve", "cr", "vr", "rf", "vd", "cc")):
            plot_data[p] = entry
        _now = _time.time()
        if _now - _last_print >= 0.5:
            print(_pbar(_i, total_cols), end="\r", flush=True)
            _last_print = _now
    print(_pbar(total_cols, total_cols) + "  done")
    print(f"  {len(plot_data)} / {total_cols} parameter(s) have data and will be embedded.\n")

    plot_data_json = json.dumps(plot_data, ensure_ascii=False, separators=(",", ":"))


    # ── HTML helpers ─────────────────────────────────────────────────────────
    def _safe_id(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "_", str(name))

    def _link(tag: str, page: int, label: str, param: str) -> str:
        fname  = f"FailedParams_{tag}_{plot_type}_p{page:02d}.html"
        anchor = _safe_id(param)
        if not os.path.exists(os.path.join(PLOT_DIR, fname)):
            return '<span class="na">—</span>'
        return f'<a href="{fname}#{anchor}" target="_blank">{label} p{page}</a>'

    def _esc(v) -> str:
        return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _dev(v) -> str:
        try:
            return str(int(float(str(v))))
        except (ValueError, TypeError):
            return _esc(v)

    # ── Build table rows ──────────────────────────────────────────────────────
    rows_html = []
    for _, row in df.iterrows():
        param     = row.get("ParamName", "")
        fail_type = str(row.get("FailType", ""))
        page      = all_pages.get((param, fail_type), 1)

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
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
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
  .meta{{color:#666;margin-bottom:10px;font-size:11px}}
  /* Quick-plot bar */
  .qp-bar{{display:flex;align-items:center;gap:8px;margin-bottom:14px;
           padding:10px 14px;background:#fff;border-radius:6px;
           box-shadow:0 1px 4px rgba(0,0,0,.1)}}
  .qp-label{{font-weight:bold;color:#2c3e50;white-space:nowrap;font-size:12px}}
  .qp-input{{flex:1;min-width:200px;max-width:500px;padding:6px 10px;
             border:1px solid #ccc;border-radius:4px;font-size:12px;cursor:pointer}}
  .qp-input:focus{{border-color:#1a6b9a;outline:none;box-shadow:0 0 0 2px rgba(26,107,154,.2)}}
  .qp-select{{padding:6px 10px;border:1px solid #ccc;border-radius:4px;font-size:12px}}
  /* Param picker modal */
  #pp-modal{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);
             z-index:1002;align-items:center;justify-content:center}}
  #pp-modal.open{{display:flex}}
  #pp-box{{background:#fff;padding:20px;border-radius:8px;width:70vw;max-width:860px;
           max-height:80vh;display:flex;flex-direction:column;
           box-shadow:0 4px 24px rgba(0,0,0,.3)}}
  #pp-box .pp-head{{display:flex;align-items:center;justify-content:space-between;
                    margin-bottom:10px}}
  #pp-box .pp-head h3{{font-size:14px;color:#2c3e50;margin:0}}
  .pp-filters{{display:flex;gap:8px;margin-bottom:10px}}
  #pp-filter{{flex:1;padding:7px 10px;border:1px solid #ccc;border-radius:4px;font-size:12px}}
  #pp-filter:focus{{border-color:#1a6b9a;outline:none}}
  #pp-status{{padding:7px 10px;border:1px solid #ccc;border-radius:4px;
              font-size:12px;min-width:130px}}
  #pp-table-wrap{{overflow-y:auto;flex:1;border:1px solid #eee;border-radius:4px}}
  #pp-table{{border-collapse:collapse;width:100%;font-size:12px}}
  #pp-table thead th{{background:#2c3e50;color:#fff;padding:7px 12px;text-align:left;
                      position:sticky;top:0;z-index:1;white-space:nowrap}}
  #pp-table tbody tr{{cursor:pointer}}
  #pp-table tbody td{{padding:6px 12px;border-bottom:1px solid #eee;white-space:nowrap}}
  #pp-table tbody td.pp-name{{white-space:normal;word-break:break-all;max-width:500px}}
  #pp-table tbody tr:last-child td{{border-bottom:none}}
  #pp-table tbody tr:nth-child(even) td{{background:#fafafa}}
  #pp-table tbody tr:hover td{{background:#dbeeff}}
  .btn-plot{{background:#27ae60;color:#fff;border:none;padding:6px 16px;
             border-radius:4px;cursor:pointer;font-size:12px;white-space:nowrap}}
  .btn-plot:hover{{background:#1e8449}}
  /* Table */
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
  /* Shared modal styles */
  .modal-close{{float:right;background:#888;color:#fff;border:none;
                padding:4px 10px;border-radius:3px;cursor:pointer;font-size:12px}}
  .modal-close:hover{{background:#555}}
  .btn-qp-zoom{{background:#1a6b9a;color:#fff;border:none;padding:4px 10px;
                border-radius:3px;cursor:pointer;font-size:12px}}
  .btn-qp-zoom:hover{{background:#135580}}
  .btn-export{{background:#27ae60;color:#fff;border:none;padding:4px 10px;
               border-radius:3px;cursor:pointer;font-size:12px}}
  .btn-export:hover{{background:#1e8449}}
  #qp-table-wrap{{overflow:auto;max-height:65vh}}
  #qp-data-table{{border-collapse:collapse;width:100%;font-size:12px}}
  #qp-data-table thead th{{background:#2c3e50;color:#fff;padding:7px 12px;text-align:left;
                            white-space:nowrap;position:sticky;top:0;z-index:1;
                            font-size:11px;letter-spacing:.4px;vertical-align:top}}
  #qp-data-table thead th input{{display:block;margin-top:4px;width:100%;padding:3px 5px;
                                  font-size:11px;border:1px solid #5a7fa0;border-radius:3px;
                                  background:#eaf3fb;color:#1a252f;outline:none;
                                  box-sizing:border-box}}
  #qp-data-table td{{padding:5px 12px;border-bottom:1px solid #eee;white-space:nowrap}}
  #qp-data-table tr:nth-child(even) td{{background:#fafafa}}
  #qp-data-table tr:hover td{{background:#e8f4fd}}
  #qpmodal-box.expanded{{width:100vw;max-width:100vw;height:100vh;max-height:100vh;
                          border-radius:0;padding:14px 18px}}
  /* Unique Params modal */
  #modal{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);
          z-index:999;align-items:center;justify-content:center}}
  #modal.open{{display:flex}}
  #modal-box{{background:#fff;padding:28px;border-radius:8px;
              width:90vw;max-width:1100px;max-height:88vh;overflow:auto;min-width:500px;
              box-shadow:0 4px 24px rgba(0,0,0,.25)}}
  #modal-box.expanded{{width:100vw;max-width:100vw;height:100vh;max-height:100vh;
                        border-radius:0;padding:14px 18px}}
  #modal-box h2{{font-size:16px;color:#2c3e50;margin:0}}
  #modal-box table{{border-collapse:collapse;width:100%;font-size:13px}}
  #modal-box th{{background:#2c3e50;color:#fff;padding:8px 14px;text-align:left;
                 white-space:nowrap;vertical-align:top;position:sticky;top:0;z-index:1}}
  #modal-box thead th input{{display:block;margin-top:4px;width:100%;padding:3px 5px;
                              font-size:11px;border:1px solid #5a7fa0;border-radius:3px;
                              background:#eaf3fb;color:#1a252f;outline:none;
                              box-sizing:border-box;font-weight:normal}}
  #modal-box td{{padding:7px 14px;border-bottom:1px solid #eee}}
  #modal-box tr:last-child td{{border-bottom:none}}
  #modal-box tr:nth-child(even) td{{background:#fafafa}}
  /* Flow Reference image modal */
  #img-modal{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);
              z-index:1001;align-items:center;justify-content:center}}
  #img-modal.open{{display:flex}}
  #img-modal-box{{background:#fff;padding:20px 24px;border-radius:8px;
                  width:90vw;max-width:1400px;max-height:92vh;overflow:auto;
                  box-shadow:0 4px 24px rgba(0,0,0,.3)}}
  #img-modal-box.expanded{{width:100vw;max-width:100vw;height:100vh;max-height:100vh;
                            border-radius:0;padding:14px 18px}}
  /* Quick-plot modal */
  #qpmodal{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);
            z-index:1000;align-items:center;justify-content:center}}
  #qpmodal.open{{display:flex}}
  #qpmodal-box{{background:#fff;padding:20px 24px;border-radius:8px;
                width:96vw;max-width:1500px;max-height:92vh;overflow:auto;
                box-shadow:0 4px 24px rgba(0,0,0,.3)}}
  .qp-header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}}
  .qp-header h2{{font-size:15px;color:#2c3e50;margin:0}}
  .qp-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
  .qp-cell{{background:#f8f9fa;border-radius:6px;padding:10px 12px}}
  .qp-cell-title{{font-size:12px;font-weight:bold;color:#2c3e50;margin-bottom:6px;
                  text-transform:uppercase;letter-spacing:.5px}}
  .qp-no-data{{color:#999;text-align:center;padding:40px 0;font-size:13px}}
</style>
</head>
<body>
<div class="top-bar">
  <h1>GU-QC Failure Summary</h1>
  <div class="top-btns">
    <button class="btn btn-blue" onclick="document.getElementById('modal').classList.add('open')">Unique Params</button>
    <button class="btn" onclick="openImgModal()">Flow Reference by TzerMing</button>
  </div>
</div>
<p class="meta">
  Plot type: <b>{plot_type}</b> &nbsp;|&nbsp;
  {len(df)} failure rows &nbsp;|&nbsp;
  {len(param_specs)} unique parameter(s)
</p>

<!-- Quick-plot bar -->
<div class="qp-bar">
  <span class="qp-label">Quick Plot:</span>
  <input id="qp-param" class="qp-input" type="text"
         placeholder="Type a name, or double-click to browse..."
         ondblclick="openPP()" title="Type a parameter name, or double-click to open the picker">
  <select id="qp-type" class="qp-select">
    <option value="box" selected>BoxPlot</option>
    <option value="line">Line</option>
    <option value="table">Table</option>
  </select>
  <button class="btn-plot" onclick="quickPlot()">&#9654; Plot</button>
</div>

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
  <th><div>LowL</div><input class="col-filter" data-col="6" type="text" placeholder=">0 or >=1 <=5" oninput="filterTable()"></th>
  <th><div>MeasureError</div><input class="col-filter" data-col="7" type="text" placeholder=">0.5 <=1.2" oninput="filterTable()"></th>
  <th><div>HighL</div><input class="col-filter" data-col="8" type="text" placeholder=">0 or >=1 <=5" oninput="filterTable()"></th>
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
<div id="modal" onclick="if(event.target===this)closeUpModal()">
  <div id="modal-box">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
      <h2>Unique Failed Parameters (<span id="up-count">{len(param_specs)}</span>)</h2>
      <div style="display:flex;gap:8px;align-items:center">
        <button class="btn-export" onclick="exportUpParams()">&#8595; Export CSV</button>
        <button class="btn-qp-zoom" id="up-zoom-btn" onclick="toggleUpZoom()">&#10697; Expand</button>
        <button class="modal-close" style="float:none" onclick="closeUpModal()">&#10005; Close</button>
      </div>
    </div>
    <div style="overflow:auto;max-height:calc(88vh - 90px)">
    <table id="up-table"><thead><tr>
      <th>#</th>
      <th><div>ParamName</div><input class="up-col-filter" data-col="1" type="text" placeholder="e.g. F_RL*" oninput="filterUpParams()"></th>
      <th><div>FailType</div><input class="up-col-filter" data-col="2" type="text" placeholder="filter..." oninput="filterUpParams()"></th>
    </tr></thead>
    <tbody id="modal-tbody"></tbody></table>
    </div>
  </div>
</div>

<!-- Flow Reference image modal -->
<div id="img-modal" onclick="if(event.target===this)closeImgModal()">
  <div id="img-modal-box">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <span style="font-weight:600;font-size:14px;color:#2c3e50">Flow Reference by TzerMing</span>
      <div style="display:flex;gap:8px;align-items:center">
        <button class="btn-qp-zoom" id="img-zoom-btn" onclick="toggleImgZoom()">&#10697; Expand</button>
        <button class="modal-close" onclick="closeImgModal()">&#10005; Close</button>
      </div>
    </div>
    <img src="{details_path}" style="max-width:100%;display:block;border-radius:4px">
  </div>
</div>

<!-- Parameter picker modal (double-click on Quick Plot input) -->
<div id="pp-modal" onclick="if(event.target===this)closePP()">
  <div id="pp-box">
    <div class="pp-head">
      <h3>Select Parameter <span id="pp-count" style="font-weight:normal;color:#888;font-size:12px"></span></h3>
      <button class="modal-close" onclick="closePP()">&#10005; Close</button>
    </div>
    <div class="pp-filters">
      <input id="pp-filter" type="text" placeholder="Filter by keyword..." oninput="filterPP()" autocomplete="off">
      <select id="pp-status" onchange="filterPP()">
        <option value="">All Status</option>
        <option value="failed">Failed</option>
        <option value="pass">Pass</option>
      </select>
    </div>
    <div id="pp-table-wrap">
      <table id="pp-table">
        <thead><tr><th>#</th><th>Parameter Name</th><th>Status</th></tr></thead>
        <tbody id="pp-tbody"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- Quick-plot modal -->
<div id="qpmodal" onclick="if(event.target===this)closeQp()">
  <div id="qpmodal-box">
    <div class="qp-header">
      <h2 id="qp-title">Quick Plot</h2>
      <div style="display:flex;gap:8px;align-items:center">
        <button class="btn-export" onclick="exportQuickPlot()">&#8595; Export</button>
        <button class="btn-qp-zoom" id="qp-zoom-btn" onclick="toggleQpZoom()">&#10697; Expand</button>
        <button class="modal-close" onclick="closeQp()">&#10005; Close</button>
      </div>
    </div>
    <div id="qp-grid" class="qp-grid">
      <div class="qp-cell">
        <div class="qp-cell-title">Corr Factor</div>
        <div id="qp-cf"></div>
      </div>
      <div class="qp-cell">
        <div class="qp-cell-title">Verify Error</div>
        <div id="qp-ve"></div>
      </div>
      <div class="qp-cell">
        <div class="qp-cell-title">Raw Before Final (CorrRaw)</div>
        <div id="qp-cr"></div>
      </div>
      <div class="qp-cell">
        <div class="qp-cell-title">Raw Before Verify (VryRaw)</div>
        <div id="qp-vr"></div>
      </div>
      <div class="qp-cell">
        <div class="qp-cell-title">Ref Final Data</div>
        <div id="qp-rf"></div>
      </div>
      <div class="qp-cell">
        <div class="qp-cell-title">Vrfy Data</div>
        <div id="qp-vd"></div>
      </div>
      <div class="qp-cell">
        <div class="qp-cell-title">Corr Coeff</div>
        <div id="qp-cc"></div>
      </div>
    </div>
    <div id="qp-table-wrap" style="display:none"></div>
  </div>
</div>

<script>
var UNIQUE_PARAMS = {modal_json};
var PLOT_DATA     = {plot_data_json};
var PALETTE = ['#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd',
               '#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf'];

renderUpParams();

function closeQp() {{
  document.getElementById('qpmodal').classList.remove('open');
  if (_qpZoomed) {{
    _qpZoomed = false;
    document.getElementById('qpmodal-box').classList.remove('expanded');
    document.getElementById('qp-zoom-btn').innerHTML = '&#10697; Expand';
  }}
}}

var _qpZoomed   = false;
var _qpHasPlot  = false;

function toggleQpZoom() {{
  _qpZoomed = !_qpZoomed;
  var box = document.getElementById('qpmodal-box');
  var btn = document.getElementById('qp-zoom-btn');
  if (_qpZoomed) {{
    box.classList.add('expanded');
    btn.innerHTML = '&#10698; Restore';
  }} else {{
    box.classList.remove('expanded');
    btn.innerHTML = '&#10697; Expand';
  }}
  if (_qpHasPlot) quickPlot();
}}

// ── Unique Params modal ───────────────────────────────────────────────────
function renderUpParams() {{
  document.getElementById('modal-tbody').innerHTML = UNIQUE_PARAMS.map(function(r, i) {{
    return '<tr><td>' + (i + 1) + '</td><td>' + r.p + '</td><td>' + r.f + '</td></tr>';
  }}).join('');
  document.getElementById('up-count').textContent = String(UNIQUE_PARAMS.length);
}}
function filterUpParams() {{
  var filters = [];
  document.querySelectorAll('.up-col-filter').forEach(function(inp) {{
    var re = globToRegex(inp.value.trim());
    if (re) filters.push({{col: parseInt(inp.dataset.col, 10), re: re}});
  }});
  var visible = 0;
  document.querySelectorAll('#modal-tbody tr').forEach(function(row) {{
    var cells = row.querySelectorAll('td');
    var show = filters.every(function(f) {{
      return f.re.test(cells[f.col] ? cells[f.col].textContent.trim() : '');
    }});
    row.style.display = show ? '' : 'none';
    if (show) visible++;
  }});
  document.getElementById('up-count').textContent =
    visible === UNIQUE_PARAMS.length
      ? String(UNIQUE_PARAMS.length)
      : visible + ' / ' + UNIQUE_PARAMS.length;
}}
function exportUpParams() {{
  function csvCell(v) {{
    return (v.indexOf(',') >= 0 || v.indexOf('"') >= 0 || v.indexOf('\\n') >= 0)
      ? '"' + v.replace(/"/g, '""') + '"' : v;
  }}
  var rows = [];
  document.querySelectorAll('#modal-tbody tr').forEach(function(row) {{
    if (row.style.display === 'none') return;
    var cells = row.querySelectorAll('td');
    rows.push(Array.from(cells).map(function(c) {{ return csvCell(c.textContent.trim()); }}).join(','));
  }});
  var csv = 'No,ParamName,FailType\\n' + rows.join('\\n');
  downloadBlob(csv, 'UniqueParams.csv', 'text/csv');
}}
var _upZoomed = false;
function closeUpModal() {{
  document.getElementById('modal').classList.remove('open');
  if (_upZoomed) {{
    _upZoomed = false;
    document.getElementById('modal-box').classList.remove('expanded');
    document.getElementById('up-zoom-btn').innerHTML = '&#10697; Expand';
  }}
}}
function toggleUpZoom() {{
  _upZoomed = !_upZoomed;
  var box = document.getElementById('modal-box');
  var btn = document.getElementById('up-zoom-btn');
  if (_upZoomed) {{
    box.classList.add('expanded');
    btn.innerHTML = '&#10698; Restore';
  }} else {{
    box.classList.remove('expanded');
    btn.innerHTML = '&#10697; Expand';
  }}
}}

// ── Flow Reference image modal ────────────────────────────────────────────
var _imgZoomed = false;
function openImgModal() {{
  document.getElementById('img-modal').classList.add('open');
}}
function closeImgModal() {{
  document.getElementById('img-modal').classList.remove('open');
  if (_imgZoomed) {{
    _imgZoomed = false;
    document.getElementById('img-modal-box').classList.remove('expanded');
    document.getElementById('img-zoom-btn').innerHTML = '&#10697; Expand';
  }}
}}
function toggleImgZoom() {{
  _imgZoomed = !_imgZoomed;
  var box = document.getElementById('img-modal-box');
  var btn = document.getElementById('img-zoom-btn');
  if (_imgZoomed) {{
    box.classList.add('expanded');
    btn.innerHTML = '&#10698; Restore';
  }} else {{
    box.classList.remove('expanded');
    btn.innerHTML = '&#10697; Expand';
  }}
}}

// ── Parameter picker ──────────────────────────────────────────────────────
var _ppRows = [];  // {{name, failed}} for all params, built once on first open

function openPP() {{
  // Build row list once
  if (_ppRows.length === 0) {{
    var failedSet = {{}};
    UNIQUE_PARAMS.forEach(function(r) {{ failedSet[r.p] = r.f; }});
    Object.keys(PLOT_DATA).sort().forEach(function(name) {{
      _ppRows.push({{name: name, failed: failedSet[name] || null}});
    }});
  }}
  document.getElementById('pp-filter').value = '';
  document.getElementById('pp-status').value = '';
  renderPP(_ppRows);
  document.getElementById('pp-modal').classList.add('open');
  setTimeout(function() {{ document.getElementById('pp-filter').focus(); }}, 80);
}}

function closePP() {{ document.getElementById('pp-modal').classList.remove('open'); }}

function selectParam(name) {{
  document.getElementById('qp-param').value = name;
  closePP();
}}

function _esc(s) {{
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function renderPP(rows) {{
  var tbody = document.getElementById('pp-tbody');
  var html  = rows.map(function(r, i) {{
    var status = r.failed
      ? '<span style="color:#e74c3c;font-weight:600">&#10007; Failed (' + _esc(r.failed) + ')</span>'
      : '<span style="color:#888">Pass</span>';
    return '<tr data-name="' + _esc(r.name) + '" style="cursor:pointer">'
         + '<td>' + (i + 1) + '</td>'
         + '<td class="pp-name">' + _esc(r.name) + '</td>'
         + '<td>' + status + '</td>'
         + '</tr>';
  }}).join('');
  tbody.innerHTML = html || '<tr><td colspan="3" style="text-align:center;color:#999;padding:20px">No match</td></tr>';
  document.getElementById('pp-count').textContent = '(' + rows.length + ' parameter(s))';
}}

// Event delegation — avoids inline onclick quote-escaping issues
document.addEventListener('DOMContentLoaded', function() {{
  document.getElementById('pp-tbody').addEventListener('click', function(e) {{
    var tr = e.target.closest('tr[data-name]');
    if (tr) selectParam(tr.getAttribute('data-name'));
  }});
  document.getElementById('pp-tbody').addEventListener('dblclick', function(e) {{
    var tr = e.target.closest('tr[data-name]');
    if (tr) selectParam(tr.getAttribute('data-name'));
  }});
}});

function filterPP() {{
  var kw     = document.getElementById('pp-filter').value.trim().toLowerCase();
  var status = document.getElementById('pp-status').value;
  renderPP(_ppRows.filter(function(r) {{
    var kwOk = !kw || r.name.toLowerCase().indexOf(kw) !== -1;
    var stOk = !status
      || (status === 'failed' && !!r.failed)
      || (status === 'pass'   && !r.failed);
    return kwOk && stOk;
  }}));
}}
// ── End parameter picker ──────────────────────────────────────────────────

function quickPlot() {{
  var param = document.getElementById('qp-param').value.trim();
  var type  = document.getElementById('qp-type').value;
  if (!param) {{ alert('Please enter a parameter name.'); return; }}
  var d = PLOT_DATA[param];
  if (!d) {{
    alert('No data found for:\\n' + param +
          '\\n\\nCheck the parameter name and try again.');
    return;
  }}
  _qpHasPlot = true;
  document.getElementById('qp-title').textContent = 'Quick Plot: ' + param;
  document.getElementById('qpmodal').classList.add('open');
  var isTable = (type === 'table');
  document.getElementById('qp-grid').style.display       = isTable ? 'none'  : 'grid';
  document.getElementById('qp-table-wrap').style.display = isTable ? 'block' : 'none';
  if (isTable) {{
    renderTable(param, d);
  }} else {{
    renderChart('qp-cf', d.cf, d.cfLowL,  d.cfHighL,  type);
    renderChart('qp-ve', d.ve, d.veLowL,  d.veHighL,  type);
    renderChart('qp-cr', d.cr, null,      null,        type);
    renderChart('qp-vr', d.vr, null,      null,        type);
    renderChart('qp-rf', d.rf, null,      null,        type);
    renderChart('qp-vd', d.vd, null,      null,        type);
    renderChart('qp-cc', d.cc, d.ccLowL,  d.ccHighL,  type);
  }}
}}

function renderChart(divId, grouped, lowL, highL, type) {{
  var el = document.getElementById(divId);
  if (!grouped || Object.keys(grouped).length === 0) {{
    el.innerHTML = '<div class="qp-no-data">No data available</div>';
    return;
  }}
  var testers = Object.keys(grouped).sort();
  var traces  = [];
  testers.forEach(function(tester, ci) {{
    var d      = grouped[tester];
    var vals   = d.v;
    var pids   = d.p;
    var arms   = d.a;
    var colour = PALETTE[ci % PALETTE.length];
    var htexts = vals.map(function(v, i) {{
      return '<b>PID:</b> '   + (pids ? pids[i] : i + 1) + '<br>'
           + '<b>ArmNo:</b> ' + (arms ? arms[i] : 'N/A') + '<br>'
           + '<b>Value:</b> ' + v;
    }});
    if (type === 'box') {{
      traces.push({{
        type: 'box', y: vals, name: tester,
        boxpoints: 'all', jitter: 0.4, pointpos: 0,
        marker: {{color: colour, size: 5, opacity: 0.6}},
        line: {{color: colour}},
        fillcolor: 'rgba(255,255,255,0.6)',
        text: htexts,
        hovertemplate: '%{{text}}<extra></extra>',
      }});
    }} else {{
      traces.push({{
        type: 'scatter',
        x: vals.map(function(_, i) {{ return i + 1; }}),
        y: vals, name: tester, mode: 'lines+markers',
        marker: {{color: colour, size: 5}},
        line: {{color: colour}},
        text: htexts,
        hovertemplate: '%{{text}}<extra></extra>',
      }});
    }}
  }});
  var shapes = [];
  if (lowL !== null && lowL !== undefined) {{
    traces.push({{
      type: 'scatter', x: [null], y: [null], mode: 'lines',
      name: 'LowL = ' + lowL, showlegend: true,
      line: {{color: '#e74c3c', dash: 'dash', width: 1.5}},
    }});
    shapes.push({{type:'line', x0:0, x1:1, xref:'paper', y0:lowL, y1:lowL,
      line:{{color:'#e74c3c', dash:'dash', width:1.5}}}});
  }}
  if (highL !== null && highL !== undefined) {{
    traces.push({{
      type: 'scatter', x: [null], y: [null], mode: 'lines',
      name: 'HighL = ' + highL, showlegend: true,
      line: {{color: '#27ae60', dash: 'dash', width: 1.5}},
    }});
    shapes.push({{type:'line', x0:0, x1:1, xref:'paper', y0:highL, y1:highL,
      line:{{color:'#27ae60', dash:'dash', width:1.5}}}});
  }}
  var layout = {{
    height: _qpZoomed ? 520 : 360, shapes: shapes,
    margin: {{l:60, r:140, t:20, b:50}},
    template: 'plotly_white',
    legend: {{orientation:'v', x:1.01, y:1, xanchor:'left'}},
    hovermode: 'closest',
  }};
  Plotly.newPlot(el, traces, layout, {{responsive: true, displayModeBar: false}});
}}

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
  var NUMERIC_COLS = {{6: true, 7: true, 8: true}};
  var filters = [];
  document.querySelectorAll('.col-filter').forEach(function(inp) {{
    var col = parseInt(inp.dataset.col, 10);
    var raw = inp.value.trim();
    if (!raw) return;
    if (NUMERIC_COLS[col]) {{
      var conds = parseNumericFilter(raw);
      if (conds) {{ filters.push({{col: col, conds: conds}}); return; }}
    }}
    var re = globToRegex(raw);
    if (re) filters.push({{col: col, re: re}});
  }});
  document.querySelectorAll('#tbl tbody tr').forEach(function(row) {{
    var cells = row.querySelectorAll('td');
    var show = filters.every(function(f) {{
      var text = cells[f.col].textContent.trim();
      return f.conds ? applyNumericFilter(f.conds, text) : f.re.test(text);
    }});
    row.style.display = show ? '' : 'none';
  }});
}}
function parseNumericFilter(expr) {{
  /* Parse expressions like: >3  >=2.5  <10  <=4  >1 <=5  (space-separated AND) */
  var parts = expr.trim().split(/\s+/);
  var conds = [];
  var re = /^(>=|<=|>|<|==?)\s*(-?[\d.]+)$/;
  for (var i = 0; i < parts.length; i++) {{
    var m = parts[i].match(re);
    if (!m) return null;
    conds.push({{op: m[1], val: parseFloat(m[2])}});
  }}
  return conds.length ? conds : null;
}}
function applyNumericFilter(conds, text) {{
  var n = parseFloat(text);
  if (isNaN(n)) return false;
  return conds.every(function(c) {{
    switch (c.op) {{
      case '>':         return n >  c.val;
      case '<':         return n <  c.val;
      case '>=':        return n >= c.val;
      case '<=':        return n <= c.val;
      case '=': case '==': return n === c.val;
      default:          return false;
    }}
  }});
}}
function filterQpTable() {{
  var tbl = document.getElementById('qp-data-table');
  if (!tbl) return;
  var NUMERIC_COLS = {{4: true, 5: true}};
  var filters = [];
  tbl.querySelectorAll('.qp-col-filter').forEach(function(inp) {{
    var col = parseInt(inp.dataset.col, 10);
    var raw = inp.value.trim();
    if (!raw) return;
    if (NUMERIC_COLS[col]) {{
      var conds = parseNumericFilter(raw);
      if (conds) {{ filters.push({{col: col, conds: conds}}); return; }}
    }}
    var re = globToRegex(raw);
    if (re) filters.push({{col: col, re: re}});
  }});
  tbl.querySelectorAll('tbody tr').forEach(function(row) {{
    var cells = row.querySelectorAll('td');
    var show = filters.every(function(f) {{
      var text = cells[f.col].textContent.trim();
      return f.conds ? applyNumericFilter(f.conds, text) : f.re.test(text);
    }});
    row.style.display = show ? '' : 'none';
  }});
}}

function renderTable(param, d) {{
  var SOURCES = [
    {{key:'cf', label:'CorrFactor'}},
    {{key:'ve', label:'VrfyError'}},
    {{key:'cr', label:'CorrRaw'}},
    {{key:'vr', label:'VryRaw'}},
    {{key:'rf', label:'RefFinal'}},
    {{key:'vd', label:'VrfyData'}},
    {{key:'cc', label:'CorrCoeff'}},
  ];
  var rows = [];
  SOURCES.forEach(function(src) {{
    var grouped = d[src.key];
    if (!grouped || !Object.keys(grouped).length) return;
    Object.keys(grouped).sort().forEach(function(tester) {{
      var entry = grouped[tester];
      for (var i = 0; i < entry.v.length; i++) {{
        rows.push({{
          source: src.label,
          zip:    entry.z ? String(entry.z[i]) : '',
          pid:    entry.p ? String(entry.p[i]) : String(i + 1),
          tester: tester,
          arm:    entry.a ? String(entry.a[i]) : 'N/A',
          value:  entry.v[i],
        }});
      }}
    }});
  }});
  var wrap = document.getElementById('qp-table-wrap');
  if (!rows.length) {{
    wrap.innerHTML = '<p style="color:#999;text-align:center;padding:40px;">No data available</p>';
    return;
  }}
  function esc(s) {{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}
  function fi(col, ph) {{
    return '<input type="text" class="qp-col-filter" data-col="' + col
         + '" placeholder="' + ph + '" oninput="filterQpTable()">';
  }}
  var html = '<table id="qp-data-table"><thead><tr>'
    + '<th><div>Source</div>'     + fi(0,'filter...') + '</th>'
    + '<th><div>ZipFile</div>'    + fi(1,'filter...') + '</th>'
    + '<th><div>PID</div>'        + fi(2,'filter...') + '</th>'
    + '<th><div>TesterName</div>' + fi(3,'filter...') + '</th>'
    + '<th><div>ArmNo</div>'      + fi(4,'>3 or >=2 <=5') + '</th>'
    + '<th><div>Value</div>'      + fi(5,'>0.5 <=1.2') + '</th>'
    + '</tr></thead><tbody>';
  rows.forEach(function(r) {{
    html += '<tr>'
          + '<td>' + esc(r.source) + '</td>'
          + '<td>' + esc(r.zip)    + '</td>'
          + '<td>' + esc(r.pid)    + '</td>'
          + '<td>' + esc(r.tester) + '</td>'
          + '<td>' + esc(r.arm)    + '</td>'
          + '<td>' + esc(r.value)  + '</td>'
          + '</tr>';
  }});
  html += '</tbody></table>';
  wrap.innerHTML = html;
}}

function exportQuickPlot() {{
  var param = document.getElementById('qp-param').value.trim();
  var type  = document.getElementById('qp-type').value;
  if (!param || !_qpHasPlot) {{ alert('Please plot a parameter first.'); return; }}
  if (type === 'table') {{ exportTableCSV(param); }}
  else {{ exportPlotsHTML(param, type); }}
}}

function exportTableCSV(param) {{
  var tbl = document.getElementById('qp-data-table');
  if (!tbl) {{ alert('No table data to export.'); return; }}
  var csv = '';
  tbl.querySelectorAll('tr').forEach(function(row) {{
    csv += Array.from(row.querySelectorAll('th,td')).map(function(c) {{
      var v = c.textContent.trim();
      return (v.indexOf(',') >= 0 || v.indexOf('"') >= 0)
             ? '"' + v.replace(/"/g, '""') + '"' : v;
    }}).join(',') + '\\n';
  }});
  downloadBlob(csv, param.replace(/[^a-zA-Z0-9_-]/g,'_') + '_table.csv', 'text/csv');
}}

function exportPlotsHTML(param, type) {{
  var SRCS = [
    {{id:'qp-cf', title:'Corr Factor'}},
    {{id:'qp-ve', title:'Verify Error'}},
    {{id:'qp-cr', title:'Raw Before Final (CorrRaw)'}},
    {{id:'qp-vr', title:'Raw Before Verify (VryRaw)'}},
    {{id:'qp-rf', title:'Ref Final Data'}},
    {{id:'qp-vd', title:'Vrfy Data'}},
    {{id:'qp-cc', title:'Corr Coeff'}},
  ];
  var charts = [];
  SRCS.forEach(function(s) {{
    var el = document.getElementById(s.id);
    charts.push({{
      title: s.title,
      data:  (el && el.data && el.data.length) ? el.data  : null,
      layout:(el && el.layout)                 ? el.layout: null,
    }});
  }});
  var sp = param.replace(/</g,'&lt;');
  var html = '<!DOCTYPE html><html><head><meta charset="utf-8"><title>QuickPlot – ' + sp + '</title>'
    + '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"><\\/script>'
    + '<style>'
    + 'body{{font-family:Segoe UI,Arial,sans-serif;background:#f0f2f5;padding:20px;margin:0}}'
    + 'h1{{font-size:18px;color:#2c3e50;margin:0 0 14px}}'
    + '.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}'
    + '.cell{{background:#f8f9fa;border-radius:6px;padding:10px 12px}}'
    + '.ct{{font-size:12px;font-weight:bold;color:#2c3e50;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}'
    + '.nd{{color:#999;text-align:center;padding:40px 0;font-size:13px}}'
    + '</style></head><body>'
    + '<h1>QuickPlot: ' + sp + '</h1>'
    + '<div class="grid">';
  charts.forEach(function(c, i) {{
    html += '<div class="cell"><div class="ct">' + c.title + '</div>';
    if (c.data) {{
      html += '<div id="c' + i + '"></div>';
    }} else {{
      html += '<div class="nd">No data</div>';
    }}
    html += '</div>';
  }});
  html += '</div><script>';
  charts.forEach(function(c, i) {{
    if (!c.data) return;
    var lay = Object.assign({{}}, c.layout, {{height:380, margin:{{l:60,r:150,t:20,b:50}}}});
    html += 'Plotly.newPlot("c' + i + '",' + JSON.stringify(c.data) + ',' + JSON.stringify(lay) + ',{{responsive:true}});';
  }});
  html += '<\\/script></body></html>';
  downloadBlob(html, param.replace(/[^a-zA-Z0-9_-]/g,'_') + '_plots.html', 'text/html');
}}

function downloadBlob(content, filename, mimeType) {{
  var blob = new Blob([content], {{type: mimeType}});
  var url  = URL.createObjectURL(blob);
  var a    = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a); URL.revokeObjectURL(url);
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
