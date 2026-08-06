"""The conversation at the point someone leaves, drafted for a coach.

Nothing used to be drafted when an athlete cancelled. Cancelling did the
opposite: a name in the CRM Exit Autopsy is excluded from every engagement
flag and every athlete-facing message, so the system went quiet on precisely
the people worth talking to.

What the CRM shows over 119 cancellations: the opening message was sent 108
times and 45 people replied, which is a better reply rate than anything
automated in this repo achieves. Only 8 were ever offered an alternative and
only 25 have a recorded outcome. The manual opening is not the problem. The
offer after the reply is.

These drafts are the highest-stakes messages in the system, so the rules they
follow are worth pinning down: only recent cancellations, an offer only when
the recorded reason has an established one behind it, and never twice.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics

TODAY = dt.date(2026, 8, 5)


def _row(name="Amy Reed", days_ago=3, contacted=False, replied=False,
         offered="", reason=""):
    return {
        "Athlete Name": name,
        "Cancel Date (dd-mm-yyyy)": (TODAY - dt.timedelta(days=days_ago)).strftime("%d-%b-%Y"),
        "Initial Message (Y/N)": "Y" if contacted else "",
        "Replied (Y/N)": "Y" if replied else "",
        "Pivot Offered": offered,
        "Bucket (Reason)": reason,
    }


def _drafts(*rows, **kw):
    kw.setdefault("today", TODAY)
    return analytics.exit_conversation_drafts(list(rows), **kw)


# ── who gets one ──────────────────────────────────────────────────────────────

def test_a_fresh_cancellation_nobody_has_written_to_gets_a_check_in():
    out = _drafts(_row(days_ago=1))
    assert len(out) == 1
    assert out[0]["kind"] == analytics.EXIT_CHECKIN


def test_someone_who_replied_and_was_never_offered_anything_gets_an_offer():
    out = _drafts(_row(contacted=True, replied=True, reason="Injury"))
    assert len(out) == 1
    assert out[0]["kind"] == analytics.EXIT_PIVOT


def test_someone_already_offered_something_is_left_alone():
    assert _drafts(_row(contacted=True, replied=True, offered="JST OS")) == []


def test_someone_contacted_who_has_not_replied_is_left_alone():
    """Chasing a non-reply is a different decision and not this one."""
    assert _drafts(_row(contacted=True, replied=False)) == []


# ── the window ────────────────────────────────────────────────────────────────

def test_an_old_cancellation_is_never_messaged():
    """A "sorry to see you go" four months late is worse than silence."""
    assert _drafts(_row(days_ago=120)) == []
    assert _drafts(_row(days_ago=120, contacted=True, replied=True)) == []


def test_the_window_edge_is_included():
    assert len(_drafts(_row(days_ago=30), recent_days=30)) == 1
    assert _drafts(_row(days_ago=31), recent_days=30) == []


def test_a_future_dated_cancellation_is_ignored():
    """Notice given for a future date is not someone who has left."""
    assert _drafts(_row(days_ago=-5)) == []


def test_the_old_backlog_is_reported_rather_than_messaged():
    rows = [_row("Old One", days_ago=120),
            _row("Old Two", days_ago=200, contacted=True, replied=True),
            _row("Recent", days_ago=2)]
    never, no_offer = analytics.stale_exit_conversations(rows, today=TODAY, recent_days=30)
    assert never == ["Old One"]
    assert no_offer == ["Old Two"]
    assert "Recent" not in never + no_offer


# ── the offer is one the coaches actually make ────────────────────────────────

def test_a_known_reason_gets_the_offer_used_for_it():
    cases = {"Injury": "JST OS", "Money": "JST Dense",
             "Competitor": "fixed-length block", "Time": "fixed-length block"}
    for reason, expected in cases.items():
        msg = _drafts(_row(contacted=True, replied=True, reason=reason))[0]["message"]
        assert expected in msg, f"{reason}: {msg}"


def test_an_unrecognised_reason_asks_rather_than_guesses():
    """A wrong offer reads worse than no offer. "Not Slipping" is the second
    most common bucket in the CRM and nobody has told me what it means."""
    msg = _drafts(_row(contacted=True, replied=True, reason="Not Slipping"))[0]["message"]
    for product in ("JST OS", "JST Dense", "one to one", "fixed-length"):
        assert product not in msg
    assert msg.rstrip().endswith("?")


def test_a_blank_reason_asks_rather_than_guesses():
    msg = _drafts(_row(contacted=True, replied=True, reason=""))[0]["message"]
    assert "JST" not in msg


def test_a_known_reason_skips_the_pointless_question():
    """If the CRM already says "Injury", asking what stopped it working reads
    like nobody read their own notes. Offer straight away instead."""
    out = _drafts(_row(days_ago=2, contacted=False, reason="Injury"))
    assert out[0]["kind"] == analytics.EXIT_PIVOT
    assert "JST OS" in out[0]["message"]


def test_the_reason_is_named_back_to_them():
    msg = _drafts(_row(contacted=True, replied=True, reason="Money"))[0]["message"]
    assert "came down to cost" in msg


# ── tone, on the highest-stakes message in the system ─────────────────────────

def _every_message():
    rows = [_row(f"Athlete {i}", days_ago=i + 1, contacted=c, replied=rp, reason=rsn)
            for i, (c, rp, rsn) in enumerate([
                (False, False, ""), (True, True, "Injury"), (True, True, "Money"),
                (True, True, "Competitor"), (True, True, "Time"),
                (True, True, "Not Slipping"), (True, True, "Another Reason"),
            ])]
    return [d["message"] for d in _drafts(*rows)]


def test_no_em_dash_anywhere():
    for m in _every_message():
        assert "—" not in m and "–" not in m


def test_no_exclamation_marks():
    """Banned outright in the tone doc, and tone-deaf to someone who just left."""
    for m in _every_message():
        assert "!" not in m


def test_every_message_ends_on_a_genuine_open_question():
    for m in _every_message():
        assert m.rstrip().endswith("?"), m


def test_no_sign_off():
    """A coach presses send and adds their own."""
    for m in _every_message():
        assert "Jak" not in m and "Coach Ed" not in m


def test_contractions_are_used():
    for m in _every_message():
        assert "'" in m, m


def test_no_banned_hype():
    for m in _every_message():
        low = m.lower()
        for banned in ("unlock", "elevate", "transform", "journey", "delve",
                       "moreover", "furthermore", "we value your", "sorry to see you go"):
            assert banned not in low, f"{banned!r} in: {m}"


def test_it_opens_with_their_first_name_only():
    msg = _drafts(_row("Pat Campbell-Jenner", days_ago=1))[0]["message"]
    assert msg.startswith("Hey Pat,")
    assert "Campbell" not in msg


# ── ordering and robustness ───────────────────────────────────────────────────

def test_most_recent_cancellation_comes_first():
    out = _drafts(_row("Older", days_ago=20), _row("Newer", days_ago=2))
    assert [d["name"] for d in out] == ["Newer", "Older"]


def test_rows_with_no_name_or_no_date_are_skipped():
    assert _drafts({"Athlete Name": "", "Cancel Date (dd-mm-yyyy)": "01-Aug-2026"}) == []
    assert _drafts({"Athlete Name": "Amy Reed", "Cancel Date (dd-mm-yyyy)": ""}) == []
    assert _drafts({"Athlete Name": "Amy Reed",
                    "Cancel Date (dd-mm-yyyy)": "not a date"}) == []


def test_empty_input_is_safe():
    assert analytics.exit_conversation_drafts([], today=TODAY) == []
    assert analytics.exit_conversation_drafts(None, today=TODAY) == []
    assert analytics.stale_exit_conversations(None, today=TODAY) == ([], [])


def test_the_crm_date_format_is_the_one_the_sheet_uses():
    """The live column is dd-Mon-yyyy, e.g. 24-Mar-2026."""
    out = analytics.exit_conversation_drafts(
        [{"Athlete Name": "Amy Reed", "Cancel Date (dd-mm-yyyy)": "03-Aug-2026"}],
        today=TODAY)
    assert len(out) == 1
    assert out[0]["days_since_cancel"] == 2


# ── the wrong shape must be loud, not silent ──────────────────────────────────
# The first live dry run of this stage drafted nothing and reported no error.
# main() passes around `exit_rows` from sheets_client.load_exit_autopsy(), which
# is a three-field projection keyed "name"/"cancel_date"/"outcome". Every row
# missed "Athlete Name", every row was skipped, and the run looked clean.

import pytest

PROJECTION = [{"name": "Amy Reed", "cancel_date": dt.date(2026, 8, 3), "outcome": ""}]


def test_the_projection_shape_raises_instead_of_drafting_nothing():
    with pytest.raises(KeyError) as e:
        analytics.exit_conversation_drafts(PROJECTION, today=TODAY)
    assert "Athlete Name" in str(e.value)


def test_the_error_names_the_reader_to_use_instead():
    with pytest.raises(KeyError) as e:
        analytics.exit_conversation_drafts(PROJECTION, today=TODAY)
    assert "read_external_records_positional" in str(e.value)


def test_the_stale_report_rejects_it_too():
    with pytest.raises(KeyError):
        analytics.stale_exit_conversations(PROJECTION, today=TODAY)


def test_a_non_dict_row_is_rejected():
    with pytest.raises(TypeError):
        analytics.exit_conversation_drafts(["Amy Reed"], today=TODAY)


def test_an_empty_list_is_still_fine():
    """No cancellations is a normal state, not a shape error."""
    assert analytics.exit_conversation_drafts([], today=TODAY) == []


def test_the_sync_reads_the_raw_columns():
    """Guard the call site, since the projection reader is still in use
    elsewhere and is the easier one to reach for."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "sync.py"),
               encoding="utf-8").read()
    block = src[src.index('with stage("exit conversations")'):]
    block = block[:block.index('with stage("message log')]
    # Code only. The comment in that block names the wrong reader on purpose,
    # to say which one not to use.
    code = "\n".join(l for l in block.split("\n") if not l.lstrip().startswith("#"))
    assert "read_external_records_positional" in code
    assert "load_exit_autopsy" not in code
