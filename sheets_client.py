"""
Google Sheets reader/writer using a service account (gspread).

Writes go through the official Sheets API — no browser, no UI typing.
Share the target sheet with the service account's email (Editor) first.
"""
import gspread
from google.oauth2.service_account import Credentials

import config

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsClient:
    def __init__(self, service_account_info=None):
        if service_account_info:
            creds = Credentials.from_service_account_info(
                service_account_info, scopes=SCOPES
            )
        else:
            creds = Credentials.from_service_account_file(
                config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES
            )
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(config.SHEET_ID)

    def worksheet(self, title):
        return self.sh.worksheet(title)

    def get_or_create(self, title, headers=None):
        try:
            return self.sh.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=title, rows=200, cols=max(10, len(headers or [])))
            if headers:
                ws.update("A1", [headers])
            return ws

    def read_records(self, title):
        """Return list of dict rows (row 1 = headers)."""
        return self.worksheet(title).get_all_records()

    def read_values(self, title):
        return self.worksheet(title).get_all_values()

    def append_rows(self, title, rows):
        """Append rows to the bottom of a tab. rows = list of lists."""
        if not rows:
            return 0
        if config.DRY_RUN:
            print(f"[DRY_RUN] would append {len(rows)} rows to '{title}'")
            return len(rows)
        ws = self.worksheet(title)
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        return len(rows)

    def update_cell(self, title, a1, value):
        if config.DRY_RUN:
            print(f"[DRY_RUN] would set {title}!{a1} = {value!r}")
            return
        self.worksheet(title).update(a1, [[value]], value_input_option="USER_ENTERED")

    def overwrite_tab(self, title, rows):
        """Clear a tab and write rows from the top. Creates the tab if missing."""
        if config.DRY_RUN:
            print(f"[DRY_RUN] would overwrite '{title}' with {len(rows)} rows")
            return
        try:
            ws = self.sh.worksheet(title)
            ws.clear()
        except gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=title, rows=max(200, len(rows) + 10), cols=20)
        if rows:
            ws.update("A1", rows, value_input_option="USER_ENTERED")

    def batch_update_by_name(self, tab_title, name_col, updates_by_name):
        """Update cells across many rows identified by name_col value.

        updates_by_name: {row_name: {col_name: value}}
        Silently skips columns that don't exist in the header.
        """
        if config.DRY_RUN:
            print(f"[DRY_RUN] would update {len(updates_by_name)} rows in '{tab_title}'")
            return
        ws = self.worksheet(tab_title)
        all_values = ws.get_all_values()
        if not all_values:
            return
        header = all_values[0]
        try:
            name_idx = header.index(name_col)
        except ValueError:
            return
        cells = []
        for row_idx, row in enumerate(all_values[1:], start=2):
            cell_name = (row[name_idx] if name_idx < len(row) else "").strip()
            row_updates = updates_by_name.get(cell_name, {})
            for col_name, value in row_updates.items():
                try:
                    col_idx = header.index(col_name)
                except ValueError:
                    continue
                cells.append(gspread.Cell(
                    row=row_idx, col=col_idx + 1,
                    value=str(value) if value is not None else "",
                ))
        if cells:
            ws.update_cells(cells, value_input_option="USER_ENTERED")

    # ----------------------------------------------------------------- archetypes
    TAB_ARCHETYPES = "Archetype Assessments"
    _ARCHETYPE_HEADERS = [
        "Athlete Name", "Assessor", "Instrument", "Version",
        "Taken At", "Primary Archetype", "Profile JSON", "Raw Answers JSON", "Notes",
    ]

    def load_archetype_assessments(self):
        """Return all rows from Archetype Assessments tab, or [] if tab missing."""
        try:
            ws = self.sh.worksheet(self.TAB_ARCHETYPES)
            return ws.get_all_records()
        except gspread.WorksheetNotFound:
            return []

    def write_archetype_assessment(self, row_dict):
        """Append one assessment row. Creates the tab with headers if needed."""
        ws = self.get_or_create(self.TAB_ARCHETYPES, self._ARCHETYPE_HEADERS)
        row = [str(row_dict.get(h, "")) for h in self._ARCHETYPE_HEADERS]
        if not config.DRY_RUN:
            ws.append_rows([row], value_input_option="USER_ENTERED")

    def read_external_records(self, sheet_id, tab_title):
        """Read all records from a different Google Sheet by ID."""
        sh = self.gc.open_by_key(sheet_id)
        return sh.worksheet(tab_title).get_all_records()

    def update_cells_by_rowmap(self, title, col_letter, rowmap):
        """Set many single cells in one column. rowmap = {row_number: value}."""
        if config.DRY_RUN:
            print(f"[DRY_RUN] would update {len(rowmap)} cells in {title}!{col_letter}")
            return
        ws = self.worksheet(title)
        cells = [gspread.Cell(row=r, col=_col_to_idx(col_letter), value=v)
                 for r, v in rowmap.items()]
        if cells:
            ws.update_cells(cells, value_input_option="USER_ENTERED")


def _col_to_idx(letter):
    idx = 0
    for ch in letter.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx
