#!/usr/bin/env python3
"""One-off migration: convert 'NNN secs' values in PR Log to mm:ss format.

Run:
    python backfill_time_format.py          # interactive (asks before writing)
    python backfill_time_format.py --dry-run  # preview only, no writes

Only touches rows where Value exactly matches a plain-seconds string like
"1530 secs", "90 sec", etc.  All other rows are left untouched.
"""
import re
import sys
import config
from sheets_client import SheetsClient

_SECS_RE = re.compile(r'^(\d+)\s+sec(?:s)?$', re.IGNORECASE)


def _to_mm_ss(total_secs):
    total = int(total_secs)
    mins, secs = divmod(total, 60)
    if mins >= 60:
        hrs, mins = divmod(mins, 60)
        return f"{hrs}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def _idx_to_col(idx):
    s = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def main():
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv
    if dry_run:
        print("[DRY RUN] No changes will be written.\n")

    sheets = SheetsClient()
    values = sheets.read_values(config.TAB_PR_LOG)
    if not values:
        print("PR Log is empty.")
        return

    header = values[0]
    try:
        value_idx = header.index("Value")
    except ValueError:
        print("'Value' column not found in PR Log header:", header)
        return

    name_idx = header.index("Athlete Name") if "Athlete Name" in header else 1
    bench_idx = header.index("Benchmark Name") if "Benchmark Name" in header else 3
    col_letter = _idx_to_col(value_idx + 1)

    updates = {}
    for i, row in enumerate(values[1:], start=2):
        if value_idx >= len(row):
            continue
        val = str(row[value_idx]).strip()
        m = _SECS_RE.match(val)
        if not m:
            continue
        secs = int(m.group(1))
        new_val = _to_mm_ss(secs)
        updates[i] = new_val
        name = row[name_idx] if name_idx < len(row) else "?"
        bench = row[bench_idx] if bench_idx < len(row) else "?"
        print(f"  Row {i}: {name} — {bench}: {val!r}  →  {new_val!r}")

    if not updates:
        print("No rows found with plain-seconds values. Nothing to do.")
        return

    print(f"\n{len(updates)} row(s) to convert.")

    if dry_run:
        print("[DRY RUN] Exiting without writing.")
        return

    confirm = input("Proceed? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    sheets.update_cells_by_rowmap(config.TAB_PR_LOG, col_letter, updates)
    print(f"Done. {len(updates)} value(s) converted to mm:ss format.")


if __name__ == "__main__":
    main()
