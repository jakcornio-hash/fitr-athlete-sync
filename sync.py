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


# ------------------------------------------- competition calendar from Typeform
def sync_competition_from_typeform(sheets, email_by_name):
    """Sync competition Typeform responses into the Competitions tab.

    Each Typeform submission represents ONE competition per athlete. Writes a new
    row per submission, deduplicating by (athlete_name, competition_name, date).
    Uses the new A/B/C form fields; falls back gracefully if they are absent.
    """
    if not config.COMP_FORM_SHEET_ID:
        return 0
    email_to_name = {v.lower(): k for k, v in email_by_name.items()}

    try:
        rows = sheets.read_external_records(config.COMP_FORM_SHEET_ID, config.COMP_FORM_TAB)
    except Exception as e:
        print(f"  ! comp form read failed: {e}")
        return 0

    # Load existing Competitions tab for dedupe
    existing = sheets.load_competitions()
    seen_keys = set()
    for r in existing:
        key = (
            str(r.get("Athlete Name", "")).strip().lower(),
            str(r.get("Competition Name", "")).strip().lower(),
            str(r.get("Date", "")).strip(),
        )
        seen_keys.add(key)

    added = 0
    for row in rows:
        # Resolve athlete from email or full name field
        email = str(row.get(config.COMP_FORM_EMAIL_COL, "")).strip().lower()
        nm = email_to_name.get(email)
        if not nm:
            # Try matching via the full name field directly
            raw_name = str(row.get(config.COMP_FORM_FULL_NAME_COL, "")).strip()
            for athlete_name in email_to_name.values():
                if athlete_name.lower() == raw_name.lower():
                    nm = athlete_name
                    break
        if not nm:
            continue

        comp_name = str(row.get(config.COMP_FORM_COMP_NAME_COL, "")).strip()
        comp_date = str(row.get(config.COMP_FORM_DATE_COL, "")).strip()
        if not comp_name or not comp_date:
            continue

        # Parse type from the dropdown answer (e.g. "🥇 A — Primary goal...")
        raw_type = str(row.get(config.COMP_FORM_TYPE_COL, "")).strip()
        if "🥈" in raw_type or raw_type.upper().startswith("B"):
            comp_type = "B"
        elif "🥉" in raw_type or raw_type.upper().startswith("C"):
            comp_type = "C"
        else:
            comp_type = "A"

        notes = str(row.get(config.COMP_FORM_NOTES_COL, "")).strip()
        synced_at = str(row.get("Submitted At", TODAY.isoformat())).strip()

        key = (nm.lower(), comp_name.lower(), comp_date)
        if key in seen_keys:
            continue

        sheets.save_competition({
            "Athlete Name": nm,
            "Competition Name": comp_name,
            "Date": comp_date,
            "Type": comp_type,
            "Notes": notes,
            "Synced At": synced_at,
        })
        seen_keys.add(key)
        added += 1

    return added


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


# ------------------------------------------------------- auto-onboard helpers
# Coach abbreviations used in CRM → Programme name used in the Coaches tab
_CRM_COACH_MAP = {
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

# CRM name → Fitr registered name where spellings differ
_CRM_NAME_ALIASES = {
    "alanis sky akin": "Alanis-Sky Akin",
    "rachel ralston-smith": "Rachael Ralston-Smith",
    "scott moore": "Scotty Moore",
    "ben chipperfield": "Ben Chips",
}


def _load_crm_name_to_programme(sheets):
    """Return {fitr_name_lower: (display_name, programme)} from CRM tabs."""
    mapping = {}
    for tab in ("Bespoke Athletes", "Junior + Youth"):
        try:
            rows = sheets.read_external_records(config.CRM_SHEET_ID, tab)
        except Exception as exc:
            print(f"  [auto-onboard] CRM tab {tab!r} unreadable: {exc}")
            continue
        for row in rows:
            name = str(row.get("Athlete Name", "")).strip()
            coach_raw = str(row.get("Coach", "")).strip().lower()
            if not name or not coach_raw:
                continue
            programme = _CRM_COACH_MAP.get(coach_raw)
            if not programme:
                continue
            fitr_name = _CRM_NAME_ALIASES.get(name.lower(), name)
            mapping[fitr_name.lower()] = (fitr_name, programme)
    return mapping


def auto_onboard_new_athletes(sheets, rooms):
    """Detect athletes present in Fitr chat rooms but missing from Benchmarks.

    Cross-references each new opponent against the CRM. Exact name matches are
    auto-added to Benchmarks and _DATA with the correct Programme. Returns the
    count of athletes added.
    """
    if not config.CRM_SHEET_ID:
        return 0

    # Build set of Fitr IDs already in Benchmarks
    bench_vals = sheets.read_values(config.TAB_BENCHMARKS)
    if not bench_vals:
        return 0
    bench_header = bench_vals[0]
    try:
        bench_fitr_idx = bench_header.index("Fitr ID")
    except ValueError:
        return 0
    existing_fitr_ids = set()
    for r in bench_vals[1:]:
        raw = r[bench_fitr_idx].strip() if bench_fitr_idx < len(r) else ""
        if raw.isdigit():
            existing_fitr_ids.add(int(raw))

    # Find chat room opponents whose Fitr ID isn't in Benchmarks yet
    new_opponents = {}  # {fitr_id: full_name}
    for room in rooms:
        opp = room.get("opponent") or {}
        raw_id = opp.get("id")
        name = (opp.get("full_name") or "").strip()
        if not raw_id or not name:
            continue
        try:
            fid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if fid not in existing_fitr_ids:
            new_opponents[fid] = name

    if not new_opponents:
        return 0

    print(f"  [auto-onboard] {len(new_opponents)} new chat-room opponents found")

    # Cross-reference against CRM for exact name matches
    crm_map = _load_crm_name_to_programme(sheets)
    to_onboard = []
    unmatched = []
    for fid, name in new_opponents.items():
        entry = crm_map.get(name.lower())
        if entry:
            _, programme = entry
            to_onboard.append((name, fid, programme))
        else:
            unmatched.append(name)

    if unmatched:
        print(f"  [auto-onboard] No CRM match (manual review needed): "
              f"{', '.join(sorted(unmatched))}")

    if not to_onboard:
        return 0

    # Add to Benchmarks: [JST ID (blank), Name, Fitr ID]
    sheets.append_rows(config.TAB_BENCHMARKS,
                       [[" ", name, str(fid)] for name, fid, _ in to_onboard])

    # Add to _DATA
    data_vals = sheets.read_values(config.TAB_DATA)
    if data_vals:
        data_header = data_vals[0]
        try:
            d_name_idx = data_header.index("Full Name")
            d_prog_idx = data_header.index("Programme")
        except ValueError:
            d_name_idx = d_prog_idx = None

        if d_name_idx is not None:
            data_names_lower = {
                r[d_name_idx].strip().lower()
                for r in data_vals[1:]
                if d_name_idx < len(r) and r[d_name_idx].strip()
            }
            empty = [""] * len(data_header)
            new_data_rows = []
            for name, _, programme in to_onboard:
                if name.lower() not in data_names_lower:
                    row = list(empty)
                    row[d_name_idx] = name
                    row[d_prog_idx] = programme
                    new_data_rows.append(row)
            if new_data_rows:
                sheets.append_rows(config.TAB_DATA, new_data_rows)

    for name, fid, prog in to_onboard:
        print(f"  [auto-onboard] Added {name!r} (Fitr ID {fid}) → {prog!r}")

    return len(to_onboard)


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

    # Build room_id lookup for sending messages to athletes
    room_id_by_name = {
        (room.get("opponent") or {}).get("full_name", "").strip(): room["id"]
        for room in rooms
        if room.get("id") and (room.get("opponent") or {}).get("full_name", "").strip()
    }

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

    # ---- auto-congratulations via Fitr chat ----
    if bench_rows and not config.DRY_RUN:
        congrats_sent = 0
        for row in bench_rows:
            if len(row) < 5:
                continue
            name = row[1]
            bench = row[3]
            value = row[4]
            prev = row[6] if len(row) > 6 else ""
            room_id = room_id_by_name.get(name)
            if not room_id:
                continue
            first = name.split()[0]
            if prev and prev not in ("", "first entry"):
                msg = f"Nice work {first} 💪 {bench}: {value} (was {prev}). Keep pushing!"
            else:
                msg = f"Great first result {first} 💪 {bench}: {value}. Looking forward to tracking your progress!"
            try:
                fitr.send_chat_message(room_id, msg)
                congrats_sent += 1
                import time as _time; _time.sleep(0.5)
            except FitrError as exc:
                print(f"  ! Congrats message failed for {name}: {exc}")
        print(f"Congratulations messages sent: {congrats_sent}")

    # ---- writes ----
    sheets.append_rows(config.TAB_PR_LOG, bench_rows + chal_rows)

    # ---- per-coach Slack notifications ----
    coach_channel_map = sheets.load_coaches()
    data_recs = sheets.read_records(config.TAB_DATA)
    programme_by_name = {
        str(r.get("Full Name", "")).strip(): str(r.get("Programme", "")).strip()
        for r in data_recs
        if str(r.get("Full Name", "")).strip()
    }
    if coach_channel_map and (bench_rows or chal_rows):
        notified = notifier.send_coach_notifications(
            bench_rows, chal_rows, programme_by_name, coach_channel_map,
        )
        print(f"Coach Slack notifications sent: {notified}")
    else:
        print("Coach notifications: Coaches tab empty or no new rows")

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

    # ---- per-coach re-engagement alerts (inactive athletes) ----
    if coach_channel_map:
        reeng_sent = notifier.send_reengagement_alerts(
            engagement_results, programme_by_name, coach_channel_map,
        )
        if reeng_sent:
            print(f"Re-engagement alerts sent to {reeng_sent} coach channel(s)")

        # ---- weekly coach squad summaries ----
        from collections import defaultdict
        athletes_by_coach = defaultdict(list)
        for a in athletes:
            prog = programme_by_name.get(a["name"], "")
            if prog:
                athletes_by_coach[prog].append(a["name"])
        milestones_by_name = defaultdict(list)
        for m in milestones:
            milestones_by_name[m[0]].append((m[1], m[2]))
        summaries_sent = notifier.send_weekly_coach_summary(
            dict(athletes_by_coach), engagement_results, trend_results,
            dict(milestones_by_name), coach_channel_map,
        )
        if summaries_sent:
            print(f"Weekly squad summaries sent to {summaries_sent} coach channel(s)")

    # ---- auto-onboard new bespoke athletes from chat rooms ----
    onboarded = auto_onboard_new_athletes(sheets, rooms)
    if onboarded:
        print(f"New bespoke athletes auto-onboarded: {onboarded}")

    # ---- athlete anniversaries via Fitr chat ----
    _ANNIVERSARY_MILESTONES = {90: "3 months", 180: "6 months", 270: "9 months",
                                365: "1 year", 730: "2 years"}
    first_log_by_name = {}
    for rec in pr_records:
        nm = str(rec.get("Athlete Name", "")).strip()
        d_str = str(rec.get("Date", "")).strip()
        if nm and d_str:
            try:
                import datetime as _dt
                d = _dt.datetime.strptime(d_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if nm not in first_log_by_name or d < first_log_by_name[nm]:
                first_log_by_name[nm] = d
    anniversaries_sent = 0
    for a in athletes:
        nm = a["name"]
        first_log = first_log_by_name.get(nm)
        if not first_log:
            continue
        days_training = (TODAY - first_log).days
        milestone_label = _ANNIVERSARY_MILESTONES.get(days_training)
        if not milestone_label:
            continue
        room_id = room_id_by_name.get(nm)
        if not room_id or config.DRY_RUN:
            continue
        first = nm.split()[0]
        msg = (
            f"Happy {milestone_label} training anniversary {first}! 🎉 "
            f"You've been logging consistently since {first_log.strftime('%d %b %Y')} — "
            f"that dedication is what makes the difference. Keep going!"
        )
        try:
            fitr.send_chat_message(room_id, msg)
            anniversaries_sent += 1
            import time as _time; _time.sleep(0.5)
        except FitrError as exc:
            print(f"  ! Anniversary message failed for {nm}: {exc}")
    if anniversaries_sent:
        print(f"Anniversary messages sent: {anniversaries_sent}")

    # ---- new athlete onboarding (first log == today) ----
    onboarding_sent = 0
    for a in athletes:
        nm = a["name"]
        first_log = first_log_by_name.get(nm)
        if not first_log or first_log != TODAY:
            continue
        room_id = room_id_by_name.get(nm)
        if not room_id or config.DRY_RUN:
            continue
        first = nm.split()[0]
        msg = (
            f"Welcome to JST Compete, {first}! 👋 Your first log is in — you're officially on the board.\n\n"
            f"Here's how to get the most from your coaching:\n"
            f"1️⃣ Log every session as soon as you finish — 2 mins of data makes coaching infinitely better\n"
            f"2️⃣ Submit your weekly recovery survey here: https://jstcompete.typeform.com/to/Q1tL7MmR\n"
            f"   (takes 2 mins, helps me adjust your training load week to week)\n"
            f"3️⃣ Message me here anytime — I'm watching your progress and will be in touch regularly\n\n"
            f"Let's go! 🔥"
        )
        try:
            fitr.send_chat_message(room_id, msg)
            onboarding_sent += 1
            import time as _time; _time.sleep(0.5)
        except FitrError as exc:
            print(f"  ! Onboarding message failed for {nm}: {exc}")
    if onboarding_sent:
        print(f"New athlete onboarding messages sent: {onboarding_sent}")

    # ---- pre-competition automated messages ----
    # A comp milestones: send once on exact day (sync runs daily)
    _COMP_MSG_DAYS = {
        70: ("10 weeks out",
             "Your 10-week competition prep block starts {today}. "
             "Everything from here points to {comp} — I'll be switching your programme over. "
             "Trust the process and let's build something special. 🏆"),
        21: ("3 weeks out",
             "Three weeks to {comp}, {first}! 🗓️ "
             "We're in the final stretch now — keep the quality high and manage your recovery. "
             "Any questions about your prep, just ask."),
        7:  ("race week",
             "Race week is here, {first}! 🔥 "
             "{comp} is 7 days away. "
             "Stick to the plan, trust your training, and stay sharp. "
             "You've put the work in — now it's time to show it."),
        1:  ("day before",
             "Tomorrow is your day, {first}. 🏁 "
             "{comp} — you're ready. "
             "Good sleep tonight, good warm-up tomorrow, and go express what you've built. "
             "We're behind you all the way. 💪"),
    }
    competition_rows = sheets.load_competitions()
    comp_msgs_sent = 0
    for row in competition_rows:
        nm = str(row.get("Athlete Name", "")).strip()
        comp_nm = str(row.get("Competition Name", "")).strip() or "your competition"
        comp_type = str(row.get("Type", "A")).strip().upper()
        raw_date = str(row.get("Date", "")).strip()
        if not nm or not raw_date:
            continue
        import datetime as _dt2
        comp_date = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
            try:
                comp_date = _dt2.datetime.strptime(raw_date, fmt).date()
                break
            except ValueError:
                continue
        if not comp_date:
            continue
        days_out = (comp_date - TODAY).days
        template = _COMP_MSG_DAYS.get(days_out)
        if not template:
            continue
        # Only auto-message for A comps; 21d applies to A+B
        if comp_type == "C":
            continue
        if days_out in (70, 1) and comp_type != "A":
            continue
        room_id = room_id_by_name.get(nm)
        if not room_id or config.DRY_RUN:
            continue
        first = nm.split()[0]
        _, msg_tpl = template
        msg = msg_tpl.format(first=first, comp=comp_nm, today=TODAY.strftime("%d %b %Y"))
        try:
            fitr.send_chat_message(room_id, msg)
            comp_msgs_sent += 1
            import time as _time; _time.sleep(0.5)
        except FitrError as exc:
            print(f"  ! Pre-comp message failed for {nm}: {exc}")
    if comp_msgs_sent:
        print(f"Pre-competition messages sent: {comp_msgs_sent}")

    # ---- weekly athlete progress emails ----
    archetype_rows = sheets.load_archetype_assessments()
    archetype_by_name = {
        str(r.get("Athlete Name", "")).strip(): r
        for r in archetype_rows
        if str(r.get("Athlete Name", "")).strip()
    }
    emails_sent = notifier.send_all_athlete_progress_emails(
        bench_rows, consistency_wins, competition_rows, email_by_name,  # competition_rows already loaded above
        archetype_by_name=archetype_by_name,
    )
    if emails_sent:
        print(f"Athlete weekly progress emails sent: {emails_sent}")

    # ---- sync log ----
    unknown = sorted({n for n in chat_notes} - valid_names)
    log_tab = sheets.get_or_create(
        config.TAB_SYNC_LOG,
        ["Run Date", "Total Athletes", "New PR Log rows", "Challenge scores added",
         "Conversations summarised", "Recovery merged", "Notes updated",
         "Athletes auto-onboarded", "Athlete Emails Sent", "Notes"],
    )
    sheets.append_rows(config.TAB_SYNC_LOG, [[
        TODAY.isoformat(), len(athletes), len(bench_rows), len(chal_rows), len(chat_notes),
        len(rec_notes), notes_written, onboarded, emails_sent,
        ("Unknown athletes seen: " + ", ".join(unknown)) if unknown else "ok",
    ]])

    print("== Done ==")


if __name__ == "__main__":
    try:
        main()
    except (FitrError, RuntimeError) as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)
