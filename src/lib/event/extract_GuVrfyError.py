import zipfile
import os
import shutil
import sys

# Allow running this script directly (py .../extract_GuVrfyError.py) -- put
# <project_root>/src on sys.path so the lib.event.* absolute imports resolve.
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from lib.event.winpath import long_path

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_DIR = os.path.join(SOURCE_DIR, "result")

os.makedirs(long_path(RESULT_DIR), exist_ok=True)

zip_files = [f for f in os.listdir(long_path(SOURCE_DIR)) if f.lower().endswith(".zip")]

if not zip_files:
    print("No ZIP files found in:", SOURCE_DIR)
else:
    print(f"Found {len(zip_files)} ZIP file(s). Extracting GuVrfyError files...\n")

extracted = 0
skipped = 0

for zip_name in sorted(zip_files):
    zip_path = os.path.join(SOURCE_DIR, zip_name)
    with zipfile.ZipFile(long_path(zip_path), "r") as zf:
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
            with zf.open(entry) as src, open(long_path(dest_path), "wb") as dst:
                shutil.copyfileobj(src, dst)
            print(f"  [OK]   {filename}")
            extracted += 1

print(f"\nDone. {extracted} file(s) extracted to '{RESULT_DIR}', {skipped} ZIP(s) skipped.")
