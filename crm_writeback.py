"""Keep the CRM Master Sheet's activity columns true to what Fitr actually says.

Ed's complaint was that he could no longer tell what in the CRM updates and
what doesn't. The answer was almost nothing: `Last Score Logged`, `Days Since
Last Log` and `Zone (G/Y/R)` held 1,189 hand-typed values and no formulas, and
every tab that looks automatic — Slipping Away, Resurrection Priority, the MAU
and retention dashboards — is a formula reading those three columns. So the
whole retention view was recalculating perfectly off numbers somebody last
typed by hand months ago.

Measured against the live PR Log on 2026-09-10, only 184 of 410 athletes were
within two days of the truth. Alex Richards-Taylor sat in the Red Zone at 182
days having logged nine days earlier.

This writes the last-log date from the PR Log, and puts formulas in the other
two so they keep counting on their own between syncs rather than freezing
again the moment this stops running.

Deliberately narrow. It touches three columns and nothing else: no Status, no
Notes, no Coach, no rows added, reordered or removed. Those are a coach's
judgement and typing over them is how you lose trust in a sheet. Rows it
cannot match to a real athlete are left exactly as they are.
"""

import datetime as dt
import re

import analytics
import config

TAB = "Master Sheet (Athlete list)"

# Green to Yellow at 8 days, Yellow to Red at 15. Not invented here: this is
# the rule already implied by the 492 rows a coach had zoned by hand — Green
# topped out at 7, Yellow ran 8 to 14, Red started at 15.
YELLOW_AT = 8
RED_AT = 15

_COLS = ("Last Score Logged", "Days Since Last Log", "Zone (G/Y/R)")


def _col_letter(idx):
    """0-based column index to a spreadsheet letter (A, B, ... AA)."""
    out = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def _cell(row, i):
    """One cell as a stripped string, tolerating short rows."""
    return str(row[i]).strip() if i < len(row) else ""


def last_log_by_athlete(pr_records):
    """{normalised name: 'YYYY-MM-DD'} — the most recent thing each one logged."""
    out = {}
    for r in pr_records or []:
        nm = analytics.normalise_client_name(r.get("Athlete Name", ""))
        d = str(r.get("Date", "")).strip()[:10]
        if nm and re.fullmatch(r"20\d\d-\d\d-\d\d", d):
            if nm not in out or d > out[nm]:
                out[nm] = d
    return out


def refresh_master_sheet(sheets, pr_records, today=None):
    """Write real activity figures into the CRM Master Sheet.

    Returns a short summary dict for the sync log. Never raises: a CRM that is
    unreachable must not take the nightly sync down with it.
    """
    today = today or dt.date.today()
    if not getattr(config, "CRM_SHEET_ID", ""):
        return {"skipped": "no CRM_SHEET_ID"}

    last_log = last_log_by_athlete(pr_records)
    if not last_log:
        return {"skipped": "no PR Log rows to work from"}

    try:
        ws = sheets.gc.open_by_key(config.CRM_SHEET_ID).worksheet(TAB)
        # Read formulas, not their results. Any cell this job leaves alone gets
        # written back exactly as it was found, and a cell holding a formula
        # must go back as that formula rather than as the number it happened to
        # show tonight.
        vals = ws.get_all_values(value_render_option="FORMULA")
    except Exception as exc:
        return {"skipped": f"could not open the CRM: {exc}"}
    if not vals:
        return {"skipped": "Master Sheet is empty"}

    head = [str(h).strip() for h in vals[0]]
    try:
        i_name = head.index("Athlete Name")
        i_email = head.index("Email")
        i_date, i_days, i_zone = (head.index(c) for c in _COLS)
    except ValueError as exc:
        return {"skipped": f"Master Sheet is missing a column: {exc}"}

    # Email is the fallback because the CRM spells some names differently from
    # Fitr, and a near miss on a name is worse than no match at all.
    by_email = {}
    for r in pr_records or []:
        em = str(r.get("Email", "")).strip().lower()
        nm = analytics.normalise_client_name(r.get("Athlete Name", ""))
        if em and nm in last_log:
            by_email.setdefault(em, nm)

    body = vals[1:]
    d_col, j_col, k_col = (_col_letter(i) for i in (i_date, i_days, i_zone))
    # Start from what is already there, so every row this job does not touch is
    # rewritten byte for byte.
    col_date = [[_cell(r, i_date)] for r in body]
    col_days = [[_cell(r, i_days)] for r in body]
    col_zone = [[_cell(r, i_zone)] for r in body]

    matched = unmatched = 0
    for idx, row in enumerate(body):
        name = _cell(row, i_name)
        if not name:
            continue
        nm = analytics.normalise_client_name(name)
        if nm not in last_log:
            nm = by_email.get(_cell(row, i_email).lower(), "")
        if nm not in last_log:
            unmatched += 1
            continue
        matched += 1
        sheet_row = idx + 2
        col_date[idx] = [last_log[nm]]
        # Days and Zone go in as formulas on purpose. A number written tonight
        # is wrong by tomorrow lunchtime, and if this job ever stops running the
        # sheet freezes silently all over again — which is the exact failure
        # being fixed. A formula keeps counting on its own.
        col_days[idx] = [f'=IF({d_col}{sheet_row}="","",TODAY()-{d_col}{sheet_row})']
        days_ref = f"{j_col}{sheet_row}"
        col_zone[idx] = [
            f'=IF({days_ref}="","",'
            f'IF({days_ref}>={RED_AT},"Red",'
            f'IF({days_ref}>={YELLOW_AT},"Yellow","Green")))'
        ]

    if config.DRY_RUN:
        return {"dry_run": True, "would_update": matched, "unmatched": unmatched}
    if matched:
        last = len(body) + 1
        # Three whole-column writes rather than three per athlete: the same work
        # in one request instead of twelve hundred.
        ws.batch_update([
            {"range": f"{d_col}2:{d_col}{last}", "values": col_date},
            {"range": f"{j_col}2:{j_col}{last}", "values": col_days},
            {"range": f"{k_col}2:{k_col}{last}", "values": col_zone},
        ], value_input_option="USER_ENTERED")
    return {"updated": matched, "unmatched": unmatched}
