"""Widget keys for the draft queue must follow the message, not its position.

Spotted live: a card headed "Monthly — Tom Woods" whose text box contained
"Cau, July done...", and the next headed "Chase Mullan" containing Tom's.

Streamlit stops honouring value= once a widget key holds session state. With a
key built from the loop index, removing one draft shifts everything up a slot
and every box keeps the previous occupant's text. The send buttons read that
same box, so the wrong athlete's message could be sent to the wrong athlete.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _key(record):
    """Mirror of dashboard._pending_widget_key."""
    raw = "|".join(str(record.get(k, ""))
                   for k in ("_row", "Athlete Name", "Message Type", "Date"))
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _draft(row, name, mtype="monthly_fitr", date="2026-08-01"):
    return {"_row": row, "Athlete Name": name, "Message Type": mtype, "Date": date}


CAU = _draft(2, "Cau Stephane")
TOM = _draft(3, "Tom Woods")
CHASE = _draft(4, "Chase Mullan")


def test_key_survives_the_list_shifting():
    """The exact live failure: Cau is dealt with, everyone moves up a slot."""
    before = [CAU, TOM, CHASE]
    after = [TOM, CHASE]              # Cau marked sent and filtered out
    assert _key(before[1]) == _key(after[0])   # Tom keeps his own key
    assert _key(before[2]) == _key(after[1])   # so does Chase


def test_index_keys_are_what_went_wrong():
    """Demonstrates the defect the fix removes: by position, Tom inherits
    Cau's slot and therefore Cau's retained message."""
    before = ["pend_msg_0", "pend_msg_1", "pend_msg_2"]   # Cau, Tom, Chase
    after = ["pend_msg_0", "pend_msg_1"]                  # Tom, Chase
    assert after[0] == before[0]      # Tom now answers to Cau's key
    assert _key(TOM) != _key(CAU)     # whereas the real keys never collide


def test_every_draft_gets_a_distinct_key():
    drafts = [CAU, TOM, CHASE,
              _draft(5, "Tom Woods", mtype="congrats"),      # same person, other type
              _draft(6, "Tom Woods", date="2026-09-01")]     # same person, other date
    keys = [_key(d) for d in drafts]
    assert len(set(keys)) == len(keys)


def test_key_is_stable_across_reruns():
    assert _key(TOM) == _key(dict(TOM))


def test_two_athletes_sharing_a_row_number_would_not_collide():
    """Belt and braces: the row alone should be unique, but the key folds in
    name and type so a reordered sheet cannot alias two messages together."""
    assert _key(_draft(2, "A")) != _key(_draft(2, "B"))
