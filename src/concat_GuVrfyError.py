import os

SOURCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "result")
OUTPUT_FILE = os.path.join(SOURCE_DIR, "GuVrfyError_ALL_CONCAT.csv")

HEADER_STARTS = {"Parameter,", "Test#,", "Unit,", "HighL,", "LowL,"}

csv_files = sorted(
    f for f in os.listdir(SOURCE_DIR)
    if "GuVrfyError" in f and f.endswith(".csv") and "CONCAT" not in f
)

if not csv_files:
    print("No GuVrfyError CSV files found in:", SOURCE_DIR)
    raise SystemExit(1)

print(f"Found {len(csv_files)} file(s) to concatenate.\n")

header_rows = []   # Parameter / Test# / Unit / HighL / LowL — taken from first file only
data_rows   = []   # PID-... rows from all files

for idx, fname in enumerate(csv_files):
    fpath = os.path.join(SOURCE_DIR, fname)
    with open(fpath, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    file_data = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if any(stripped.startswith(h) for h in HEADER_STARTS):
            if idx == 0:
                header_rows.append(line if line.endswith("\n") else line + "\n")
        elif stripped.startswith("PID-"):
            file_data.append(line if line.endswith("\n") else line + "\n")

    print(f"  {fname}  ->  {len(file_data)} data row(s)")
    data_rows.extend(file_data)

with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as out:
    out.writelines(header_rows)
    out.writelines(data_rows)

print(f"\nDone. {len(data_rows)} total data rows written to:")
print(f"  {OUTPUT_FILE}")
