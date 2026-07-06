"""
Google Sheets reader/writer using a service account (gspread).

Writes go through the official Sheets API — no browser, no UI typing.
Share the target sheet with the service account's email (Editor) first.
"""
import gspread

import config


class SheetsClient:
    def __init__(self, service_account_info=None, sheet_id=None):
        if service_account_info:
            # Ensure private_key has real newlines (Streamlit secrets may escape them)
            info = dict(service_account_info)
            if "private_key" in info:
                info["private_key"] = info["private_key"].replace("\\n", "\n")
            self.gc = gspread.service_account_from_dict(info)
        else:
            self.gc = gspread.service_account(filename=config.GOOGLE_SERVICE_ACCOUNT_FILE)
        sid = sheet_id or config.SHEET_ID
        try:
            self.sh = self.gc.open_by_key(sid)
        except gspread.exceptions.APIError as e:
            raise RuntimeError(
                f"gspread open_by_key failed for sheet_id={sid!r}. "
                f"Response status: {getattr(e.response, 'status_code', '?')}. "
                f"Body snippet: {str(e)[:300]}"
            ) from e
        # Every gspread `Spreadsheet.worksheet(title)` call issues a fresh
        # fetch_sheet_metadata() request (a full read of every tab's metadata).
        # With ~20 tabs and dozens of call sites — several run per-athlete —
        # a single sync run can blow through Sheets API's per-minute read quota.
        # Cache resolved Worksheet objects for the lifetime of this client so
        # repeated lookups of the same tab cost one API call, not one per call site.
        self._ws_cache = {}

    def worksheet(self, title):
        if title not in self._ws_cache:
            self._ws_cache[title] = self.sh.worksheet(title)
        return self._ws_cache[title]

    def get_or_create(self, title, headers=None):
        try:
            return self.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=title, rows=200, cols=max(10, len(headers or [])))
            if headers:
                ws.update("A1", [headers])
            self._ws_cache[title] = ws
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
            ws = self.worksheet(title)
            ws.clear()
        except gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=title, rows=max(200, len(rows) + 10), cols=20)
            self._ws_cache[title] = ws
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
            ws = self.worksheet(self.TAB_ARCHETYPES)
            return ws.get_all_records()
        except gspread.WorksheetNotFound:
            return []

    def write_archetype_assessment(self, row_dict):
        """Append one assessment row. Creates the tab with headers if needed."""
        ws = self.get_or_create(self.TAB_ARCHETYPES, self._ARCHETYPE_HEADERS)
        row = [str(row_dict.get(h, "")) for h in self._ARCHETYPE_HEADERS]
        if not config.DRY_RUN:
            ws.append_rows([row], value_input_option="USER_ENTERED")

    # ---------------------------------------------------------- competitions
    TAB_COMPETITIONS = config.TAB_COMPETITIONS
    _COMP_HEADERS = ["Athlete Name", "Competition Name", "Date", "Type", "Notes", "Synced At", "Result", "Post-comp Response"]

    def load_competitions(self):
        """Return all rows from Competitions tab, or [] if not yet created."""
        try:
            ws = self.worksheet(self.TAB_COMPETITIONS)
            return ws.get_all_records()
        except gspread.WorksheetNotFound:
            return []

    def save_competition(self, row_dict):
        """Append one competition row. Creates tab with headers if needed."""
        ws = self.get_or_create(self.TAB_COMPETITIONS, self._COMP_HEADERS)
        row = [str(row_dict.get(h, "")) for h in self._COMP_HEADERS]
        if not config.DRY_RUN:
            ws.append_rows([row], value_input_option="USER_ENTERED")

    def update_competition_result(self, athlete_name, comp_name, result_text):
        """Write a result string to the first matching competition row.

        Matches on (athlete_name, comp_name) case-insensitively.
        Returns True if a row was found and updated.
        """
        if config.DRY_RUN:
            print(f"[DRY_RUN] would update result for {athlete_name} — {comp_name}: {result_text!r}")
            return True
        try:
            ws = self.worksheet(self.TAB_COMPETITIONS)
        except gspread.WorksheetNotFound:
            return False
        values = ws.get_all_values()
        if not values:
            return False
        header = values[0]
        try:
            name_idx = header.index("Athlete Name")
            comp_idx = header.index("Competition Name")
            result_idx = header.index("Result")
        except ValueError:
            return False
        for i, row in enumerate(values[1:], start=2):
            row_name = (row[name_idx] if name_idx < len(row) else "").strip().lower()
            row_comp = (row[comp_idx] if comp_idx < len(row) else "").strip().lower()
            if row_name == athlete_name.strip().lower() and row_comp == comp_name.strip().lower():
                ws.update_cell(i, result_idx + 1, result_text)
                return True
        return False

    # ------------------------------------------------------------ coaches / Slack
    def load_coaches(self):
        """Return {programme: slack_channel} from the Coaches tab, or {} if missing.

        Expected columns: Programme | Slack Channel | Active
        Set Active to FALSE/NO/0/OFF to silence notifications for that coach.
        """
        try:
            rows = self.worksheet(config.TAB_COACHES).get_all_records()
            return {
                str(r.get("Programme", "")).strip(): str(r.get("Slack Channel", "")).strip()
                for r in rows
                if str(r.get("Programme", "")).strip()
                and str(r.get("Slack Channel", "")).strip()
                and str(r.get("Active", "TRUE")).strip().upper() not in ("FALSE", "NO", "0", "OFF")
            }
        except gspread.WorksheetNotFound:
            return {}

    def load_coach_names(self):
        """Return {programme: coach_name} from the Coaches tab 'Coach Name' column.

        Returns {} if the column doesn't exist or the tab is missing — coach filter
        simply won't appear in the dashboard until the column is populated.
        """
        try:
            rows = self.worksheet(config.TAB_COACHES).get_all_records()
            return {
                str(r.get("Programme", "")).strip(): str(r.get("Coach Name", "")).strip()
                for r in rows
                if str(r.get("Programme", "")).strip() and str(r.get("Coach Name", "")).strip()
            }
        except Exception:
            return {}

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

    # ---------------------------------------------------------- churn history
    _CHURN_HEADERS = ["Date", "Athlete Name", "Score", "Label", "Factors"]

    def write_churn_snapshot(self, rows):
        """Append one row per athlete to the Churn History tab.

        rows: [{"Date": str, "Athlete Name": str, "Score": int,
                "Label": str, "Factors": str}, ...]
        """
        ws = self.get_or_create(config.TAB_CHURN_HISTORY, self._CHURN_HEADERS)
        data = [[str(r.get(h, "")) for h in self._CHURN_HEADERS] for r in rows]
        if data and not config.DRY_RUN:
            ws.append_rows(data, value_input_option="USER_ENTERED")

    def load_churn_history(self):
        """Return all rows from Churn History tab, or [] if tab missing."""
        try:
            return self.worksheet(config.TAB_CHURN_HISTORY).get_all_records()
        except Exception:
            return []

    # ---------------------------------------------------------- message log
    _MSG_LOG_HEADERS = ["Date", "Athlete Name", "Message Type", "Room ID", "Replied", "Reply Date"]

    def log_messages(self, rows):
        """Append sent-message records to the Message Log tab.

        rows: [{"Date": str, "Athlete Name": str, "Message Type": str,
                "Room ID": str}, ...]
        Replied and Reply Date are left blank — updated later by mark_message_replied().
        """
        ws = self.get_or_create(config.TAB_MESSAGE_LOG, self._MSG_LOG_HEADERS)
        data = [
            [str(r.get("Date", "")), str(r.get("Athlete Name", "")),
             str(r.get("Message Type", "")), str(r.get("Room ID", "")), "", ""]
            for r in rows
        ]
        if data and not config.DRY_RUN:
            ws.append_rows(data, value_input_option="USER_ENTERED")

    def load_pending_messages(self, max_age_days=4):
        """Return message log rows where Replied is blank and Date is within max_age_days."""
        import datetime as dt
        today = dt.date.today()
        try:
            rows = self.worksheet(config.TAB_MESSAGE_LOG).get_all_records()
        except Exception:
            return []
        pending = []
        for r in rows:
            if str(r.get("Replied", "")).strip():
                continue
            raw = str(r.get("Date", "")).strip()
            for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
                try:
                    d = dt.datetime.strptime(raw, fmt).date()
                    if (today - d).days <= max_age_days:
                        pending.append(r)
                    break
                except ValueError:
                    continue
        return pending

    def mark_message_replied(self, athlete_name, message_type, sent_date_str, reply_date_str):
        """Set Replied=Yes and Reply Date on the first matching un-replied row."""
        if config.DRY_RUN:
            return
        try:
            ws = self.worksheet(config.TAB_MESSAGE_LOG)
        except Exception:
            return
        values = ws.get_all_values()
        if not values:
            return
        header = values[0]
        try:
            name_i = header.index("Athlete Name")
            type_i = header.index("Message Type")
            date_i = header.index("Date")
            replied_i = header.index("Replied")
            reply_date_i = header.index("Reply Date")
        except ValueError:
            return
        for row_num, row in enumerate(values[1:], start=2):
            if (len(row) > max(name_i, type_i, date_i, replied_i) and
                    row[name_i].strip() == athlete_name.strip() and
                    row[type_i].strip() == message_type.strip() and
                    row[date_i].strip() == sent_date_str.strip() and
                    not row[replied_i].strip()):
                ws.update_cell(row_num, replied_i + 1, "Yes")
                ws.update_cell(row_num, reply_date_i + 1, reply_date_str)
                return

    # --------------------------------------------------------- draft replies
    _DRAFT_HEADERS = ["Date", "Athlete Name", "Room ID", "Draft Reply", "Cleared"]

    def write_draft_reply(self, athlete_name, room_id, draft_text):
        """Upsert a draft reply for an athlete. Replaces any existing non-cleared row."""
        if config.DRY_RUN:
            return
        ws = self.get_or_create(config.TAB_DRAFT_REPLIES, self._DRAFT_HEADERS)
        import datetime as _dt
        today = _dt.date.today().isoformat()
        # Check if row already exists for this athlete
        try:
            values = ws.get_all_values()
            header = values[0] if values else self._DRAFT_HEADERS
            name_i = header.index("Athlete Name")
            cleared_i = header.index("Cleared")
            for row_num, row in enumerate(values[1:], start=2):
                if (len(row) > name_i and row[name_i].strip() == athlete_name.strip()
                        and (len(row) <= cleared_i or not row[cleared_i].strip())):
                    # Update existing row
                    draft_i = header.index("Draft Reply")
                    date_i = header.index("Date")
                    ws.update_cell(row_num, date_i + 1, today)
                    ws.update_cell(row_num, draft_i + 1, draft_text)
                    return
        except Exception:
            pass
        # Append new row
        ws.append_row([today, athlete_name, str(room_id), draft_text, ""],
                      value_input_option="USER_ENTERED")

    def load_draft_replies(self):
        """Return all non-cleared draft reply rows."""
        try:
            rows = self.worksheet(config.TAB_DRAFT_REPLIES).get_all_records()
            return [r for r in rows if not str(r.get("Cleared", "")).strip()]
        except Exception:
            return []

    def clear_draft_reply(self, athlete_name):
        """Mark draft reply as cleared (coach has actioned it)."""
        if config.DRY_RUN:
            return
        try:
            ws = self.worksheet(config.TAB_DRAFT_REPLIES)
            values = ws.get_all_values()
            if not values:
                return
            header = values[0]
            name_i = header.index("Athlete Name")
            cleared_i = header.index("Cleared")
            for row_num, row in enumerate(values[1:], start=2):
                if (len(row) > name_i and row[name_i].strip() == athlete_name.strip()
                        and (len(row) <= cleared_i or not row[cleared_i].strip())):
                    ws.update_cell(row_num, cleared_i + 1, "Yes")
                    return
        except Exception:
            pass

    # ------------------------------------------------------- training load
    _TRAINING_LOAD_HEADERS = ["Date", "Athlete Name", "Week", "Sessions"]

    def write_training_load(self, rows):
        """Append training load snapshot rows to the Training Load tab.

        rows: [{"Date": str, "Athlete Name": str, "Week": str, "Sessions": int}]
        """
        ws = self.get_or_create(config.TAB_TRAINING_LOAD, self._TRAINING_LOAD_HEADERS)
        data = [[str(r.get("Date", "")), str(r.get("Athlete Name", "")),
                 str(r.get("Week", "")), str(r.get("Sessions", 0))] for r in rows]
        if data and not config.DRY_RUN:
            ws.append_rows(data, value_input_option="USER_ENTERED")

    def load_training_load(self):
        """Return all rows from Training Load tab, or [] if tab missing."""
        try:
            return self.worksheet(config.TAB_TRAINING_LOAD).get_all_records()
        except Exception:
            return []

    # --------------------------------------------------------- referrals
    _REFERRAL_HEADERS = [
        "Date", "Referrer Name", "Referred Name", "Referred Email", "Notes", "Status",
        "Trial End", "Join Ack Sent", "Convert Ack Sent",
    ]

    def load_referrals(self):
        try:
            ws = self.get_or_create(config.TAB_REFERRALS, self._REFERRAL_HEADERS)
            return ws.get_all_records()
        except Exception as e:
            print(f"  ! referrals load failed: {e}")
            return []

    def ensure_referral_columns(self):
        """Add any columns from _REFERRAL_HEADERS missing from the live tab header row."""
        try:
            ws = self.get_or_create(config.TAB_REFERRALS, self._REFERRAL_HEADERS)
            current = ws.row_values(1)
            new_cols = [h for h in self._REFERRAL_HEADERS if h not in current]
            if not new_cols:
                return
            start_col = len(current) + 1
            for i, h in enumerate(new_cols):
                ws.update_cell(1, start_col + i, h)
        except Exception as e:
            print(f"  ! ensure_referral_columns failed: {e}")

    def update_referral_ack(self, row_number, updates):
        """Update specific fields in a referral row. updates = {column_name: value}."""
        try:
            ws = self.get_or_create(config.TAB_REFERRALS, self._REFERRAL_HEADERS)
            headers = ws.row_values(1)
            cells = [
                gspread.Cell(row_number, headers.index(col) + 1, val)
                for col, val in updates.items()
                if col in headers
            ]
            if cells:
                ws.update_cells(cells)
        except Exception as e:
            print(f"  ! update_referral_ack failed (row {row_number}): {e}")

    def add_referral(self, date, referrer, referred_name, referred_email, notes="", trial_end=""):
        ws = self.get_or_create(config.TAB_REFERRALS, self._REFERRAL_HEADERS)
        ws.append_rows(
            [[date, referrer, referred_name, referred_email, notes, "Pending", trial_end, "", ""]],
            value_input_option="USER_ENTERED",
        )

    def update_referral_status(self, row_number, status):
        """row_number is 1-indexed (row 1 = header, data starts at 2)."""
        ws = self.get_or_create(config.TAB_REFERRALS, self._REFERRAL_HEADERS)
        status_col = self._REFERRAL_HEADERS.index("Status") + 1
        ws.update_cell(row_number, status_col, status)

    # --------------------------------------------------------- summit tickets
    _SUMMIT_HEADERS = [
        "Date Promised", "Athlete Name", "Event", "Ticket Sent", "Date Sent", "Notes",
    ]

    def load_summit_tickets(self):
        try:
            ws = self.get_or_create(config.TAB_SUMMIT_TICKETS, self._SUMMIT_HEADERS)
            return ws.get_all_records()
        except Exception as e:
            print(f"  ! summit tickets load failed: {e}")
            return []

    def add_summit_ticket(self, date_promised, athlete_name, event="", notes=""):
        ws = self.get_or_create(config.TAB_SUMMIT_TICKETS, self._SUMMIT_HEADERS)
        ws.append_rows(
            [[date_promised, athlete_name, event, "No", "", notes]],
            value_input_option="USER_ENTERED",
        )

    def mark_summit_ticket_sent(self, row_number, date_sent, event=""):
        ws = self.get_or_create(config.TAB_SUMMIT_TICKETS, self._SUMMIT_HEADERS)
        sent_col   = self._SUMMIT_HEADERS.index("Ticket Sent") + 1
        date_col   = self._SUMMIT_HEADERS.index("Date Sent") + 1
        event_col  = self._SUMMIT_HEADERS.index("Event") + 1
        ws.update_cell(row_number, sent_col, "Yes")
        ws.update_cell(row_number, date_col, date_sent)
        if event:
            ws.update_cell(row_number, event_col, event)

    # ─────────────────────────── Gym Owner Referral Programme ──────────────────

    _GYM_DIRECTORY_HEADERS = [
        "Gym Name", "Gym Code", "Owner Name", "Owner Email",
        "Tier", "Monthly Fee", "Coach", "Notes",
    ]
    _GYM_REFERRAL_HEADERS = [
        "Referral ID", "Referring Gym", "Gym Code", "Referred Member",
        "Product", "Monthly Sub", "Credit Rate", "Monthly Credit",
        "Sign-Up Date", "Credit Start", "Credit End", "Status", "Notes",
    ]

    def load_gym_directory(self):
        """Return all rows from Gym Directory tab, creating the tab with headers if absent."""
        ws = self.get_or_create(config.TAB_GYM_DIRECTORY, self._GYM_DIRECTORY_HEADERS)
        return ws.get_all_records()

    def load_gym_referrals(self):
        """Return all rows from Gym Referrals tab, creating the tab with headers if absent."""
        ws = self.get_or_create(config.TAB_GYM_REFERRALS, self._GYM_REFERRAL_HEADERS)
        return ws.get_all_records()

    def add_gym_referral(self, referral_id, gym_name, gym_code, referred_member,
                         product, monthly_sub, credit_rate, monthly_credit,
                         sign_up_date, credit_start, credit_end="",
                         status="Active", notes=""):
        if config.DRY_RUN:
            print(f"[DRY_RUN] would add gym referral {referral_id} for {referred_member}")
            return
        ws = self.get_or_create(config.TAB_GYM_REFERRALS, self._GYM_REFERRAL_HEADERS)
        ws.append_rows(
            [[referral_id, gym_name, gym_code, referred_member,
              product, monthly_sub, credit_rate, monthly_credit,
              sign_up_date, credit_start, credit_end, status, notes]],
            value_input_option="USER_ENTERED",
        )

    def update_gym_referral_status(self, referral_id, new_status, notes=""):
        """Find a referral row by ID and update its Status (and optionally Notes)."""
        if config.DRY_RUN:
            print(f"[DRY_RUN] would update gym referral {referral_id} → {new_status}")
            return
        ws = self.get_or_create(config.TAB_GYM_REFERRALS, self._GYM_REFERRAL_HEADERS)
        all_vals = ws.get_all_values()
        if len(all_vals) < 2:
            return
        headers = all_vals[0]
        try:
            id_col     = headers.index("Referral ID")
            status_col = headers.index("Status")
            notes_col  = headers.index("Notes")
        except ValueError:
            return
        for row_idx, row in enumerate(all_vals[1:], start=2):
            if len(row) > id_col and row[id_col] == referral_id:
                ws.update_cell(row_idx, status_col + 1, new_status)
                if notes:
                    existing = row[notes_col] if notes_col < len(row) else ""
                    combined = f"{existing}; {notes}" if existing else notes
                    ws.update_cell(row_idx, notes_col + 1, combined)
                return

    # --------------------------------------------------------- intake form
    def load_intake_responses(self):
        """Read athlete intake Typeform responses from an external sheet."""
        if not config.INTAKE_FORM_SHEET_ID:
            return []
        try:
            return self.read_external_records(config.INTAKE_FORM_SHEET_ID,
                                              config.INTAKE_FORM_TAB)
        except Exception as e:
            print(f"  ! intake form read failed: {e}")
            return []


def _col_to_idx(letter):
    idx = 0
    for ch in letter.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx
