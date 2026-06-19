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
