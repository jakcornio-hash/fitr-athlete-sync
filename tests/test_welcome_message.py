"""Who a welcome may go to, which turned out to be the whole problem.

A welcome draft briefly fired from auto_onboard_new_athletes. On its first
live run it drafted to every athlete it found, and a coach confirmed all of
them were wrong: squad members on the junior pathway, and an individually
coached athlete. Not one was a new subscriber. Getting all of them wrong is a
population error, not a copy error.

The reason is structural. auto_onboard detects a new opponent in a coach's
chat room, which is how individually-coached and squad athletes arrive. Those
are the people bespoke suppression exists to keep out of automated messaging,
and the bespoke_names guard could not catch them, because that set is built
before they are added to it. "New to a coach's chat room" is not "new
subscriber".

The earlier version of this file asserted the opposite, so it is worth being
explicit about what is now true.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

SRC = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "sync.py"),
           encoding="utf-8").read()


def _auto_onboard_source():
    start = SRC.index("def auto_onboard_new_athletes")
    return SRC[start:SRC.index("\ndef ", start + 1)]


# ── nothing is messaged off the chat-room path ────────────────────────────────

def test_auto_onboard_drafts_no_welcome():
    assert "onboarding_welcome" not in _auto_onboard_source()


def test_auto_onboard_messages_nobody_at_all():
    """It is a bookkeeping stage: it adds rows, it does not talk to anyone."""
    assert "_deliver(" not in _auto_onboard_source()


def test_the_reason_is_recorded_where_it_will_be_read():
    """So whoever next considers a welcome here reads why it was removed,
    without athlete names going into a public repo to say it."""
    src = _auto_onboard_source()
    assert "not a new subscriber" in src or "new subscriber" in src
    assert "bespoke_names" in src


# ── the first-log onboarding message is a different thing and still stands ────

def _first_log_block():
    start = SRC.index("# ---- new athlete onboarding (first log == today) ----")
    return SRC[start:SRC.index("# ---- pre-competition", start)]


def test_the_first_log_onboarding_message_survives():
    """That one fires on an athlete's first logged result, not on discovery."""
    assert '"onboarding")' in _first_log_block()


def test_the_first_log_message_excludes_bespoke():
    assert "if nm in bespoke_names:" in _first_log_block()


# ── the dashboard must still render what is already queued ────────────────────

def test_the_dashboard_can_still_label_a_queued_welcome():
    """Three of these sit in Pending Messages marked skipped. The dashboard
    must not fall over on a message type it no longer generates."""
    dash = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "dashboard.py"), encoding="utf-8").read()
    assert '"onboarding_welcome": "👋 Welcome"' in dash
