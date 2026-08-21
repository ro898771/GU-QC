"""
plot.py
=======
CLI entry point for plot generation.
Run this script after main.py has produced the result/ folder.

Usage:
  py src/lib/event/plot.py
"""

import os
import sys

# Allow running this script directly (py .../plot.py) -- put <project_root>/src
# on sys.path so the lib.event.* absolute imports below resolve.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from lib.event.winpath import long_path
from lib.event import telemetry


def _report_uncaught(exc_type, exc_value, exc_tb):
    """sys.excepthook: report any uncaught failure to the Telemetry API
    before falling back to the normal traceback/exit behavior.

    Note: the interpreter special-cases SystemExit and never actually
    invokes sys.excepthook for it, so `sys.exit(...)` sites (e.g. "Invalid
    choice" below) must call telemetry.log_feature_error directly rather
    than relying on this hook."""
    if exc_type is not KeyboardInterrupt:
        telemetry.log_feature_error("GeneratePlots", f"{exc_type.__name__}: {exc_value}")
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _report_uncaught


def _run_lineplot():
    from lib.event.LinePlot import main
    main()
    generate_summary_html("LinePlot")


def _run_boxplot():
    from lib.event.Boxplot import main
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

    # This file lives at <project_root>/src/lib/event/plot.py -- up 4 levels to root.
    BASE_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
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

    if not os.path.exists(long_path(SUMMARY_CSV)):
        print("  [INFO] GuLog_FailedSummary.csv not found — generating page with QuickPlot only.")
        df = pd.DataFrame()
    else:
        with open(long_path(SUMMARY_CSV), encoding="utf-8") as f:
            _first_line = f.readline().strip()
        if _first_line.startswith("All Pass"):
            print("  All Pass — generating Summary page with QuickPlot only.")
            df = pd.DataFrame()
        else:
            df = pd.read_csv(long_path(SUMMARY_CSV))

    if not df.empty:
        # Unique (ParamName, FailType) in first-seen order
        param_specs = (
            df.groupby(["ParamName", "FailType"], sort=False)
            .first()
            .reset_index()
        )
        all_pages = {
            (row["ParamName"], row["FailType"]): (i // PER_PAGE) + 1
            for i, (_, row) in enumerate(param_specs.iterrows())
        }
        _fail_counts = df.groupby(["ParamName", "FailType"]).size().rename("FailedCount")
        param_specs = param_specs.join(_fail_counts, on=["ParamName", "FailType"])
        modal_json = json.dumps(
            [{"p": r["ParamName"], "f": r["FailType"], "c": int(r["FailedCount"])}
             for _, r in param_specs.iterrows()],
            ensure_ascii=False,
        )
    else:
        all_pages  = {}
        modal_json = "[]"

    # ── Quick-plot: load concat CSVs and extract per-param data ─────────────
    def _load_csv(path):
        """Load concat CSV (header=row[0], data=rows[5+])."""
        if not os.path.exists(long_path(path)):
            return pd.DataFrame()
        with open(long_path(path), encoding="utf-8", errors="replace") as fh:
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
        if not os.path.exists(long_path(path)):
            return {}
        with open(long_path(path), encoding="utf-8", errors="replace") as fh:
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
        import numpy as np
        if src_df.empty or "TesterName" not in src_df.columns:
            return {}
        param_cols = _param_cols(src_df)
        if not param_cols:
            return {}
        has_pid  = ("Parameter" in src_df.columns and
                    src_df["Parameter"].astype(str).str.startswith("PID-").any())
        has_arm  = "M_Handler-ArmNo" in src_df.columns
        has_zipf = "ZipFile" in src_df.columns
        has_zip  = device_map is not None and has_zipf
        meta     = ["TesterName"]
        if has_pid:  meta.append("Parameter")
        if has_arm:  meta.append("M_Handler-ArmNo")
        if has_zipf: meta.append("ZipFile")
        sub = src_df[meta + param_cols].copy()
        # Bulk-convert all param columns to float in one call instead of 2168 individual calls
        sub[param_cols] = sub[param_cols].apply(pd.to_numeric, errors="coerce")
        lookup = {}
        for tester, grp in sub.groupby("TesterName"):
            grp = grp.reset_index(drop=True)
            # Extract param values as a numpy matrix once — avoids per-column pandas overhead
            mat      = grp[param_cols].to_numpy(dtype=float, na_value=float("nan"))
            pid_list = grp["Parameter"].tolist()        if has_pid  else None
            arm_list = grp["M_Handler-ArmNo"].tolist()  if has_arm  else None
            zip_list = grp["ZipFile"].tolist()          if has_zipf else None
            for ci, c in enumerate(param_cols):
                col   = mat[:, ci]
                valid = ~np.isnan(col)
                if not valid.any():
                    continue
                idx  = np.where(valid)[0]
                vals = [round(float(col[i]), 6) for i in idx]
                if has_pid:
                    pids = [str(pid_list[i]).replace("PID-", "") for i in idx]
                elif has_zip:
                    pids = [device_map.get(str(zip_list[i]), f"*{i + 1}") for i in idx]
                else:
                    pids = [str(i + 1) for i in range(len(vals))]
                arms = [str(arm_list[i]) for i in idx] if has_arm  else ["N/A"] * len(vals)
                zips = [str(zip_list[i]) for i in idx] if has_zipf else [""] * len(vals)
                if c not in lookup:
                    lookup[c] = {}
                lookup[c][str(tester)] = {"v": vals, "p": pids, "a": arms, "z": zips}
        return lookup

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading as _threading

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
    _ntasks = len(_tasks)
    print(f"\n  Pre-processing {total_cols} parameter(s) across {_ntasks} DataFrames (parallel)...")
    _t0 = _time.time()
    _lk_results = {}
    _done_count  = [0]
    _print_lock  = _threading.Lock()
    _stop_ticker = _threading.Event()

    def _ticker():
        elapsed = 0
        while not _stop_ticker.wait(1.0):
            elapsed += 1
            msg = f"\r    [{_done_count[0]}/{_ntasks}] processing... {elapsed}s elapsed"
            with _print_lock:
                sys.stdout.write(msg)
                sys.stdout.flush()

    _tick_thread = _threading.Thread(target=_ticker, daemon=True)
    _tick_thread.start()

    with ThreadPoolExecutor(max_workers=_ntasks) as _pool:
        _futures = {
            _pool.submit(_build_lookup, df, dm): label
            for label, df, dm in _tasks
        }
        for _fut in as_completed(_futures):
            _label = _futures[_fut]
            _lk    = _fut.result()
            _lk_results[_label] = _lk
            _done_count[0] += 1
            with _print_lock:
                print(f"\r    [{_done_count[0]}/{_ntasks}] {_label:14s} done  "
                      f"{len(_lk)} param(s)", flush=True)

    _stop_ticker.set()
    _tick_thread.join()
    print(f"  All {_ntasks} lookups built in {_time.time() - _t0:.1f}s")

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
        if not os.path.exists(long_path(os.path.join(PLOT_DIR, fname))):
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
    for _row_num, (_, row) in enumerate(df.iterrows(), start=1):
        param     = row.get("ParamName", "")
        fail_type = str(row.get("FailType", ""))
        page      = all_pages.get((param, fail_type), 1)

        col_corr_factor  = _link("CorrFactor", page, "CorrFactor", param)
        col_verify_error = _link("Verify",     page, "Verify",     param)
        col_raw_b4final  = _link("CorrRaw",    page, "CorrRaw",    param)
        col_raw_b4vry    = _link("VryRaw",     page, "VryRaw",     param)

        rows_html.append(
            f"<tr>"
            f"<td style='text-align:center;color:#888'>{_row_num}</td>"
            f"<td>{_esc(row.get('TesterName',''))}</td>"
            f"<td>{_esc(row.get('Product',''))}</td>"
            f"<td>{_esc(row.get('Sublot',''))}</td>"
            f"<td>{_dev(row.get('Device',''))}</td>"
            f"<td>{_esc(fail_type)}</td>"
            f"<td class='param'>{_esc(param)}</td>"
            f"<td><input class='lim-inp lsl-inp' data-param='{_esc(param)}' data-ftype='{_esc(fail_type)}' data-orig='{_esc(row.get('LowL',''))}' value='{_esc(row.get('LowL',''))}' onfocus='onLimitFocus(this)' oninput='onLimitChange(this,\"lsl\")' onblur='onLimitBlur()'></td>"
            f"<td>{_esc(row.get('MeasureError',''))}</td>"
            f"<td><input class='lim-inp usl-inp' data-param='{_esc(param)}' data-ftype='{_esc(fail_type)}' data-orig='{_esc(row.get('HighL',''))}' value='{_esc(row.get('HighL',''))}' onfocus='onLimitFocus(this)' oninput='onLimitChange(this,\"usl\")' onblur='onLimitBlur()'></td>"
            f"<td class='chg-cell'>No</td>"
            f"<td class='st-cell'></td>"
            f"<td>{col_corr_factor}</td>"
            f"<td>{col_verify_error}</td>"
            f"<td>{col_raw_b4final}</td>"
            f"<td>{col_raw_b4vry}</td>"
            f"<td>{_esc(row.get('Date',''))}</td>"
            f"<td>{_esc(row.get('FinishTime',''))}</td>"
            f"</tr>"
        )

    details_path = "../../Info/TzerMingCalculation.png"

    _no_fail_row = (
        "<tr><td colspan='18' style='text-align:center;padding:30px;"
        "color:#27ae60;font-weight:600;font-size:15px'>"
        "&#10003; All Pass &#8212; No failures detected. Use Quick Plot above to inspect parameters."
        "</td></tr>"
    )
    _tbody = "".join(rows_html) if rows_html else _no_fail_row

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
  .btn-update{{background:#e67e22;color:#fff;border:none;padding:3px 7px;border-radius:3px;
               cursor:pointer;font-size:11px;white-space:nowrap;display:block;
               width:100%;margin-top:3px;text-align:center}}
  .btn-update:hover{{background:#d35400}}
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
  thead th select{{display:block;margin-top:4px;width:100%;padding:3px 4px;
                   font-size:11px;border:1px solid #5a7fa0;border-radius:3px;
                   background:#eaf3fb;color:#1a252f;outline:none;cursor:pointer}}
  td{{padding:6px 10px;border-bottom:1px solid #eee;white-space:nowrap}}
  td.param{{white-space:normal;max-width:300px;word-break:break-word}}
  tr:last-child td{{border-bottom:none}}
  tr:nth-child(even) td{{background:#fafafa}}
  tr:hover td{{background:#e8f4fd}}
  .lim-inp{{width:72px;padding:2px 5px;border:1px solid #b0bec5;border-radius:3px;
             font-size:12px;text-align:right;background:#fffde7;color:#333;outline:none}}
  .lim-inp:focus{{border-color:#f9a825;box-shadow:0 0 0 2px rgba(249,168,37,.25)}}
  td.st-pass{{background:#d4edda!important;color:#155724;font-weight:700;
               text-align:center;font-size:12px}}
  td.st-fail{{background:#f8d7da!important;color:#721c24;font-weight:700;
               text-align:center;font-size:12px}}
  td.chg-yes{{background:#fff3cd!important;color:#856404;font-weight:700;
               text-align:center;font-size:12px}}
  td.chg-cell{{text-align:center;font-size:12px;color:#555}}
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
  .qp-cell-title{{font-size:12px;font-weight:bold;color:#2c3e50;
                  text-transform:uppercase;letter-spacing:.5px;flex:1}}
  .qp-no-data{{color:#999;text-align:center;padding:40px 0;font-size:13px}}
  .qp-cell-title-bar{{display:flex;align-items:center;gap:4px;margin-bottom:6px}}
  .btn-box-xmode{{background:#6c757d;color:#fff;border:none;padding:3px 8px;border-radius:3px;
                  font-size:11px;cursor:pointer}}
  .btn-box-xmode:hover{{background:#5a6268}}
  .btn-expand-chart{{background:#1a6b9a;color:#fff;border:none;padding:3px 8px;border-radius:3px;
                     font-size:11px;cursor:pointer}}
  .btn-expand-chart:hover{{background:#155a82}}
  .btn-remove-unit{{background:#c0392b;color:#fff;border:none;padding:3px 8px;border-radius:3px;
                    cursor:pointer;font-size:11px;white-space:nowrap}}
  .btn-remove-unit:hover{{background:#922b21}}
  .btn-remove-unit:disabled{{background:#bdc3c7;cursor:default}}
  #qp-removed-panel{{margin-top:14px;background:#fff5f5;border:1px solid #f5c6cb;
                      border-radius:6px;padding:10px 12px}}
  #qp-removed-table{{border-collapse:collapse;width:100%;font-size:12px}}
  #qp-removed-table th{{background:#c0392b;color:#fff;padding:6px 10px;text-align:left;
                         white-space:nowrap;position:sticky;top:0;z-index:1}}
  #qp-removed-table td{{padding:5px 10px;border-bottom:1px solid #f5c6cb;white-space:nowrap}}
  #qp-removed-table tr:nth-child(even) td{{background:#fff0f0}}
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

<div style="margin-bottom:6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
  <button id="undo-btn" class="btn" onclick="undoLimitChange()" disabled
          title="Undo last LSL/USL edit (Ctrl+Z)">&#8630; Undo</button>
  <button class="btn btn-update" style="display:inline-block;width:auto;margin-top:0;padding:6px 14px;font-size:12px"
          onclick="updateCorrTemplate()"
          title="Select CorrTemplate CSV to update Factor_Add_LowLimit / Factor_Add_HighLimit for changed Corr-factor rows">&#128196; CorrTemplate Update</button>
  <button class="btn btn-update" style="display:inline-block;width:auto;margin-top:0;padding:6px 14px;font-size:12px"
          onclick="updateGuBench()"
          title="Select GuBenchDataFile CSV to update LowL / HighL for changed Verification rows">&#128196; GuBench File Update</button>
  <input type="file" id="corr-template-file" accept=".csv" style="display:none">
  <input type="file" id="gubench-file" accept=".csv" style="display:none">
</div>
<div class="wrap">
<table id="tbl">
<thead>
<tr>
  <th style="text-align:center">#</th>
  <th><div>TesterName</div><input class="col-filter" data-col="1" type="text" placeholder="e.g. F_RL*" oninput="filterTable()"></th>
  <th><div>Product</div><input class="col-filter" data-col="2" type="text" placeholder="Filter..." oninput="filterTable()"></th>
  <th><div>Sublot</div><input class="col-filter" data-col="3" type="text" placeholder="Filter..." oninput="filterTable()"></th>
  <th><div>Device</div><input class="col-filter" data-col="4" type="text" placeholder="Filter..." oninput="filterTable()"></th>
  <th><div>FailType</div><input class="col-filter" data-col="5" type="text" placeholder="Filter..." oninput="filterTable()"></th>
  <th><div>ParamName</div><input class="col-filter" data-col="6" type="text" placeholder="e.g. F_RL*" oninput="filterTable()"></th>
  <th><div>LowL</div><input class="col-filter" data-col="7" type="text" placeholder=">0 or >=1 <=5" oninput="filterTable()"></th>
  <th><div>MeasureError</div><input class="col-filter" data-col="8" type="text" placeholder=">0.5 <=1.2" oninput="filterTable()"></th>
  <th><div>HighL</div><input class="col-filter" data-col="9" type="text" placeholder=">0 or >=1 <=5" oninput="filterTable()"></th>
  <th><div>Changes</div><select class="col-filter-sel" data-col="10" onchange="filterTable()">
    <option value="">All</option>
    <option value="Yes">Yes</option>
    <option value="No">No</option>
  </select></th>
  <th><div>Status</div><select class="col-filter-sel" data-col="11" onchange="filterTable()">
    <option value="">All</option>
    <option value="Pass">Pass</option>
    <option value="Fail">Fail</option>
  </select></th>
  <th class="lk">Corr_Factor</th>
  <th class="lk">Verify_Error</th>
  <th class="lk">Raw_B4Final</th>
  <th class="lk">Raw_B4VryError</th>
  <th>Date</th><th>FinishTime</th>
</tr>
</thead>
<tbody>
{_tbody}
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
      <th><div>Failed Count</div><input class="up-col-filter" data-col="3" type="text" placeholder="filter..." oninput="filterUpParams()"></th>
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
        <div class="qp-cell-title-bar"><span class="qp-cell-title">Corr Factor</span><button class="btn-box-xmode" id="xm-qp-cf" onclick="toggleBoxXMode('cf','qp-cf')" style="display:none">ZipIndex</button><button class="btn-expand-chart" onclick="expandChart('cf','qp-cf','Corr Factor')">&#10697; Expand</button><button class="btn-remove-unit" id="rm-qp-cf" onclick="removeSelectedUnit('cf','qp-cf','CorrFactor')" disabled>&#10006; Remove Unit</button></div>
        <div id="qp-cf"></div>
      </div>
      <div class="qp-cell">
        <div class="qp-cell-title-bar"><span class="qp-cell-title">Verify Error</span><button class="btn-box-xmode" id="xm-qp-ve" onclick="toggleBoxXMode('ve','qp-ve')" style="display:none">ZipIndex</button><button class="btn-expand-chart" onclick="expandChart('ve','qp-ve','Verify Error')">&#10697; Expand</button><button class="btn-remove-unit" id="rm-qp-ve" onclick="removeSelectedUnit('ve','qp-ve','VrfyError')" disabled>&#10006; Remove Unit</button></div>
        <div id="qp-ve"></div>
      </div>
      <div class="qp-cell">
        <div class="qp-cell-title-bar"><span class="qp-cell-title">Raw Before Final (CorrRaw)</span><button class="btn-box-xmode" id="xm-qp-cr" onclick="toggleBoxXMode('cr','qp-cr')" style="display:none">ZipIndex</button><button class="btn-expand-chart" onclick="expandChart('cr','qp-cr','Raw Before Final')">&#10697; Expand</button><button class="btn-remove-unit" id="rm-qp-cr" onclick="removeSelectedUnit('cr','qp-cr','CorrRaw')" disabled>&#10006; Remove Unit</button></div>
        <div id="qp-cr"></div>
      </div>
      <div class="qp-cell">
        <div class="qp-cell-title-bar"><span class="qp-cell-title">Raw Before Verify (VryRaw)</span><button class="btn-box-xmode" id="xm-qp-vr" onclick="toggleBoxXMode('vr','qp-vr')" style="display:none">ZipIndex</button><button class="btn-expand-chart" onclick="expandChart('vr','qp-vr','Raw Before Verify')">&#10697; Expand</button><button class="btn-remove-unit" id="rm-qp-vr" onclick="removeSelectedUnit('vr','qp-vr','VryRaw')" disabled>&#10006; Remove Unit</button></div>
        <div id="qp-vr"></div>
      </div>
      <div class="qp-cell">
        <div class="qp-cell-title-bar"><span class="qp-cell-title">Ref Final Data</span><button class="btn-box-xmode" id="xm-qp-rf" onclick="toggleBoxXMode('rf','qp-rf')" style="display:none">ZipIndex</button><button class="btn-expand-chart" onclick="expandChart('rf','qp-rf','Ref Final Data')">&#10697; Expand</button><button class="btn-remove-unit" id="rm-qp-rf" onclick="removeSelectedUnit('rf','qp-rf','RefFinal')" disabled>&#10006; Remove Unit</button></div>
        <div id="qp-rf"></div>
      </div>
      <div class="qp-cell">
        <div class="qp-cell-title-bar"><span class="qp-cell-title">Vrfy Data</span><button class="btn-box-xmode" id="xm-qp-vd" onclick="toggleBoxXMode('vd','qp-vd')" style="display:none">ZipIndex</button><button class="btn-expand-chart" onclick="expandChart('vd','qp-vd','Vrfy Data')">&#10697; Expand</button><button class="btn-remove-unit" id="rm-qp-vd" onclick="removeSelectedUnit('vd','qp-vd','VrfyData')" disabled>&#10006; Remove Unit</button></div>
        <div id="qp-vd"></div>
      </div>
      <div class="qp-cell">
        <div class="qp-cell-title-bar"><span class="qp-cell-title">Corr Coeff</span><button class="btn-box-xmode" id="xm-qp-cc" onclick="toggleBoxXMode('cc','qp-cc')" style="display:none">ZipIndex</button><button class="btn-expand-chart" onclick="expandChart('cc','qp-cc','Corr Coeff')">&#10697; Expand</button><button class="btn-remove-unit" id="rm-qp-cc" onclick="removeSelectedUnit('cc','qp-cc','CorrCoeff')" disabled>&#10006; Remove Unit</button></div>
        <div id="qp-cc"></div>
      </div>
    </div>
    <!-- Individual chart expand overlay (inside qpmodal-box so it inherits z-index context) -->
    <div id="qp-expand-overlay" onclick="if(event.target===this)closeExpandChart()"
         style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);
                z-index:1100;align-items:center;justify-content:center">
      <div style="background:#fff;border-radius:8px;padding:16px 20px;
                  width:92vw;max-width:1300px;max-height:92vh;overflow:auto;
                  box-shadow:0 6px 32px rgba(0,0,0,.4)">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
          <span id="qp-expand-title" style="font-weight:700;font-size:14px;color:#2c3e50"></span>
          <button onclick="closeExpandChart()"
                  style="background:#c0392b;color:#fff;border:none;padding:4px 14px;
                         border-radius:4px;cursor:pointer;font-size:12px">&#10005; Close</button>
        </div>
        <div id="qp-expand-chart"></div>
      </div>
    </div>
    <div id="qp-removed-panel" style="display:none;margin-top:14px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <span style="font-weight:600;color:#c0392b;font-size:13px">&#9888; Removed Units (<span id="qp-removed-count">0</span>)</span>
        <div style="display:flex;gap:6px">
          <button id="qp-undo-btn" class="btn-qp-zoom" onclick="undoRemoveUnit()" disabled title="Undo last removal (Ctrl+Z)">&#8630; Undo</button>
          <button class="btn-export" onclick="exportRemovedUnits()">&#8595; Export CSV</button>
        </div>
      </div>
      <div style="overflow:auto;max-height:180px">
        <table id="qp-removed-table"><thead><tr>
          <th>#</th><th>ParamName</th><th>Source</th><th>PID</th><th>TesterName</th><th>ZipFile</th><th>Value</th>
        </tr></thead><tbody id="qp-removed-tbody"></tbody></table>
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

var _qpZoomed      = false;
var _qpHasPlot     = false;
var _currentParam  = '';
var _selectedPoint = {{}};
var _removedUnits      = [];
var _removedMask       = {{}};
var _removeUndoStack   = [];
var _boxXMode          = {{}}; // {{srcKey: 'tester'|'zip'}}
var _isExpandRender    = false;
var _currentLimits = {{}};

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
    return '<tr><td>' + (i + 1) + '</td><td>' + r.p + '</td><td>' + r.f + '</td><td>' + r.c + '</td></tr>';
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
  var csv = 'No,ParamName,FailType,FailedCount\\n' + rows.join('\\n');
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
  if (_currentParam !== param) {{
    _selectedPoint = {{}};
    _removedUnits    = [];
    _removedMask     = {{}};
    _removeUndoStack = [];
    _boxXMode        = {{}};
    renderRemovedTable();
    ['qp-cf','qp-ve','qp-cr','qp-vr','qp-rf','qp-vd','qp-cc'].forEach(function(id) {{
      var b = document.getElementById('rm-' + id); if (b) b.disabled = true;
    }});
  }}
  var _isBox = (type === 'box');
  ['qp-cf','qp-ve','qp-cr','qp-vr','qp-rf','qp-vd','qp-cc'].forEach(function(id) {{
    var xb = document.getElementById('xm-' + id);
    if (xb) {{ xb.style.display = _isBox ? 'inline-block' : 'none'; xb.textContent = 'ZipIndex'; }}
  }});
  _currentParam  = param;
  _currentLimits = {{
    cf: {{lowL: d.cfLowL, highL: d.cfHighL}},
    ve: {{lowL: d.veLowL, highL: d.veHighL}},
    cr: {{lowL: null,     highL: null}},
    vr: {{lowL: null,     highL: null}},
    rf: {{lowL: null,     highL: null}},
    vd: {{lowL: null,     highL: null}},
    cc: {{lowL: d.ccLowL, highL: d.ccHighL}},
  }};
  _qpHasPlot = true;
  document.getElementById('qp-title').textContent = 'Quick Plot: ' + param;
  document.getElementById('qpmodal').classList.add('open');
  var isTable = (type === 'table');
  document.getElementById('qp-grid').style.display       = isTable ? 'none'  : 'grid';
  document.getElementById('qp-table-wrap').style.display = isTable ? 'block' : 'none';
  if (isTable) {{
    renderTable(param, d);
  }} else {{
    renderChart('qp-cf', d.cf, d.cfLowL,  d.cfHighL,  type, 'cf');
    renderChart('qp-ve', d.ve, d.veLowL,  d.veHighL,  type, 've');
    renderChart('qp-cr', d.cr, null,      null,        type, 'cr');
    renderChart('qp-vr', d.vr, null,      null,        type, 'vr');
    renderChart('qp-rf', d.rf, null,      null,        type, 'rf');
    renderChart('qp-vd', d.vd, null,      null,        type, 'vd');
    renderChart('qp-cc', d.cc, d.ccLowL,  d.ccHighL,  type, 'cc');
  }}
}}

function expandChart(srcKey, divId, title) {{
  var d = PLOT_DATA[_currentParam];
  if (!d || !d[srcKey]) return;
  document.getElementById('qp-expand-title').textContent = title + '  —  ' + _currentParam;
  document.getElementById('qp-expand-overlay').style.display = 'flex';
  var lims = _currentLimits[srcKey] || {{lowL: null, highL: null}};
  var type = document.getElementById('qp-type').value;
  _isExpandRender = true;
  renderChart('qp-expand-chart', d[srcKey], lims.lowL, lims.highL, type, srcKey);
  _isExpandRender = false;
}}
function closeExpandChart() {{
  document.getElementById('qp-expand-overlay').style.display = 'none';
}}

function toggleBoxXMode(srcKey, divId) {{
  _boxXMode[srcKey] = (_boxXMode[srcKey] === 'zip') ? 'tester' : 'zip';
  var btn = document.getElementById('xm-' + divId);
  if (btn) btn.textContent = _boxXMode[srcKey] === 'zip' ? 'TesterName' : 'ZipIndex';
  var d    = PLOT_DATA[_currentParam];
  if (!d || !d[srcKey]) return;
  var lims = _currentLimits[srcKey] || {{lowL: null, highL: null}};
  renderChart(divId, d[srcKey], lims.lowL, lims.highL, 'box', srcKey);
}}

function renderChart(divId, grouped, lowL, highL, type, srcKey) {{
  var el = document.getElementById(divId);
  if (!grouped || Object.keys(grouped).length === 0) {{
    el.innerHTML = '<div class="qp-no-data">No data available</div>';
    var rmBtn = document.getElementById('rm-' + divId);
    if (rmBtn) rmBtn.disabled = true;
    return;
  }}
  var testers  = Object.keys(grouped).sort();
  var traces   = [];
  var srcMask  = (srcKey && _removedMask[srcKey]) || {{}};
  testers.forEach(function(tester, ci) {{
    var d          = grouped[tester];
    var removedSet = srcMask[tester] || new Set();
    var indices    = [];
    for (var ii = 0; ii < d.v.length; ii++) {{
      if (!removedSet.has(ii)) indices.push(ii);
    }}
    var vals   = indices.map(function(i) {{ return d.v[i]; }});
    var pids   = d.p ? indices.map(function(i) {{ return d.p[i]; }}) : null;
    var arms   = d.a ? indices.map(function(i) {{ return d.a[i]; }}) : null;
    var colour = PALETTE[ci % PALETTE.length];
    var zips   = d.z ? indices.map(function(i) {{ return d.z[i]; }}) : null;
    var htexts = vals.map(function(v, ii) {{
      var zf  = zips ? zips[ii] : '';
      var idx = zf ? zf.replace(/\\.[^.]+$/, '').split('_').pop() : '';
      return '<b>PID:</b> '    + (pids ? pids[ii] : indices[ii] + 1) + '<br>'
           + (idx ? '<b>ZipIdx:</b> ' + idx + '<br>' : '')
           + '<b>ArmNo:</b> '  + (arms ? arms[ii] : 'N/A') + '<br>'
           + '<b>Value:</b> '  + v;
    }});
    var zipIdxs = vals.map(function(_, ii) {{
      var zf = zips ? zips[ii] : '';
      return zf ? zf.replace(/\\.[^.]+$/, '').split('_').pop() : String(ii + 1);
    }});
    if (type === 'box') {{
      var useZipX = !!(srcKey && _boxXMode[srcKey] === 'zip');
      traces.push({{
        type: 'box',
        x: useZipX ? zipIdxs : undefined,
        y: vals, name: tester,
        boxpoints: 'all', jitter: 0.4, pointpos: 0,
        marker: {{color: colour, size: 5, opacity: 0.6}},
        line: {{color: colour}},
        fillcolor: 'rgba(255,255,255,0.6)',
        text: htexts, customdata: indices,
        hovertemplate: '%{{text}}<extra></extra>',
      }});
    }} else {{
      var sortOrder = zipIdxs.map(function(zi, ii) {{ return {{zi: zi, ii: ii}}; }});
      sortOrder.sort(function(a, b) {{ return a.zi < b.zi ? -1 : a.zi > b.zi ? 1 : 0; }});
      traces.push({{
        type: 'scatter',
        x: sortOrder.map(function(_, i) {{ return i + 1; }}),
        y: sortOrder.map(function(p) {{ return vals[p.ii]; }}),
        name: tester, mode: 'lines+markers',
        marker: {{color: colour, size: 5}},
        line: {{color: colour}},
        text: sortOrder.map(function(p) {{ return htexts[p.ii]; }}),
        customdata: sortOrder.map(function(p) {{ return indices[p.ii]; }}),
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
  var _useZipLayout = type === 'box' && !!(srcKey && _boxXMode[srcKey] === 'zip');
  var _chartH = _isExpandRender ? 580 : (_qpZoomed ? 520 : 360);
  var layout = {{
    height: _chartH, shapes: shapes,
    margin: {{l:60, r:140, t:20, b: _useZipLayout ? 80 : 50}},
    template: 'plotly_white',
    legend: {{orientation:'v', x:1.01, y:1, xanchor:'left'}},
    hovermode: 'closest',
    boxmode: _useZipLayout ? 'group' : undefined,
    xaxis: _useZipLayout ? {{tickangle: -45}} : {{}},
  }};
  Plotly.newPlot(el, traces, layout, {{responsive: true, displayModeBar: false}});
  if (srcKey) {{
    el.on('plotly_click', function(data) {{
      if (!data.points || !data.points.length) return;
      var pt = data.points[0];
      if (pt.customdata === undefined || pt.customdata === null) return;
      _selectedPoint[divId] = {{
        tester:     pt.fullData.name,
        pointIndex: pt.customdata,
        srcKey:     srcKey,
      }};
      var rmBtn = document.getElementById('rm-' + divId);
      if (rmBtn) rmBtn.disabled = false;
    }});
  }}
}}

function removeSelectedUnit(srcKey, divId, srcLabel) {{
  var sel = _selectedPoint[divId];
  if (!sel) {{ alert('Click a data point on this chart first.'); return; }}
  var param  = _currentParam;
  var tester = sel.tester;
  var pi     = sel.pointIndex;
  var d = PLOT_DATA[param];
  if (!d || !d[srcKey] || !d[srcKey][tester]) return;
  var tData = d[srcKey][tester];
  if (!_removedMask[srcKey]) _removedMask[srcKey] = {{}};
  if (!_removedMask[srcKey][tester]) _removedMask[srcKey][tester] = new Set();
  if (_removedMask[srcKey][tester].has(pi)) {{ alert('This unit is already removed.'); return; }}
  _removedMask[srcKey][tester].add(pi);
  _removedUnits.push({{
    param:  param,
    source: srcLabel,
    pid:    tData.p ? String(tData.p[pi]) : String(pi + 1),
    tester: tester,
    zip:    tData.z ? String(tData.z[pi]) : '',
    value:  tData.v ? tData.v[pi] : '',
  }});
  _removeUndoStack.push({{srcKey: srcKey, tester: tester, pointIndex: pi, divId: divId}});
  delete _selectedPoint[divId];
  var rmBtn = document.getElementById('rm-' + divId);
  if (rmBtn) rmBtn.disabled = true;
  var undoBtn = document.getElementById('qp-undo-btn');
  if (undoBtn) undoBtn.disabled = false;
  var lims = _currentLimits[srcKey] || {{lowL: null, highL: null}};
  var type = document.getElementById('qp-type').value;
  renderChart(divId, d[srcKey], lims.lowL, lims.highL, type, srcKey);
  renderRemovedTable();
}}

function undoRemoveUnit() {{
  if (!_removeUndoStack.length) return;
  var last = _removeUndoStack.pop();
  _removedUnits.pop();
  if (_removedMask[last.srcKey] && _removedMask[last.srcKey][last.tester]) {{
    _removedMask[last.srcKey][last.tester].delete(last.pointIndex);
  }}
  var undoBtn = document.getElementById('qp-undo-btn');
  if (undoBtn) undoBtn.disabled = (_removeUndoStack.length === 0);
  var d    = PLOT_DATA[_currentParam];
  var lims = _currentLimits[last.srcKey] || {{lowL: null, highL: null}};
  var type = document.getElementById('qp-type').value;
  if (d && d[last.srcKey]) {{
    renderChart(last.divId, d[last.srcKey], lims.lowL, lims.highL, type, last.srcKey);
  }}
  renderRemovedTable();
}}

function renderRemovedTable() {{
  var panel = document.getElementById('qp-removed-panel');
  var tbody = document.getElementById('qp-removed-tbody');
  if (!panel || !tbody) return;
  if (!_removedUnits.length) {{ panel.style.display = 'none'; return; }}
  panel.style.display = 'block';
  document.getElementById('qp-removed-count').textContent = String(_removedUnits.length);
  function esc(s) {{ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}
  tbody.innerHTML = _removedUnits.map(function(r, i) {{
    return '<tr>'
      + '<td>' + (i + 1)        + '</td>'
      + '<td>' + esc(r.param)   + '</td>'
      + '<td>' + esc(r.source)  + '</td>'
      + '<td>' + esc(r.pid)     + '</td>'
      + '<td>' + esc(r.tester)  + '</td>'
      + '<td>' + esc(r.zip)     + '</td>'
      + '<td>' + esc(r.value)   + '</td>'
      + '</tr>';
  }}).join('');
}}

function exportRemovedUnits() {{
  if (!_removedUnits.length) {{ alert('No removed units to export.'); return; }}
  function csvCell(v) {{
    var s = String(v);
    return (s.indexOf(',') >= 0 || s.indexOf('"') >= 0 || s.indexOf('\\n') >= 0)
           ? '"' + s.replace(/"/g, '""') + '"' : s;
  }}
  var hdr  = 'ParamName,Source,PID,TesterName,ZipFile,Value';
  var rows = _removedUnits.map(function(r) {{
    return [r.param, r.source, r.pid, r.tester, r.zip, r.value].map(csvCell).join(',');
  }});
  downloadBlob(hdr + '\\n' + rows.join('\\n'),
    (_currentParam || 'removed').replace(/[^a-zA-Z0-9_-]/g,'_') + '_removed_units.csv',
    'text/csv');
}}

function globToRegex(pat) {{
  if (!pat) return null;
  if (pat.indexOf('*') === -1 && pat.indexOf('?') === -1) {{
    return new RegExp(pat.replace(/[.+^${{}}()|[\\]\\\\]/g, '\\\\$&'), 'i');
  }}
  var esc = pat.replace(/[.+^${{}}()|[\\]\\\\]/g, '\\\\$&')
               .replace(/\\*/g, '.*')
               .replace(/\\?/g, '.');
  return new RegExp('^' + esc + '$', 'i');
}}
// ── LSL/USL undo stack ────────────────────────────────────────────────────
var _undoStack = [];
var _focusSnapshot = null;
function onLimitFocus(inp) {{
  var param = inp.dataset.param, ftype = inp.dataset.ftype;
  var snap = [];
  document.querySelectorAll('#tbl tbody tr').forEach(function(row) {{
    var lslInp = row.querySelector('.lsl-inp');
    if (!lslInp || lslInp.dataset.param !== param || lslInp.dataset.ftype !== ftype) return;
    snap.push({{row: row, lsl: lslInp.value, usl: row.querySelector('.usl-inp').value}});
  }});
  _focusSnapshot = snap;
}}
function onLimitBlur() {{
  if (!_focusSnapshot) return;
  var changed = _focusSnapshot.some(function(s) {{
    var l = s.row.querySelector('.lsl-inp'), u = s.row.querySelector('.usl-inp');
    return (l && l.value !== s.lsl) || (u && u.value !== s.usl);
  }});
  if (changed) {{ _undoStack.push(_focusSnapshot); updateUndoBtn(); }}
  _focusSnapshot = null;
}}
function undoLimitChange() {{
  if (!_undoStack.length) return;
  var snap = _undoStack.pop();
  snap.forEach(function(s) {{
    var l = s.row.querySelector('.lsl-inp'), u = s.row.querySelector('.usl-inp');
    if (l) l.value = s.lsl;
    if (u) u.value = s.usl;
    updateChangesCell(s.row);
    updateRowStatus(s.row);
  }});
  updateUndoBtn();
}}
function updateUndoBtn() {{
  var btn = document.getElementById('undo-btn');
  if (!btn) return;
  btn.disabled = !_undoStack.length;
  btn.title = _undoStack.length
    ? 'Undo last LSL/USL edit (' + _undoStack.length + ' step(s)) — Ctrl+Z'
    : 'Nothing to undo';
}}
document.addEventListener('keydown', function(e) {{
  if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {{
    if (document.activeElement && document.activeElement.classList.contains('lim-inp')) return;
    e.preventDefault();
    if (document.getElementById('qpmodal').classList.contains('open')) {{
      undoRemoveUnit();
    }} else {{
      undoLimitChange();
    }}
  }}
}});
function getCellText(cell) {{
  var inp = cell.querySelector('.lim-inp');
  return inp ? inp.value.trim() : cell.textContent.trim();
}}
function calcStatus(lsl, measure, usl) {{
  var m = parseFloat(measure);
  if (isNaN(m)) return '';
  var lo = parseFloat(lsl), hi = parseFloat(usl);
  var ok = (!isNaN(lo) ? m >= lo : true) && (!isNaN(hi) ? m <= hi : true);
  return ok ? 'Pass' : 'Fail';
}}
function updateChangesCell(row) {{
  var cells = row.querySelectorAll('td');
  var lslInp = cells[7].querySelector('.lsl-inp');
  var uslInp = cells[9].querySelector('.usl-inp');
  var changed = (lslInp && lslInp.value !== lslInp.dataset.orig) ||
                (uslInp && uslInp.value !== uslInp.dataset.orig);
  cells[10].textContent = changed ? 'Yes' : 'No';
  cells[10].className   = changed ? 'chg-yes' : 'chg-cell';
}}
function updateRowStatus(row) {{
  var cells = row.querySelectorAll('td');
  var lslInp = cells[7].querySelector('.lsl-inp');
  var uslInp = cells[9].querySelector('.usl-inp');
  var lsl  = lslInp ? lslInp.value : '';
  var meas = cells[8].textContent.trim();
  var usl  = uslInp ? uslInp.value : '';
  var st   = calcStatus(lsl, meas, usl);
  cells[11].textContent = st;
  cells[11].className  = st === 'Pass' ? 'st-pass' : (st === 'Fail' ? 'st-fail' : 'st-cell');
}}
function onLimitChange(inp, type) {{
  var param = inp.dataset.param;
  var ftype = inp.dataset.ftype;
  var val   = inp.value;
  document.querySelectorAll('#tbl tbody tr').forEach(function(row) {{
    var ref = row.querySelector('.lsl-inp');
    if (!ref || ref.dataset.param !== param || ref.dataset.ftype !== ftype) return;
    if (type === 'lsl') {{
      if (ref !== inp) ref.value = val;
    }} else {{
      var uslInp = row.querySelector('.usl-inp');
      if (uslInp && uslInp !== inp) uslInp.value = val;
    }}
    updateChangesCell(row);
    updateRowStatus(row);
  }});
}}
function filterTable() {{
  var NUMERIC_COLS = {{7: true, 8: true, 9: true}};
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
  document.querySelectorAll('.col-filter-sel').forEach(function(sel) {{
    var val = sel.value;
    if (!val) return;
    filters.push({{col: parseInt(sel.dataset.col, 10), exact: val}});
  }});
  document.querySelectorAll('#tbl tbody tr').forEach(function(row) {{
    var cells = row.querySelectorAll('td');
    var show = filters.every(function(f) {{
      var text = getCellText(cells[f.col]);
      if (f.exact) return text === f.exact;
      return f.conds ? applyNumericFilter(f.conds, text) : f.re.test(text);
    }});
    row.style.display = show ? '' : 'none';
  }});
  var _n = 0;
  document.querySelectorAll('#tbl tbody tr').forEach(function(row) {{
    if (row.style.display !== 'none') row.querySelectorAll('td')[0].textContent = ++_n;
  }});
}}
// Initialise Changes and Status columns on page load
document.querySelectorAll('#tbl tbody tr').forEach(function(row) {{
  updateChangesCell(row);
  updateRowStatus(row);
}});
function parseNumericFilter(expr) {{
  /* Parse expressions like: >3  >=2.5  <10  <=4  >1 <=5  (space-separated AND) */
  var parts = expr.trim().split(/\\s+/);
  var conds = [];
  var re = /^(>=|<=|>|<|==?)\\s*(-?[\\d.]+)$/;
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
  var NUMERIC_COLS = {{5: true, 6: true}};
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
  var _n = 0;
  tbl.querySelectorAll('tbody tr').forEach(function(row) {{
    if (row.style.display !== 'none') row.querySelectorAll('td')[0].textContent = ++_n;
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
    + '<th style="text-align:center">#</th>'
    + '<th><div>Source</div>'     + fi(1,'filter...') + '</th>'
    + '<th><div>ZipFile</div>'    + fi(2,'filter...') + '</th>'
    + '<th><div>PID</div>'        + fi(3,'filter...') + '</th>'
    + '<th><div>TesterName</div>' + fi(4,'filter...') + '</th>'
    + '<th><div>ArmNo</div>'      + fi(5,'>3 or >=2 <=5') + '</th>'
    + '<th><div>Value</div>'      + fi(6,'>0.5 <=1.2') + '</th>'
    + '</tr></thead><tbody>';
  rows.forEach(function(r, ri) {{
    html += '<tr>'
          + '<td style="text-align:center;color:#888">' + (ri + 1) + '</td>'
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

// ── Limit file updaters ───────────────────────────────────────────────────
function collectChangedRows(failTypeFilter) {{
  var changed = {{}};
  document.querySelectorAll('#tbl tbody tr').forEach(function(row) {{
    var cells = row.querySelectorAll('td');
    if (cells[10].textContent.trim() !== 'Yes') return;
    var ftype = cells[5].textContent.trim();
    if (ftype !== failTypeFilter) return;
    var param  = cells[6].textContent.trim();
    var lslInp = cells[7].querySelector('.lsl-inp');
    var uslInp = cells[9].querySelector('.usl-inp');
    var lsl = lslInp ? lslInp.value.trim() : '';
    var usl = uslInp ? uslInp.value.trim() : '';
    if (param && !changed[param]) changed[param] = {{lsl: lsl, usl: usl}};
  }});
  return changed;
}}
function updateCorrTemplate() {{
  var changed = collectChangedRows('Failed GU Corr-factor limits');
  if (!Object.keys(changed).length) {{
    alert('No changed Corr-factor rows found.\\nEdit LowL/HighL for "Failed GU Corr-factor limits" rows first (Changes = Yes).');
    return;
  }}
  document.getElementById('corr-template-file').click();
}}
function updateGuBench() {{
  var changed = collectChangedRows('Failed GU Verification limits');
  if (!Object.keys(changed).length) {{
    alert('No changed Verification rows found.\\nEdit LowL/HighL for "Failed GU Verification limits" rows first (Changes = Yes).');
    return;
  }}
  document.getElementById('gubench-file').click();
}}
document.getElementById('corr-template-file').addEventListener('change', function(e) {{
  var file = e.target.files[0]; if (!file) return;
  var changed = collectChangedRows('Failed GU Corr-factor limits');
  var reader = new FileReader();
  reader.onload = function(ev) {{
    var text  = ev.target.result;
    var sep   = text.indexOf('\\r\\n') >= 0 ? '\\r\\n' : '\\n';
    var lines = text.split(/\\r?\\n/);
    var hdr   = lines[0].split(',');
    var pIdx  = hdr.indexOf('ParameterName');
    var loIdx = hdr.indexOf('Factor_Add_LowLimit');
    var hiIdx = hdr.indexOf('Factor_Add_HighLimit');
    if (pIdx < 0 || loIdx < 0 || hiIdx < 0) {{
      alert('CorrTemplate format error — expected columns:\\nParameterName, Factor_Add_LowLimit, Factor_Add_HighLimit'); return;
    }}
    var updated = 0;
    var out = lines.map(function(line, i) {{
      if (i === 0 || !line.trim()) return line;
      var cols  = line.split(',');
      var pname = (cols[pIdx] || '').trim();
      if (changed[pname]) {{
        cols[loIdx] = changed[pname].lsl;
        cols[hiIdx] = changed[pname].usl;
        updated++;
      }}
      return cols.join(',');
    }});
    if (!updated) {{ alert('No matching parameters found in the selected CorrTemplate file.'); return; }}
    downloadBlob(out.join(sep), file.name, 'text/csv');
    alert('CorrTemplate updated: ' + updated + ' parameter(s) modified.\\nDownloaded as: ' + file.name);
  }};
  reader.readAsText(file); e.target.value = '';
}});
document.getElementById('gubench-file').addEventListener('change', function(e) {{
  var file = e.target.files[0]; if (!file) return;
  var changed = collectChangedRows('Failed GU Verification limits');
  var reader = new FileReader();
  reader.onload = function(ev) {{
    var text = ev.target.result;
    if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
    var sep   = text.indexOf('\\r\\n') >= 0 ? '\\r\\n' : '\\n';
    var lines = text.split(/\\r?\\n/);
    if (lines.length < 5) {{ alert('GuBench format error — expected at least 5 header rows (row 3=HighL, row 4=LowL).'); return; }}
    var hdr = lines[0].split(',');
    var colMap = {{}};
    for (var ci = 0; ci < hdr.length; ci++) colMap[hdr[ci].trim()] = ci;
    var hiRow = lines[3].split(',');
    var loRow = lines[4].split(',');
    var updated = 0;
    for (var param in changed) {{
      var ci = colMap[param];
      if (ci !== undefined) {{
        hiRow[ci] = changed[param].usl;
        loRow[ci] = changed[param].lsl;
        updated++;
      }}
    }}
    if (!updated) {{ alert('No matching parameters found in the selected GuBench file.'); return; }}
    lines[3] = hiRow.join(',');
    lines[4] = loRow.join(',');
    downloadBlob(lines.join(sep), file.name, 'text/csv');
    alert('GuBench updated: ' + updated + ' parameter(s) modified.\\nDownloaded as: ' + file.name);
  }};
  reader.readAsText(file); e.target.value = '';
}});
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

    os.makedirs(long_path(PLOT_DIR), exist_ok=True)
    with open(long_path(OUT_HTML), "w", encoding="utf-8") as f:
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
        telemetry.log_feature_click("SelectPlotType_1")
        _run_lineplot()
    elif choice == "2":
        telemetry.log_feature_click("SelectPlotType_2")
        _run_boxplot()
    else:
        print("Invalid choice. Please enter 1 or 2.")
        telemetry.log_feature_error("GeneratePlots", f"Invalid plot-type choice: {choice!r}")
        sys.exit(1)


if __name__ == "__main__":
    main()
