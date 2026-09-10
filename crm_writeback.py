"""Keep the CRM Master Sheet true to what the system actually knows.

Ed's complaint was that he could no longer tell what in the CRM updates and
what doesn't. The answer was almost nothing: `Last Score Logged`, `Days Since
Last Log` and `Zone (G/Y/R)` held 1,189 hand-typed values and no formulas, and
every tab that looks automatic — Slipping Away, Resurrection Priority, the MAU
and retention dashboards — is a formula reading those three columns. So the
whole retention view was recalculating perfectly off numbers somebody last
typed by hand months ago. Measured against the live PR Log on 2026-09-10, only
184 of 410 athletes were within two days of the truth.

Jak wants the CRM kept as a backup and as the place to pull a batch of emails
from, so this also fills what a backup needs: Email (552 of 636 names had
none), Coach, Last Contacted, Time Served and the onboarding due dates.

Two rules, because a sheet the system types over carelessly stops being
trusted:

  * Facts only. Status, Notes, Slipping Reason, Going Forward, the ghost-hunt
    columns and the Day 3/14/30 *Done?* ticks are a coach's judgement. They are
    never touched. Nor is the Leaderboard tab, which is a hand-curated
    competition board rather than a feed.
  * Never blank or overwrite something a person typed unless what replaces it
    is strictly better. Email and Coach fill gaps only. Dates and counters are
    replaced with formulas that keep themselves current, so if this job ever
    stops running the sheet ages correctly instead of freezing silently again.

No rows are added, reordered or removed. Rows that cannot be matched to a real
athlete are rewritten byte for byte.
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

# Every column this job is allowed to write. Anything not listed is untouchable.
MANAGED = (
    "Email", "Time Served", "Last Score Logged", "Days Since Last Log",
    "Zone (G/Y/R)", "Coach", "Day 3 Due Date", "Day 14 Due Date",
    "Day 30 Due Date", "Last Contacted",
)


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


def _norm(s):
    return analytics.normalise_client_name(s)


def last_log_by_athlete(pr_records):
    """{normalised name: 'YYYY-MM-DD'} — the most recent thing each one logged."""
    out = {}
    for r in pr_records or []:
        nm = _norm(r.get("Athlete Name", ""))
        d = str(r.get("Date", "")).strip()[:10]
        if nm and re.fullmatch(r"20\d\d-\d\d-\d\d", d):
            if nm not in out or d > out[nm]:
                out[nm] = d
    return out


def _gather(sheets, pr_records, data_records):
    """Everything the system knows per athlete, keyed by normalised name."""
    last_log = last_log_by_athlete(pr_records)

    email, programme = {}, {}
    for r in data_records or []:
        nm = _norm(r.get("Full Name", ""))
        if not nm:
            continue
        em = str(r.get("Email", "")).strip()
        if em:
            email.setdefault(nm, em)
        pg = str(r.get("Programme", "")).strip()
        if pg:
            programme.setdefault(nm, pg)
    for r in pr_records or []:
        nm, em = _norm(r.get("Athlete Name", "")), str(r.get("Email", "")).strip()
        if nm and em:
            email.setdefault(nm, em)

    # The coach map only names the bespoke coaches, so this fills a minority
    # of rows. Better a true blank than a guessed name.
    try:
        coach_map = sheets.load_coach_names() or {}
    except Exception:
        coach_map = {}
    coach = {nm: coach_map[pg] for nm, pg in programme.items() if pg in coach_map}

    # Last Contacted comes from the Message Log: the record of what an athlete
    # was actually sent, from either the sync or a coach marking a draft sent.
    contacted = {}
    try:
        for r in sheets.read_records(config.TAB_MESSAGE_LOG):
            nm = _norm(r.get("Athlete Name", ""))
            d = str(r.get("Date", "")).strip()[:10]
            if nm and re.fullmatch(r"20\d\d-\d\d-\d\d", d):
                if nm not in contacted or d > contacted[nm]:
                    contacted[nm] = d
    except Exception:
        pass

    # Email is the fallback key: the CRM spells some names differently from
    # Fitr, and a near miss on a name is worse than no match at all.
    by_email = {em.lower(): nm for nm, em in email.items()}
    return last_log, email, coach, contacted, by_email


def refresh_master_sheet(sheets, pr_records, data_records=None, today=None):
    """Write what the system knows into the CRM Master Sheet.

    Returns a summary dict for the sync log. Never raises: a CRM that is
    unreachable must not take the nightly sync down with it.
    """
    if not getattr(config, "CRM_SHEET_ID", ""):
        return {"skipped": "no CRM_SHEET_ID"}
    last_log, email, coach, contacted, by_email = _gather(sheets, pr_records, data_records)
    if not last_log:
        return {"skipped": "no PR Log rows to work from"}

    try:
        ws = sheets.gc.open_by_key(config.CRM_SHEET_ID).worksheet(TAB)
        # Read formulas, not their results. Any cell this job leaves alone gets
        # written back exactly as it was found, and a cell holding a formula
        # must go back as that formula rather than as tonight's value.
        vals = ws.get_all_values(value_render_option="FORMULA")
    except Exception as exc:
        return {"skipped": f"could not open the CRM: {exc}"}
    if not vals:
        return {"skipped": "Master Sheet is empty"}

    head = [str(h).strip() for h in vals[0]]
    try:
        ix = {c: head.index(c) for c in MANAGED + ("Athlete Name", "Join Date")}
    except ValueError as exc:
        return {"skipped": f"Master Sheet is missing a column: {exc}"}
    L = {c: _col_letter(i) for c, i in ix.items()}

    body = vals[1:]
    # Start from what is already there, so every cell not deliberately changed
    # is rewritten byte for byte.
    cols = {c: [[_cell(r, ix[c])] for r in body] for c in MANAGED}

    n = {"activity": 0, "email": 0, "coach": 0, "contacted": 0, "dates": 0, "unmatched": 0}
    for idx, row in enumerate(body):
        name = _cell(row, ix["Athlete Name"])
        if not name:
            continue
        rn = idx + 2
        nm = _norm(name)
        if nm not in last_log and nm not in email:
            nm = by_email.get(_cell(row, ix["Email"]).lower(), nm)

        # ── Activity: the three columns the retention tabs are built on ──
        if nm in last_log:
            n["activity"] += 1
            d = L["Last Score Logged"]
            cols["Last Score Logged"][idx] = [last_log[nm]]
            cols["Days Since Last Log"][idx] = [f'=IF({d}{rn}="","",TODAY()-{d}{rn})']
            j = f'{L["Days Since Last Log"]}{rn}'
            cols["Zone (G/Y/R)"][idx] = [
                f'=IF({j}="","",IF({j}>={RED_AT},"Red",IF({j}>={YELLOW_AT},"Yellow","Green")))']
        else:
            n["unmatched"] += 1

        # ── Gaps only: never type over an address or a name a coach entered ──
        if not _cell(row, ix["Email"]) and nm in email:
            cols["Email"][idx] = [email[nm]]
            n["email"] += 1
        if not _cell(row, ix["Coach"]) and nm in coach:
            cols["Coach"][idx] = [coach[nm]]
            n["coach"] += 1

        if nm in contacted:
            cols["Last Contacted"][idx] = [contacted[nm]]
            n["contacted"] += 1

        # ── Derived from Join Date, only where it is a real date ──
        # 55 rows hold the join date as text; a formula on those would show
        # an error where a typed value used to be, so they keep what they have.
        jd_raw = row[ix["Join Date"]] if ix["Join Date"] < len(row) else ""
        if isinstance(jd_raw, (int, float)) and not isinstance(jd_raw, bool):
            f = f'{L["Join Date"]}{rn}'
            n["dates"] += 1
            # Same "6y 5m 18d" shape the sheet already uses.
            cols["Time Served"][idx] = [
                f'=IFERROR(DATEDIF({f},TODAY(),"Y")&"y "&DATEDIF({f},TODAY(),"YM")'
                f'&"m "&DATEDIF({f},TODAY(),"MD")&"d","")']
            for days, c in ((3, "Day 3 Due Date"), (14, "Day 14 Due Date"), (30, "Day 30 Due Date")):
                cols[c][idx] = [f'=IF({f}="","",{f}+{days})']

    if config.DRY_RUN:
        return {"dry_run": True, **n}

    last = len(body) + 1
    try:
        # One request for every managed column, rather than one per cell.
        ws.batch_update(
            [{"range": f"{L[c]}2:{L[c]}{last}", "values": cols[c]} for c in MANAGED],
            value_input_option="USER_ENTERED")
        # Due dates would otherwise display as serial numbers; days as 9.00.
        for c in ("Day 3 Due Date", "Day 14 Due Date", "Day 30 Due Date", "Last Contacted"):
            ws.format(f"{L[c]}2:{L[c]}", {"numberFormat": {"type": "DATE", "pattern": "d mmm yyyy"}})
        ws.format(f'{L["Days Since Last Log"]}2:{L["Days Since Last Log"]}',
                  {"numberFormat": {"type": "NUMBER", "pattern": "0"}})
    except Exception as exc:
        return {"failed": str(exc), **n}
    return n
