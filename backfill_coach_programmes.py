"""
Populate the Programme column in _DATA for all bespoke / J+Y athletes
based on the CRM Bespoke Athletes and Junior + Youth tabs.

Programme values are set to the coach's full name (e.g. "Denis Smith").
These are matched against the Coaches tab in the main sheet to route
Slack notifications to the right coach.

Run any time new athletes are added:
    python backfill_coach_programmes.py
    python backfill_coach_programmes.py --dry-run   # preview only
"""

import sys
import gspread
from dotenv import load_dotenv

load_dotenv()
import config

DRY_RUN = "--dry-run" in sys.argv

CRM_SHEET_ID = "1LA58Pnvgte5HliXXTSvioB1RKnwGGWumkxsXasm7nSo"
CRM_TABS = ["Bespoke Athletes", "Junior + Youth"]

# Maps CRM athlete names to their Fitr/_DATA name where spellings differ.
NAME_ALIASES = {
    "alanis sky akin": "Alanis-Sky Akin",
    "rachel ralston-smith": "Rachael Ralston-Smith",
    "scott moore": "Scotty Moore",
}

# Maps coach abbreviations (Coach column in CRM) → Programme name (Coaches tab in main sheet).
# Coaches absent here have no Slack routing configured and are skipped.
COACH_ABBREV_TO_PROGRAMME = {
    "jamie w": "Jamie Warr",
    "jamie h": "Jamie Harrop",
    "dcon": "Dan Connolly",
    "denis": "Denis Smith",
    "ed": "Ed Cook",
    "jak": "Jak Cornthwaite",
    "louis": "Louis Towers",
    "huw": "Huw Davis",
    "pete": "Pete Crudge",
}


def load_crm_athlete_coach_map(gc):
    """Read CRM tabs and return {athlete_name_lower: (display_name, programme)}."""
    sh = gc.open_by_key(CRM_SHEET_ID)
    mapping = {}

    for tab in CRM_TABS:
        rows = sh.worksheet(tab).get_all_values()
        if not rows:
            continue
        header = rows[0]
        try:
            name_idx = header.index("Athlete Name")
            coach_idx = header.index("Coach")
        except ValueError:
            print(f"  WARNING: '{tab}' tab missing expected columns, skipping.")
            continue

        for row in rows[1:]:
            name = (row[name_idx] if name_idx < len(row) else "").strip()
            coach_raw = (row[coach_idx] if coach_idx < len(row) else "").strip()

            if not name or not coach_raw:
                continue

            programme = COACH_ABBREV_TO_PROGRAMME.get(coach_raw.lower())
            if programme:
                fitr_name = NAME_ALIASES.get(name.lower(), name)
                mapping[fitr_name.lower()] = (fitr_name, programme)

    return mapping


def main():
    gc = gspread.service_account(filename=config.GOOGLE_SERVICE_ACCOUNT_FILE)

    print("Loading CRM athlete → coach mapping...")
    crm_map = load_crm_athlete_coach_map(gc)
    print(f"  Found {len(crm_map)} athletes in CRM with recognised coaches.")

    sh = gc.open_by_key(config.SHEET_ID)
    ws = sh.worksheet(config.TAB_DATA)
    all_values = ws.get_all_values()

    if not all_values:
        print("_DATA tab is empty — aborting.")
        return

    header = all_values[0]
    try:
        name_col = header.index("Full Name")
    except ValueError:
        print("'Full Name' column not found in _DATA header — aborting.")
        return

    if "Programme" not in header:
        print("'Programme' column not found in _DATA — aborting.")
        return
    prog_col = header.index("Programme")

    updates = []
    matched = []

    for row_idx, row in enumerate(all_values[1:], start=2):
        raw_name = (row[name_col] if name_col < len(row) else "").strip()
        if not raw_name:
            continue

        crm_entry = crm_map.get(raw_name.lower())
        if not crm_entry:
            continue

        _, programme = crm_entry
        current = (row[prog_col] if prog_col < len(row) else "").strip()
        if current == programme:
            continue

        updates.append((row_idx, prog_col + 1, programme))
        matched.append(f"  {raw_name!r:35s} → {programme!r}  (was: {current!r})")

    # Report CRM athletes not found in _DATA at all
    data_names_lower = {
        (r[name_col] if name_col < len(r) else "").strip().lower()
        for r in all_values[1:]
    }
    missing_from_data = [
        f"  {display!r:35s} (coach: {prog})"
        for lower, (display, prog) in crm_map.items()
        if lower not in data_names_lower
    ]

    print(f"\n{'DRY RUN — ' if DRY_RUN else ''}Matches to update: {len(matched)}")
    for m in matched:
        print(m)

    if missing_from_data:
        print(f"\nCRM athletes not found in _DATA ({len(missing_from_data)}) — not on Fitr yet:")
        for s in missing_from_data:
            print(s)

    if not matched:
        print("Nothing to update.")
        return

    if DRY_RUN:
        print("\n[DRY RUN] No changes written.")
        return

    cells = [gspread.Cell(row=r, col=c, value=v) for r, c, v in updates]
    ws.update_cells(cells, value_input_option="USER_ENTERED")
    print(f"\n✓ Updated {len(cells)} cells in _DATA 'Programme' column.")


if __name__ == "__main__":
    main()
