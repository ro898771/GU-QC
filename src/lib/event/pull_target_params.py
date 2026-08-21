import csv
import os

from lib.event.winpath import long_path

SOURCE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "result", "GuVrfyError_ALL_CONCAT.csv")
OUTPUT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "result", "GuVrfyError_TargetParams.csv")

# Partial name fragments — each must match exactly one column
TARGET_FRAGMENTS = [
    "F_Gain_Rx_RT_N79_ANTUL_ANT2_RXOUT2-N79_MIN_G0H_1.12Vdd_CH7_S42_4400MHz_5000MHz",
    "F_Gain_Rx_RT_N79_ANTUL_ANT2_RXOUT2-N79_MIN_G0H_1.12Vdd_CH7_S42_4400MHz_4500MHz",
    "F_Gain_Rx_RT_N79_ANTUL_ANT2_RXOUT2-N79_MIN_G0L_1.12Vdd_CH28_S42_4400MHz_5000MHz",
    "F_Gain_Rx_RT_N79_ANTUL_ANT2_RXOUT2-N79_MIN_G0L_1.12Vdd_CH28_S42_4400MHz_4500MHz",
    "F_RL_ANT_RT_ANTL_ANTUL_ANT2_x_MAX_x_1.12Vdd_CH10_S22_3300MHz_4200MHz",
    "F_RL_ANT_RT_ANTL_ANTUL_ANT1_x_MAX_x_1.12Vdd_CH9_S22_3300MHz_4200MHz",
    "F_RL_ANT_RT_ANTL_ANTUL_ANT1_x_MAX_x_1.12Vdd_CH9_S22_4400MHz_5000MHz",
]

# Number of leading identity columns to always keep
ID_COL_COUNT = 10   # Parameter, SBIN, HBIN, DIE_X, DIE_Y, SITE, TIME, TOTAL_TESTS, LOT_ID, WAFER_ID

with open(long_path(SOURCE), encoding="utf-8") as f:
    reader = csv.reader(f)
    all_rows = list(reader)

param_header = all_rows[0]   # "Parameter" row — contains column names

# Resolve target column indices (in order of TARGET_FRAGMENTS)
target_cols = []
for frag in TARGET_FRAGMENTS:
    matches = [i for i, h in enumerate(param_header) if frag in h]
    if not matches:
        print(f"WARNING: not found -> {frag}")
    else:
        target_cols.extend(matches)

id_cols    = list(range(ID_COL_COUNT))
keep_cols  = id_cols + target_cols

print("Target columns resolved:")
for ci in target_cols:
    print(f"  [{ci:4d}] {param_header[ci]}")

# Build output rows (keep meta rows + all data rows)
out_rows = []
for row in all_rows:
    # Pad short rows so indexing never fails
    padded = row + [""] * (max(keep_cols) + 1 - len(row))
    out_rows.append([padded[c] for c in keep_cols])

with open(long_path(OUTPUT), "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerows(out_rows)

data_rows = len(all_rows) - 5   # subtract 5 meta rows (Parameter/Test#/Unit/HighL/LowL)
print(f"\nDone. {data_rows} data rows x {len(target_cols)} target parameters")
print(f"Output: {OUTPUT}")
