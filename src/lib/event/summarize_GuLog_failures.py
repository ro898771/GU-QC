import zipfile
import os
import re
import csv

from lib.event.winpath import long_path

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_DIR = os.path.join(SOURCE_DIR, "result")
OUTPUT_CSV = os.path.join(RESULT_DIR, "GuLog_FailedSummary.csv")

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

HEADER_KEYS = [
    "Date", "StartTime", "FinishTime", "TestPlanVersion",
    "Product", "TestPlan", "Lot", "Sublot", "Wafer",
    "TesterName", "TesterIPaddress", "Operator",
]

zip_files = sorted(f for f in os.listdir(long_path(SOURCE_DIR)) if f.lower().endswith(".zip"))

rows = []

for zip_name in zip_files:
    zip_path = os.path.join(SOURCE_DIR, zip_name)
    with zipfile.ZipFile(long_path(zip_path), "r") as zf:
        log_entries = [e for e in zf.namelist() if "GuLogPrintout" in e]
        if not log_entries:
            print(f"  [SKIP] No GuLogPrintout in: {zip_name}")
            continue

        with zf.open(log_entries[0]) as f:
            content = f.read().decode("utf-8", errors="replace")

    # --- parse header metadata ---
    meta = {k: "" for k in HEADER_KEYS}
    for line in content.splitlines():
        for key in HEADER_KEYS:
            if line.startswith(key + ","):
                meta[key] = line[len(key) + 1:].strip()
                break

    # --- parse failed lines ---
    fail_count = 0
    for line in content.splitlines():
        m_ve = FAIL_VE_RE.search(line)
        m_cf = FAIL_CF_RE.search(line)
        if m_ve:
            site, device, test_num, param_name, lowl, error, highl = m_ve.groups()
            fail_type = "Failed GU Verification limits"
        elif m_cf:
            site, test_num, param_name, lowl, error, highl = m_cf.groups()
            device = ""
            fail_type = "Failed GU Corr-factor limits"
        else:
            continue
        rows.append({
            "ZipFile":         zip_name,
            "Date":            meta["Date"],
            "StartTime":       meta["StartTime"],
            "FinishTime":      meta["FinishTime"],
            "Product":         meta["Product"],
            "TestPlan":        meta["TestPlan"],
            "TesterName":      meta["TesterName"],
            "TesterIPaddress": meta["TesterIPaddress"],
            "Operator":        meta["Operator"],
            "Sublot":          meta["Sublot"],
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

    print(f"  {zip_name}")
    print(f"    Tester: {meta['TesterName']}  |  IP: {meta['TesterIPaddress']}  |  Date: {meta['Date']}")
    print(f"    Failed lines: {fail_count}")

# --- write CSV ---
if rows:
    fieldnames = list(rows[0].keys())
    with open(long_path(OUTPUT_CSV), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nDone. {len(rows)} failure row(s) written to:")
    print(f"  {OUTPUT_CSV}")
else:
    print("\nNo failed lines found.")
