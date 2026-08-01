"""The digest email must survive non-ascii, because it is full of it.

A production run sent the Slack digest fine and failed the email with
'ascii' codec can't encode character '\\xa0' — so the findings reached Slack
and silently never reached the inbox. Nobody would notice: the run reports
success and one of two channels quietly stops.
"""
import os
import sys
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Everything the digest actually contains.
NASTY = (
    "JST Compete — Today's Coaching Actions | 2026-08-01\n"
    " SYSTEM HEALTH\n"                      # the non-breaking space that broke it
    "\U0001f6d1 BROKEN — needs fixing (1)\n"  # emoji
    "  • 4 athletes — £220/month\n"  # bullet, em dash, sterling
    "  • Dave Colakovic \U0001f98d\n"          # emoji in an athlete's name
)


def _build(charset):
    msg = EmailMessage()
    msg["Subject"] = "JST Compete Coaching Digest — 2026-08-01"
    msg["From"] = "JST Compete <a@b.c>"
    msg["To"] = "d@e.f"
    if charset:
        msg.set_content(NASTY, charset=charset)
    else:
        msg.set_content(NASTY)
    return msg


def test_utf8_digest_serialises():
    """What the fix does: the message becomes sendable bytes."""
    raw = _build("utf-8").as_bytes()
    assert b"SYSTEM HEALTH" in raw or b"U1lTVEVN" in raw or len(raw) > 0


def test_non_breaking_space_is_the_character_that_broke_it():
    assert " " in NASTY


def test_notifier_sets_utf8_on_every_send_path():
    """Guards the actual regression: a bare set_content anywhere in notifier
    re-opens it, and the failure only shows on the email side."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "notifier.py"), encoding="utf-8").read()
    bare = [ln.strip() for ln in src.splitlines()
            if "set_content(" in ln and "charset=" not in ln and not ln.strip().startswith("#")]
    assert bare == [], f"set_content without charset: {bare}"

    bare_mime = [ln.strip() for ln in src.splitlines()
                 if "MIMEText(" in ln and "utf-8" not in ln and not ln.strip().startswith("#")]
    assert bare_mime == [], f"MIMEText without utf-8: {bare_mime}"
