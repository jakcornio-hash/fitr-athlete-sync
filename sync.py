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
import contextlib
import datetime as dt
import json
import re
import sys
import traceback

import config
from fitr_client import (FitrClient, FitrError, format_thread, message_date,
                         message_is_from, profiles_from_rooms)
import sheets_client
from sheets_client import SheetsClient
import analytics
import archetypes
import health_check
import notifier
import summariser
import coaching_voice
import recovery

TODAY = dt.date.today()
CUTOFF = TODAY - dt.timedelta(days=config.LOOKBACK_DAYS)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def _looks_like_email(s):
    """True only for a plausibly-deliverable address.

    The PR Log has been imported from a client export that redacted some
    addresses to the literal string 'Hidden by client'. Stored as-is, that
    string was handed to the mail server as a recipient and bounced. Anything
    that isn't shaped like an email is treated as no email at all, so the
    _DATA address (where we hold a real one) is used instead.
    """
    return bool(_EMAIL_RE.match(str(s or "").strip()))


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
    sym = (v.get("symbol") or "").strip()
    units = (v.get("units") or "").strip().lower()
    if val is None:
        return ""
    # Fitr returns time benchmarks with symbol='mm:ss' and units='second' (value in seconds).
    # The value may come back as a string ("1200") rather than an int, so coerce before checking.
    is_time = sym.lower() in ("secs", "sec", "seconds", "s", "mm:ss") or units in ("second", "seconds")
    if is_time:
        try:
            num_val = float(val)
        except (TypeError, ValueError):
            num_val = None
        if num_val is not None and num_val >= 0:
            total = int(round(num_val))
            mins, secs = divmod(total, 60)
            if mins >= 60:
                hrs, mins = divmod(mins, 60)
                return f"{hrs}:{mins:02d}:{secs:02d}"
            return f"{mins}:{secs:02d}"
    return f"{val} {sym}".strip() if sym else str(val)


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
        if name and _looks_like_email(r.get("Email")):
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


def refresh_challenge_measures(fitr, sheets):
    """Maintain the 'Challenge Measures' tab: {Title: Measure}. Fitr stores each
    score as a raw value whose unit depends on the challenge's measure (weight in
    grams, time in ms, reps as a count), but the per-score payload doesn't carry
    the measure. The dashboard needs it to display scores correctly, so we mirror
    each challenge's measure here. Merge with what's already stored so the map only
    grows as older challenges scroll out of the recent pull."""
    try:
        challenges = fitr.challenges(pages=10)
    except FitrError as e:
        print(f"  ! challenge measures pull failed: {e}")
        return
    measures = {}
    try:
        for r in sheets.read_records("Challenge Measures"):
            t = str(r.get("Title", "")).strip()
            if t:
                measures[t] = str(r.get("Measure", "")).strip()
    except Exception:
        pass
    added = 0
    for c in challenges:
        t = str(c.get("title", "")).strip()
        m = str(c.get("measure", "")).strip()
        if t and m and measures.get(t) != m:
            measures[t] = m
            added += 1
    header = ["Title", "Measure"]
    rows = [header] + [[t, measures[t]] for t in sorted(measures)]
    ws = sheets.get_or_create("Challenge Measures", header)
    ws.clear()
    ws.update(values=rows, range_name="A1", value_input_option="RAW")
    print(f"Challenge Measures refreshed: {len(measures)} titles ({added} new/changed)")


def collect_chat_summaries(rooms, valid_names, fitr):
    """Return ({athlete_name: summary_line}, {athlete_name: (room_id, thread_text, msg_date)}).

    Second dict contains athletes whose most recent message is FROM them (needs a reply);
    msg_date is when that message arrived, so alerts can say how long they've waited.
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
    pending_reply_candidates = {}  # name -> (room_id, thread_text, msg_date)
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
                pending_reply_candidates[name] = (room_id, thread_text, msg_date)
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
    # All known athlete names — start from email map, then supplement with
    # every name in Benchmarks (catches athletes who have no email in _DATA)
    all_known_names = list(email_to_name.values())
    try:
        bm_values = sheets.read_values(config.TAB_BENCHMARKS)
        if bm_values:
            bm_header = bm_values[0]
            for r in bm_values[1:]:
                rec = dict(zip(bm_header, r))
                nm = (rec.get("Name") or "").strip()
                if nm and nm not in all_known_names:
                    all_known_names.append(nm)
    except Exception:
        pass

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
        seen_keys.add((
            str(r.get("Athlete Name", "")).strip().lower(),
            str(r.get("Competition Name", "")).strip().lower(),
            analytics.canonical_date_key(r.get("Date", "")),
        ))

    added = 0
    skipped_unresolved = []
    for row in rows:
        # Resolve athlete: email → exact name → fuzzy name
        import difflib as _difflib
        email = str(row.get(config.COMP_FORM_EMAIL_COL, "")).strip().lower()
        nm = email_to_name.get(email)
        raw_name = str(row.get(config.COMP_FORM_FULL_NAME_COL, "")).strip()
        if not nm:
            for known in all_known_names:
                if known.lower() == raw_name.lower():
                    nm = known
                    break
        if not nm and raw_name:
            nm = analytics.match_athlete_name(raw_name, all_known_names)
            if nm:
                print(f"  [comp form] fuzzy matched '{raw_name}' → '{nm}'")
        if not nm:
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

        key = (nm.lower(), comp_name.lower(), analytics.canonical_date_key(comp_date))
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
def sync_archetype_from_typeform(sheets, email_by_name):
    """Import athlete archetype self-assessments from the Typeform response tab.

    Scores the RAW answers with the canonical forced-choice engine rather than
    trusting Typeform's own variable tallies, so an athlete's self-read is
    directly comparable with their coach read (the two must be scored the same
    way or any "divergence" is just a scoring artefact).

    Deduped on the Typeform response Token, stored in the assessment's Notes,
    so re-running never double-imports. Returns count imported.
    """
    rows = sheets.load_archetype_form_responses()
    if not rows:
        return 0, []

    # Tokens we've already imported
    seen_tokens = set()
    for r in sheets.load_archetype_assessments():
        note = str(r.get("Notes", "")).strip()
        if note.startswith("typeform:"):
            seen_tokens.add(note.split("typeform:", 1)[1].strip())

    # Athlete resolution: email -> exact name -> fuzzy name.
    # Collect names even where we hold no email — an athlete with a blank Email
    # cell can still be matched by name, and gating the name list on having an
    # email silently dropped ~90 roster members from ever matching.
    merged = dict(email_by_name)
    all_known = [n for n in merged if n]
    try:
        for rec in sheets.read_records(config.TAB_DATA):
            nm2 = str(rec.get("Full Name", "")).strip()
            em2 = str(rec.get("Email", "")).strip().lower()
            if not nm2:
                continue
            if em2:
                merged.setdefault(nm2, em2)
            if nm2 not in all_known:
                all_known.append(nm2)
    except Exception:
        pass
    email_to_name = {v.lower(): k for k, v in merged.items() if v}

    def _submitted_date(s):
        for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return dt.datetime.strptime(str(s).strip(), fmt).date()
            except (ValueError, TypeError):
                continue
        return None

    q_texts = archetypes.forced_choice_question_texts()
    imported = 0
    new_reads = []  # (name, primary_archetype) for athletes newly scored this run
    unresolved, unmapped = [], []

    for row in rows:
        token = str(row.get("Token", "")).strip()
        if not token or token in seen_tokens:
            continue

        email = str(row.get("What's your email?", "")).strip().lower()
        raw_name = str(row.get("What's your name?", "")).strip()

        nm = email_to_name.get(email)
        if not nm and raw_name:
            for known in all_known:
                if known.lower() == raw_name.lower():
                    nm = known
                    break
        if not nm and raw_name:
            nm = analytics.match_athlete_name(raw_name, all_known)
            if nm:
                print(f"  [archetype form] fuzzy matched '{raw_name}' → '{nm}'")
        if not nm:
            unresolved.append(raw_name or f"<{email or 'no id'}>")
            continue

        # Map each answer's text back to its option index
        answers = []
        for i, qt in enumerate(q_texts):
            idx = archetypes.forced_choice_answer_index(i, row.get(qt, ""))
            if idx is None:
                break
            answers.append(idx)
        if len(answers) != len(q_texts):
            unmapped.append(nm)
            continue

        result = archetypes.score_forced_choice(answers)
        taken = _submitted_date(row.get("Submitted At", "")) or TODAY
        if config.DRY_RUN:
            print(f"[DRY_RUN] archetype self-read for {nm}: {result.get('primary')}")
            imported += 1
            seen_tokens.add(token)
            continue
        sheets.write_archetype_assessment({
            "Athlete Name": nm,
            "Assessor": "Athlete (Self)",
            "Instrument": "forced_choice",
            "Version": str(archetypes.FORCED_CHOICE.get("version", 1)),
            "Taken At": taken.isoformat(),
            "Primary Archetype": result.get("primary", ""),
            "Profile JSON": json.dumps(result),
            "Raw Answers JSON": json.dumps(answers),
            "Notes": f"typeform:{token}",
        })
        seen_tokens.add(token)
        imported += 1
        new_reads.append((nm, result.get("primary", "")))

    if unresolved:
        print(f"  ! [archetype form] couldn't match to an athlete (add their email to _DATA): "
              f"{', '.join(unresolved[:10])}")
    if unmapped:
        print(f"  ! [archetype form] answers didn't map to the instrument, skipped: "
              f"{', '.join(unmapped[:10])}")
    return imported, new_reads


def sync_video_analysis(sheets, email_by_name):
    """Import movement-analysis video submissions from the Google Form.

    Three outcomes per new submission, and no fourth: a dated Coaching Notes
    entry, a row on the Video Reviews tab that stays open until a coach marks
    it reviewed, and nothing sent to the athlete. Video feedback is the coach's
    job; an automated "thanks, we got it" would be the exact bot-voice noise
    we've been stripping out everywhere else.

    Deduped on the Drive video link, which is unique per upload.

    Athlete resolution is deliberately conservative. Only ~30% of submitters
    sign in with the email we have on file, so email alone silently drops most
    rows; but attaching a video to the WRONG athlete's notes is worse than
    dropping it, so anything that can't be resolved confidently is reported
    for a human to place rather than guessed at.

    Returns (imported, unresolved_list).
    """
    rows = sheets.load_video_form_responses()
    if not rows:
        return 0, []

    seen_links = {
        str(r.get("Video Link", "")).strip()
        for r in sheets.load_video_reviews()
        if str(r.get("Video Link", "")).strip()
    }

    # Roster: name -> email, from the caller's map plus _DATA.
    # Names are collected even when we hold no email for the athlete: ~90 of
    # them have a blank Email cell, and those are precisely the people email
    # matching cannot rescue, so gating the name list on having an email would
    # drop the rows that need the name fallback most.
    merged = dict(email_by_name)
    all_known = [n for n in merged if n]
    try:
        for rec in sheets.read_records(config.TAB_DATA):
            nm2 = str(rec.get("Full Name", "")).strip()
            em2 = str(rec.get("Email", "")).strip().lower()
            if not nm2:
                continue
            if em2:
                merged.setdefault(nm2, em2)
            if nm2 not in all_known:
                all_known.append(nm2)
    except Exception:
        pass
    email_to_name = {v.lower(): k for k, v in merged.items() if v}

    def _norm(s):
        return re.sub(r"[^a-z]", "", str(s or "").lower())

    def _resolve(auto_email, typed_name, typed_email):
        """auto_email -> exact name -> surname+first-initial -> unique first name."""
        nm = email_to_name.get(auto_email)
        if nm:
            return nm, "email"
        # The typed name and typed email get swapped by some submitters, so
        # treat whichever field holds a non-email string as the name candidate.
        cands = [c for c in (typed_name, typed_email) if c and "@" not in c]
        for cand in cands:
            n = _norm(cand)
            if not n:
                continue
            for known in all_known:
                if _norm(known) == n:
                    return known, "name"
        for cand in cands:
            parts = [p for p in re.split(r"\s+", cand.strip()) if p]
            if len(parts) < 2:
                continue
            first, last = parts[0].lower(), parts[-1].lower()
            # "Gav Donald" -> "Gavin Donald": surname must match exactly and the
            # first name must be a prefix, and the result must be unambiguous.
            hits = [
                k for k in all_known
                if (kp := [p for p in re.split(r"\s+", k.strip()) if p]) and len(kp) >= 2
                and kp[-1].lower() == last
                and (kp[0].lower().startswith(first) or first.startswith(kp[0].lower()))
            ]
            if len(hits) == 1:
                return hits[0], "fuzzy"
        for cand in cands:
            parts = [p for p in re.split(r"\s+", cand.strip()) if p]
            if len(parts) != 1:
                continue
            # First name only ("Luc"): accept only if exactly one athlete has it.
            hits = [
                k for k in all_known
                if (kp := [p for p in re.split(r"\s+", k.strip()) if p])
                and kp[0].lower() == parts[0].lower()
            ]
            if len(hits) == 1:
                return hits[0], "first-name"
        return None, None

    def _submitted_date(s):
        for fmt in ("%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return dt.datetime.strptime(str(s).strip(), fmt).date()
            except (ValueError, TypeError):
                continue
        return None

    new_rows, note_lines, unresolved = [], {}, []
    for row in rows:
        link = str(row.get(config.VIDEO_FORM_VIDEO_COL, "")).strip()
        if not link or link in seen_links:
            continue

        auto_email = str(row.get(config.VIDEO_FORM_AUTO_EMAIL_COL, "")).strip().lower()
        typed_name = str(row.get(config.VIDEO_FORM_TYPED_NAME_COL, "")).strip()
        typed_email = str(row.get(config.VIDEO_FORM_TYPED_EMAIL_COL, "")).strip()
        movement = str(row.get(config.VIDEO_FORM_MOVEMENT_COL, "")).strip()
        bottleneck = str(row.get(config.VIDEO_FORM_BOTTLENECK_COL, "")).strip()
        track = str(row.get(config.VIDEO_FORM_TRACK_COL, "")).strip()
        submitted = _submitted_date(row.get("Timestamp", "")) or TODAY

        nm, how = _resolve(auto_email, typed_name, typed_email)
        if not nm:
            unresolved.append(f"{typed_name or auto_email or '?'} ({movement or 'video'})")
            continue
        if how != "email":
            print(f"  [video form] matched by {how}: "
                  f"'{typed_name or auto_email}' → '{nm}'")

        seen_links.add(link)
        new_rows.append({
            "Submitted At": submitted.isoformat(),
            "Athlete Name": nm,
            "Movement": movement,
            "Bottleneck": bottleneck,
            "Video Link": link,
            "Track": track,
            "Reviewed": "No",
            "Reviewed By": "",
            "Reviewed At": "",
            "Submitter Email": auto_email,
        })

        # Dated Coaching Notes entry, in the established [date — kind] format
        summary = bottleneck if len(bottleneck) <= 220 else bottleneck[:217] + "..."
        line = f"[{submitted.isoformat()} — video] {movement or 'Movement'} submitted for analysis."
        if summary:
            line += f" Athlete says: {summary}"
        line += f" Video: {link}"
        note_lines[nm] = (note_lines.get(nm, "") + "\n" + line).strip()

    if not new_rows:
        if unresolved:
            print(f"  ! [video form] couldn't match to an athlete: "
                  f"{', '.join(unresolved[:10])}")
        return 0, unresolved

    if config.DRY_RUN:
        for r in new_rows:
            print(f"[DRY_RUN] video review: {r['Athlete Name']} — {r['Movement']}")
        return len(new_rows), unresolved

    sheets.append_video_reviews(new_rows)
    append_coaching_notes(sheets, note_lines)
    if unresolved:
        print(f"  ! [video form] couldn't match to an athlete: "
              f"{', '.join(unresolved[:10])}")
    return len(new_rows), unresolved


def sync_story_forms(sheets, email_by_name):
    """Import new story submissions into the Story Reviews tab.

    For each submission not already imported (deduped on the form Token):
    resolve the athlete from the name/email they gave, tag it for the coverage
    grid with Claude (segment, objection, lead-vs-fragment), and record the two
    consent answers. Attribution and tagging are best-effort: a story we can't
    match or can't tag still gets imported, flagged, so it surfaces for a human
    rather than vanishing.

    Returns (imported, unresolved_names).
    """
    rows = sheets.load_story_form_responses()
    if not rows:
        return 0, []

    seen = {
        str(r.get("Token", "")).strip()
        for r in sheets.load_story_reviews()
        if str(r.get("Token", "")).strip()
    }

    # Roster for attribution: email -> name, plus a name list (some athletes
    # have no email on file, so don't gate the name list on having one).
    merged = dict(email_by_name)
    all_known = [n for n in merged if n]
    try:
        for rec in sheets.read_records(config.TAB_DATA):
            nm2 = str(rec.get("Full Name", "")).strip()
            em2 = str(rec.get("Email", "")).strip().lower()
            if not nm2:
                continue
            if em2:
                merged.setdefault(nm2, em2)
            if nm2 not in all_known:
                all_known.append(nm2)
    except Exception:
        pass
    email_to_name = {v.lower(): k for k, v in merged.items() if v}

    def _resolve(name, email):
        e = str(email or "").strip().lower()
        if e and e in email_to_name:
            return email_to_name[e]
        n = str(name or "").strip()
        for known in all_known:  # exact, case-insensitive
            if known.lower() == n.lower() and n:
                return known
        import difflib
        m = difflib.get_close_matches(n.lower(), [k.lower() for k in all_known], n=1, cutoff=0.85)
        if m:
            return next(k for k in all_known if k.lower() == m[0])
        return ""

    new_rows, unresolved = [], []
    for row in rows:
        token = str(row.get(config.STORY_COL_TOKEN, "")).strip()
        if not token or token in seen:
            continue
        seen.add(token)  # guard against the Typeform double-write we saw

        sub_name = str(row.get(config.STORY_COL_NAME, "")).strip()
        sub_email = str(row.get(config.STORY_COL_EMAIL, "")).strip()
        athlete = _resolve(sub_name, sub_email)
        if not athlete:
            unresolved.append(sub_name or sub_email or "<anonymous>")

        answers = {
            "Before / why looking": row.get(config.STORY_COL_BEFORE, ""),
            "What nearly stopped them": row.get(config.STORY_COL_OBJECTION, ""),
            "What tipped them": row.get(config.STORY_COL_TIPPED, ""),
            "What's different now": row.get(config.STORY_COL_CHANGED, ""),
            "Advice to their past self": row.get(config.STORY_COL_ADVICE, ""),
        }
        tags = summariser.tag_story(answers)

        new_rows.append({
            "Token": token,
            "Athlete Name": athlete,
            "Submitter Name": sub_name,
            "Submitter Email": sub_email,
            "Segment": tags.get("segment", ""),
            "Objection": tags.get("objection", ""),
            "Quality": tags.get("quality", ""),
            "Consent": row.get(config.STORY_COL_USAGE_CONSENT, ""),
            "Numbers OK": row.get(config.STORY_COL_NUMBERS_CONSENT, ""),
            "Reviewed": "No", "Reviewed By": "", "Reviewed At": "",
            "Imported At": TODAY.isoformat(),
        })

    if config.DRY_RUN:
        for r in new_rows:
            print(f"[DRY_RUN] story: {r['Athlete Name'] or r['Submitter Name']} "
                  f"[{r['Segment']}/{r['Objection']}/{r['Quality']}]")
        return len(new_rows), unresolved

    if new_rows:
        sheets.append_story_reviews(new_rows)
    if unresolved:
        print(f"  ! [story form] couldn't match to an athlete: {', '.join(unresolved[:10])}")
    return len(new_rows), unresolved


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


# ------------------------------------------------------------ message delivery
_PENDING_MESSAGES = []
# Messages that genuinely reached an athlete. The Message Log is supposed to be
# the record of what we actually said to people — the reply scanner reads it to
# look for answers, and the response-rate figures divide by it. Call sites used
# to log every message they *composed*, so with automatic sending off the log
# would fill with drafts nobody had sent, and we would scan for replies to
# messages that were never delivered. Recorded here instead, at the one place
# that knows whether it sent or queued.
_DELIVERED = []


def _deliver(fitr, room_id, msg, name, msg_type):
    """Send an athlete message, or queue it as a draft for a coach to send.

    With config.AUTO_SEND_ATHLETE_MESSAGES off (the default), nothing goes to an
    athlete automatically. The message is written to the Pending Messages tab
    and appears on the dashboard action list, where a coach reads it, edits it
    if they want, and sends it. Same drafting, same triggers, a person on the end.
    """
    if config.AUTO_SEND_ATHLETE_MESSAGES and config.DRY_RUN:
        print(f"[DRY_RUN] would send {msg_type} to {name} (not sent)")
        return False
    if config.AUTO_SEND_ATHLETE_MESSAGES:
        fitr.send_chat_message(room_id, msg)
        _DELIVERED.append({
            "Date": TODAY.isoformat(), "Athlete Name": str(name),
            "Message Type": str(msg_type), "Room ID": str(room_id),
        })
        return True
    _PENDING_MESSAGES.append([
        TODAY.isoformat(), str(name), str(msg_type), str(msg), str(room_id), "pending",
    ])
    return False


TRAINING_WINDOW = "2weeks"   # the longest window Fitr's activity view supports


def write_training_activity(sheets, activity_by_name, known_names=None):
    """Write each athlete's real training summary onto their _DATA row.

    Goes into _DATA rather than a tab of its own so every existing view gets it
    for nothing — the squad cards, the athlete profile, the action list and the
    billing check all read _DATA already.

    known_names restricts the write to athletes actually on the sheet. Fitr
    returns the entire client base — 1,698 people, most of whom bought a one-off
    plan years ago — against a _DATA tab of about 360.
    """
    keep = ({analytics.normalise_client_name(n) for n in known_names}
            if known_names else None)
    updates = {}
    for name, a in activity_by_name.items():
        if keep is not None and analytics.normalise_client_name(name) not in keep:
            continue
        updates[name] = {
            "Last Trained": a["last_trained"].isoformat() if a["last_trained"] else "",
            "Sessions (14d)": a["sessions"],
            "Adherence (14d)": f"{a['adherence_pct']}%" if a["adherence_pct"] is not None else "",
            "Fitr Plan": a["plan"],
        }
    if not updates:
        return 0
    try:
        sheets.ensure_headers_present(config.TAB_DATA, [
            "Last Trained", "Sessions (14d)", "Adherence (14d)", "Fitr Plan"])
        return sheets.batch_update_by_name(config.TAB_DATA, "Full Name", updates) or len(updates)
    except Exception as exc:
        print(f"  ! Could not write training summary to _DATA: {exc}")
        return 0


def _sent_or_drafted(n):
    """Wording for the run log: these only leave the building if sending is on."""
    return (f"{n} sent" if config.AUTO_SEND_ATHLETE_MESSAGES
            else f"{n} drafted for a coach to send")


def flush_pending_messages(sheets):
    """Write queued drafts to the Pending Messages tab for the dashboard."""
    if not _PENDING_MESSAGES or config.DRY_RUN:
        return 0
    header = ["Date", "Athlete Name", "Message Type", "Message", "Room ID", "Status"]
    sheets.ensure_headers(config.TAB_PENDING_MESSAGES, header)
    sheets.append_rows(config.TAB_PENDING_MESSAGES, _PENDING_MESSAGES)
    n = len(_PENDING_MESSAGES)
    _PENDING_MESSAGES.clear()
    return n


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
                               messages_sent_log=None, dashboard_base_url=None,
                               bespoke_names=None):
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
    # data_names_lower is the reliable "have we met this person before" signal —
    # unlike a Fitr chat-room ID, which can apparently be re-issued/duplicated for
    # the same person (e.g. after Fitr merges a re-registered account), a _DATA row
    # persists as long as no one deletes it. Compute unconditionally (defaulting to
    # empty) so it's available below even if _DATA is unreadable or malformed.
    data_names_lower = set()
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

    # A welcome DRAFT, never an automatic send.
    #
    # This block used to auto-send a checklist on top of Fitr's own welcome
    # (WhatsApp group, intake form, Athlete Handbook), with a competing intake
    # link, and it got a 0% reply rate. It was removed for good reason. What
    # goes out now is different in the way that mattered: it duplicates none of
    # the logistics Fitr already covers, it is one question rather than a list,
    # and a coach reads it and presses send. A person getting in touch in
    # someone's first week is not the same thing as a second automated welcome.
    welcomed = 0
    if fitr is not None and room_id_by_name:
        for name, _fid, _prog in to_onboard:
            if bespoke_names and name in bespoke_names:
                continue
            room_id = room_id_by_name.get(name)
            if not room_id:
                continue
            first = name.split()[0] if name else ""
            if not first:
                continue
            msg = (
                f"Hey {first}, welcome to JST. Good to have you with us. "
                f"Before you get stuck in, what are you hoping to get out of "
                f"the next few months?"
            )
            try:
                _deliver(fitr, room_id, msg, name, "onboarding_welcome")
                welcomed += 1
            except FitrError as exc:
                print(f"  ! Welcome draft failed for {name}: {exc}")
    if welcomed:
        print(f"Welcome messages: {_sent_or_drafted(welcomed)}")

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

        # Written by the athlete, on or after the day we asked. Authorship comes
        # from author.full_name and the date from a Unix timestamp; the old
        # is_mine / string-slice pair never matched a single message.
        _send_on = _parse_date(send_date)
        replies = [
            str(m.get("text", "")).strip()
            for m in messages
            if message_is_from(m, nm)
            and (message_date(m) or dt.date.min) >= (_send_on or dt.date.min)
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


# --------------------------------------------------------- weekly digest writer
def generate_weekly_athlete_digests(sheets, athletes, pr_records, rec_by_name, data_by_name_all):
    """Generate AI coaching insights for all athletes and prepend to Coaching Notes.

    Runs on Monday or on first-ever deployment (no existing digests).
    Guards per-athlete: skips if a weekly_digest note already exists since the current
    Monday (Mon-Sun window), so re-runs on the same day are safe.
    Returns count of athletes whose digest was written.
    """
    import re as _re
    import datetime as _dtw

    this_monday = TODAY - _dtw.timedelta(days=TODAY.weekday())
    is_monday = TODAY.weekday() == 0
    fourteen_days_ago = TODAY - _dtw.timedelta(days=14)
    digest_pattern = _re.compile(r'\[(\d{4}-\d{2}-\d{2}) — weekly_digest\]')

    pr_by_name = {}
    for r in pr_records:
        nm = str(r.get("Athlete Name", "")).strip()
        d_str = str(r.get("Date", "")).strip()
        bench = str(r.get("Benchmark Name", "")).strip()
        val = str(r.get("Value", "")).strip()
        prev_val = str(r.get("Previous Value", "")).strip()
        if nm and d_str and bench:
            pr_by_name.setdefault(nm, []).append((d_str, bench, val, prev_val))

    note_lines = {}
    for a in athletes:
        nm = a["name"]
        profile = data_by_name_all.get(nm, {})
        existing_notes = str(profile.get("Coaching Notes", "")).strip()

        has_any_digest = bool(digest_pattern.search(existing_notes))
        if not is_monday and has_any_digest:
            continue  # only run on Mondays (or first-ever)

        recent_digest_dates = [
            _dtw.date.fromisoformat(m.group(1))
            for m in digest_pattern.finditer(existing_notes)
            if _dtw.date.fromisoformat(m.group(1)) >= this_monday
        ]
        if recent_digest_dates:
            continue  # already generated this week

        recent_prs = sorted(
            [(d, b, v, pv) for d, b, v, pv in pr_by_name.get(nm, [])
             if d >= fourteen_days_ago.isoformat()],
            reverse=True,
        )
        pr_lines = [
            f"{d}: {b} — {v}" + (f" (prev: {pv})" if pv else "")
            for d, b, v, pv in recent_prs
        ]

        rec_row = rec_by_name.get(nm, {})
        rec_lines = []
        if rec_row:
            submitted = str(rec_row.get("Submitted At", "")).strip()
            sor = str(rec_row.get("Soreness", "")).strip()
            st_ = str(rec_row.get("Stress", "")).strip()
            mo = str(rec_row.get("Motivation", "")).strip()
            if submitted:
                try:
                    s_f, st_f, mo_f = float(sor), float(st_), float(mo)
                    score = (10 - s_f + 10 - st_f + mo_f) / 3
                    rec_lines.append(
                        f"{submitted[:10]}: Soreness {sor}, Stress {st_}, Motivation {mo} "
                        f"(composite: {score:.1f}/10)"
                    )
                except (ValueError, TypeError):
                    pass

        if not pr_lines and not rec_lines:
            continue  # no data to digest

        goal = str(profile.get("North Star Goal", "")).strip()
        programme = str(profile.get("Programme", "")).strip()

        insight = summariser.weekly_athlete_insight(nm, pr_lines, rec_lines, goal, programme)
        if insight:
            note_lines[nm] = f"[{TODAY.isoformat()} — weekly_digest] {insight}"

    if note_lines and not config.DRY_RUN:
        append_coaching_notes(sheets, note_lines)
    elif note_lines and config.DRY_RUN:
        for nm, line in note_lines.items():
            print(f"[DRY_RUN] Weekly digest for {nm}: {line[:100]}…")

    return len(note_lines)


# --------------------------------------------------------------------- driver

# Stages that failed this run. Reported at the end, on Slack and in the Health
# Log, and they set the exit code so the workflow still goes red.
STAGE_FAILURES = []


@contextlib.contextmanager
def stage(name):
    """Run one pipeline stage. A failure costs that stage and nothing else.

    On 5 August a single 429 from Sheets landed inside auto-onboard, two thirds
    of the way through main(), and ended the process. The anniversaries, the
    new athlete welcomes, the competition messages, the monthly check-in, the
    message log and the Sync Log row were all skipped, and the only trace was a
    traceback in a workflow log nobody reads. The stages do not depend on each
    other; only the process did.

    Anything that outlives its stage is given a default before the first one
    runs, so a failure here cannot surface as a NameError further down.
    """
    try:
        yield
    except Exception as exc:
        STAGE_FAILURES.append((name, f"{type(exc).__name__}: {exc}"[:300]))
        print(f"  !! Stage '{name}' failed. Carrying on with the rest of the run.")
        traceback.print_exc()


def report_stage_failures(sheets):
    """Put a mid-run failure where a coach will actually see it.

    The digest has already gone out by the time these stages run, so this is a
    separate Slack message plus a Health Log row. The Health Log is the leg
    that cannot fail quietly: Slack and email have each gone silent on their
    own, but the dashboard reads the sheet.
    """
    findings = [
        health_check.Finding(
            health_check.FAIL, "sync",
            f"The '{name}' stage of today's sync failed and was skipped", detail)
        for name, detail in STAGE_FAILURES
    ]
    try:
        health_check.write_health_log(sheets, findings)
    except Exception as exc:
        print(f"  ! Could not record the stage failures: {exc}")
    body = "\n".join(f"  • *{name}* — {detail}" for name, detail in STAGE_FAILURES)
    try:
        notifier.send_slack(
            f"🛑 *The daily sync finished with {len(STAGE_FAILURES)} failed stage(s).*\n"
            f"{body}\n"
            f"Everything else ran. These stages did not, so anything they would "
            f"have drafted or written is missing for today."
        )
    except Exception as exc:
        print(f"  ! Could not post the stage failures to Slack: {exc}")


def main():
    if not config.SHEET_ID:
        raise RuntimeError("Missing required env var: SHEET_ID")
    print(f"== Fitr sync {TODAY} (lookback {config.LOOKBACK_DAYS}d, dry_run={config.DRY_RUN}) ==")
    fitr = FitrClient()
    fitr.authenticate()
    print("Fitr: authenticated")
    sheets = SheetsClient()
    print("Sheets: connected")

    # Load the tone-of-voice document before anything generates a message.
    # It lives in the Sheet rather than the repo, so this has to happen at
    # runtime; without it every generated message falls back to the built-in
    # summary, which is a fraction of the real rules.
    if coaching_voice.refresh_from_sheet(sheets):
        print("Tone of voice: loaded from Sheet")

    athletes = load_athletes(sheets)
    print(f"Athletes with Fitr IDs: {len(athletes)}")

    if config.TEST_ATHLETES:
        athletes = [a for a in athletes if a["name"] in config.TEST_ATHLETES]
        print(f"TEST_ATHLETES filter active — restricted to: {[a['name'] for a in athletes]}")

    existing_keys, email_by_name, prev_lookup = load_existing_prlog(sheets)

    if config.TEST_ATHLETES:
        email_by_name = {k: v for k, v in email_by_name.items() if k in config.TEST_ATHLETES}

    bench_rows, bench_notes, scraped_rows = collect_benchmarks(
        fitr, athletes, existing_keys, email_by_name, prev_lookup
    )
    print(f"New benchmark results: {len(bench_rows)}")

    chal_rows = collect_challenges(fitr, existing_keys)
    print(f"New challenge scores: {len(chal_rows)}")
    refresh_challenge_measures(fitr, sheets)

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
    rec_history_by_name = {}
    if rec_latest:
        email_to_name = {v.lower(): k for k, v in email_by_name.items()}
        for email, row in rec_latest.items():
            nm = email_to_name.get(email.lower())
            if nm:
                rec_by_name[nm] = row
                rstr = recovery.readiness_string(row)
                if rstr:
                    rec_notes[nm] = rstr
        # Full history so the alerts can tell a run of bad weeks from one bad
        # week. Read failure degrades to no trend context, not to no alerts.
        try:
            for email, rows in (recovery.all_by_email(sheets) or {}).items():
                nm = email_to_name.get(email.lower())
                if nm and rows:
                    rec_history_by_name[nm] = rows
        except Exception as exc:
            print(f"  ! Recovery history unavailable, alerts lose trend context: {exc}")
    print(f"Recovery responses merged: {len(rec_notes)}")

    progs_updated = sync_programme_from_recovery(sheets, rec_latest, email_by_name)
    print(f"Programme assignments synced from survey: {progs_updated}")

    comps_updated = sync_competition_from_typeform(sheets, email_by_name)
    print(f"Competition dates synced from Typeform: {comps_updated}")

    archetypes_imported, archetype_new_reads = sync_archetype_from_typeform(sheets, email_by_name)
    if archetypes_imported:
        print(f"Archetype self-assessments imported from Typeform: {archetypes_imported}")

    intake_updated = sync_intake_from_typeform(sheets, email_by_name)
    if intake_updated:
        print(f"Athlete profiles updated from intake form: {intake_updated}")

    videos_imported, videos_unresolved = sync_video_analysis(sheets, email_by_name)
    if videos_imported:
        print(f"Movement analysis videos imported: {videos_imported}")

    stories_imported, stories_unresolved = sync_story_forms(sheets, email_by_name)
    if stories_imported:
        print(f"Story submissions imported and tagged: {stories_imported}")

    # A scenario with no Playbook rows injects nothing and says nothing, so the
    # message still sends, just unguided. Name them so they get written.
    _pb_gaps = coaching_voice.playbook_gaps(sheets)
    if _pb_gaps:
        print(f"  ! Coaching Playbook has no rows for: {', '.join(_pb_gaps)}")

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
    # Programming Tier, not Subscription Plan: that column holds the billing
    # plan (Monthly (£54.99), Yearly (£549)), so keying bespoke off it matched
    # nobody and every individually-coached athlete was still being sent the
    # automated messages they are meant to be exempt from.
    bespoke_names = {
        nm for nm, r in data_by_name_all.items()
        if str(r.get("Programming Tier", "")).strip().lower() == "bespoke"
    }
    if bespoke_names:
        print(f"Bespoke athletes (automated messages suppressed): {len(bespoke_names)}")

    # ---- declining results: collected daily for the coach digest ----
    # Athlete-facing congrats moved to a weekly Monday roundup (built further
    # down from the past week's PR Log), but coaches still hear about a result
    # coming in WORSE the same day — that's actionable now, not next Monday.
    declining_singles = []  # (name, bench, value, prev)
    if bench_rows and not config.DRY_RUN:
        for row in bench_rows:
            if len(row) < 5:
                continue
            name = row[1]
            if name in bespoke_names:
                continue
            bench = row[3]
            value = row[4]
            prev = row[6] if len(row) > 6 else ""
            if prev and prev not in ("", "first entry"):
                if analytics.compare_result(bench, prev, value) == "declined":
                    declining_singles.append((name, bench, value, prev))
        if declining_singles:
            print(f"Declining results flagged to coaches (not messaged to athlete): {len(declining_singles)}")

    # ---- writes ----
    # Row 9 is the athlete's own note on the result. The header only named the
    # first eight for a long time, so 396 athlete comments sat in the sheet
    # unreadable by name — read_records skips blank headers.
    try:
        _pr_repaired = sheets.ensure_headers(
            config.TAB_PR_LOG,
            ["Date", "Athlete Name", "Email", "Benchmark Name", "Value",
             "Type", "Previous Value", "Days Since Last", "Athlete Note"],
        )
        if _pr_repaired:
            print(f"Repaired stale '{config.TAB_PR_LOG}' header (was: {_pr_repaired})")
    except Exception as exc:
        print(f"  ! Could not check the PR Log header: {exc}")

    sheets.append_rows(config.TAB_PR_LOG, bench_rows + chal_rows)

    # ---- draft replies for pending athlete messages ----
    coach_channel_map = sheets.load_coaches()
    if pending_reply_candidates and not config.DRY_RUN:
        drafted = []  # (name, waiting_since_date)
        for nm, (room_id, thread_text, msg_date) in pending_reply_candidates.items():
            profile = data_by_name_all.get(nm, {})
            draft = summariser.draft_reply(
                nm, thread_text, profile_data=profile,
                playbook=coaching_voice.playbook_prompt(sheets),
            )
            if draft:
                sheets.write_draft_reply(nm, room_id, draft)
                drafted.append((nm, msg_date))
        if drafted:
            print(f"Reply drafts generated: {len(drafted)}")
            notifier.send_draft_reply_alerts(
                drafted, programme_by_name=programme_by_name,
                coach_channel_map=coach_channel_map,
            )

    # ---- per-coach Slack notifications ----
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

    # Cancelled athletes (CRM Exit Autopsy) are excluded from all engagement
    # flags and athlete-facing messages — someone who consciously cancelled
    # shouldn't be counted as "at risk" or get a check-in DM months later.
    # Anyone in Exit Autopsy who has logged training well after their cancel
    # date has evidently come back and is NOT excluded.
    exit_rows = sheets.load_exit_autopsy()
    cancelled_names_lower, rejoined_names = analytics.cancelled_athletes(exit_rows, pr_records)

    # "Genuinely gone" for message exclusions. Broader and safer than the raw
    # Exit Autopsy: adds Auto-Cancelled Fitr statuses the CRM never got (Dave),
    # matches names hyphen-proof (Pat Campbell-Jenner), and is overridden by the
    # Active Roster so a still-active "Cancelled by client" being saved (Jimmy,
    # Gavin) keeps getting messaged. Same helper the dashboard uses, so they
    # can't drift.
    try:
        _active_roster = [str(r.get("Full Name", "")).strip()
                          for r in sheets.read_records("Active Roster")
                          if str(r.get("Full Name", "")).strip()]
    except Exception:
        _active_roster = []
    # Coach overrides set in the dashboard beat both the roster and the CRM, so
    # marking someone cancelled there actually stops their automated messages.
    try:
        _status_overrides = {
            str(r.get("Name", "")).strip(): str(r.get("Status", "")).strip().lower()
            for r in sheets.read_records("Athlete Status Overrides")
            if str(r.get("Name", "")).strip() and str(r.get("Status", "")).strip()
        }
    except Exception:
        _status_overrides = {}
    _gone_norm = analytics.not_current_client_names(
        cancelled_names_lower, data_recs, _active_roster, overrides=_status_overrides)

    def _is_gone(nm):
        return analytics.normalise_client_name(nm) in _gone_norm

    active_athletes = [a for a in athletes if not _is_gone(a["name"])]
    if _gone_norm:
        print(f"Gone athletes excluded from flags/messages "
              f"(Exit Autopsy + Auto-Cancelled, minus Active Roster): "
              f"{len(athletes) - len(active_athletes)} of {len(athletes)} on roster")
    if rejoined_names:
        print(f"  ! In Exit Autopsy but training again — NOT excluded, update the CRM: "
              f"{', '.join(rejoined_names)}")

    trend_results = analytics.trend_analysis(pr_records)
    # Athletes contacted in this sync run count as recently reached — don't flag them
    last_contact_by_name = {name: TODAY for name in chat_notes}
    # ---- real training adherence from Fitr ----
    # The PR Log only records benchmark retests, so on its own it cannot answer
    # "is this athlete still training". This is the coach client list Fitr
    # itself shows: per day, whether they ticked off a full session, part of
    # one, or nothing.
    activity_by_name = {}
    try:
        _activity_items = fitr.client_activity(period=TRAINING_WINDOW)
        activity_by_name = analytics.training_activity(_activity_items)
        _trained = sum(1 for a in activity_by_name.values() if a["sessions"])
        print(f"Training adherence pulled from Fitr: {len(activity_by_name)} athletes, "
              f"{_trained} trained in the last {config.TRAINING_WINDOW_DAYS} days")
    except FitrError as exc:
        print(f"  ! Could not pull training adherence: {exc}")
    except Exception as exc:
        print(f"  ! Training adherence failed: {type(exc).__name__}: {exc}")

    if activity_by_name:
        _written = write_training_activity(
            sheets, activity_by_name, known_names=data_by_name_all.keys())
        print(f"Training summary written to _DATA: {_written} athletes")

    engagement_results = analytics.engagement_check(
        pr_records, active_athletes,
        threshold_days=config.ENGAGEMENT_THRESHOLD_DAYS,
        last_contact_by_name=last_contact_by_name,
        activity_by_name=activity_by_name,
    )
    milestones = analytics.milestone_detection(bench_rows)
    consistency_wins = analytics.consistency_check(pr_records, active_athletes)
    streak_hits = analytics.daily_streak_check(pr_records, active_athletes)
    rec_alert_rows = analytics.recovery_alerts(
        rec_by_name, history_by_name=rec_history_by_name)

    # ---- weekly-send guard: run the Monday sends at most once per week ----
    # If the sync runs more than once on a Monday (manual trigger + a delayed
    # scheduled run, or a rare double cron), the PB roundup / progress emails /
    # offboarding must not fire twice. Mark the week done up front so any later
    # run today sees it and skips.
    # Checked here, marked done at the END of the weekly block (after the
    # progress emails) so a mid-run crash doesn't skip the week permanently.
    _this_monday = TODAY - dt.timedelta(days=TODAY.weekday())
    _do_weekly = TODAY.weekday() == 0 and not config.DRY_RUN
    if _do_weekly and sheets.weekly_send_done(_this_monday.isoformat()):
        _do_weekly = False
        print(f"Weekly sends already ran for week of {_this_monday.isoformat()} — skipping (guard).")

    # ---- weekly PB roundup (Mondays): wins from the past 7 days ----
    # PB congrats are weekly, not per-result: on Monday each athlete gets one
    # roundup DM covering the whole week. Streak milestones stay on their exact
    # day (they're milestones, like anniversaries) — on a Monday they ride
    # along in the roundup; any other day they send standalone.
    _wins_by_name = {}  # name -> list of (kind, bench, value, prev)
    if _do_weekly:
        _roundup_window_start = TODAY - dt.timedelta(days=7)
        _latest_result = {}  # (name, bench) -> (date, value, prev) — best-dated row per benchmark
        for _rec in pr_records:
            _d = _parse_date(str(_rec.get("Date", "")))
            if not _d or _d <= _roundup_window_start:
                continue
            _nm = str(_rec.get("Athlete Name", "")).strip()
            _b  = str(_rec.get("Benchmark Name", "")).strip()
            _v  = str(_rec.get("Value", "")).strip()
            _p  = str(_rec.get("Previous Value", "")).strip()
            if not _nm or not _b or not _v or _nm in bespoke_names:
                continue
            if _is_gone(_nm):
                continue
            if not config.is_achievement_benchmark(_b):
                continue  # heart rate, bodyweight, food/macros, steps — data, not a PB
            if not room_id_by_name.get(_nm):
                continue
            _key = (_nm, _b)
            if _key not in _latest_result or _d > _latest_result[_key][0]:
                _latest_result[_key] = (_d, _v, _p)
        for (_nm, _b), (_d, _v, _p) in _latest_result.items():
            _goal = goals_by_name.get(_nm, "")
            if _goal and _goal_achieved(_goal, _b, _v):
                _wins_by_name.setdefault(_nm, []).append(("goal", _b, _v, _p))
            elif _p and _p != "first entry":
                if analytics.compare_result(_b, _p, _v) == "improved":
                    _wins_by_name.setdefault(_nm, []).append(("pb", _b, _v, _p))
            else:
                _wins_by_name.setdefault(_nm, []).append(("first", _b, _v, _p))

    # Fallbacks only — used if the Claude message generator is unavailable.
    # No em dashes; each ends with a genuine question.
    _streak_msgs = {
        7:  "{first}, 7 days on the bounce. Fair play. What's making it easier to show up?",
        14: "{first}, two weeks straight, no misses. What's driving it at the moment?",
        21: "{first}, 21 days in a row. Proper run that. How's the body feeling?",
        30: "{first}, 30 days straight. A full month. What's changed for you?",
        60: "{first}, 60 days in a row. Serious consistency. Still enjoying it?",
        90: "{first}, 90 days straight. Not many people manage that. What's it taught you?",
    }
    _pending_streaks = {}  # name -> streak_days, guards already applied
    if streak_hits and not config.DRY_RUN:
        for nm, streak_days in streak_hits:
            if nm in bespoke_names or streak_days not in _streak_msgs:
                continue
            note_key = f"streak_{streak_days}d"
            if note_key in str(data_by_name_all.get(nm, {}).get("Coaching Notes", "")):
                continue  # already sent this milestone
            if not room_id_by_name.get(nm):
                continue
            _pending_streaks[nm] = streak_days
    elif streak_hits and config.DRY_RUN:
        for nm, sd in streak_hits:
            print(f"[DRY_RUN] Streak {sd}d milestone for {nm}")

    _streak_notes = {}
    _streak_sent = 0

    def _tidy_bench(b):
        # Full humanising, not just dropping the dash: these names go straight
        # into a congratulations message an athlete reads.
        return analytics.humanise_benchmark(b)

    if _wins_by_name:
        congrats_sent = 0
        for name, wins in _wins_by_name.items():
            room_id = room_id_by_name.get(name)
            first = name.split()[0]
            goal_wins = [w for w in wins if w[0] == "goal"]
            pb_wins = [w for w in wins if w[0] == "pb"]
            first_wins = [w for w in wins if w[0] == "first"]

            # Build a plain-English situation for the Claude generator, plus a
            # safe tone-compliant fallback used if the API is unavailable.
            if goal_wins:
                _, bench, value, _ = goal_wins[0]
                situation = f'Goal hit, {first} reached their goal on "{bench}" at {value}.'
                fallback = f"{first}, {_tidy_bench(bench)} at {value}. That's the goal you set. How does it feel?"
            elif len(wins) == 1 and pb_wins:
                _, bench, value, prev = pb_wins[0]
                direction = "down to" if analytics.is_lower_better(bench) else "up to"
                situation = f'PB, {first} improved "{bench}" from {prev} to {value}.'
                fallback = f"{first}, {_tidy_bench(bench)} {direction} {value} from {prev}. What's clicked for you there?"
            elif len(wins) == 1 and first_wins:
                _, bench, value, _ = first_wins[0]
                situation = f'First result, {first} logged "{bench}" at {value}.'
                fallback = f"{first}, first {_tidy_bench(bench)} number in at {value}. How did it feel?"
            elif pb_wins:
                items = "; ".join(f'"{b}" {v} (from {p})' for _, b, v, p in pb_wins[:4])
                situation = f"Multiple PBs this week, {first} improved: {items}."
                fb_items = ", ".join(f"{_tidy_bench(b)} to {v}" for _, b, v, _ in pb_wins[:4])
                fallback = f"{first}, good week. {fb_items}. What's been working?"
            else:
                items = "; ".join(f'"{b}" {v}' for _, b, v, _ in wins[:4])
                situation = f"First results logged, {first}: {items}."
                fb_items = ", ".join(f"{_tidy_bench(b)}: {v}" for _, b, v, _ in wins[:4])
                fallback = (f"{first}, first results logged: {fb_items}. Good to look back on. "
                            f"Give us a shout if you want any pointers.")

            _ride_along_streak = _pending_streaks.get(name)
            if _ride_along_streak:
                situation += f" Also on a {_ride_along_streak}-day training streak this week."
                fallback += f" And {_ride_along_streak} days on the bounce, keep it going."

            # first_result and new_pr are different conversations: one is "here's
            # your baseline", the other "you beat your baseline".
            _pb_scenario = "first_result" if situation.startswith("First results") else "new_pr"
            msg = summariser.athlete_message(
                situation, fallback,
                playbook=coaching_voice.playbook_prompt(sheets, _pb_scenario),
            )

            try:
                _deliver(fitr, room_id, msg, name, "congrats")
                congrats_sent += 1
                messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": name,
                    "Message Type": "congrats", "Room ID": room_id})
                if _ride_along_streak:
                    note_key = f"streak_{_ride_along_streak}d"
                    _streak_notes[name] = f"[{TODAY.isoformat()} — {note_key}]"
                    _streak_sent += 1
                    messages_sent_log.append({
                        "Date": TODAY.isoformat(), "Athlete Name": name,
                        "Message Type": note_key, "Room ID": room_id,
                    })
                    del _pending_streaks[name]
                import time as _time; _time.sleep(0.5)
            except FitrError as exc:
                print(f"  ! Congrats message failed for {name}: {exc}")
        print(f"Congratulations messages: {_sent_or_drafted(congrats_sent)}")

    # Standalone streak messages — athletes with a milestone but no PBs this run
    for nm, streak_days in _pending_streaks.items():
        room_id = room_id_by_name.get(nm)
        first = nm.split()[0]
        note_key = f"streak_{streak_days}d"
        _streak_situation = f"Streak, {first} has logged {streak_days} training days in a row."
        msg = summariser.athlete_message(
            _streak_situation, _streak_msgs[streak_days].format(first=first),
            playbook=coaching_voice.playbook_prompt(sheets, "consistency"))
        try:
            _deliver(fitr, room_id, msg, nm, note_key)
            _streak_notes[nm] = f"[{TODAY.isoformat()} — {note_key}]"
            _streak_sent += 1
            messages_sent_log.append({
                "Date": TODAY.isoformat(), "Athlete Name": nm,
                    "Message Type": note_key, "Room ID": room_id,
            })
            import time as _time; _time.sleep(0.5)
        except FitrError as exc:
            print(f"  ! Streak message failed for {nm}: {exc}")
    if _streak_notes:
        append_coaching_notes(sheets, _streak_notes)
    if _streak_sent:
        print(f"Streak milestone messages: {_sent_or_drafted(_streak_sent)}")

    # ---- archetype delivery is now coach-sent from the dashboard ----
    # The Typeform shows the athlete a provisional result instantly; the coach
    # then sends the confirmed, engine-scored read from the dashboard's
    # "Archetype results to send" list, so a person signs it off. The sync no
    # longer auto-DMs it. Scoring still happens here (sync_archetype_from_typeform).

    # ---- weekly AI coaching digest per athlete ----
    digests_written = generate_weekly_athlete_digests(
        sheets, athletes, pr_records, rec_by_name, data_by_name_all
    )
    if digests_written:
        print(f"Weekly athlete coaching digests written: {digests_written}")

    flagged_count = sum(1 for e in engagement_results if e["flag"])
    concern_count = sum(
        1 for signals in trend_results.values()
        for s in signals if s["trend"] == "declining" or s["peak_drop_flag"]
    )
    print(f"Engagement flags: {flagged_count}  |  Performance concerns: {concern_count}")
    print(f"Milestones: {len(milestones)}  |  Consistency streaks: {len(consistency_wins)}")

    # ---- grandslam scores — update Journey Stage + Status Label in _DATA ----
    grandslam_results = analytics.grandslam_score(athletes, pr_records, data_recs)
    if not config.DRY_RUN and grandslam_results:
        stage_updates = {
            r["name"]: {"Journey Stage": r["journey_stage"], "Status Label": r["status_label"]}
            for r in grandslam_results
        }
        try:
            sheets.batch_update_by_name(config.TAB_DATA, "Full Name", stage_updates)
            print(f"Grandslam stages updated in _DATA: {len(stage_updates)}")
        except Exception as exc:
            print(f"  ! Failed to write grandslam stages to _DATA: {exc}")

    alert_rows = analytics.build_coach_alerts_rows(
        engagement_results, trend_results, rec_by_name, milestones, consistency_wins
    )
    # raw=True: the "== SECTION ==" headings are text, not formulas.
    sheets.overwrite_tab(config.TAB_COACH_ALERTS, alert_rows, raw=True)

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

    # ---- off-boarding: final check-in around 60d inactive + critical risk ----
    # Weekly (Mondays), catching anyone who crossed 60 days in the past week.
    # The 60-66 window spans exactly one Monday per athlete, so it still fires
    # once — and unlike the old exact-day-60 gate, a failed sync on their
    # day 60 no longer means they're skipped forever. Guarded by _do_weekly.
    if _do_weekly:
        offboarding_sent = 0
        for snap in churn_snapshot_rows:
            nm = snap["Athlete Name"]
            if nm in bespoke_names:
                continue
            if snap["Score"] < 60:
                continue
            e = next((x for x in engagement_results if x["name"] == nm), {})
            _days_inactive = e.get("days_since")
            if not isinstance(_days_inactive, int) or not (60 <= _days_inactive <= 66):
                continue
            room_id = room_id_by_name.get(nm)
            if not room_id:
                continue
            first = nm.split()[0]
            _off_situation = (f"Re-engagement, {first} has not logged any training in about "
                              f"{_days_inactive} days. Gentle, low-pressure check-in.")
            _off_fallback = (f"{first}, haven't seen you in a while, no pressure, just checking in. "
                             f"Everything alright?")
            msg = summariser.athlete_message(
                _off_situation, _off_fallback,
                playbook=coaching_voice.playbook_prompt(sheets, "inactive_60d"))
            try:
                _deliver(fitr, room_id, msg, nm, "offboarding")
                offboarding_sent += 1
                messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": nm,
                    "Message Type": "offboarding", "Room ID": room_id})
                import time as _time; _time.sleep(0.5)
            except FitrError as exc:
                print(f"  ! Off-boarding message failed for {nm}: {exc}")
        if offboarding_sent:
            print(f"Off-boarding check-in messages: {_sent_or_drafted(offboarding_sent)}")

    # ---- referral acknowledgements ----
    _referrals = sheets.load_referrals()
    if _referrals:
        sheets.ensure_referral_columns()

        # name → full name lookup for referred athletes
        _athlete_name_lower = {a["name"].lower(): a["name"] for a in athletes}

        # most recent log date per athlete (activity check for conversion)
        _pr_dates_ref = {}
        for _rec in pr_records:
            _nm = str(_rec.get("Athlete Name", "")).strip()
            _d  = _parse_date(str(_rec.get("Date", "")))
            if _nm and _d:
                _pr_dates_ref.setdefault(_nm, []).append(_d)

        # coach name → slack channel (for coach referrers)
        _coach_name_map_ref = sheets.load_coach_names()  # {programme: coach_name}
        _coach_slack = {}  # {coach_name: channel}
        for _prog, _channel in coach_channel_map.items():
            _cname = _coach_name_map_ref.get(_prog, "")
            if _cname:
                _coach_slack.setdefault(_cname, _channel)

        _ref_join_sent = 0
        _ref_convert_sent = 0

        for _row_idx, _ref in enumerate(_referrals, start=2):
            _status      = str(_ref.get("Status", "")).strip()
            _referred    = str(_ref.get("Referred Name", "")).strip()
            _referrer    = str(_ref.get("Referrer Name", "")).strip()
            _trial_end_s = str(_ref.get("Trial End", "")).strip()
            _join_ack    = str(_ref.get("Join Ack Sent", "")).strip()
            _conv_ack    = str(_ref.get("Convert Ack Sent", "")).strip()

            if not _referred or not _referrer:
                continue

            _referred_full = _athlete_name_lower.get(_referred.lower())
            _referrer_first = _referrer.split()[0]

            def _send_referral_msg(msg):
                """Try Fitr DM first; fall back to coach Slack channel."""
                _room = room_id_by_name.get(_referrer)
                if _room:
                    _deliver(fitr, _room, msg, _referrer, "referral")
                    return True
                _ch = _coach_slack.get(_referrer)
                if _ch:
                    notifier.send_slack_message(_ch, msg)
                    return True
                return False

            # Phase 1 — detect join: referred athlete has logged at least once
            if _status == "Pending" and not _join_ack:
                if _referred_full and _pr_dates_ref.get(_referred_full):
                    _referred_first = _referred_full.split()[0]
                    _msg = f"{_referrer_first}, {_referred_first} just joined. Nice one, thanks for the introduction. How do you know them?"
                    if not config.DRY_RUN:
                        try:
                            _sent = _send_referral_msg(_msg)
                            if _sent:
                                sheets.update_referral_ack(
                                    _row_idx,
                                    {"Status": "Joined", "Join Ack Sent": TODAY.isoformat()},
                                )
                                _ref_join_sent += 1
                                messages_sent_log.append({
                                    "Date": TODAY.isoformat(), "Athlete Name": _referrer,
                                    "Message Type": "referral_join", "Room ID": room_id_by_name.get(_referrer, ""),
                                })
                                import time as _time; _time.sleep(0.5)
                        except FitrError as _exc:
                            print(f"  ! Referral join message failed for {_referrer}: {_exc}")
                    else:
                        print(f"[DRY_RUN] Referral join ack — {_referred_full} joined, referrer: {_referrer}")

            # Phase 2 — detect conversion: trial end date passed, referred athlete still active
            elif _status == "Joined" and not _conv_ack and _trial_end_s:
                _trial_end = _parse_date(_trial_end_s)
                if _trial_end and TODAY >= _trial_end:
                    _last_logs = _pr_dates_ref.get(_referred_full or "", [])
                    _last_log  = max(_last_logs) if _last_logs else None
                    if _last_log and (TODAY - _last_log).days <= 30:
                        _referred_first = (_referred_full or _referred).split()[0]
                        _msg = (
                            f"{_referrer_first}, {_referred_first}'s stuck around after their trial. Nice one. "
                            f"Your free month's on its way, sorted this week."
                        )
                        if not config.DRY_RUN:
                            try:
                                _sent = _send_referral_msg(_msg)
                                if _sent:
                                    sheets.update_referral_ack(
                                        _row_idx,
                                        {"Status": "Converted", "Convert Ack Sent": TODAY.isoformat()},
                                    )
                                    _ref_convert_sent += 1
                                    messages_sent_log.append({
                                        "Date": TODAY.isoformat(), "Athlete Name": _referrer,
                                        "Message Type": "referral_converted", "Room ID": room_id_by_name.get(_referrer, ""),
                                    })
                                    import time as _time; _time.sleep(0.5)
                            except FitrError as _exc:
                                print(f"  ! Referral convert message failed for {_referrer}: {_exc}")
                        else:
                            print(f"[DRY_RUN] Referral convert ack — {_referred_full} converted, referrer: {_referrer}")

        if _ref_join_sent:
            print(f"Referral join acknowledgements sent: {_ref_join_sent}")
        if _ref_convert_sent:
            print(f"Referral conversion acknowledgements sent: {_ref_convert_sent}")

    # ---- digest notification ----
    print("Sending digest...")
    # ---- health check: run before the digest so failures ride out with it ----
    # Deliberately on the digest a coach already opens rather than in the run
    # log. Every check here exists because the thing it looks for broke and
    # nobody noticed for weeks.
    health_findings = []
    try:
        health_findings = health_check.run_health_check(
            sheets, analytics,
            data_records=data_recs,
            bespoke_names=bespoke_names,
            gone_norm=_gone_norm,
            engagement_results=engagement_results,
            check_pages=config.HEALTH_CHECK_PAGES,
        )
        _fails = [f for f in health_findings if f.severity == health_check.FAIL]
        print(f"Health check: {len(_fails)} broken, "
              f"{len(health_findings) - len(_fails)} worth a look")
        # Recorded to the sheet as well as the digest. Slack and email can both
        # fail quietly; a tab on the dashboard cannot.
        health_check.write_health_log(sheets, health_findings)
        for f in health_findings:
            print(f"  [{f.severity}] {f.line()}")
    except Exception as exc:
        print(f"  ! Health check failed to run: {exc}")

    notifier.send_digest(
        TODAY, engagement_results, trend_results,
        rec_alert_rows, milestones, consistency_wins,
        declining_singles=declining_singles,
        health_findings=health_findings,
    )

    # ---- first-Monday reminder: refresh the Active Roster ----
    # The Active Roster tab is the source of truth for who's a current client,
    # so it keeps cancelled athletes off the dashboard and out of the automated
    # messages. It only stays accurate if it's re-pasted from Fitr, and there's
    # no API to do it automatically. Nudge on the first Monday of the month
    # (weekday 0, day 1-7), on both Slack and the digest email so it's not missed.
    if TODAY.weekday() == 0 and TODAY.day <= 7 and not config.DRY_RUN:
        _roster_reminder = (
            "First Monday of the month: refresh the Active Roster.\n\n"
            "Copy your current active client list from Fitr into the 'Active Roster' "
            "tab of the coaching sheet. That list is what keeps cancelled athletes "
            "off the dashboard and out of the automated messages. It can't update "
            "itself (Fitr has no API for it), so it only stays right if you re-paste "
            "it each month."
        )
        try:
            notifier.send_slack(f"🗓️ *{_roster_reminder}*")
        except Exception as exc:
            print(f"  ! roster reminder (Slack) failed: {exc}")
        try:
            notifier.send_email("Refresh the JST Active Roster (first Monday)", _roster_reminder)
        except Exception as exc:
            print(f"  ! roster reminder (email) failed: {exc}")
        print("First-Monday Active Roster reminder sent")

    # ---- per-coach re-engagement alerts + squad summaries (Mondays only) ----
    # Both are per-coach Slack briefings with no per-athlete dedup — sent daily
    # they'd repeat the same inactive athletes to the same coach every morning
    # (and the squad summary is "weekly" by name). One Monday briefing per coach.
    # The daily digest to the main channel still surfaces inactive athletes daily.
    if coach_channel_map and TODAY.weekday() == 0:
        reeng_sent = notifier.send_reengagement_alerts(
            engagement_results, programme_by_name, coach_channel_map,
        )
        if reeng_sent:
            print(f"Re-engagement alerts sent to {reeng_sent} coach channel(s)")

        from collections import defaultdict
        athletes_by_coach = defaultdict(list)
        for a in active_athletes:
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


    # Defaults for every value that outlives the stage that sets it. Without
    # these, one failed stage turns into a NameError three stages later and
    # the cause is buried.
    onboarded = 0
    first_log_by_name = {}
    competition_rows = []
    sent_comp_keys = set()
    emails_sent = 0
    with stage("auto-onboard new athletes"):
        # ---- auto-onboard new bespoke athletes from chat rooms ----
        onboarded = auto_onboard_new_athletes(
            sheets, rooms, fitr=fitr, room_id_by_name=room_id_by_name,
            messages_sent_log=messages_sent_log, bespoke_names=bespoke_names,
        )
        if onboarded:
            print(f"New bespoke athletes auto-onboarded: {onboarded}")

    with stage("athlete anniversaries"):
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
        summit_flag_names = []
        for a in active_athletes:
            nm = a["name"]
            if nm in bespoke_names:
                continue
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
            # No booking link here any more: the 90-day message offers to send one
            # if they want it rather than pushing a calendar at them.
            _tshirt_url = getattr(config, "TSHIRT_FORM_URL", "")
            if days_training == 90:
                # Jak's copy. Note "your first result", not "you joined us": the
                # milestone counts from their first logged result because Join Date
                # in _DATA can't be trusted (for the median athlete it lands months
                # AFTER their first log, which can't happen). First log is the only
                # date here we can stand behind.
                msg = (
                    f"Hey {first}, that's 90 days since your first result with us. Worth a "
                    f"proper catch up at this point. Not because anything's wrong, but because "
                    f"three months in is when we can actually see what's working and what "
                    f"isn't, and we want to make sure you are on the right track rather than "
                    f"guessing from your numbers.\n\n"
                    f"If you are keen, let us know and we can send you a booking link. If not, "
                    f"that's all good as well.\n\n"
                    f"One final thing is, what's felt like the biggest change since you started?"
                )
            elif days_training == 180:
                msg = (
                    f"Hey {first}, six months with us! The core of our community is down to "
                    f"committed athletes like yourself.\n\n"
                    f"We send a JST tee at this point, so drop your address and size here: "
                    f"{_tshirt_url}\n\n"
                    f"And if you know anyone who'd do really well joining JST, please send them "
                    f"our way. If it's anyone within your gym or any friends, let us know and we "
                    f"can also give you a code for them.\n\n"
                    f"What are you chasing during your next 6 months?"
                )
            elif days_training == 270:
                msg = (
                    f"Hey {first}, that's nine months of results logged with us now. No ask "
                    f"here, we just don't think that kind of consistency should go "
                    f"unmentioned. You're three months out from your first full year, so if "
                    f"there's anything in your training you want us to look at before then, "
                    f"this is a good time to say. How's it all feeling compared to when you "
                    f"started?"
                )
            elif days_training == 365:
                # The athlete is still flagged for the summit list, but the message no
                # longer promises it. "I'll send details when it's confirmed" is a
                # promise the system can't keep: if nothing is booked when this fires,
                # it sits unanswered at the biggest milestone we have. The invite goes
                # out personally once dates exist.
                summit_flag_names.append(nm)
                msg = (
                    f"Hey {first}, one year today since your first result with us. That's a "
                    f"proper milestone and we don't say it lightly. We'd like to do a full "
                    f"year-one review with you, sit down with your coach, go through the "
                    f"whole year's numbers, and set year two up properly. If you're keen, let "
                    f"us know and we'll send a booking link. And so we mark it right: what's "
                    f"the one result from this year you're proudest of?"
                )
            elif days_training == 730:
                msg = (
                    f"Hey {first}, two years with us today. Very few athletes stay this "
                    f"consistent with anything, and honestly, athletes like you are why JST "
                    f"works. Thank you. Going into year three, is there anything about your "
                    f"programming or how we work with you that you'd change? We'd rather hear "
                    f"it from you than guess."
                )
            else:
                # Safety net: only fires if a milestone is added to the table above
                # without copy of its own. Same shape as the rest so a forgotten
                # milestone still sounds like us.
                msg = (
                    f"Hey {first}, that's {milestone_label} of training logged with us. Worth "
                    f"marking. How's it feeling compared to when you started?"
                )
            try:
                _deliver(fitr, room_id, msg, nm, "anniversary")
                anniversaries_sent += 1
                messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": nm,
                        "Message Type": "anniversary", "Room ID": room_id})
                import time as _time; _time.sleep(0.5)
            except FitrError as exc:
                print(f"  ! Anniversary message failed for {nm}: {exc}")
        if anniversaries_sent:
            print(f"Anniversary messages: {_sent_or_drafted(anniversaries_sent)}")
        if summit_flag_names:
            try:
                _lines = "\n".join(f"  • {nm}" for nm in summit_flag_names)
                notifier.send_slack(
                    f"🏔️ *Summit ticket — action needed*\n"
                    f"The following athlete(s) just hit 12 months and have been promised a free summit ticket:\n"
                    f"{_lines}\n"
                    f"Send them the event details and access code when the next summit is confirmed."
                )
                print(f"Summit flag sent to Slack for: {', '.join(summit_flag_names)}")
            except Exception as exc:
                print(f"  ! Summit flag Slack alert failed: {exc}")

    with stage("new athlete onboarding"):
        # ---- new athlete onboarding (first log == today) ----
        onboarding_sent = 0
        for a in active_athletes:
            nm = a["name"]
            if nm in bespoke_names:
                continue
            first_log = first_log_by_name.get(nm)
            if not first_log or first_log != TODAY:
                continue
            room_id = room_id_by_name.get(nm)
            if not room_id or config.DRY_RUN:
                continue
            first = nm.split()[0]
            msg = (
                f"{first}, first log's in. Nice one. Two things to help me coach you well from day one:\n\n"
                f"1. Log every session as soon as you finish, even a quick note. Makes a big difference.\n"
                f"2. Weekly recovery check-in: https://jstcompete.typeform.com/to/Q1tL7MmR. "
                f"2 minutes, helps me manage your load week to week.\n\n"
                f"Message me here anytime."
            )
            try:
                _deliver(fitr, room_id, msg, nm, "onboarding")
                onboarding_sent += 1
                messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": nm,
                        "Message Type": "onboarding", "Room ID": room_id})
                import time as _time; _time.sleep(0.5)
            except FitrError as exc:
                print(f"  ! Onboarding message failed for {nm}: {exc}")
        if onboarding_sent:
            print(f"New athlete onboarding messages: {_sent_or_drafted(onboarding_sent)}")

    with stage("pre-competition messages"):
        # ---- pre-competition automated messages ----
        # A comp milestones: send once on exact day (sync runs daily)
        _COMP_MSG_DAYS = {
            70: ("10 weeks out",
                 "Hey {first}, 10 weeks to {comp} starts {today}. "
                 "Everything from here is pointed at that day. "
                 "Before I lock in your programme, what does a successful performance look like to you at {comp}?"),
            21: ("3 weeks out",
                 "Hey {first}, three weeks to {comp}. "
                 "The preparation is in. This is the sharpening phase now. "
                 "What's your headspace going into the final stretch?"),
            7:  ("race week",
                 "Hey {first}, {comp} is seven days away. "
                 "The work is done. "
                 "How are you feeling going into race week?"),
            1:  ("day before",
                 "Hey {first}, {comp} is tomorrow. "
                 "I've watched what you've put into this prep and you've done the work. "
                 "How are you feeling going into the day?"),
        }
        competition_rows = sheets.load_competitions()
        comp_msgs_sent = 0
        for row in competition_rows:
            nm = str(row.get("Athlete Name", "")).strip()
            if not nm or nm in bespoke_names or _is_gone(nm):
                continue
            comp_nm = str(row.get("Competition Name", "")).strip() or "your competition"
            comp_type = str(row.get("Type", "A")).strip().upper()
            raw_date = str(row.get("Date", "")).strip()
            if not raw_date:
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
                _deliver(fitr, room_id, msg, nm, f"pre_comp_{days_out}d")
                comp_msgs_sent += 1
                messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": nm,
                        "Message Type": f"pre_comp_{days_out}d", "Room ID": room_id})
                import time as _time; _time.sleep(0.5)
            except FitrError as exc:
                print(f"  ! Pre-comp message failed for {nm}: {exc}")
        if comp_msgs_sent:
            print(f"Pre-competition messages: {_sent_or_drafted(comp_msgs_sent)}")

    with stage("comp message dedup"):
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

    with stage("post-competition messages"):
        # ---- post-competition messages (fires the day after each competition) ----
        post_comp_msgs_sent = 0
        for row in competition_rows:
            nm = str(row.get("Athlete Name", "")).strip()
            if not nm or nm in bespoke_names or _is_gone(nm):
                continue
            comp_nm = str(row.get("Competition Name", "")).strip() or "your competition"
            comp_type = str(row.get("Type", "A")).strip().upper()
            raw_date = str(row.get("Date", "")).strip()
            if not raw_date:
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
                    f"{first}, how did {comp_nm} go? When you get a chance:\n\n"
                    f"1. Result / placing\n"
                    f"2. What went well\n"
                    f"3. One thing you'd do differently\n"
                    f"4. How your body's feeling right now"
                )
            else:
                msg = f"{first}, how did {comp_nm} go? What's the result and what are you taking from it?"
            try:
                _deliver(fitr, room_id, msg, nm, msg_type_key)
                post_comp_msgs_sent += 1
                messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": nm,
                        "Message Type": msg_type_key, "Room ID": room_id})
                import time as _time; _time.sleep(0.5)
            except FitrError as exc:
                print(f"  ! Post-comp message failed for {nm}: {exc}")
        if post_comp_msgs_sent:
            print(f"Post-competition messages: {_sent_or_drafted(post_comp_msgs_sent)}")

    with stage("competition result congratulations"):
        # ---- competition result congratulations ----
        # Send once when a result is entered for a recent comp, deduped via sent_comp_keys.
        comp_result_msgs_sent = 0
        for row in competition_rows:
            nm = str(row.get("Athlete Name", "")).strip()
            if not nm or nm in bespoke_names or _is_gone(nm):
                continue
            result = str(row.get("Result", "")).strip()
            comp_nm = str(row.get("Competition Name", "")).strip()
            if not result or not comp_nm:
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
            msg = f"{first}, {result} at {comp_nm}. Nice one. What stood out from the day?"
            try:
                _deliver(fitr, room_id, msg, nm, msg_type_key)
                comp_result_msgs_sent += 1
                messages_sent_log.append({"Date": TODAY.isoformat(), "Athlete Name": nm,
                        "Message Type": msg_type_key, "Room ID": room_id})
                import time as _time; _time.sleep(0.5)
            except FitrError as exc:
                print(f"  ! Comp result message failed for {nm}: {exc}")
        if comp_result_msgs_sent:
            print(f"Competition result messages: {_sent_or_drafted(comp_result_msgs_sent)}")

    with stage("capture post-comp replies"):
        # ---- capture athlete replies to post-comp messages ----
        post_comp_replies = capture_postcomp_responses(fitr, sheets, TODAY)
        if post_comp_replies:
            print(f"Post-competition responses captured: {post_comp_replies}")

    with stage("weekly progress emails"):
        # ---- weekly athlete progress emails (Mondays only) ----
        # The sync runs daily, but this email fired every run — and because
        # "has an upcoming competition" stays true for weeks at a time, athletes
        # with a comp booked were getting the "weekly snapshot" every single day.
        # Gate to Mondays, and widen the PR window to the past 7 days so the
        # snapshot covers the whole week rather than just Monday morning's rows.
        emails_sent = 0  # referenced unconditionally by the Sync Log row below
        if _do_weekly:  # Monday, once per week (see weekly-send guard)
            archetype_rows = sheets.load_archetype_assessments()
            archetype_by_name = {
                str(r.get("Athlete Name", "")).strip(): r
                for r in archetype_rows
                if str(r.get("Athlete Name", "")).strip()
            }
            _week_cutoff = TODAY - dt.timedelta(days=7)
            _weekly_pr_rows = []
            for _rec in pr_records:
                _d = _parse_date(str(_rec.get("Date", "")))
                if not _d or _d < _week_cutoff:
                    continue
                _nm = str(_rec.get("Athlete Name", "")).strip()
                _b  = str(_rec.get("Benchmark Name", "")).strip()
                _v  = str(_rec.get("Value", "")).strip()
                if _nm and _b:
                    # same positional shape as bench_rows: [date, name, email, bench, value]
                    _weekly_pr_rows.append([str(_rec.get("Date", "")), _nm, "", _b, _v])
            # Start from the PR Log emails, then backfill from _DATA for anyone we
            # don't have a valid address for. email_by_name comes from PR Log only,
            # and _DATA often holds a real address where PR Log holds none or held
            # a redacted "Hidden by client" that has now been filtered out. Without
            # this backfill those athletes are silently skipped despite us having
            # their email one tab over.
            _email_map = dict(email_by_name)
            for _rec in data_recs:
                _nm = str(_rec.get("Full Name", "")).strip()
                _em = str(_rec.get("Email", "")).strip()
                if _nm and _nm not in _email_map and _looks_like_email(_em):
                    _email_map[_nm] = _em
            non_bespoke_email_by_name = {
                k: v for k, v in _email_map.items()
                if k not in bespoke_names and not _is_gone(k)
            }
            # On the first Monday of the month, the email carries the month in
            # review instead of the week. Athlete emails now only ever leave on a
            # Monday, alongside the Fitr batch, so there's one moment we speak to
            # them rather than a monthly report landing on whatever weekday the
            # 1st happened to be.
            _month_review = None
            if TODAY.day <= 7:
                _prev_month_end   = TODAY.replace(day=1) - dt.timedelta(days=1)
                _prev_month_start = _prev_month_end.replace(day=1)
                _m_sessions, _m_prs = {}, {}
                for _rec in pr_records:
                    _nm = str(_rec.get("Athlete Name", "")).strip()
                    _ds = str(_rec.get("Date", "")).strip()
                    if not _nm or not (_prev_month_start.isoformat() <= _ds <= _prev_month_end.isoformat()):
                        continue
                    _m_sessions.setdefault(_nm, set()).add(_ds)
                    _b = str(_rec.get("Benchmark Name", "")).strip()
                    _v = str(_rec.get("Value", "")).strip()
                    if _b and _v:
                        _m_prs.setdefault(_nm, []).append((_b, _v))
                _month_review = {
                    "label": _prev_month_end.strftime("%B"),
                    "by_name": {
                        _nm: (len(_dates), _m_prs.get(_nm, []))
                        for _nm, _dates in _m_sessions.items()
                    },
                }

            emails_sent = notifier.send_all_athlete_progress_emails(
                _weekly_pr_rows, consistency_wins, competition_rows, non_bespoke_email_by_name,
                archetype_by_name=archetype_by_name, month_review=_month_review,
            )
            if emails_sent:
                _kind = "month in review" if _month_review else "weekly progress"
                print(f"Athlete {_kind} emails sent: {emails_sent}")

            # All Monday sends done — mark the week so any later run today skips them.
            sheets.mark_weekly_send_done(_this_monday.isoformat())

    with stage("monthly Fitr check-in"):
        # ---- monthly Fitr check-in (fires on the 1st of each month) ----
        # The monthly REPORT EMAIL used to fire here too. It moved into the Monday
        # block above (first Monday of the month) for two reasons: it was the only
        # athlete-facing send with no double-send guard, so a second run on the 1st
        # mailed everyone twice; and landing on the 1st meant it arrived on
        # whatever weekday that was, unhooked from the Monday moment.
        if TODAY.day == 1:
            # Monthly Fitr progress message — short personal check-in for every athlete with a room
            _last_month_end   = TODAY.replace(day=1) - dt.timedelta(days=1)
            _last_month_start = _last_month_end.replace(day=1)
            _month_label      = _last_month_end.strftime("%B")
            # Matches the note this block writes — "[2026-08-01 — monthly_fitr]" —
            # for any day in the current month. The old guard looked for the
            # substring "2026-08 — monthly_fitr", which never appears in that note
            # because the date is written in full, so the once-a-month guard has
            # never actually held. It only stayed hidden because the block runs on
            # day 1 and the sync normally runs once a day; the moment a second run
            # happened on the 1st it queued all 91 monthly messages again.
            _month_guard_re = re.compile(
                rf"\[{TODAY.strftime('%Y-%m')}-\d\d — monthly_fitr\]")

            _month_sessions: dict = {}
            _month_prs: dict = {}
            for _rec in pr_records:
                _nm    = str(_rec.get("Athlete Name", "")).strip()
                _d_str = str(_rec.get("Date", "")).strip()
                if _nm and _last_month_start.isoformat() <= _d_str <= _last_month_end.isoformat():
                    _month_sessions.setdefault(_nm, set()).add(_d_str)
                    _b = str(_rec.get("Benchmark Name", "")).strip()
                    _v = str(_rec.get("Value", "")).strip()
                    # Bodyweight, heart rate and macro logs are data, not results.
                    # Leading a monthly note with "saw you logged 78 kg on the
                    # bodyweight" would be worse than saying nothing.
                    if _b and _v and config.is_achievement_benchmark(_b):
                        # Carry whether it beat their previous, so the message can
                        # lead with a PB rather than whatever happened to be first.
                        _prev = str(_rec.get("Previous Value", "")).strip()
                        _improved = (
                            bool(_prev) and _prev.lower() != "first entry"
                            and analytics.compare_result(_b, _prev, _v) == "improved"
                        )
                        _month_prs.setdefault(_nm, []).append((_b, _v, _improved))

            _data_names_norm = {analytics.normalise_client_name(n) for n in data_by_name_all}
            _mfitr_notes: dict = {}
            _mfitr_sent = 0
            _skipped_not_ours = 0
            for _nm, _sess_dates in _month_sessions.items():
                if _nm in bespoke_names or _is_gone(_nm):
                    continue
                # Must be a JST athlete, i.e. on _DATA. Fitr hands over every chat
                # contact the account has ever had — 1,698 people, most of whom
                # bought a one-off plan years ago — and 19 of the 90 monthly messages
                # drafted this month went to people with no row on the sheet. Those
                # are not ours to send a personal monthly note to, and the guard
                # cannot work for them either: there is no row to record it against.
                #
                # Matched on the normalised name, not the exact one. Fitr spells
                # people differently from the sheet ("Jake foster" against "Jake
                # Foster"), and an exact check would have quietly dropped a real
                # athlete while claiming to filter out strangers.
                if analytics.normalise_client_name(_nm) not in _data_names_norm:
                    _skipped_not_ours += 1
                    continue
                _room = room_id_by_name.get(_nm)
                if not _room:
                    continue
                if _month_guard_re.search(str(data_by_name_all.get(_nm, {}).get("Coaching Notes", ""))):
                    continue
                _prs   = _month_prs.get(_nm, [])
                _first = _nm.split()[0]
                # Lead with one real result and the consistency story. No counts:
                # "you hit 4 results, 3 sessions logged" advertises a thin month and
                # makes the praise ring hollow, and the session figure was wrong
                # anyway — those were days a benchmark got retested, not training
                # sessions. No result worth citing means no message.
                _streak = analytics.months_logging_streak(pr_records, _nm, today=TODAY)
                _msg = analytics.monthly_message(_first, _prs, streak_months=_streak)
                if not _msg:
                    continue
                try:
                    _deliver(fitr, _room, _msg, _nm, "monthly_fitr")
                    _mfitr_notes[_nm] = f"[{TODAY.isoformat()} — monthly_fitr]"
                    _mfitr_sent += 1
                    messages_sent_log.append({
                        "Date": TODAY.isoformat(), "Athlete Name": _nm,
                        "Message Type": "monthly_fitr", "Room ID": _room,
                    })
                    import time as _time; _time.sleep(0.5)
                except FitrError as _exc:
                    print(f"  ! Monthly Fitr message failed for {_nm}: {_exc}")
            if _mfitr_notes:
                append_coaching_notes(sheets, _mfitr_notes)
            if _skipped_not_ours:
                print(f"  Monthly message skipped for {_skipped_not_ours} Fitr contact(s) "
                      f"who are not on the athlete sheet")
            if _mfitr_sent:
                print(f"Monthly Fitr progress messages: {_sent_or_drafted(_mfitr_sent)}")

            # Monthly gym owner credit statements
            try:
                _gym_directory = sheets.load_gym_directory()
                _gym_referrals = sheets.load_gym_referrals()
                if _gym_directory or _gym_referrals:
                    _gym_summaries = analytics.gym_credit_summary(
                        _gym_referrals, _gym_directory
                    )
                    _month_label_gym = _last_month_end.strftime("%B %Y")
                    _gym_emails_sent = notifier.send_gym_owner_credits(
                        _gym_summaries, _month_label_gym
                    )
                    if _gym_emails_sent:
                        print(f"Gym owner credit emails sent: {_gym_emails_sent}")
            except Exception as _gym_err:
                print(f"  ! Gym credit emails failed: {_gym_err}")

    with stage("message log and replies"):
        # ---- log automated messages + check for replies ----
        # _DELIVERED, not messages_sent_log: only what actually reached an athlete
        # belongs in the Message Log. With automatic sending off that is nothing,
        # and the drafts are on the Pending Messages tab waiting for a coach.
        if _DELIVERED:
            sheets.log_messages(_DELIVERED)
            print(f"Automated messages logged: {len(_DELIVERED)}")
        elif messages_sent_log:
            print(f"Messages composed: {len(messages_sent_log)} — none sent "
                  f"(automatic sending is off), so none logged as sent")

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
                sent_on = _parse_date(sent_date)
                try:
                    recent = fitr.chat_messages(room_id, max_messages=10)
                    for cmsg in recent:
                        if not message_is_from(cmsg, pnm):
                            continue
                        if not str(cmsg.get("text", "")).strip():
                            continue
                        msg_on = message_date(cmsg)
                        if msg_on and sent_on and msg_on >= sent_on:
                            sheets.mark_message_replied(
                                pnm, msg_type, sent_date, msg_on.isoformat())
                            replies_found += 1
                            break
                except FitrError:
                    pass
            if replies_found:
                print(f"Athlete replies recorded: {replies_found}")

    with stage("sync log"):
        # ---- sync log ----
        unknown = sorted({n for n in chat_notes} - valid_names)
        # ensure_headers, not get_or_create: get_or_create only writes the header when
        # it creates the tab, so when this row grew from 6 values to 10 the header kept
        # the old names and every reader mislabelled the columns.
        try:
            _repaired = sheets.ensure_headers(
                config.TAB_SYNC_LOG,
                ["Run Date", "Total Athletes", "New PR Log rows", "Challenge scores added",
                 "Conversations summarised", "Recovery merged", "Notes updated",
                 "Athletes auto-onboarded", "Athlete Emails Sent", "Notes"],
            )
            if _repaired:
                print(f"Repaired stale '{config.TAB_SYNC_LOG}' header (was: {_repaired})")
        except Exception as exc:
            # Tidying the header must never cost us the run's log row.
            print(f"  ! Could not check the Sync Log header: {exc}")
        sheets.append_rows(config.TAB_SYNC_LOG, [[
            TODAY.isoformat(), len(athletes), len(bench_rows), len(chal_rows), len(chat_notes),
            len(rec_notes), notes_written, onboarded, emails_sent,
            ("Unknown athletes seen: " + ", ".join(unknown)) if unknown else "ok",
        ]])

    # ---- queue the drafts a coach will send ----
    # Its own stage: a failed Sync Log write must not cost a day of drafts.
    with stage("flush pending drafts"):
        _queued = flush_pending_messages(sheets)
        if _queued:
            print(f"Messages queued as drafts for a coach to send: {_queued} "
                  f"(automatic sending is off)")

    _throttled = sheets_client.throttle_summary()
    if _throttled:
        print(_throttled)

    if STAGE_FAILURES:
        report_stage_failures(sheets)
        print(f"== Done, with {len(STAGE_FAILURES)} failed stage(s) ==")
    else:
        print("== Done ==")


if __name__ == "__main__":
    try:
        main()
    except (FitrError, RuntimeError) as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)
    # A stage that failed is still a failed run: the workflow goes red so the
    # failure is not only visible to whoever opens Slack.
    if STAGE_FAILURES:
        print(f"FATAL: {len(STAGE_FAILURES)} stage(s) failed: "
              f"{', '.join(n for n, _ in STAGE_FAILURES)}", file=sys.stderr)
        sys.exit(1)
