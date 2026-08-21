"""Watch Fitr for athletes waiting on a reply, and draft one into Slack.

The daily sync already does this once a morning, which means someone who asks a
question at nine gets an answer drafted the next day. This runs every half hour
instead, so a coach sees the question and a proposed answer while it still feels
like a conversation.

Deliberately lighter than the sync's chat pass, which summarises every room with
an AI call. This one only looks at who spoke last, and only spends a model call
on rooms where the athlete is actually waiting.

Each athlete message is drafted once. The key is the message's own timestamp, so
a coach who hasn't replied yet doesn't get the same draft again every half hour,
but a new message from the same athlete does get a fresh one.
"""
import datetime as dt

import config
import notifier
import summariser
from fitr_client import FitrClient, FitrError, format_thread
from sheets_client import SheetsClient

TAB = "Reply Watch"
HEADER = ["Athlete Name", "Message Key", "Posted At"]
# Only look at conversations that have moved recently. Anything older was either
# already handled or belongs to the daily sync's wider sweep.
LOOKBACK_HOURS = 12
# A ceiling on model spend per run. If a backlog ever exceeds this, the rest are
# picked up on the next run rather than all at once.
MAX_DRAFTS_PER_RUN = 8


def _seen(sheets):
    try:
        return {str(r.get("Message Key", "")).strip()
                for r in sheets.read_records(TAB) if str(r.get("Message Key", "")).strip()}
    except Exception:
        return set()


def _record(sheets, rows):
    if not rows or config.DRY_RUN:
        return
    sheets.get_or_create(TAB, HEADER)
    sheets.append_rows(TAB, rows)


def main():
    started = dt.datetime.now()
    print(f"== Reply watch {started:%Y-%m-%d %H:%M} ==")
    sheets = SheetsClient()
    fitr = FitrClient()
    fitr.authenticate()

    # The dedupe tab has to exist before it can be batched: values_batch_get
    # fails the whole request if any one range is unknown, which quietly cost
    # the batch its point on every run until the first write created it.
    sheets.get_or_create(TAB, HEADER)

    # One batched request rather than five. This runs 48 times a day, so the
    # per-request latency and the quota slots both matter more here than they
    # do on a once-a-morning job.
    batch = sheets.read_many([
        config.TAB_DATA, config.TAB_PR_LOG, "Active Roster",
        "Athlete Status Overrides", TAB,
    ])
    data = batch.get(config.TAB_DATA) or []
    by_name = {str(r.get("Full Name", "")).strip(): r for r in data if str(r.get("Full Name", "")).strip()}

    # Don't draft replies to people who have left.
    try:
        import analytics
        pr = batch.get(config.TAB_PR_LOG) or []
        cancelled, _ = analytics.cancelled_athletes(sheets.load_exit_autopsy(), pr)
        roster = [str(r.get("Full Name", "")).strip()
                  for r in (batch.get("Active Roster") or [])
                  if str(r.get("Full Name", "")).strip()]
        overrides = {str(r.get("Name", "")).strip(): str(r.get("Status", "")).strip().lower()
                     for r in (batch.get("Athlete Status Overrides") or [])
                     if str(r.get("Name", "")).strip()}
        gone = analytics.not_current_client_names(cancelled, data, roster, overrides=overrides)
        _norm = analytics.normalise_client_name
    except Exception as exc:
        print(f"  ! cancellation filter unavailable ({exc}); continuing without it")
        gone, _norm = set(), lambda x: x

    cutoff = dt.datetime.now() - dt.timedelta(hours=LOOKBACK_HOURS)
    try:
        rooms = fitr.chat_rooms()
    except FitrError as exc:
        print(f"  ! could not read chat rooms: {exc}")
        return

    candidates = []
    for room in rooms:
        if room.get("chat_room_type") != "individual":
            continue
        opp = room.get("opponent") or {}
        name = (opp.get("full_name") or opp.get("name") or "").strip()
        if not name or _norm(name) in gone:
            continue
        msg_date = room.get("last_message_date")
        if msg_date and hasattr(msg_date, "year"):
            if dt.datetime.combine(msg_date, dt.time()) < cutoff - dt.timedelta(hours=12):
                continue
        candidates.append((room["id"], name, msg_date))

    seen = {str(r.get("Message Key", "")).strip()
            for r in (batch.get(TAB) or []) if str(r.get("Message Key", "")).strip()}
    knowledge = ""
    try:
        import coach_knowledge
        knowledge = coach_knowledge.load()
        print(f"  {coach_knowledge.summary()}")
    except Exception as exc:
        print(f"  ! coaching knowledge unavailable: {exc}")

    import coaching_voice
    try:
        coaching_voice.refresh_from_sheet(sheets)
        playbook = coaching_voice.playbook_prompt(sheets)
    except Exception:
        playbook = ""

    review, new_rows, checked = [], [], 0
    for room_id, name, msg_date in candidates:
        if len(review) >= MAX_DRAFTS_PER_RUN:
            print(f"  reached the {MAX_DRAFTS_PER_RUN}-draft ceiling; the rest wait for the next run")
            break
        try:
            messages = fitr.chat_messages(room_id, max_messages=40)
        except FitrError:
            continue
        checked += 1
        if not messages:
            continue
        last = messages[0]
        author = (last.get("author") or {}).get("full_name", "").strip()
        if author.lower() != name.lower():
            continue  # we spoke last, nothing owed
        key = f"{name}|{last.get('created_at') or last.get('id') or ''}"
        if key in seen:
            continue  # already drafted for this exact message

        thread = format_thread(messages)
        draft, why = summariser.draft_reply(
            name, thread, profile_data=by_name.get(name, {}),
            playbook=playbook, knowledge=knowledge, with_reason=True)
        if not draft:
            continue
        try:
            sheets.write_draft_reply(name, room_id, draft)
        except Exception as exc:
            print(f"  ! could not save draft for {name}: {exc}")
        waiting = (dt.date.today() - msg_date).days if msg_date and hasattr(msg_date, "year") else None
        review.append({"athlete": name, "question": "\n".join(
            [l for l in str(thread).strip().splitlines() if l.strip()][-6:]),
            "draft": draft, "reason": why, "waiting": waiting})
        new_rows.append([name, key, dt.datetime.now().isoformat(timespec="seconds")])

    print(f"  rooms checked: {checked} | new drafts: {len(review)}")
    if config.DRY_RUN and review:
        # Show what would have been posted. A dry run that silently discards the
        # drafts tells you the plumbing works but nothing about whether the
        # answers are any good, which is the part worth checking.
        for e in review:
            print(f"\n--- would post: {e['athlete']} ---")
            print(f"    asked : {e['question'].strip().splitlines()[-1][:160]}")
            print(f"    reply : {e['draft'][:400]}")
            print(f"    why   : {e['reason'][:160]}")
    if review and not config.DRY_RUN:
        posted = notifier.send_reply_for_review(review)
        print(f"  posted to Slack: {posted}")
        _record(sheets, new_rows)
    print(f"== done in {(dt.datetime.now() - started).seconds}s ==")


if __name__ == "__main__":
    main()
