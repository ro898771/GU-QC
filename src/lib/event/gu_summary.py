"""
gu_summary.py
=============
Builds a per-zip "GuSummary" CSV that lays GuRefFinalData, GuCorrRawData,
GuCorrFactor, GuRawData, GuCorrCoeff and GuVrfyError side by side (one row
per test parameter instead of one row per device), adds pass/fail flags for
GuCorrFactor / GuVrfyError, and appends GU_* metadata rows — matching the
"1GuSummary" output produced by the reference GU Cal DTS tool.
"""

from __future__ import annotations

import csv
import re

from lib.event.winpath import long_path

COL_HEADER_STARTS = {"Parameter", "Test#", "Unit", "HighL", "LowL"}


def parse_wide_gu_text(text: str) -> tuple[dict, dict, dict]:
    """
    Parse one GU wide-format CSV (rows = Parameter/Test#/Unit/HighL/LowL/PID-xxx,
    columns = individual test parameters).

    Returns (meta, columns, rows):
      meta    : {key: value} from the "--- Global Info:" block (empty if absent,
                 e.g. GuRefFinalData has none)
      columns : {'Parameter': [...], 'Test#': [...], 'Unit': [...], 'HighL': [...], 'LowL': [...]}
      rows    : {'PID-xxxxxxx': [...], '999': [...]}  (device / site-aggregate data rows)
    """
    lines = text.splitlines()
    meta: dict[str, str] = {}
    columns: dict[str, list[str]] = {}
    rows: dict[str, list[str]] = {}

    i = 0
    if lines and lines[0].strip() == "--- Global Info:":
        i = 1
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            first = next(csv.reader([line]))[0].strip()
            if first in COL_HEADER_STARTS:
                break
            parts = next(csv.reader([line]))
            meta[parts[0]] = parts[1] if len(parts) > 1 else ""
            i += 1

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        parts = next(csv.reader([line]))
        first = parts[0].strip()
        if first.startswith("#"):
            break
        if first in COL_HEADER_STARTS:
            columns[first] = parts[1:]
        elif first.startswith("PID-") or first.isdigit():
            rows[first] = parts[1:]
        i += 1

    return meta, columns, rows


def _pid_sort_key(pid: str) -> int:
    try:
        return int(pid.split("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _pass_fail(value: str, high: str, low: str) -> str:
    try:
        v, hi, lo = float(value), float(high), float(low)
    except (TypeError, ValueError):
        return ""
    return "1" if (v > hi or v < lo) else "0"


_STATUS_RE = re.compile(r"_(PP|PF|FP|FF)_")


def status_from_zip_name(zip_stem: str) -> str:
    m = _STATUS_RE.search(zip_stem)
    return m.group(1) if m else ""


# Block definitions, in the exact left-to-right order used by the reference tool.
_BLOCKS = ("GuRefFinalData", "GuCorrRawData", "GuCorrFactor", "GuRawData", "GuCorrCoeff", "GuVrfyError")


def build_gu_summary_rows(parsed: dict[str, tuple[dict, dict, dict]], zip_stem: str, gu_batch_id: int) -> list[list[str]]:
    """
    parsed : {block_name: (meta, columns, rows)} for whichever of the 6 blocks
             were found in the zip (missing blocks are simply omitted from output).
    """
    present = [b for b in _BLOCKS if b in parsed]
    if not present:
        return []

    pids = sorted(
        {
            p
            for b in ("GuRefFinalData", "GuCorrRawData", "GuRawData", "GuVrfyError")
            if b in parsed
            for p in parsed[b][2]
            if p.startswith("PID-")
        },
        key=_pid_sort_key,
    )

    status = status_from_zip_name(zip_stem)

    meta = {}
    for b in ("GuVrfyError", "GuCorrFactor", "GuRawData", "GuCorrRawData", "GuCorrCoeff"):
        if b in parsed and parsed[b][0]:
            meta = parsed[b][0]
            break

    # Per-block column layout: (n_param_cols, value_labels, has_pf, has_cumm)
    layouts: dict[str, dict] = {}
    for b in present:
        if b in ("GuRefFinalData", "GuCorrRawData", "GuRawData"):
            layouts[b] = {"lead": ["Parameter", "Test#"], "values": list(pids), "pf": False, "cumm": False}
        elif b == "GuCorrFactor":
            layouts[b] = {"lead": ["Parameter", "Test#", "HighL", "LowL"], "values": ["PID-999[CF]"], "pf": True, "cumm": False}
        elif b == "GuCorrCoeff":
            layouts[b] = {"lead": ["Parameter", "Test#"], "values": ["PID-999"], "pf": False, "cumm": False}
        elif b == "GuVrfyError":
            layouts[b] = {"lead": ["Parameter", "Test#", "HighL", "LowL"], "values": list(pids), "pf": True, "cumm": True}

    header_rows: list[list[str]] = [[], []]
    for b in present:
        lay = layouts[b]
        cols = list(lay["lead"]) + list(lay["values"])
        if lay["pf"]:
            cols += [f"{v}_PF" for v in lay["values"]]
        if lay["cumm"]:
            cols += ["Cumm_PF"]
        header_rows[0].extend([b] + [""] * (len(cols) - 1))
        header_rows[1].extend(cols)

    num_params = 0
    for b in present:
        columns = parsed[b][1]
        if columns.get("Parameter"):
            num_params = len(columns["Parameter"])
            break

    data_rows: list[list[str]] = []
    for i in range(num_params):
        row: list[str] = []
        for b in present:
            _, columns, rows = parsed[b]
            lay = layouts[b]
            param = columns.get("Parameter", [""] * num_params)[i] if i < len(columns.get("Parameter", [])) else ""
            test_num = columns.get("Test#", [""] * num_params)[i] if i < len(columns.get("Test#", [])) else ""

            if b == "GuCorrFactor" or b == "GuVrfyError":
                high = columns.get("HighL", [""])[i] if i < len(columns.get("HighL", [])) else ""
                low = columns.get("LowL", [""])[i] if i < len(columns.get("LowL", [])) else ""
                lead_vals = [param, test_num, high, low]
            else:
                lead_vals = [param, test_num]

            if b in ("GuRefFinalData", "GuCorrRawData", "GuRawData"):
                vals = [rows.get(pid, [""])[i] if i < len(rows.get(pid, [])) else "" for pid in pids]
                row += lead_vals + vals
            elif b == "GuCorrFactor":
                agg = rows.get("999", [""])
                val = agg[i] if i < len(agg) else ""
                pf = _pass_fail(val, high, low)
                row += lead_vals + [val, pf]
            elif b == "GuCorrCoeff":
                agg = rows.get("999", [""])
                val = agg[i] if i < len(agg) else ""
                row += lead_vals + [val]
            elif b == "GuVrfyError":
                vals = [rows.get(pid, [""])[i] if i < len(rows.get(pid, [])) else "" for pid in pids]
                pfs = [_pass_fail(v, high, low) for v in vals]
                cumm = str(sum(int(p) for p in pfs if p)) if any(pfs) else ""
                row += lead_vals + vals + pfs + [cumm]
        data_rows.append(row)

    def footer_row(label: str, scalar_value) -> list[str]:
        row: list[str] = []
        for b in present:
            lay = layouts[b]
            n_lead = len(lay["lead"])
            n_values = len(lay["values"])
            n_pf = n_values if lay["pf"] else 0
            n_cumm = 1 if lay["cumm"] else 0
            value = scalar_value(b) if callable(scalar_value) else scalar_value
            values = [value] * n_values
            row += [label] + [""] * (n_lead - 1) + values + [""] * (n_pf + n_cumm)
        return row

    def id_values(b: str) -> list[str]:
        lay = layouts[b]
        if b == "GuCorrFactor":
            return [f"{gu_batch_id}_PID-999[CF]"]
        if b == "GuCorrCoeff":
            return [f"{gu_batch_id}_PID-999"]
        return [f"{gu_batch_id}_{pid}" for pid in lay["values"]]

    def id_row() -> list[str]:
        row: list[str] = []
        for b in present:
            lay = layouts[b]
            n_lead = len(lay["lead"])
            n_values = len(lay["values"])
            n_pf = n_values if lay["pf"] else 0
            n_cumm = 1 if lay["cumm"] else 0
            row += [label_id] + [""] * (n_lead - 1) + id_values(b) + [""] * (n_pf + n_cumm)
        return row

    label_id = "GU_ID"

    footer_rows = [
        footer_row("GU_Filename", zip_stem),
        footer_row("GU_Date", meta.get("Date", "").replace("_", "/", 2)),
        footer_row("GU_TestPlan", meta.get("TestPlan", "")),
        footer_row("GU_TesterName", meta.get("TesterName", "")),
        footer_row("GU_LoadBoardName", meta.get("LoadBoardName", "")),
        footer_row("GU_ContractorID", meta.get("ContactorID", "")),
        footer_row("GU_InstrumentInfo", meta.get("InstrumentInfo", "")),
        footer_row("GU_GuBatchID", str(gu_batch_id)),
        footer_row("GU_Status", status),
        footer_row("GU_DataType", lambda b: b),
        id_row(),
    ]

    return header_rows + data_rows + footer_rows


def write_gu_summary_csv(rows: list[list[str]], out_path: str) -> None:
    with open(long_path(out_path), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
