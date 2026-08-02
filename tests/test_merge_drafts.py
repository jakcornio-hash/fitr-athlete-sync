"""One message per athlete, not one per trigger.

Andy Saxon had a monthly summary and a nine-month anniversary queued on the
same day. Sent as-is he would have received two messages minutes apart, each
opening with his name — which reads like automation, the one thing these
messages exist to avoid.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics


def _draft(row, name, mtype, message, date="2026-08-01"):
    return {"_row": row, "Athlete Name": name, "Message Type": mtype,
            "Message": message, "Date": date, "Status": "pending"}


ANDY_MONTHLY = _draft(
    2, "Andy Saxon", "monthly_fitr",
    "Andy, July done. You hit 4 results, 1RM Bench Press at 130 kg stands out. "
    "3 sessions logged. Solid month.")
ANDY_ANNIV = _draft(
    3, "Andy Saxon", "anniversary",
    "Hey Andy, that's nine months of results logged with us now. No ask here, "
    "we just don't think it should pass unmentioned.")


# ── the merge ─────────────────────────────────────────────────────────────────

def test_two_drafts_become_one_message():
    out = analytics.merge_athlete_drafts([ANDY_MONTHLY, ANDY_ANNIV])
    assert len(out) == 1
    assert out[0]["_merged_count"] == 2


def test_the_greeting_appears_once():
    msg = analytics.merge_athlete_drafts([ANDY_MONTHLY, ANDY_ANNIV])[0]["Message"]
    assert msg.startswith("Andy, July done.")
    assert "Hey Andy" not in msg
    assert msg.count("Andy") == 1


def test_the_second_message_survives_intact_after_its_greeting():
    msg = analytics.merge_athlete_drafts([ANDY_MONTHLY, ANDY_ANNIV])[0]["Message"]
    assert "That's nine months of results logged with us now." in msg
    assert "we just don't think it should pass unmentioned." in msg


def test_paragraphs_are_separated():
    msg = analytics.merge_athlete_drafts([ANDY_MONTHLY, ANDY_ANNIV])[0]["Message"]
    assert "\n\n" in msg


def test_every_underlying_row_is_kept():
    """Marking the card sent has to settle both rows, or the leftover
    reappears tomorrow and the athlete is messaged twice anyway."""
    out = analytics.merge_athlete_drafts([ANDY_MONTHLY, ANDY_ANNIV])[0]
    assert sorted(out["_rows"]) == [2, 3]
    assert sorted(out["_types"]) == ["anniversary", "monthly_fitr"]
    assert len(out["_records"]) == 2


# ── athletes with a single draft are untouched ────────────────────────────────

def test_a_lone_draft_is_unchanged():
    out = analytics.merge_athlete_drafts([ANDY_MONTHLY])
    assert len(out) == 1
    assert out[0]["Message"] == ANDY_MONTHLY["Message"]
    assert out[0]["_merged_count"] == 1


def test_different_athletes_stay_separate():
    other = _draft(4, "Tom Woods", "monthly_fitr", "Tom, July done. Solid month.")
    out = analytics.merge_athlete_drafts([ANDY_MONTHLY, other, ANDY_ANNIV])
    assert len(out) == 2
    by_name = {o["Athlete Name"]: o for o in out}
    assert by_name["Tom Woods"]["_merged_count"] == 1
    assert by_name["Andy Saxon"]["_merged_count"] == 2


def test_name_spelling_variants_merge():
    """Fitr and the sheet disagree on hyphens and case."""
    a = _draft(2, "Pat Campbell-Jenner", "congrats", "Pat, great lift.")
    b = _draft(3, "pat campbell jenner", "anniversary", "Hey Pat, one year today.")
    assert len(analytics.merge_athlete_drafts([a, b])) == 1


# ── greeting stripping is conservative ────────────────────────────────────────

def test_a_message_not_opening_with_the_name_is_left_alone():
    a = _draft(2, "Amy Reed", "congrats", "Amy, great lift.")
    b = _draft(3, "Amy Reed", "referral", "Quick one about a mate of yours.")
    msg = analytics.merge_athlete_drafts([a, b])[0]["Message"]
    assert "Quick one about a mate of yours." in msg


def test_another_persons_name_is_not_stripped():
    a = _draft(2, "Amy Reed", "congrats", "Amy, great lift.")
    b = _draft(3, "Amy Reed", "referral", "Ben, your training partner, just joined.")
    msg = analytics.merge_athlete_drafts([a, b])[0]["Message"]
    assert "Ben, your training partner, just joined." in msg


def test_blank_and_empty_input():
    assert analytics.merge_athlete_drafts([]) == []
    assert analytics.merge_athlete_drafts([_draft(2, "", "congrats", "hi")]) == []


def test_three_drafts_merge_in_sheet_order():
    c = _draft(4, "Andy Saxon", "congrats", "Andy, new PB on the squat.")
    msg = analytics.merge_athlete_drafts([ANDY_MONTHLY, ANDY_ANNIV, c])[0]["Message"]
    assert msg.index("July done") < msg.index("nine months") < msg.index("PB on the squat")


def test_a_stripped_greeting_leaves_a_capital():
    """"new PB on the squat" mid-message reads as a typo; "New PB" does not."""
    c = _draft(4, "Andy Saxon", "congrats", "Andy, new PB on the squat.")
    msg = analytics.merge_athlete_drafts([ANDY_MONTHLY, c])[0]["Message"]
    assert "New PB on the squat." in msg
