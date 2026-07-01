#!/usr/bin/env python3
"""One-off cleanup: apply BENCHMARK_NAME_MAP to all existing PR Log entries.

Finds rows where the stored benchmark name has a canonical equivalent in
BENCHMARK_NAME_MAP and optionally rewrites them to use the canonical name.
Run this after adding new normalisations to config.BENCHMARK_NAME_MAP.

Run:
    python dedup_benchmarks.py           # interactive (asks before writing)
    python dedup_benchmarks.py --dry-run # preview only, no writes
"""
import sys
import config
from sheets_client import SheetsClient


def _normalize(name):
    return config.BENCHMARK_NAME_MAP.get(name.strip().lower(), name.strip())


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
        bench_idx = header.index("Benchmark Name")
    except ValueError:
        print("'Benchmark Name' column not found in PR Log header:", header)
        return

    name_idx = header.index("Athlete Name") if "Athlete Name" in header else 1
    col_letter = _idx_to_col(bench_idx + 1)

    updates = {}
    variant_map = {}  # (raw, canonical) -> list of athlete names
    for i, row in enumerate(values[1:], start=2):
        if bench_idx >= len(row):
            continue
        raw = str(row[bench_idx]).strip()
        canonical = _normalize(raw)
        if raw == canonical:
            continue
        updates[i] = canonical
        nm = row[name_idx] if name_idx < len(row) else "?"
        variant_map.setdefault((raw, canonical), []).append(nm)

    if not updates:
        print("Nothing to normalise — all benchmark names already match BENCHMARK_NAME_MAP.")
        return

    print(f"\n{len(updates)} row(s) to normalise across {len(variant_map)} variant(s):\n")
    for (raw, canonical), names in sorted(variant_map.items()):
        unique = sorted(set(names))
        suffix = f"({len(unique)} athlete(s): {', '.join(unique[:5])}{'…' if len(unique) > 5 else ''})"
        print(f"  {len(names):3d} row(s)  '{raw}'  →  '{canonical}'  {suffix}")

    if dry_run:
        print("\n[DRY RUN] Exiting without writing.")
        return

    confirm = input(f"\nApply {len(updates)} normalisation(s)? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    sheets.update_cells_by_rowmap(config.TAB_PR_LOG, col_letter, updates)
    print(f"\nDone. {len(updates)} benchmark name(s) normalised.")


if __name__ == "__main__":
    main()
