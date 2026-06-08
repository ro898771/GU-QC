import zipfile
import os
import shutil

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_DIR = os.path.join(SOURCE_DIR, "result")

os.makedirs(RESULT_DIR, exist_ok=True)

zip_files = [f for f in os.listdir(SOURCE_DIR) if f.lower().endswith(".zip")]

if not zip_files:
    print("No ZIP files found in:", SOURCE_DIR)
else:
    print(f"Found {len(zip_files)} ZIP file(s). Extracting GuVrfyError files...\n")

extracted = 0
skipped = 0

for zip_name in sorted(zip_files):
    zip_path = os.path.join(SOURCE_DIR, zip_name)
    with zipfile.ZipFile(zip_path, "r") as zf:
        matches = [
            entry for entry in zf.namelist()
            if "GuVrfyError" in os.path.basename(entry)
            and entry.startswith("4_VerifyAnalysis/")
        ]
        if not matches:
            print(f"  [SKIP] No GuVrfyError file found in: {zip_name}")
            skipped += 1
            continue
        for entry in matches:
            filename = os.path.basename(entry)
            dest_path = os.path.join(RESULT_DIR, filename)
            with zf.open(entry) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            print(f"  [OK]   {filename}")
            extracted += 1

print(f"\nDone. {extracted} file(s) extracted to '{RESULT_DIR}', {skipped} ZIP(s) skipped.")
