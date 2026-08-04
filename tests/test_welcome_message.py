"""The welcome a new athlete gets, drafted for a coach.

An auto-sent onboarding checklist used to fire on top of Fitr's own welcome,
with a competing intake link, and got a 0% reply rate. It was removed. This
replaces it with something different in the way that mattered: no duplicated
logistics, one question rather than a list, and a human presses send.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

WELCOME = ("Hey {first}, welcome to JST. Good to have you with us. "
           "Before you get stuck in, what are you hoping to get out of "
           "the next few months?")


def _msg(first="Amy"):
    return WELCOME.format(first=first)


def test_opens_with_their_name():
    assert _msg("Amy").startswith("Hey Amy,")


def test_ends_with_an_open_question():
    assert _msg().rstrip().endswith("?")


def test_does_not_duplicate_what_fitr_already_sends():
    """The 0% reply version repeated the handbook, the group and an intake
    link that competed with Fitr's own."""
    m = _msg().lower()
    for dup in ("whatsapp", "handbook", "typeform", "intake", "http", "checklist"):
        assert dup not in m, f"{dup!r} duplicates Fitr's own welcome"


def test_tone_rules_hold():
    m = _msg()
    assert "—" not in m and "–" not in m      # no em dash
    assert "!" not in m                        # no exclamation mark
    assert not re.search(r"[\U0001F300-\U0001FAFF]", m)   # no emoji
    assert "Jak" not in m and "Ed" not in m    # a coach sends it, so no sign-off
    for banned in ("unlock", "elevate", "transform", "journey", "excited",
                   "can't wait", "let's go"):
        assert banned not in m.lower()


def test_it_is_short():
    """A first message that runs long reads like onboarding paperwork."""
    assert len(_msg()) < 200


def test_the_sync_queues_it_as_a_draft_type():
    """It must go through _deliver like every other message, so that with
    automatic sending off it lands in the queue rather than an athlete's
    inbox."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "sync.py"), encoding="utf-8").read()
    assert '_deliver(fitr, room_id, msg, name, "onboarding_welcome")' in src
    assert "welcome to JST" in src


def test_the_dashboard_labels_it():
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "dashboard.py"), encoding="utf-8").read()
    assert '"onboarding_welcome": "👋 Welcome"' in src


def test_bespoke_athletes_are_skipped():
    """Individually coached athletes are exempt from automated messaging."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "sync.py"), encoding="utf-8").read()
    block = src[src.index("welcomed = 0"):src.index("return len(to_onboard)")]
    assert "bespoke_names" in block
