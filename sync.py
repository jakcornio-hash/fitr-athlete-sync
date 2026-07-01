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


def _normalize_benchmark(name):
    """Map Fitr variant name to canonical display name using BENCHMARK_NAME_MAP."""
    if not name:
        return name
    canonical = config.BENCHMARK_NAME_MAP.get(name.strip().lower())
    return canonical if canonical else name.strip()


def _goal_achieved(goal_text, bench_name, value_str):
    """Return True if this result appears to meet the athlete's North Star Goal.

    Heuristic: benchmark name must be a substring of the goal text AND the
    numeric value must meet or exceed the first number found in the goal.
    For goals containing "under", "sub", "below", or "faster", lower is better.
    """
    import re as _re
    if not goal_text or not bench_name or not value_str:
        return False
    goal_lower = goal_text.lower()
    if bench_name.lower() not in goal_lower:
        return False
    goal_nums = [float(x) for x in _re.findall(r'\d+(?:\.\d+)?', goal_text)]
    if not goal_nums:
        return False
    goal_val = goal_nums[0]
    val_nums = [float(x) for x in _re.findall(r'\d+(?:\.\d+)?', str(value_str))]
    if not val_nums:
        return False
    result_val = val_nums[0]
    lower_is_better = any(kw in goal_lower for kw in ("under", "sub", "below", "faster", "less than"))
    return result_val <= goal_val if lower_is_better else result_val >= goal_val


def _fmt_value(v):
    """Pretty value+symbol from a Fitr last_value dict."""
    val = v.get("value")
    sym = (v.get("symbol") or v.get("units") or "").strip()
    if val is None:
        return ""
    # Convert seconds-based benchmarks to mm:ss (or h:mm:ss) for readability.
    if sym.lower() in ("secs", "sec", "seconds", "s") and isinstance(val, (int, float)) and val >= 0:
        total = int(round(val))
        mins, secs = divmod(total, 60)
        if mins >= 60:
            hrs, mins = divmod(mins, 60)
            return f"{hrs}:{mins:02d}:{secs:02d}"
        return f"{mins}:{secs:02d}"
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
            bench = _normalize_benchmark(b.get("name", ""))
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
    """Return ({athlete_name: summary_line}, {athlete_name: (room_id, thread_text)}).

    Second dict contains athletes whose most recent message is FROM them (needs a reply).
    Accepts pre-fetched rooms to avoid a second API call.
    """
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
    pending_reply_candidates = {}  # name -> (room_id, thread_text)
    for room_id, name, msg_date in candidates[: config.MAX_CHAT_SUMMARIES]:
        try:
            messages = fitr.chat_messages(room_id, max_messages=40)
        except FitrError as e:
            print(f"  ! chat_messages failed for {name}: {e}")
            messages = []
        thread_text = format_thread(messages)
        if not thread_text:
            continue
        # Detect if the most recent message is from the athlete (needs a reply)
        if messages:
            last_author = (messages[0].get("author") or {}).get("full_name", "").strip()
            if last_author.lower() == name.lower():
                pending_reply_candidates[name] = (room_id, thread_text)
        summary = summariser.summarise_conversation(name, thread_text, activity_date=msg_date)
        if summary:
            out[name] = f"[{TODAY.isoformat()} — chat] {summary}"
    return out, pending_reply_candidates


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

    # Build email→name from PR Log, then supplement with _DATA (so athletes
    # who haven't logged any benchmarks yet can still be resolved).
    merged_email_by_name = dict(email_by_name)
    try:
        data_recs = sheets.read_records(config.TAB_DATA)
        for rec in data_recs:
            nm = str(rec.get("Full Name", "")).strip()
            em = str(rec.get("Email", "")).strip().lower()
            if nm and em:
                merged_email_by_name.setdefault(nm, em)
    except Exception:
        pass

    email_to_name = {v.lower(): k for k, v in merged_email_by_name.items()}
    # All known athlete names (for name-based fallback)
    all_known_names = list(email_to_name.values())

    try:
        rows = sheets.read_external_records(config.COMP_FORM_SHEET_ID, config.COMP_FORM_TAB)
    except Exception as e:
        print(f"  ! comp form read failed: {e}")
        return 0

    if rows:
        # Warn once if expected columns are missing from the Typeform sheet
        sample = rows[0]
        for col in (config.COMP_FORM_EMAIL_COL, config.COMP_FORM_FULL_NAME_COL,
                    config.COMP_FORM_COMP_NAME_COL, config.COMP_FORM_DATE_COL):
            if col not in sample:
                print(f"  ! comp form: column {col!r} not found. "
                      f"Available: {list(sample.keys())[:8]}")

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
    skipped_unresolved = []
    for row in rows:
        # Resolve athlete: email first, then exact name, then case-insensitive name
        email = str(row.get(config.COMP_FORM_EMAIL_COL, "")).strip().lower()
        nm = email_to_name.get(email)
        if not nm:
            raw_name = str(row.get(config.COMP_FORM_FULL_NAME_COL, "")).strip()
            for known in all_known_names:
                if known.lower() == raw_name.lower():
                    nm = known
                    break
        if not nm:
            raw_name = str(row.get(config.COMP_FORM_FULL_NAME_COL, "")).strip()
            skipped_unresolved.append(raw_name or f"<email:{email}>")
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

    if skipped_unresolved:
        print(f"  ! comp form: {len(skipped_unresolved)} submissions could not be matched to an athlete "
              f"(email + name lookup failed): {', '.join(sorted(skipped_unresolved))}")

    return added


# ------------------------------------------- athlete intake Typeform sync
def sync_intake_from_typeform(sheets, email_by_name):
    """Populate _DATA from athlete intake Typeform responses.

    Takes the most recent submission per athlete. Only updates fields that
    the athlete filled in and that differ from the current _DATA value.
    Returns count of athletes updated.
    """
    rows = sheets.load_intake_responses()
    if not rows:
        return 0

    merged_email_by_name = dict(email_by_name)
    try:
        data_recs_intake = sheets.read_records(config.TAB_DATA)
        for rec in data_recs_intake:
            nm2 = str(rec.get("Full Name", "")).strip()
            em2 = str(rec.get("Email", "")).strip().lower()
            if nm2 and em2:
                merged_email_by_name.setdefault(nm2, em2)
    except Exception:
        pass
    email_to_name = {v.lower(): k for k, v in merged_email_by_name.items()}

    # Group by athlete, keep latest submission
    latest_by_name = {}
    for row in rows:
        email = str(row.get(config.INTAKE_FORM_EMAIL_COL, "")).strip().lower()
        raw_name = str(row.get(config.INTAKE_FORM_FULL_NAME_COL, "")).strip()
        nm = email_to_name.get(email) or raw_name
        if not nm:
            continue
        submitted = str(row.get("Submitted At", "")).strip()
        existing = latest_by_name.get(nm)
        if existing is None or submitted > existing.get("_submitted", ""):
            row["_submitted"] = submitted
            latest_by_name[nm] = row

    if not latest_by_name:
        return 0

    # Field mapping: intake form column → _DATA column
    _INTAKE_FIELD_MAP = {
        config.INTAKE_FORM_GOAL_COL: "North Star Goal",
        config.INTAKE_FORM_TIER_COL: "Tier",
        config.INTAKE_FORM_OCCUPATION_COL: "Occupation",
        config.INTAKE_FORM_EQUIPMENT_COL: "Equipment Access",
        config.INTAKE_FORM_NOTES_COL: "Coaching Notes",
    }

    updates = {}
    for nm, row in latest_by_name.items():
        row_updates = {}
        for form_col, data_col in _INTAKE_FIELD_MAP.items():
            val = str(row.get(form_col, "")).strip()
            if val:
                row_updates[data_col] = val
        if row_updates:
            updates[nm] = row_updates

    if updates:
        sheets.batch_update_by_name(config.TAB_DATA, "Full Name", updates)
    return len(updates)


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


def auto_onboard_new_athletes(sheets, rooms, fitr=None, room_id_by_name=None,
                               messages_sent_log=None, dashboard_base_url=None):
    """Detect athletes present in Fitr chat rooms but missing from Benchmarks.

    Cross-references each new opponent against the CRM. Exact name matches are
    auto-added to Benchmarks and _DATA with the correct Programme. If fitr and
    room_id_by_name are provided, sends an intake form link + self-assessment prompt
    to newly onboarded athletes. Returns the count of athletes added.
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

    # Send onboarding checklist to newly added athletes
    if fitr and room_id_by_name and not config.DRY_RUN:
        import time as _time
        for name, fid, prog in to_onboard:
            room_id = room_id_by_name.get(name)
            if not room_id:
                continue
            first = name.split()[0]
            intake_msg = (
                f"Hi {first}! 👋 You've been added to the JST Compete coaching system — "
                f"really looking forward to working with you.\n\n"
                f"Two quick things to get us started:\n\n"
                f"1️⃣ Fill in your athlete intake form — takes about 3 minutes and gives me "
                f"everything I need to personalise your programming:\n"
                f"https://jstcompete.typeform.com/to/Q1tL7MmR\n\n"
                f"2️⃣ Submit your first weekly recovery check-in (once you're training) at the same link.\n\n"
                f"Message me here anytime. Let's get to work! 🔥"
            )
            try:
                fitr.send_chat_message(room_id, intake_msg)
                if messages_sent_log is not None:
                    messages_sent_log.append({
                        "Date": TODAY.isoformat(), "Athlete Name": name,
                        "Message Type": "onboard_checklist", "Room ID": room_id,
                    })
                _time.sleep(0.5)
                print(f"  [auto-onboard] Sent onboarding checklist to {name!r}")
            except Exception as exc:
                print(f"  ! Onboarding checklist failed for {name}: {exc}")

    return len(to_onboard)


def capture_postcomp_responses(fitr, sheets, TODAY):
    """Scan Fitr for athlete replies to post-comp messages; store in Competitions tab.

    Only captures responses for messages sent in the last 45 days that don't already
    have a response recorded. Returns count of rows updated.
    """
    try:
        msg_log = sheets.sh.worksheet(config.TAB_MESSAGE_LOG).get_all_records()
    except Exception:
        return 0

    pending = [
        r for r in msg_log
        if str(r.get("Message Type", "")).startswith("post_comp_")
        and str(r.get("Room ID", "")).strip()
    ]
    if not pending:
        return 0

    try:
        ws = sheets.sh.worksheet(config.TAB_COMPETITIONS)
        all_values = ws.get_all_values()
    except Exception:
        return 0

    if not all_values:
        return 0

    comp_header = all_values[0]
    if "Athlete Name" not in comp_header:
        return 0

    name_col = comp_header.index("Athlete Name")
    date_col = comp_header.index("Date") if "Date" in comp_header else None

    if "Post-comp Response" not in comp_header:
        resp_col = len(comp_header)
        if not config.DRY_RUN:
            ws.update_cell(1, resp_col + 1, "Post-comp Response")
        comp_header.append("Post-comp Response")
        for row_vals in all_values[1:]:
            while len(row_vals) < len(comp_header):
                row_vals.append("")
    else:
        resp_col = comp_header.index("Post-comp Response")

    import datetime as _dt5
    captured = 0

    for pm in pending:
        nm = str(pm.get("Athlete Name", "")).strip()
        room_id = str(pm.get("Room ID", "")).strip()
        send_date = str(pm.get("Date", "")).strip()
        if not nm or not room_id or not send_date:
            continue
        try:
            send_dt = _dt5.date.fromisoformat(send_date)
        except (ValueError, TypeError):
            continue
        if (TODAY - send_dt).days > 45:
            continue

        try:
            messages = fitr.chat_messages(room_id, max_messages=25)
        except Exception:
            continue

        replies = [
            str(m.get("text", "")).strip()
            for m in messages
            if not m.get("is_mine")
            and str(m.get("created_at", ""))[:10] >= send_date
            and str(m.get("text", "")).strip()
        ]
        if not replies:
            continue

        response_text = "\n".join(reversed(replies))

        for row_i, row in enumerate(all_values[1:], start=2):
            if name_col >= len(row) or str(row[name_col]).strip() != nm:
                continue
            if resp_col < len(row) and str(row[resp_col]).strip():
                continue  # already captured
            if date_col is not None and date_col < len(row):
                row_date_str = str(row[date_col]).strip()
                row_date = None
                for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
                    try:
                        row_date = _dt5.datetime.strptime(row_date_str, fmt).date()
                        break
                    except Exception:
                        continue
                if row_date and abs((row_date - send_dt).days) > 10:
                    continue  # date mismatch — different competition
            if config.DRY_RUN:
                print(f"[DRY_RUN] {nm}: would capture post-comp response ({len(replies)} msg(s))")
            else:
                ws.update_cell(row_i, resp_col + 1, response_text)
            captured += 1
            break

    return captured


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
    chat_notes, pending_reply_candidates = collect_chat_summaries(rooms, valid_names, fitr)
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

    intake_updated = sync_intake_from_typeform(sheets, email_by_name)
    if intake_updated:
        print(f"Athlete profiles updated from intake form: {intake_updated}")

    messages_sent_log = []  # collects all automated messages for Message Log

    # ---- load _DATA once for use throughout the rest of main() ----
    data_recs = sheets.read_records(config.TAB_DATA)
    data_by_name_all = {
        str(r.get("Full Name", "")).strip(): r for r in data_recs
        if str(r.get("Full Name", "")).strip()
    }
    goals_by_name = {
        nm: str(r.get("North Star Goal", "")).strip()
        for nm, r in data_by_name_all.items()
    }
    programme_by_name = {
        nm: str(r.get("Programme", "")).strip()
        for nm, r in data_by_name_all.items()
    }

    # ---- auto-congratulations + goal celebrations via Fitr chat ----
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
            goal = goals_by_name.get(name, "")
            if goal and _goal_achieved(goal, bench, value):
                msg = (
                    f"🎉 {first}!!! You just hit your North Star Goal — "
                    f"{bench}: {value}!\n\n"
                    f"This is exactly what we've been building towards. "
                    f"Massive achievement — so proud of what you've done. 🏆\n\n"
                    f"Time to set the next one!"
                )
            elif prev and prev not in ("", "first entry"):
                msg = f"Nice work {first} 💪 {bench}: {value} (was {prev}). Keep pushing!"
            else:
                msg = f"Great first result {first} 💪 {bench}: {value}. Looking forward to tracking your progress!"
            try:
                fitr.send_chat_message(room_id, msg)
                congrats_sent += 1
                messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": name,
                                          "Message Type": "congrats", "Room ID": room_id})
                import time as _time; _time.sleep(0.5)
            except FitrError as exc:
                print(f"  ! Congrats message failed for {name}: {exc}")
        print(f"Congratulations messages sent: {congrats_sent}")

    # ---- writes ----
    sheets.append_rows(config.TAB_PR_LOG, bench_rows + chal_rows)

    # ---- draft replies for pending athlete messages ----
    if pending_reply_candidates and not config.DRY_RUN:
        drafted_names = []
        for nm, (room_id, thread_text) in pending_reply_candidates.items():
            profile = data_by_name_all.get(nm, {})
            draft = summariser.draft_reply(nm, thread_text, profile_data=profile)
            if draft:
                sheets.write_draft_reply(nm, room_id, draft)
                drafted_names.append(nm)
        if drafted_names:
            print(f"Reply drafts generated: {len(drafted_names)}")
            notifier.send_draft_reply_alerts(drafted_names)

    # ---- per-coach Slack notifications ----
    coach_channel_map = sheets.load_coaches()
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

    # ---- churn risk snapshot (written daily) ----
    churn_snapshot_rows = []
    for e in engagement_results:
        nm = e["name"]
        risk = analytics.churn_risk_score(
            nm, engagement_results, trend_results,
            rec_by_name=rec_by_name, last_contact_by_name=last_contact_by_name,
        )
        churn_snapshot_rows.append({
            "Date": TODAY.isoformat(),
            "Athlete Name": nm,
            "Score": risk["score"],
            "Label": risk["label"],
            "Factors": ", ".join(risk["factors"]),
        })
    sheets.write_churn_snapshot(churn_snapshot_rows)
    print(f"Churn history snapshot written: {len(churn_snapshot_rows)} athletes")

    # ---- off-boarding: final check-in when 60d inactive + critical risk ----
    if not config.DRY_RUN:
        offboarding_sent = 0
        for snap in churn_snapshot_rows:
            nm = snap["Athlete Name"]
            if snap["Score"] < 60:
                continue
            e = next((x for x in engagement_results if x["name"] == nm), {})
            if e.get("days_since") != 60:
                continue
            room_id = room_id_by_name.get(nm)
            if not room_id:
                continue
            first = nm.split()[0]
            msg = (
                f"Hey {first} 👋 It's been a little while since I've seen you in the logs — "
                f"just checking in to see how you're getting on.\n\n"
                f"Life gets busy, totally get it. If there's anything going on or you want "
                f"to adjust your programme, just say the word — I'm here for you whenever "
                f"you're ready to get back at it. 💪"
            )
            try:
                fitr.send_chat_message(room_id, msg)
                offboarding_sent += 1
                messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": nm,
                                          "Message Type": "offboarding", "Room ID": room_id})
                import time as _time; _time.sleep(0.5)
            except FitrError as exc:
                print(f"  ! Off-boarding message failed for {nm}: {exc}")
        if offboarding_sent:
            print(f"Off-boarding check-in messages sent: {offboarding_sent}")

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
    onboarded = auto_onboard_new_athletes(
        sheets, rooms, fitr=fitr, room_id_by_name=room_id_by_name,
        messages_sent_log=messages_sent_log,
    )
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
            messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": nm,
                                      "Message Type": "anniversary", "Room ID": room_id})
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
            messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": nm,
                                      "Message Type": "onboarding", "Room ID": room_id})
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
            messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": nm,
                                      "Message Type": f"pre_comp_{days_out}d", "Room ID": room_id})
            import time as _time; _time.sleep(0.5)
        except FitrError as exc:
            print(f"  ! Pre-comp message failed for {nm}: {exc}")
    if comp_msgs_sent:
        print(f"Pre-competition messages sent: {comp_msgs_sent}")

    # ---- comp message dedup set (covers post_comp_ and comp_result_ prefixes) ----
    sent_comp_keys = set()
    if not config.DRY_RUN:
        try:
            _msg_log_rows = sheets.sh.worksheet(config.TAB_MESSAGE_LOG).get_all_records()
            sent_comp_keys = {
                (str(r.get("Athlete Name", "")).strip(), str(r.get("Message Type", "")).strip())
                for r in _msg_log_rows
                if str(r.get("Message Type", "")).startswith(("post_comp_", "comp_result_"))
            }
        except Exception:
            pass

    # ---- post-competition messages (fires the day after each competition) ----
    post_comp_msgs_sent = 0
    for row in competition_rows:
        nm = str(row.get("Athlete Name", "")).strip()
        comp_nm = str(row.get("Competition Name", "")).strip() or "your competition"
        comp_type = str(row.get("Type", "A")).strip().upper()
        raw_date = str(row.get("Date", "")).strip()
        if not nm or not raw_date:
            continue
        import datetime as _dt4
        comp_date = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
            try:
                comp_date = _dt4.datetime.strptime(raw_date, fmt).date()
                break
            except ValueError:
                continue
        if not comp_date:
            continue
        days_out = (comp_date - TODAY).days
        if days_out != -1:
            continue
        msg_type_key = f"post_comp_{comp_nm[:25].replace(' ', '_')}"
        if (nm, msg_type_key) in sent_comp_keys:
            continue
        room_id = room_id_by_name.get(nm)
        if not room_id or config.DRY_RUN:
            continue
        first = nm.split()[0]
        if comp_type in ("A", "B"):
            msg = (
                f"Hey {first} — hope you're recovering well after {comp_nm}! 💪\n\n"
                f"Would love to hear how it went. When you get a moment, can you share:\n"
                f"1️⃣ What was your result / placing?\n"
                f"2️⃣ What went well that you want to build on?\n"
                f"3️⃣ What's one thing you'd do differently next time?\n"
                f"4️⃣ How is your body feeling right now?\n\n"
                f"Really proud of the work you put in to get here. 🏆"
            )
        else:
            msg = (
                f"Hey {first} — how did {comp_nm} go yesterday? "
                f"Would love to hear your result and any thoughts from the day! 💪"
            )
        try:
            fitr.send_chat_message(room_id, msg)
            post_comp_msgs_sent += 1
            messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": nm,
                                      "Message Type": msg_type_key, "Room ID": room_id})
            import time as _time; _time.sleep(0.5)
        except FitrError as exc:
            print(f"  ! Post-comp message failed for {nm}: {exc}")
    if post_comp_msgs_sent:
        print(f"Post-competition messages sent: {post_comp_msgs_sent}")

    # ---- competition result congratulations ----
    # Send once when a result is entered for a recent comp, deduped via sent_comp_keys.
    comp_result_msgs_sent = 0
    for row in competition_rows:
        nm = str(row.get("Athlete Name", "")).strip()
        result = str(row.get("Result", "")).strip()
        comp_nm = str(row.get("Competition Name", "")).strip()
        if not nm or not result or not comp_nm:
            continue
        raw_date = str(row.get("Date", "")).strip()
        comp_date = None
        import datetime as _dt3
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
            try:
                comp_date = _dt3.datetime.strptime(raw_date, fmt).date()
                break
            except ValueError:
                continue
        if not comp_date or (TODAY - comp_date).days > 14 or comp_date > TODAY:
            continue
        msg_type_key = f"comp_result_{comp_nm[:25].replace(' ', '_')}"
        if (nm, msg_type_key) in sent_comp_keys:
            continue
        room_id = room_id_by_name.get(nm)
        if not room_id or config.DRY_RUN:
            continue
        first = nm.split()[0]
        msg = (
            f"Well done on {comp_nm}, {first}! 🏆 Result: {result}. "
            f"Really proud of the effort you put in — let's debrief when you're ready "
            f"and use this to plan the next block. 💪"
        )
        try:
            fitr.send_chat_message(room_id, msg)
            comp_result_msgs_sent += 1
            messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": nm,
                                      "Message Type": msg_type_key, "Room ID": room_id})
            import time as _time; _time.sleep(0.5)
        except FitrError as exc:
            print(f"  ! Comp result message failed for {nm}: {exc}")
    if comp_result_msgs_sent:
        print(f"Competition result messages sent: {comp_result_msgs_sent}")

    # ---- capture athlete replies to post-comp messages ----
    post_comp_replies = capture_postcomp_responses(fitr, sheets, TODAY)
    if post_comp_replies:
        print(f"Post-competition responses captured: {post_comp_replies}")

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

    # ---- monthly athlete reports (fires on the 1st of each month) ----
    if TODAY.day == 1:
        monthly_sent = notifier.send_monthly_athlete_reports(data_recs, email_by_name, pr_records)
        if monthly_sent:
            print(f"Monthly athlete reports sent: {monthly_sent}")

    # ---- log automated messages + check for replies ----
    if messages_sent_log:
        sheets.log_messages(messages_sent_log)
        print(f"Automated messages logged: {len(messages_sent_log)}")

    pending_msgs = sheets.load_pending_messages()
    if pending_msgs and not config.DRY_RUN:
        replies_found = 0
        for pending in pending_msgs:
            pnm = str(pending.get("Athlete Name", "")).strip()
            room_id = room_id_by_name.get(pnm)
            if not room_id:
                continue
            sent_date = str(pending.get("Date", "")).strip()
            msg_type = str(pending.get("Message Type", "")).strip()
            try:
                recent = fitr.chat_messages(room_id, max_messages=10)
                for cmsg in recent:
                    if not cmsg.get("is_mine") and str(cmsg.get("text", "")).strip():
                        msg_ts = str(cmsg.get("created_at", ""))[:10]
                        if msg_ts >= sent_date:
                            sheets.mark_message_replied(pnm, msg_type, sent_date, msg_ts)
                            replies_found += 1
                            break
            except FitrError:
                pass
        if replies_found:
            print(f"Athlete replies recorded: {replies_found}")

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
