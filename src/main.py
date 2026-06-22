"""
gufile_process.py
=================
Processes all ZIP files in a given folder and writes outputs to AllResult/
inside that same folder.

Usage:
  py gufile_process.py  → prompts for GUFILE folder path at runtime

Tasks:
  a) Extract GuCorrFactor   (3_CorrAnalysis)   → <folder>/AllResult/GuCorrFactor/
  b) Concat GuCorrFactor                        → <folder>/AllResult/GuCorrFactor_ALL_CONCAT.csv
  c) Extract GuVrfyError    (4_VerifyAnalysis)  → <folder>/AllResult/GuVrfyError/
  d) Concat GuVrfyError                         → <folder>/AllResult/GuVrfyError_ALL_CONCAT.csv
  e) Extract GuCorrRawData  (3_CorrAnalysis)    → <folder>/AllResult/Corr_GuCorrRawData/
  f) Concat GuCorrRawData                       → <folder>/AllResult/Corr_GuCorrRawData_ALL_CONCAT.csv
  h) Extract GuRawData      (4_VerifyAnalysis)  → <folder>/AllResult/Vry_GuRawData/
  i) Concat GuRawData                           → <folder>/AllResult/Vry_GuRawData_ALL_CONCAT.csv
  g) GuLogPrintout failures summary             → <folder>/AllResult/GuLog_FailedSummary.csv
"""

from __future__ import annotations

import zipfile
import os
import re
import csv
import shutil
import stat


def _force_remove_readonly(func, path, _exc_info):
    """onerror handler for shutil.rmtree: make file writable then retry."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _force_delete(path: str) -> None:
    """Delete a single file, forcing writable if needed. No-op if not found."""
    if not os.path.exists(path):
        return
    try:
        os.remove(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)

# ── paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

folder_input = input("Enter the GUFILE folder path (absolute or relative to project root): ").strip().strip('"').strip("'")

if os.path.isabs(folder_input):
    GUFILE_DIR = folder_input
else:
    GUFILE_DIR = os.path.join(BASE_DIR, folder_input)

folder_name = os.path.basename(GUFILE_DIR)
OUT_DIR     = os.path.join(os.getcwd(), "result")

# Force-clean the result folder before each run to avoid stale file conflicts
if os.path.exists(OUT_DIR):
    print(f"Cleaning previous result folder: {OUT_DIR}")
    shutil.rmtree(OUT_DIR, onerror=_force_remove_readonly)

# sub-folders for individual extracted files
DIR_CF  = os.path.join(OUT_DIR, "GuCorrFactor")
DIR_VE  = os.path.join(OUT_DIR, "GuVrfyError")
DIR_CR  = os.path.join(OUT_DIR, "Corr_GuCorrRawData")
DIR_VR  = os.path.join(OUT_DIR, "Vry_GuRawData")
DIR_RF  = os.path.join(OUT_DIR, "GuRefFinalData")
DIR_VD  = os.path.join(OUT_DIR, "GuVrfyData")
DIR_CC  = os.path.join(OUT_DIR, "GuCorrCoeff")

for d in (OUT_DIR, DIR_CF, DIR_VE, DIR_CR, DIR_VR, DIR_RF, DIR_VD, DIR_CC):
    os.makedirs(d, exist_ok=True)

# ── helpers ────────────────────────────────────────────────────────────────────
HEADER_META_KEYS = [
    "Date", "StartTime", "FinishTime", "TestPlanVersion",
    "Product", "TestPlan", "Lot", "Sublot", "Wafer",
    "TesterName", "TesterIPaddress", "Operator",
]

# Rows whose first field is one of these are column-descriptor rows (keep as header)
COL_HEADER_STARTS = {"Parameter", "Test#", "Unit", "HighL", "LowL"}

# Rows whose first field starts with PID- or is a bare number (device ID) are data
def is_data_row(first_field: str) -> bool:
    f = first_field.strip()
    return f.startswith("PID-") or (f and f[0].isdigit())


def parse_meta(lines: list[str]) -> dict:
    """Extract key-value pairs from the Global Info header block."""
    meta = {k: "" for k in HEADER_META_KEYS}
    for line in lines:
        for key in HEADER_META_KEYS:
            if line.startswith(key + ","):
                meta[key] = line[len(key) + 1:].strip()
                break
    return meta


def extract_file_from_zip(zf: zipfile.ZipFile, keyword: str, subfolder: str) -> str | None:
    """
    Find the first entry whose basename contains `keyword` and lives under `subfolder/`.
    Returns the entry name or None.
    """
    for entry in zf.namelist():
        if entry.startswith(subfolder + "/") and keyword in os.path.basename(entry):
            return entry
    return None


def read_zip_text(zf: zipfile.ZipFile, entry: str) -> list[str]:
    with zf.open(entry) as f:
        return f.read().decode("utf-8", errors="replace").splitlines()


def extract_and_save(zf: zipfile.ZipFile, entry: str, dest_dir: str) -> str:
    """Extract a single zip entry flat into dest_dir. Returns dest path."""
    filename = os.path.basename(entry)
    dest = os.path.join(dest_dir, filename)
    with zf.open(entry) as src, open(dest, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return dest


def get_tester_name(lines: list[str]) -> str:
    """Read TesterName value from a file's Global Info header lines."""
    for line in lines:
        if line.startswith("TesterName,"):
            return line[len("TesterName,"):].strip()
    return ""


def build_zip_tester_map(extracted_list: list[tuple[str, str]]) -> dict[str, str]:
    """Return {zip_name: tester_name} by reading TesterName from extracted CSV files."""
    mapping: dict[str, str] = {}
    for path, zip_name in extracted_list:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("TesterName,"):
                        tester = line[len("TesterName,"):].strip()
                        if tester:
                            mapping[zip_name] = tester
                        break
        except OSError:
            continue
    return mapping


def concat_csv_files(file_list: list[tuple[str, str]], output_path: str,
                     pid_map: dict | None = None,
                     tester_map: dict | None = None) -> int:
    """
    Concatenate CSV files that share the GuVrfyError / GuCorrFactor / GuCorrRawData
    format (Global Info header + col-descriptor rows + PID data rows).

    file_list : list of (file_path, zip_name) tuples
    pid_map    : optional {zip_name: '#PID1,#PID2,...'} — when provided, rows whose
                 PID field is exactly 999 have that field replaced with the real device
                 IDs (CSV-quoted so commas inside the field are handled correctly).
    tester_map : optional {zip_name: tester_name} — fallback when the file itself has
                 no TesterName header (e.g. GuRefFinalData).

    Output columns: TesterName, ZipFile, <original columns...>
      - Col-descriptor rows (Parameter / Test# / Unit / HighL / LowL) → first file only
        "TesterName" and "ZipFile" labels on Parameter row; blank for the rest
      - PID data rows → all files, with TesterName and ZipFile prepended
      - Everything else (metadata, blanks, summaries) → skip
    """
    col_header_written = False
    col_header_rows: list[str] = []
    data_rows: list[str] = []

    for path, zip_name in file_list:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        tester_name = get_tester_name(lines)
        if not tester_name and tester_map:
            tester_name = tester_map.get(zip_name, "")

        for line in lines:
            raw = line.rstrip("\n").rstrip("\r")
            first = raw.split(",")[0].strip()

            if first in COL_HEADER_STARTS:
                if not col_header_written:
                    if first == "Parameter":
                        prefix = "TesterName,ZipFile"
                    else:
                        prefix = ","
                    col_header_rows.append(f"{prefix},{raw}\n")
            elif is_data_row(first):
                if pid_map is not None:
                    pid_val = first.replace("PID-", "") if first.startswith("PID-") else first
                    if pid_val == "999":
                        real_pids = pid_map.get(zip_name)
                        if real_pids:
                            comma_pos = raw.index(",") if "," in raw else len(raw)
                            rest = raw[comma_pos:]
                            raw = f'"PID-{real_pids}"{rest}'
                data_rows.append(f"{tester_name},{zip_name},{raw}\n")

        col_header_written = True

    with open(output_path, "w", encoding="utf-8", newline="") as out:
        out.writelines(col_header_rows)
        out.writelines(data_rows)

    return len(data_rows)


def build_cr_pid_map(extracted_cr: list[tuple[str, str]]) -> dict[str, str]:
    """
    Return {zip_name: '#7000007,#7000008,...'} by reading the extracted
    CorrRawData CSV files.  Used to replace PID-999 in GuCorrFactor rows.
    """
    mapping: dict[str, str] = {}
    for path, zip_name in extracted_cr:
        pids: set[str] = set()
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    first = line.split(",")[0].strip()
                    if first.startswith("PID-"):
                        pid_val = first[4:]  # strip "PID-"
                    elif first and first[0].isdigit():
                        pid_val = first
                    else:
                        continue
                    if pid_val and pid_val != "999":
                        pids.add(pid_val)
        except OSError:
            continue
        if pids:
            mapping[zip_name] = ",".join("#" + p for p in sorted(pids))
    return mapping


# ── GuLogPrintout failure regexes ─────────────────────────────────────────────
# Verification: site, Device, test, param, LowL, measure error, HighL
FAIL_VE_RE = re.compile(
    r"\*\*\* Failed GU Verification limits,\s*"
    r"site\s*(\S+),\s*"
    r"Device\s*(\S+),\s*"
    r"test\s*(\S+),\s*"
    r"(.+?),\s*"
    r"LowL:\s*(\S+),\s*"
    r"measure error:\s*(\S+),\s*"
    r"HighL:\s*(\S+)"
)

# Corr-factor: site, test, param, LowL, calfactor, HighL  (no Device field)
FAIL_CF_RE = re.compile(
    r"\*\*\* Failed GU Corr-factor limits,\s*"
    r"site\s*(\S+),\s*"
    r"test\s*(\S+),\s*"
    r"(.+?),\s*"
    r"LowL:\s*(\S+),\s*"
    r"calfactor:\s*(\S+),\s*"
    r"HighL:\s*(\S+)"
)

# Device context line preceding Corr-factor failures
DEVICE_RE = re.compile(r"GU Device #(\d+) being run")

# ── main processing ────────────────────────────────────────────────────────────
_top_zips = [f for f in os.listdir(GUFILE_DIR) if f.lower().endswith(".zip")]
_sub_zips = [
    os.path.join(sub, f)
    for sub in os.listdir(GUFILE_DIR)
    if os.path.isdir(os.path.join(GUFILE_DIR, sub))
    for f in os.listdir(os.path.join(GUFILE_DIR, sub))
    if f.lower().endswith(".zip")
]
zip_files = sorted(_top_zips + _sub_zips)

if not zip_files:
    print("No ZIP files found in:", GUFILE_DIR)
    raise SystemExit(1)

print(f"Found {len(zip_files)} ZIP file(s) in {folder_name}\n")
print("=" * 70)

extracted_cf  = []   # list of (path, zip_name) for extracted GuCorrFactor files
extracted_ve  = []   # list of (path, zip_name) for extracted GuVrfyError files
extracted_cr  = []   # list of (path, zip_name) for extracted GuCorrRawData files
extracted_vr  = []   # list of (path, zip_name) for extracted GuRawData files
extracted_rf  = []   # list of (path, zip_name) for extracted GuRefFinalData files
extracted_vd  = []   # list of (path, zip_name) for extracted GuVrfyData files
extracted_cc  = []   # list of (path, zip_name) for extracted GuCorrCoeff files
failure_rows  = []   # dicts for the summary table

for zip_name in zip_files:
    zip_path = os.path.join(GUFILE_DIR, zip_name)
    print(f"\n[ZIP] {zip_name}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        entries = zf.namelist()

        # ── a) GuCorrFactor ───────────────────────────────────────────────────
        entry_cf = extract_file_from_zip(zf, "GuCorrFactor", "3_CorrAnalysis")
        if entry_cf and "NoDemoOffset" not in entry_cf:
            dest = extract_and_save(zf, entry_cf, DIR_CF)
            extracted_cf.append((dest, zip_name))
            print(f"  [CF ] {os.path.basename(entry_cf)}")
        else:
            # try again without subfolder restriction in case naming differs
            fallback = [e for e in entries if "GuCorrFactor" in e and "NoDemoOffset" not in e]
            if fallback:
                dest = extract_and_save(zf, fallback[0], DIR_CF)
                extracted_cf.append((dest, zip_name))
                print(f"  [CF ] {os.path.basename(fallback[0])}")
            else:
                print(f"  [CF ] SKIP — not found")

        # ── c) GuVrfyError ────────────────────────────────────────────────────
        entry_ve = extract_file_from_zip(zf, "GuVrfyError", "4_VerifyAnalysis")
        if entry_ve:
            dest = extract_and_save(zf, entry_ve, DIR_VE)
            extracted_ve.append((dest, zip_name))
            print(f"  [VE ] {os.path.basename(entry_ve)}")
        else:
            print(f"  [VE ] SKIP — not found")

        # ── e) GuCorrRawData ──────────────────────────────────────────────────
        entry_cr = extract_file_from_zip(zf, "GuCorrRawData", "3_CorrAnalysis")
        if entry_cr:
            dest = extract_and_save(zf, entry_cr, DIR_CR)
            extracted_cr.append((dest, zip_name))
            print(f"  [CR ] {os.path.basename(entry_cr)}")
        else:
            print(f"  [CR ] SKIP — not found")

        # ── h) GuRawData ──────────────────────────────────────────────────────
        entry_vr = extract_file_from_zip(zf, "GuRawData", "4_VerifyAnalysis")
        if entry_vr:
            dest = extract_and_save(zf, entry_vr, DIR_VR)
            extracted_vr.append((dest, zip_name))
            print(f"  [VR ] {os.path.basename(entry_vr)}")
        else:
            print(f"  [VR ] SKIP — not found")

        # ── i) GuRefFinalData ─────────────────────────────────────────────────
        entry_rf = extract_file_from_zip(zf, "GuRefFinalData", "1_RefDataAnalysis")
        if entry_rf:
            dest = extract_and_save(zf, entry_rf, DIR_RF)
            extracted_rf.append((dest, zip_name))
            print(f"  [RF ] {os.path.basename(entry_rf)}")
        else:
            print(f"  [RF ] SKIP — not found")

        # ── j) GuVrfyData ────────────────────────────────────────────────────
        entry_vd = extract_file_from_zip(zf, "GuVrfyData", "4_VerifyAnalysis")
        if entry_vd:
            dest = extract_and_save(zf, entry_vd, DIR_VD)
            extracted_vd.append((dest, zip_name))
            print(f"  [VD ] {os.path.basename(entry_vd)}")
        else:
            print(f"  [VD ] SKIP — not found")

        # ── k2) GuCorrCoeff ───────────────────────────────────────────────────
        entry_cc = extract_file_from_zip(zf, "GuCorrCoeff", "4_VerifyAnalysis")
        if entry_cc and "LooseDemo" not in entry_cc:
            dest = extract_and_save(zf, entry_cc, DIR_CC)
            extracted_cc.append((dest, zip_name))
            print(f"  [CC ] {os.path.basename(entry_cc)}")
        else:
            fallback_cc = [e for e in entries if "GuCorrCoeff" in e and "LooseDemo" not in e
                           and "4_VerifyAnalysis" in e]
            if fallback_cc:
                dest = extract_and_save(zf, fallback_cc[0], DIR_CC)
                extracted_cc.append((dest, zip_name))
                print(f"  [CC ] {os.path.basename(fallback_cc[0])}")
            else:
                print(f"  [CC ] SKIP — not found")

        # ── g) GuLogPrintout failures ─────────────────────────────────────────
        log_entries = [e for e in entries if "GuLogPrintout" in e]
        if log_entries:
            lines = read_zip_text(zf, log_entries[0])
            meta  = parse_meta(lines)
            in_messages = False
            fail_count  = 0
            current_devices: list[str] = []
            cf_devices_used = False
            for line in lines:
                if "Messages logged during GU Calibration" in line:
                    in_messages = True
                if in_messages:
                    m_dev = DEVICE_RE.search(line)
                    if m_dev:
                        if cf_devices_used:
                            current_devices.clear()
                            cf_devices_used = False
                        current_devices.append("#" + m_dev.group(1))
                        continue
                    m_ve = FAIL_VE_RE.search(line)
                    m_cf = FAIL_CF_RE.search(line)
                    if m_ve:
                        site, device, test_num, param_name, lowl, error, highl = m_ve.groups()
                        fail_type = "Failed GU Verification limits"
                        current_devices.clear()
                        cf_devices_used = False
                    elif m_cf:
                        site, test_num, param_name, lowl, error, highl = m_cf.groups()
                        device = ",".join(current_devices)
                        cf_devices_used = True
                        fail_type = "Failed GU Corr-factor limits"
                    else:
                        continue
                    failure_rows.append({
                        "TesterName":      meta["TesterName"],
                        "TesterIPaddress": meta["TesterIPaddress"],
                        "Date":            meta["Date"],
                        "FinishTime":      meta["FinishTime"],
                        "Product":         meta["Product"],
                        "Sublot":          meta["Sublot"],
                        "ZipFile":         zip_name,
                        "FailType":        fail_type,
                        "Site":            site,
                        "Device":          device,
                        "TestNum":         test_num,
                        "ParamName":       param_name.strip(),
                        "LowL":            lowl,
                        "MeasureError":    error,
                        "HighL":           highl,
                    })
                    fail_count += 1
            print(f"  [LOG] TesterName={meta['TesterName']}  |  Failed lines={fail_count}")
        else:
            print(f"  [LOG] SKIP — GuLogPrintout not found")

# ── b) Concat GuCorrFactor ─────────────────────────────────────────────────────
print("\n" + "=" * 70)
if extracted_cf:
    out_cf = os.path.join(OUT_DIR, "GuCorrFactor_ALL_CONCAT.csv")
    _cr_pid_map = build_cr_pid_map(extracted_cr) if extracted_cr else {}
    n = concat_csv_files(extracted_cf, out_cf, pid_map=_cr_pid_map)
    print(f"[b] GuCorrFactor concat  : {n} data rows  ->  {out_cf}")
else:
    print("[b] GuCorrFactor concat  : no files to concat")

# ── d) Concat GuVrfyError ──────────────────────────────────────────────────────
if extracted_ve:
    out_ve = os.path.join(OUT_DIR, "GuVrfyError_ALL_CONCAT.csv")
    n = concat_csv_files(extracted_ve, out_ve)
    print(f"[d] GuVrfyError concat   : {n} data rows  ->  {out_ve}")
else:
    print("[d] GuVrfyError concat   : no files to concat")

# ── f) Concat GuCorrRawData ────────────────────────────────────────────────────
if extracted_cr:
    out_cr = os.path.join(OUT_DIR, "Corr_GuCorrRawData_ALL_CONCAT.csv")
    n = concat_csv_files(extracted_cr, out_cr)
    print(f"[f] GuCorrRawData concat : {n} data rows  ->  {out_cr}")
else:
    print("[f] GuCorrRawData concat : no files to concat")

# ── i) Concat GuRawData ────────────────────────────────────────────────────────
if extracted_vr:
    out_vr = os.path.join(OUT_DIR, "Vry_GuRawData_ALL_CONCAT.csv")
    n = concat_csv_files(extracted_vr, out_vr)
    print(f"[i] GuRawData concat     : {n} data rows  ->  {out_vr}")
else:
    print("[i] GuRawData concat     : no files to concat")

# ── k) Concat GuRefFinalData ───────────────────────────────────────────────────
if extracted_rf:
    out_rf = os.path.join(OUT_DIR, "GuRefFinalData_ALL_CONCAT.csv")
    _rf_tester_map = build_zip_tester_map(extracted_ve if extracted_ve else extracted_vd)
    n = concat_csv_files(extracted_rf, out_rf, tester_map=_rf_tester_map)
    print(f"[k] GuRefFinalData concat: {n} data rows  ->  {out_rf}")
else:
    print("[k] GuRefFinalData concat: no files to concat")

# ── l) Concat GuVrfyData ───────────────────────────────────────────────────────
if extracted_vd:
    out_vd = os.path.join(OUT_DIR, "GuVrfyData_ALL_CONCAT.csv")
    n = concat_csv_files(extracted_vd, out_vd)
    print(f"[l] GuVrfyData concat    : {n} data rows  ->  {out_vd}")
else:
    print("[l] GuVrfyData concat    : no files to concat")

# ── m) Concat GuCorrCoeff ──────────────────────────────────────────────────────
if extracted_cc:
    out_cc = os.path.join(OUT_DIR, "GuCorrCoeff_ALL_CONCAT.csv")
    _rf_pid_map = build_cr_pid_map(extracted_rf) if extracted_rf else {}
    n = concat_csv_files(extracted_cc, out_cc, pid_map=_rf_pid_map)
    print(f"[m] GuCorrCoeff concat   : {n} data rows  ->  {out_cc}")
else:
    print("[m] GuCorrCoeff concat   : no files to concat")

# ── g) Write failure summary CSV ───────────────────────────────────────────────
out_log = os.path.join(OUT_DIR, "GuLog_FailedSummary.csv")
_force_delete(out_log)
fieldnames = [
    "TesterName", "TesterIPaddress", "Date", "FinishTime",
    "Product", "Sublot", "ZipFile", "FailType",
    "Site", "Device", "TestNum", "ParamName", "LowL", "MeasureError", "HighL",
]
with open(out_log, "w", newline="", encoding="utf-8") as f:
    if failure_rows:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(failure_rows)
    else:
        f.write("All Pass\n")
print(f"[g] GuLog failure summary: {len(failure_rows)} rows  ->  {out_log}")

# ── final summary ──────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("ALL DONE")
print(f"  GuCorrFactor    : {len(extracted_cf)} files extracted,  concat written")
print(f"  GuVrfyError     : {len(extracted_ve)} files extracted,  concat written")
print(f"  GuCorrRawData   : {len(extracted_cr)} files extracted,  concat written")
print(f"  GuRawData       : {len(extracted_vr)} files extracted,  concat written")
print(f"  GuRefFinalData  : {len(extracted_rf)} files extracted,  concat written")
print(f"  GuVrfyData      : {len(extracted_vd)} files extracted,  concat written")
print(f"  GuCorrCoeff     : {len(extracted_cc)} files extracted,  concat written")
print(f"  Failure rows    : {len(failure_rows)}")
print(f"  Output folder : {OUT_DIR}")

os.startfile(OUT_DIR)
