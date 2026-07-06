"""
One-off script: seeds the Gym Directory tab with all JST Class gyms.

Usage:
    SHEET_ID=<your_sheet_id> \
    GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json \
    python seed_gym_directory.py

Safe to re-run — skips gyms already present (matched on Gym Code).
Owner Name, Owner Email, Tier, and Monthly Fee are left blank for you
to fill in on the sheet.
"""

import os
import sys
import gspread

SHEET_ID = os.environ.get("SHEET_ID", "")
SA_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
TAB = "Gym Directory"

HEADERS = [
    "Gym Name", "Gym Code", "Owner Name", "Owner Email",
    "Tier", "Monthly Fee", "Coach", "Notes",
]

# (Gym Name, Gym Code)
GYMS = [
    ("CrossFit Hunta",                    "HUNTA"),
    ("CrossFit Jacana",                   "JACANA"),
    ("Evolve Hartlepool",                 "EVOLVE"),
    ("Unit 11",                           "UNIT11"),
    ("Arc Athletics",                     "ARC"),
    ("CrossFit Blackpool",                "BLACKPOOL"),
    ("Cattle Dog CrossFit",               "CATTLEDOG"),
    ("Fell Training Collective",          "FELL"),
    ("CrossFit Symbiote",                 "SYMBIOTE"),
    ("CrossFit Fife",                     "FIFE"),
    ("CrossFit MTS",                      "MTS"),
    ("CrossFit Wigan",                    "WIGAN"),
    ("Clubhaus",                          "CLUBHAUS"),
    ("180 Project / CrossFit Delta Fox",  "DELTAFOX"),
    ("Outset",                            "OUTSET"),
    ("CrossFit House of Wolves",          "WOLVES"),
    ("Strength & Perform Fitness",        "SPFITNESS"),
    ("CrossFit Clifton",                  "CLIFTON"),
    ("Anthony Fitzsimmons",               "FITZ"),
    ("CrossFit Mercia",                   "MERCIA"),
    ("CrossFit Reading",                  "READING"),
    ("CrossFit Tigerlily",                "TIGERLILY"),
    ("Nomads Training Club",              "NOMADS"),
    ("Cortex Training Centre",            "CORTEX"),
    ("CrossFit Lightning Bolt",           "LIGHTNING"),
    ("CrossFit Chester",                  "CHESTER"),
    ("CrossFit Reigate",                  "REIGATE"),
    ("CrossFit Basingstoke",              "BASINGSTOKE"),
]


def main():
    if not SHEET_ID:
        sys.exit("SHEET_ID env var is required.")

    gc = gspread.service_account(filename=SA_FILE)
    sh = gc.open_by_key(SHEET_ID)

    try:
        ws = sh.worksheet(TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB, rows=100, cols=len(HEADERS))
        ws.update("A1", [HEADERS])
        print(f"Created '{TAB}' tab with headers.")

    all_values = ws.get_all_values()
    headers = all_values[0] if all_values else []
    try:
        code_idx = headers.index("Gym Code")
    except ValueError:
        sys.exit(f"No 'Gym Code' column found in '{TAB}' — headers are {headers}")

    existing_codes = {
        row[code_idx].strip().upper()
        for row in all_values[1:]
        if len(row) > code_idx and row[code_idx].strip()
    }

    new_rows = []
    skipped = []
    for name, code in GYMS:
        if code in existing_codes:
            skipped.append((name, code))
        else:
            new_rows.append([name, code, "", "", "", "", "", ""])

    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")

    print(f"Added {len(new_rows)} gym(s):")
    for row in new_rows:
        print(f"  {row[0]:38s} {row[1]}")

    if skipped:
        print(f"\nSkipped {len(skipped)} already present:")
        for name, code in skipped:
            print(f"  {name:38s} {code}")

    print(
        "\nNext: fill in Owner Name, Owner Email, and Monthly Fee on the "
        "Gym Directory tab. Owner Email is where monthly credit statements go."
    )


if __name__ == "__main__":
    main()
