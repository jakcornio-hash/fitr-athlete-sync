"""Reply detection was broken in two ways that cancelled out into silence.

Fitr sends created_at as a Unix timestamp and has no is_mine flag. The old
code sliced the timestamp as text and compared it to a "YYYY-MM-DD" date, so
no message ever counted as a reply — and had that comparison ever passed, the
missing is_mine would have counted the coach's own messages as replies.

Both have to stay fixed together, so both are pinned here.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import fitr_client


def _msg(author, ts, text="hello"):
    return {"author": {"full_name": author}, "created_at": ts, "text": text}


# ── message_date ──────────────────────────────────────────────────────────────

def test_unix_timestamp_becomes_a_date():
    expected = dt.datetime.fromtimestamp(1785527048).date()
    assert fitr_client.message_date(_msg("Amy R", 1785527048)) == expected


def test_timestamp_as_string_also_works():
    expected = dt.datetime.fromtimestamp(1785527048).date()
    assert fitr_client.message_date(_msg("Amy R", "1785527048")) == expected


def test_iso_string_still_parses():
    assert fitr_client.message_date(_msg("Amy R", "2026-07-28")) == dt.date(2026, 7, 28)


def test_missing_or_junk_timestamp_is_none():
    assert fitr_client.message_date(_msg("Amy R", None)) is None
    assert fitr_client.message_date(_msg("Amy R", "")) is None
    assert fitr_client.message_date(_msg("Amy R", "not a date")) is None


def test_the_old_string_slice_would_have_failed():
    """Guards the actual defect: text-comparing a timestamp to a date."""
    assert not (str(1785527048)[:10] >= "2026-07-28")


# ── message_is_from ───────────────────────────────────────────────────────────

def test_athlete_message_is_from_the_athlete():
    assert fitr_client.message_is_from(_msg("Amy R", 1785527048), "Amy R")


def test_coach_message_is_not_from_the_athlete():
    """The case the missing is_mine flag got wrong: our own message."""
    assert not fitr_client.message_is_from(_msg("Jak Cornthwaite", 1785527048), "Amy R")


def test_author_match_ignores_case_and_padding():
    assert fitr_client.message_is_from(_msg("  amy r ", 1785527048), "Amy R")


def test_missing_author_is_not_a_match():
    assert not fitr_client.message_is_from({"created_at": 1785527048}, "Amy R")
    assert not fitr_client.message_is_from({"author": {}, "created_at": 1}, "Amy R")


def test_blank_target_name_never_matches():
    assert not fitr_client.message_is_from(_msg("Amy R", 1), "")
