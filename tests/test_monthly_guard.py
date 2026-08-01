"""The once-a-month guard on the monthly progress message.

It had never worked. The guard searched for the substring
"2026-08 — monthly_fitr" while the note it was matching against reads
"[2026-08-01 — monthly_fitr]" — the full date means that substring never
appears. It stayed invisible because the block only runs on the 1st and the
sync normally runs once a day; the first time a second run happened on the
1st, all 91 monthly messages were queued to athletes a second time.
"""
import datetime as dt
import re


def _guard(today):
    """The expression sync.py builds."""
    return re.compile(rf"\[{today.strftime('%Y-%m')}-\d\d — monthly_fitr\]")


def _note(day):
    """The note sync.py writes."""
    return f"[{day.isoformat()} — monthly_fitr]"


def test_guard_matches_the_note_it_is_meant_to_match():
    today = dt.date(2026, 8, 1)
    assert _guard(today).search(_note(today))


def test_the_old_substring_guard_never_matched():
    """Pins the actual defect so it cannot be reintroduced."""
    today = dt.date(2026, 8, 1)
    old_guard = f"{today.strftime('%Y-%m')} — monthly_fitr"
    assert old_guard not in _note(today)


def test_guard_matches_any_day_within_the_month():
    today = dt.date(2026, 8, 1)
    assert _guard(today).search(_note(dt.date(2026, 8, 1)))
    assert _guard(today).search(_note(dt.date(2026, 8, 28)))


def test_guard_does_not_match_a_previous_month():
    """Next month's message must still go out."""
    assert not _guard(dt.date(2026, 9, 1)).search(_note(dt.date(2026, 8, 1)))
    assert not _guard(dt.date(2026, 8, 1)).search(_note(dt.date(2026, 7, 1)))


def test_guard_ignores_other_note_types():
    today = dt.date(2026, 8, 1)
    assert not _guard(today).search(f"[{today.isoformat()} — anniversary]")
    assert not _guard(today).search(f"[{today.isoformat()} — chat] monthly_fitr mentioned")
