"""
One-off script: adds/populates the 'Coach Name' column on the Coaches tab.

Usage:
    SHEET_ID=<your_sheet_id> \
    GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json \
    python add_coach_names.py

Prints a summary of what it wrote. Safe to re-run — overwrites existing values.
"""

import os
import sys
import gspread

SHEET_ID = os.environ.get("SHEET_ID", "")
SA_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
TAB = "Coaches"

COACH_MAP = {
    "Dan Connolly":                  "Dan Connolly",
    "Denis Smith":                   "Denis Smith",
    "Jamie Harrop":                  "Jamie Harrop",
    "Ed Cook - Individual Programming": "Ed Cook",
    "Louis Towers":                  "Louis Towers",
    "Peter Crudge":                  "Peter Crudge",
    "Individual Coaching - Jamie Warr": "Jamie Warr",
    "Individual programming - Jak":  "Jak Cornthwaite",
}


def main():
    if not SHEET_ID:
        sys.exit("SHEET_ID env var is required.")

    gc = gspread.service_account(filename=SA_FILE)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(TAB)

    all_values = ws.get_all_values()
    if not all_values:
        sys.exit(f"'{TAB}' tab is empty.")

    headers = all_values[0]
    print(f"Existing columns: {headers}")

    # Locate Programme column (1-indexed for gspread)
    try:
        prog_col = headers.index("Programme") + 1
    except ValueError:
        sys.exit("No 'Programme' column found in the Coaches tab.")

    # Find or create Coach Name column
    if "Coach Name" in headers:
        coach_col = headers.index("Coach Name") + 1
        print(f"'Coach Name' column already exists at column {coach_col}.")
    else:
        coach_col = len(headers) + 1
        ws.update_cell(1, coach_col, "Coach Name")
        print(f"Created 'Coach Name' header at column {coach_col}.")

    # Build updates
    cells = []
    matched, skipped = [], []

    for row_idx, row in enumerate(all_values[1:], start=2):
        prog = row[prog_col - 1].strip() if len(row) >= prog_col else ""
        if not prog:
            continue
        coach = COACH_MAP.get(prog)
        if coach:
            cells.append(gspread.Cell(row_idx, coach_col, coach))
            matched.append((prog, coach))
        else:
            skipped.append(prog)

    if cells:
        ws.update_cells(cells)

    print(f"\nWrote {len(cells)} coach name(s):")
    for prog, coach in matched:
        print(f"  {prog!r:50s} → {coach}")

    if skipped:
        print(f"\nNo mapping found for {len(skipped)} programme(s) — left blank:")
        for p in skipped:
            print(f"  {p!r}")
        print("\nAdd these to COACH_MAP in the script if needed, then re-run.")


if __name__ == "__main__":
    main()
