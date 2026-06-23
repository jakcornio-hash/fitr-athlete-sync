"""
Add bespoke / J+Y athletes to the Benchmarks tab (so the sync can pull their
results) and to _DATA (so coaches see them in the dashboard).

Athletes listed below were identified by cross-referencing the CRM Bespoke
Athletes sheet against Fitr chat rooms on 2026-06-23.

Run:
    python onboard_bespoke_athletes.py
    python onboard_bespoke_athletes.py --dry-run
"""

import sys
import gspread
from dotenv import load_dotenv

load_dotenv()
import config

DRY_RUN = "--dry-run" in sys.argv

# (Fitr name, Fitr user ID, Programme / coach)
# Names are exactly as they appear in Fitr chat rooms.
# Existing athletes already in the system are intentionally omitted.
BESPOKE_ATHLETES = [
    # Jamie Warr
    ("Caitlin Hickman",         166773,  "Jamie Warr"),
    ("Fergus Clark",            425194,  "Jamie Warr"),
    # Jamie Harrop
    ("Ella Henrey",             186633,  "Jamie Harrop"),
    ("Eve Larard-Tansley",      250057,  "Jamie Harrop"),
    ("Florence Wong",           137219,  "Jamie Harrop"),
    ("Lauren Field",            483019,  "Jamie Harrop"),
    ("Олександр Старанчук",     381290,  "Jamie Harrop"),
    # Dan Connolly
    ("Caitlin Lane",            46930,   "Dan Connolly"),
    ("Gareth Hughes",           2826,    "Dan Connolly"),
    ("Georgina Landy",          240231,  "Dan Connolly"),
    ("Huw Davis",               944,     "Dan Connolly"),
    ("Lucy Wilde",              4611,    "Dan Connolly"),
    ("Rob Metz",                568834,  "Dan Connolly"),
    # Denis Smith
    ("Alfie Roberts",           311223,  "Denis Smith"),
    ("Cerian Harries",          544100,  "Denis Smith"),
    ("David Wright",            2083,    "Denis Smith"),
    ("Ellis Gal",               127304,  "Denis Smith"),
    ("Eva Carroll",             241587,  "Denis Smith"),
    ("Ffion Williams",          99966,   "Denis Smith"),
    ("Jake Jamieson",           119872,  "Denis Smith"),
    ("Kayleigh Acaster",        23183,   "Denis Smith"),
    ("Tate Crossley",           279061,  "Denis Smith"),
    ("Torin Longstaff",         435313,  "Denis Smith"),
    # Ed Cook
    ("Ben Higton",              52510,   "Ed Cook"),
    ("Izzy Gowler",             416555,  "Ed Cook"),
    ("Oliver Freestone",        445453,  "Ed Cook"),
    ("Rich Wilson",             343300,  "Ed Cook"),
    ("Sophie Miller",           603963,  "Ed Cook"),
    # Jak Cornthwaite
    ("Lewin Tubuna",            581056,  "Jak Cornthwaite"),
    # Louis Towers
    ("Calum Durrand",           365301,  "Louis Towers"),
    ("Chelsea Soojeri",         244647,  "Louis Towers"),
    ("Matthew Wimmer",          199427,  "Louis Towers"),
    ("Ryan Gemmell",            303205,  "Louis Towers"),
    # Huw Davis
    ("Chris Lowden",            55308,   "Huw Davis"),
    ("Eric Gilvray",            18629,   "Huw Davis"),
    ("Gerard Queen",            39534,   "Huw Davis"),
    ("Isla Mitchell",           280883,  "Huw Davis"),
    ("Izzy DuBois",             325055,  "Huw Davis"),
    ("Leona Anderson",          132320,  "Huw Davis"),
    ("Simon Shaw",              564877,  "Huw Davis"),
    # Pete Crudge
    ("Benjamin Milner",         282241,  "Pete Crudge"),
    ("Chase Mullan",            1159,    "Pete Crudge"),
    ("Danielle Jarvis",         169750,  "Pete Crudge"),
    ("Ella-Haf Rees",           372070,  "Pete Crudge"),
    ("Harry Richards",          570678,  "Pete Crudge"),
    ("Megan Murphy",            294918,  "Pete Crudge"),
    ("Ronnie Summers",          378959,  "Pete Crudge"),
    ("Sophia Kolokotroni",      511436,  "Pete Crudge"),
    # Jamie Harrop
    ("Ben Chipperfield",        55562,   "Jamie Harrop"),
    ("Summer Wood",             389711,  "Jamie Harrop"),
    ("Sam Woodhead",            1088,    "Jamie Harrop"),
    ("Justine Kacheava",        41457,   "Jamie Harrop"),
    # Pete Crudge
    ("Lachlan Plews",           345736,  "Pete Crudge"),
    ("Seb Ellis",               387763,  "Pete Crudge"),
    # Jak Cornthwaite
    ("Jack Lydon",              372528,  "Jak Cornthwaite"),
]

# Evie Dixon, Freddie Wylde, Seren Heigh (Pete Crudge) — no longer with the programme.


def main():
    gc = gspread.service_account(filename=config.GOOGLE_SERVICE_ACCOUNT_FILE)
    sh = gc.open_by_key(config.SHEET_ID)

    # ── Load Benchmarks ────────────────────────────────────────────────────
    ws_bench = sh.worksheet(config.TAB_BENCHMARKS)
    bench_rows = ws_bench.get_all_values()
    bench_header = bench_rows[0]
    bench_name_idx = bench_header.index("Name")
    bench_fitr_idx = bench_header.index("Fitr ID")
    bench_names_lower = {r[bench_name_idx].strip().lower() for r in bench_rows[1:] if r[bench_name_idx].strip()}
    bench_fitr_ids = {r[bench_fitr_idx].strip() for r in bench_rows[1:] if r[bench_fitr_idx].strip()}

    # ── Load _DATA ─────────────────────────────────────────────────────────
    ws_data = sh.worksheet(config.TAB_DATA)
    data_rows = ws_data.get_all_values()
    data_header = data_rows[0]
    data_name_idx = data_header.index("Full Name")
    data_prog_idx = data_header.index("Programme")
    data_names_lower = {r[data_name_idx].strip().lower(): i + 2
                        for i, r in enumerate(data_rows[1:]) if r[data_name_idx].strip()}

    bench_to_add = []   # rows for Benchmarks
    data_to_add = []    # full name + programme for _DATA (new rows)
    data_to_update = [] # (row_number, programme) for existing _DATA rows

    for fitr_name, fitr_id, programme in BESPOKE_ATHLETES:
        key = fitr_name.lower()

        # Benchmarks: add if name and Fitr ID are both absent
        if key not in bench_names_lower and str(fitr_id) not in bench_fitr_ids:
            bench_to_add.append([" ", fitr_name, str(fitr_id)])  # JST ID blank

        # _DATA: update Programme if row exists but is empty/wrong; add row if missing
        if key in data_names_lower:
            row_num = data_names_lower[key]
            existing_prog = (data_rows[row_num - 1][data_prog_idx]
                             if data_prog_idx < len(data_rows[row_num - 1]) else "").strip()
            if existing_prog != programme:
                data_to_update.append((row_num, programme, fitr_name, existing_prog))
        else:
            data_to_add.append((fitr_name, programme))

    # ── Report ─────────────────────────────────────────────────────────────
    print(f"\n{'DRY RUN — ' if DRY_RUN else ''}Benchmarks: {len(bench_to_add)} to add")
    for r in bench_to_add:
        print(f"  + {r[1]!r} (Fitr ID {r[2]})")

    print(f"\n_DATA: {len(data_to_add)} new rows to add")
    for name, prog in data_to_add:
        print(f"  + {name!r} → {prog!r}")

    print(f"\n_DATA: {len(data_to_update)} Programme fields to update")
    for row_num, prog, name, old in data_to_update:
        print(f"  row {row_num} {name!r}: {old!r} → {prog!r}")

    if DRY_RUN:
        print("\n[DRY RUN] No changes written.")
        return

    # ── Write Benchmarks ───────────────────────────────────────────────────
    if bench_to_add:
        ws_bench.append_rows(bench_to_add, value_input_option="USER_ENTERED")
        print(f"\n✓ Added {len(bench_to_add)} rows to Benchmarks.")

    # ── Update _DATA Programme for existing rows ───────────────────────────
    if data_to_update:
        prog_col_letter = _col_letter(data_prog_idx + 1)
        cells = [gspread.Cell(row=rn, col=data_prog_idx + 1, value=prog)
                 for rn, prog, _, _ in data_to_update]
        ws_data.update_cells(cells, value_input_option="USER_ENTERED")
        print(f"✓ Updated Programme for {len(data_to_update)} existing _DATA rows.")

    # ── Add new _DATA rows ─────────────────────────────────────────────────
    if data_to_add:
        empty_row = [""] * len(data_header)
        new_rows = []
        for name, prog in data_to_add:
            row = list(empty_row)
            row[data_name_idx] = name
            row[data_prog_idx] = prog
            new_rows.append(row)
        ws_data.append_rows(new_rows, value_input_option="USER_ENTERED")
        print(f"✓ Added {len(data_to_add)} new rows to _DATA.")


def _col_letter(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


if __name__ == "__main__":
    main()
