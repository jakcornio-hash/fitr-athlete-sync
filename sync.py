#!/usr/bin/env python3
"""
Weekly Fitr -> Google Sheet sync for the JST Compete athlete database.

What it does each run:
  1. Reads the Benchmarks tab to map athletes -> Fitr IDs, and the PR Log
     for dedupe.
  2. Pulls each athlete's benchmark results; appends ones logged in the last
     LOOKBACK_DAYS to PR Log (with previous value + the athlete's note).
  3. Pulls recent challenge leaderboards; appends new scores (Type=Challenge).
  4. Sweeps the Fitr inbox; AI-summarises recent conversations and appends a
     dated line to each athlete's Coaching Notes in _DATA (append-only).
  5. Merges the latest recovery-survey response per athlete (if a Recovery tab
     exists).
  6. Updates 'Last Scraped' and writes a Sync Log row.

Run:  python sync.py            (live)
      DRY_RUN=1 python sync.py  (pull + print, write nothing)
"""
import datetime as dt
import sys

import config
from fitr_client import FitrClient, FitrError, format_thread, profiles_from_rooms
from sheets_client import SheetsClient
import analytics
import notifier
import summariser
import recovery

TODAY = dt.date.today()
CUTOFF = TODAY - dt.timedelta(days=config.LOOKBACK_DAYS)


def _parse_date(s):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%d %b, %Y"):
        try:
            return dt.datetime.strptime(str(s).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _fmt_value(v):
    """Pretty value+symbol from a Fitr last_value dict."""
    val = v.get("value")
    sym = v.get("symbol") or v.get("units") or ""
    if val is None:
        return ""
    # Fitr stores some measures in base units (e.g. grams); value already
    # comes back human-scaled in last_value, so just join with the symbol.
    return f"{val} {sym}".strip()


# ----------------------------------------------------------------- load sheet
def load_athletes(sheets):
    """From Benchmarks tab. Returns list of {jst_id, name, fitr_id, row_number}."""
    rows = sheets.read_values(config.TAB_BENCHMARKS)
    header = rows[0]
    out = []
    for i, r in enumerate(rows[1:], start=2):  # row 2 = first data row
        rec = dict(zip(header, r))
        fitr_id = (rec.get("Fitr ID") or "").strip()
        name = (rec.get("Name") or "").strip()
        if not fitr_id or not name:
            continue
        out.append({
            "jst_id": (rec.get("JST ID") or "").strip(),
            "name": name,
            "fitr_id": fitr_id,
            "row": i,
        })
    return out


def load_existing_prlog(sheets):
    """Set of dedupe keys already present, plus name->email + previous values."""
    recs = sheets.read_records(config.TAB_PR_LOG)
    keys = set()
    email_by_name = {}
    prev_by_name_bench = {}
    for r in recs:
        name = str(r.get("Athlete Name", "")).strip()
        bench = str(r.get("Benchmark Name", "")).strip()
        value = str(r.get("Value", "")).strip()
        date = str(r.get("Date", "")).strip()
        keys.add((name.lower(), bench.lower(), value.lower(), date))
        if name and r.get("Email"):
            email_by_name.setdefault(name, str(r["Email"]).strip())
        if name and bench:
            prev_by_name_bench[(name.lower(), bench.lower())] = value
    return keys, email_by_name, prev_by_name_bench


# ------------------------------------------------------------------- sections
def collect_benchmarks(fitr, athletes, existing_keys, email_by_name, prev_lookup):
    new_rows = []
    note_updates = []   # (athlete_name, note_text)
    scraped_rows = {}   # row_number -> today's date (for Last Scraped)
    for a in athletes:
        try:
            items = fitr.benchmarks(a["fitr_id"])
        except FitrError as e:
            print(f"  ! benchmarks failed for {a['name']}: {e}")
            continue
        scraped_rows[a["row"]] = TODAY.isoformat()
        for b in items:
            lv = b.get("last_value") or {}
            d = _parse_date(lv.get("date"))
            if not d or d < CUTOFF or d > TODAY:
                continue
            bench = b.get("name", "")
            value = _fmt_value(lv)
            key = (a["name"].lower(), bench.lower(), value.lower(), d.isoformat())
            if key in existing_keys:
                continue
            prev = prev_lookup.get((a["name"].lower(), bench.lower()), "")
            email = email_by_name.get(a["name"], "")
            note = (lv.get("note") or "").strip()
            new_rows.append([
                d.isoformat(), a["name"], email, bench, value,
                "Benchmark", prev, "", note,
            ])
            existing_keys.add(key)
            if note:
                note_updates.append((a["name"], f"[{d.isoformat()} — result] {bench}: {note}"))
    return new_rows, note_updates, scraped_rows


def collect_challenges(fitr, existing_keys):
    new_rows = []
    challenges = fitr.challenges(pages=3)
    for ch in challenges:
        if not ch.get("count_entries"):
            continue
        created = _parse_date(str(ch.get("created_at", ""))[:10])
        # Pull recent challenges; rely on dedupe to avoid re-adding old scores.
        try:
            scores = fitr.challenge_scores(ch["id"])
        except FitrError as e:
            print(f"  ! scores failed for challenge {ch.get('title')}: {e}")
            continue
        comments = {}
        if any(s.get("comment_count") for s in scores):
            for c in fitr.challenge_comments(ch["id"]):
                uid = (c.get("user") or {}).get("id")
                if uid:
                    comments.setdefault(uid, []).append(c.get("text", ""))
        title = ch.get("title", "")
        for s in scores:
            user = s.get("user") or {}
            name = user.get("full_name", "").strip()
            value = str(s.get("value", "")).strip()
            if not name or not value:
                continue
            date = (created or TODAY).isoformat()
            key = (name.lower(), title.lower(), value.lower(), date)
            if key in existing_keys:
                continue
            note = " | ".join(comments.get(user.get("id"), []))[:300]
            new_rows.append([date, name, "", title, value, "Challenge", "", "", note])
            existing_keys.add(key)
    return new_rows


def collect_chat_summaries(rooms, valid_names, fitr):
    """Return {athlete_name: summary_line} for conversations active within CHAT_LOOKBACK_DAYS.
    Accepts pre-fetched rooms to avoid a second API call."""
    chat_cutoff = TODAY - dt.timedelta(days=config.CHAT_LOOKBACK_DAYS)
    candidates = []
    for room in rooms:
        if room.get("chat_room_type") != "individual":
            continue
        opp = room.get("opponent") or {}
        name = (opp.get("full_name") or opp.get("name") or "").strip()
        if not name:
            continue
        msg_date = room.get("last_message_date")
        if msg_date and msg_date < chat_cutoff:
            break  # rooms are newest-first, stop early once stale
        if not msg_date and not room.get("last_message_text", "").strip():
            continue
        candidates.append((room["id"], name, msg_date))

    out = {}
    for room_id, name, msg_date in candidates[: config.MAX_CHAT_SUMMARIES]:
        try:
            messages = fitr.chat_messages(room_id, max_messages=40)
        except FitrError as e:
            print(f"  ! chat_messages failed for {name}: {e}")
            messages = []
        thread_text = format_thread(messages)
        if not thread_text:
            continue
        summary = summariser.summarise_conversation(name, thread_text, activity_date=msg_date)
        if summary:
            out[name] = f"[{TODAY.isoformat()} — chat] {summary}"
    return out


def update_athlete_profiles(sheets, athletes, fitr_profiles_by_id):
    """Write Fitr-sourced profile fields (Email, Age) into _DATA.
    Only updates cells that Fitr provided a non-empty value for."""
    updates = {}
    for a in athletes:
        fitr_id = a.get("fitr_id")
        if not fitr_id:
            continue
        profile = fitr_profiles_by_id.get(int(fitr_id) if str(fitr_id).isdigit() else fitr_id, {})
        row_updates = {}
        if profile.get("email"):
            row_updates["Email"] = profile["email"]
        if profile.get("age") is not None:
            row_updates["Age"] = profile["age"]
        if row_updates:
            updates[a["name"]] = row_updates
    if updates:
        sheets.batch_update_by_name(config.TAB_DATA, "Full Name", updates)
    return len(updates)


# ------------------------------------------- competition dates from Typeform
def sync_competition_from_typeform(sheets, email_by_name):
    """Update _DATA competition fields from a dedicated competition Typeform.

    Only updates athletes whose Typeform answer is more recent than any existing
    Competition Date in _DATA (latest response wins).
    """
    if not config.COMP_FORM_SHEET_ID:
        return 0
    email_to_name = {v.lower(): k for k, v in email_by_name.items()}

    try:
        rows = sheets.read_external_records(config.COMP_FORM_SHEET_ID, config.COMP_FORM_TAB)
    except Exception as e:
        print(f"  ! comp form read failed: {e}")
        return 0

    latest_by_name = {}
    for row in rows:
        email = str(row.get(config.COMP_FORM_EMAIL_COL, "")).strip().lower()
        nm = email_to_name.get(email)
        if not nm:
            continue
        comp_name = str(row.get(config.COMP_FORM_NAME_COL, "")).strip()
        comp_date = str(row.get(config.COMP_FORM_DATE_COL, "")).strip()
        if comp_name or comp_date:
            # later rows overwrite earlier ones (Typeform appends newest last)
            latest_by_name[nm] = {"Next Competition": comp_name, "Competition Date": comp_date}

    if not latest_by_name:
        return 0
    sheets.batch_update_by_name(config.TAB_DATA, "Full Name", latest_by_name)
    return len(latest_by_name)


# ------------------------------------------------------- programme from survey
def sync_programme_from_recovery(sheets, rec_latest, email_by_name):
    """Update _DATA 'Programme' from the Typeform recovery response.

    Only updates athletes who answered the programme question with a recognised
    track name AND whose current _DATA value differs (so manual assignments by
    the coach are only overwritten if the athlete explicitly changed track).
    """
    if not rec_latest or not config.RECOVERY_PROGRAMME_COL:
        return 0
    email_to_name = {v.lower(): k for k, v in email_by_name.items()}

    values = sheets.read_values(config.TAB_DATA)
    if not values:
        return 0
    header = values[0]
    try:
        name_idx = header.index("Full Name")
        prog_idx = header.index("Programme")
    except ValueError:
        return 0  # column doesn't exist yet — safe no-op

    current_by_name = {}
    for row in values[1:]:
        nm = (row[name_idx] if name_idx < len(row) else "").strip()
        prog = (row[prog_idx] if prog_idx < len(row) else "").strip()
        if nm:
            current_by_name[nm] = prog

    updates = {}
    for email, row in rec_latest.items():
        nm = email_to_name.get(email.lower())
        if not nm:
            continue
        reported = str(row.get(config.RECOVERY_PROGRAMME_COL, "")).strip()
        if reported in config.JST_TRACKS and reported != current_by_name.get(nm, ""):
            updates[nm] = {"Programme": reported}

    if updates:
        sheets.batch_update_by_name(config.TAB_DATA, "Full Name", updates)
    return len(updates)


# --------------------------------------------------------------- notes writer
def append_coaching_notes(sheets, note_lines_by_name):
    """Append (never overwrite) to the Coaching Notes column in _DATA."""
    if not note_lines_by_name:
        return 0
    values = sheets.read_values(config.TAB_DATA)
    header = values[0]
    try:
        name_idx = header.index("Full Name")
    except ValueError:
        name_idx = 1
    try:
        notes_idx = header.index("Coaching Notes")
    except ValueError:
        print("  ! _DATA has no 'Coaching Notes' column; skipping note writes")
        return 0
    col_letter = _idx_to_col(notes_idx + 1)
    rowmap = {}
    for i, r in enumerate(values[1:], start=2):
        nm = (r[name_idx] if name_idx < len(r) else "").strip()
        if nm in note_lines_by_name:
            existing = r[notes_idx] if notes_idx < len(r) else ""
            addition = note_lines_by_name[nm]
            rowmap[i] = (existing + "\n" + addition).strip() if existing else addition
    sheets.update_cells_by_rowmap(config.TAB_DATA, col_letter, rowmap)
    return len(rowmap)


def _idx_to_col(idx):
    s = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


# --------------------------------------------------------------------- driver
def main():
    if not config.SHEET_ID:
        raise RuntimeError("Missing required env var: SHEET_ID")
    print(f"== Fitr sync {TODAY} (lookback {config.LOOKBACK_DAYS}d, dry_run={config.DRY_RUN}) ==")
    fitr = FitrClient()
    fitr.authenticate()
    print("Fitr: authenticated")
    sheets = SheetsClient()
    print("Sheets: connected")

    athletes = load_athletes(sheets)
    print(f"Athletes with Fitr IDs: {len(athletes)}")
    existing_keys, email_by_name, prev_lookup = load_existing_prlog(sheets)

    bench_rows, bench_notes, scraped_rows = collect_benchmarks(
        fitr, athletes, existing_keys, email_by_name, prev_lookup
    )
    print(f"New benchmark results: {len(bench_rows)}")

    chal_rows = collect_challenges(fitr, existing_keys)
    print(f"New challenge scores: {len(chal_rows)}")

    valid_names = {a["name"] for a in athletes}
    rooms = fitr.chat_rooms()
    chat_notes = collect_chat_summaries(rooms, valid_names, fitr)
    print(f"Conversations summarised: {len(chat_notes)}")

    fitr_profiles = profiles_from_rooms(rooms)
    profiles_updated = update_athlete_profiles(sheets, athletes, fitr_profiles)
    print(f"Athlete profiles updated: {profiles_updated}")

    # recovery merge (optional)
    rec_latest = recovery.latest_by_email(sheets)
    rec_by_name = {}
    rec_notes = {}
    if rec_latest:
        email_to_name = {v.lower(): k for k, v in email_by_name.items()}
        for email, row in rec_latest.items():
            nm = email_to_name.get(email.lower())
            if nm:
                rec_by_name[nm] = row
                rstr = recovery.readiness_string(row)
                if rstr:
                    rec_notes[nm] = rstr
    print(f"Recovery responses merged: {len(rec_notes)}")

    progs_updated = sync_programme_from_recovery(sheets, rec_latest, email_by_name)
    print(f"Programme assignments synced from survey: {progs_updated}")

    comps_updated = sync_competition_from_typeform(sheets, email_by_name)
    print(f"Competition dates synced from Typeform: {comps_updated}")

    # ---- writes ----
    sheets.append_rows(config.TAB_PR_LOG, bench_rows + chal_rows)

    note_lines = {}
    for nm, line in bench_notes:
        note_lines[nm] = (note_lines.get(nm, "") + "\n" + line).strip()
    for nm, line in {**chat_notes, **rec_notes}.items():
        note_lines[nm] = (note_lines.get(nm, "") + "\n" + line).strip()
    notes_written = append_coaching_notes(sheets, note_lines)
    print(f"Athletes with notes updated: {notes_written}")

    if scraped_rows:
        sheets.update_cells_by_rowmap(config.TAB_BENCHMARKS, "E", scraped_rows)

    # ---- analytics: trends + engagement + milestones + consistency ----
    pr_records = sheets.read_records(config.TAB_PR_LOG)
    trend_results = analytics.trend_analysis(pr_records)
    # Athletes contacted in this sync run count as recently reached — don't flag them
    last_contact_by_name = {name: TODAY for name in chat_notes}
    engagement_results = analytics.engagement_check(
        pr_records, athletes,
        threshold_days=config.ENGAGEMENT_THRESHOLD_DAYS,
        last_contact_by_name=last_contact_by_name,
    )
    milestones = analytics.milestone_detection(bench_rows)
    consistency_wins = analytics.consistency_check(pr_records, athletes)
    rec_alert_rows = analytics.recovery_alerts(rec_by_name)

    flagged_count = sum(1 for e in engagement_results if e["flag"])
    concern_count = sum(
        1 for signals in trend_results.values()
        for s in signals if s["trend"] == "declining" or s["peak_drop_flag"]
    )
    print(f"Engagement flags: {flagged_count}  |  Performance concerns: {concern_count}")
    print(f"Milestones: {len(milestones)}  |  Consistency streaks: {len(consistency_wins)}")

    alert_rows = analytics.build_coach_alerts_rows(
        engagement_results, trend_results, rec_by_name, milestones, consistency_wins
    )
    sheets.overwrite_tab(config.TAB_COACH_ALERTS, alert_rows)

    # ---- digest notification ----
    print("Sending digest...")
    notifier.send_digest(
        TODAY, engagement_results, trend_results,
        rec_alert_rows, milestones, consistency_wins,
    )

    # ---- sync log ----
    unknown = sorted({n for n in chat_notes} - valid_names)
    log_tab = sheets.get_or_create(
        config.TAB_SYNC_LOG,
        ["Run Date", "New PR Log rows", "Challenge scores added",
         "Conversations summarised", "Recovery merged", "Notes updated", "Notes"],
    )
    sheets.append_rows(config.TAB_SYNC_LOG, [[
        TODAY.isoformat(), len(bench_rows), len(chal_rows), len(chat_notes),
        len(rec_notes), notes_written,
        ("Unknown athletes seen: " + ", ".join(unknown)) if unknown else "ok",
    ]])

    print("== Done ==")


if __name__ == "__main__":
    try:
        main()
    except (FitrError, RuntimeError) as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)
