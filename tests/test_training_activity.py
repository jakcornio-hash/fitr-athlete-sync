"""Real training adherence, from Fitr's coach activity view.

Before this, engagement was judged on how often an athlete retested a
benchmark. On the live roster that flagged 87 athletes who had trained in the
past fortnight — Chad Croot among them, at 13 sessions out of 14.

Fitr day statuses: done (whole session), partial (some sections), skipped
(work was scheduled and none was done), empty (nothing scheduled).
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics

TODAY = dt.date(2026, 8, 1)


def _item(name, statuses, fitr_id=1, plan="JST Athlete", membership="active", start=None):
    """statuses: list of (days_ago, status)."""
    days = []
    for ago, status in statuses:
        days.append({"date": (TODAY - dt.timedelta(days=ago)).isoformat(),
                     "status": status, "online": True})
    return {"id": fitr_id, "full_name": name, "chat_room_id": 99,
            "plan": {"title": plan, "membership": {"state": membership}},
            "display_plan": {"performance_by_days": days}}


def _one(item):
    return analytics.training_activity([item], today=TODAY)


# ── counting ──────────────────────────────────────────────────────────────────

def test_done_and_partial_both_count_as_trained():
    a = _one(_item("A", [(1, "done"), (2, "partial")]))["A"]
    assert a["sessions"] == 2
    assert a["missed"] == 0
    assert a["days_since_trained"] == 1


def test_skipped_is_a_missed_session():
    a = _one(_item("A", [(1, "done"), (2, "skipped"), (3, "skipped")]))["A"]
    assert a["sessions"] == 1
    assert a["missed"] == 2
    assert a["scheduled"] == 3
    assert a["adherence_pct"] == 33


def test_empty_is_not_a_missed_session():
    """Nothing was programmed that day. Counting it as missed would make every
    rest day look like a failure and every athlete look non-compliant."""
    a = _one(_item("A", [(1, "done"), (2, "empty"), (3, "empty")]))["A"]
    assert a["sessions"] == 1
    assert a["missed"] == 0
    assert a["scheduled"] == 1
    assert a["adherence_pct"] == 100


def test_nothing_scheduled_gives_no_adherence_rather_than_zero():
    """0% would read as "never trains"; None means "we programmed nothing"."""
    a = _one(_item("A", [(1, "empty"), (2, "empty")]))["A"]
    assert a["adherence_pct"] is None
    assert a["sessions"] == 0


def test_never_trained_has_no_last_trained_date():
    a = _one(_item("A", [(1, "skipped"), (2, "skipped")]))["A"]
    assert a["last_trained"] is None
    assert a["days_since_trained"] is None
    assert a["adherence_pct"] == 0


def test_future_days_are_not_counted_as_missed():
    """A programme runs ahead of today; an athlete has not failed to do
    tomorrow's session."""
    a = _one(_item("A", [(-3, "skipped"), (-1, "skipped"), (1, "done")]))["A"]
    assert a["missed"] == 0
    assert a["sessions"] == 1


def test_latest_trained_day_wins():
    a = _one(_item("A", [(9, "done"), (2, "done"), (5, "partial")]))["A"]
    assert a["days_since_trained"] == 2


# ── metadata carried through ──────────────────────────────────────────────────

def test_plan_and_membership_are_captured():
    a = _one(_item("A", [(1, "done")], plan="Strength Bias", membership="canceled"))["A"]
    assert a["plan"] == "Strength Bias"
    assert a["membership_state"] == "canceled"
    assert a["fitr_id"] == 1


def test_unnamed_athletes_are_skipped():
    assert analytics.training_activity([_item("", [(1, "done")])], today=TODAY) == {}


def test_missing_display_plan_is_survivable():
    out = analytics.training_activity([{"id": 1, "full_name": "A"}], today=TODAY)
    assert out["A"]["sessions"] == 0
    assert out["A"]["window_days"] == 0


def test_unparseable_date_does_not_break_the_row():
    item = _item("A", [(1, "done")])
    item["display_plan"]["performance_by_days"].append({"date": "not-a-date", "status": "done"})
    a = _one(item)["A"]
    assert a["sessions"] == 1   # the good day still counts


# ── the point of the whole thing ──────────────────────────────────────────────

def test_real_training_overrides_a_stale_benchmark_flag():
    """Chad Croot: benchmark 39 days old, trained 13 of 14 days."""
    athletes = [{"name": "Chad Croot", "jst_id": "JST-1"}]
    pr = [{"Athlete Name": "Chad Croot", "Benchmark Name": "Back Squat",
           "Date": (dt.date.today() - dt.timedelta(days=39)).isoformat(), "Value": "100"}]
    act = analytics.training_activity(
        [_item("Chad Croot", [(i, "done") for i in range(1, 14)])], today=dt.date.today())

    without = analytics.engagement_check(pr, athletes, threshold_days=28)
    assert without[0]["flag"] is True

    with_activity = analytics.engagement_check(pr, athletes, threshold_days=28,
                                               activity_by_name=act)
    assert with_activity[0]["flag"] is False
    assert with_activity[0]["sessions_14d"] == 13


def test_athlete_absent_from_fitr_still_uses_the_benchmark_signal():
    """78% of the roster is in Fitr's activity view. The rest must not silently
    become un-flaggable."""
    athletes = [{"name": "Ghost Athlete", "jst_id": "JST-2"}]
    pr = [{"Athlete Name": "Ghost Athlete", "Benchmark Name": "Snatch",
           "Date": (dt.date.today() - dt.timedelta(days=200)).isoformat(), "Value": "50"}]
    out = analytics.engagement_check(pr, athletes, threshold_days=28,
                                     activity_by_name={"Someone Else": {}})
    assert out[0]["flag"] is True
    assert out[0]["sessions_14d"] is None


def test_name_matching_is_normalised():
    """Fitr and the sheet disagree on hyphens and case."""
    athletes = [{"name": "Pat Campbell-Jenner", "jst_id": "JST-3"}]
    pr = [{"Athlete Name": "Pat Campbell-Jenner", "Benchmark Name": "Row",
           "Date": (dt.date.today() - dt.timedelta(days=90)).isoformat(), "Value": "1"}]
    act = analytics.training_activity(
        [_item("pat campbell jenner", [(1, "done")])], today=dt.date.today())
    out = analytics.engagement_check(pr, athletes, threshold_days=28, activity_by_name=act)
    assert out[0]["flag"] is False


# ── reading the signal back off _DATA ─────────────────────────────────────────

def test_zero_sessions_is_read_as_known_not_missing():
    """Fitr knows this athlete and they trained nothing. That is the strongest
    evidence they are not training, so it must not be mistaken for no data."""
    rows = [{"Full Name": "A", "Sessions (14d)": "0", "Last Trained": "",
             "Adherence (14d)": "0%"}]
    got = analytics.activity_from_data_records(rows)
    assert "A" in got
    assert got["A"]["sessions"] == 0
    assert got["A"]["last_trained"] is None
    assert got["A"]["days_since_trained"] is None


def test_athlete_fitr_has_never_heard_of_is_absent():
    rows = [{"Full Name": "A", "Sessions (14d)": "", "Last Trained": ""}]
    assert analytics.activity_from_data_records(rows) == {}


def test_values_round_trip_off_the_sheet():
    rows = [{"Full Name": "Chad Croot", "Sessions (14d)": "13",
             "Last Trained": "2026-08-01", "Adherence (14d)": "93%",
             "Fitr Plan": "JST Athlete"}]
    a = analytics.activity_from_data_records(rows)["Chad Croot"]
    assert a["sessions"] == 13
    assert a["adherence_pct"] == 93
    assert a["last_trained"] == dt.date(2026, 8, 1)
    assert a["plan"] == "JST Athlete"


def test_a_zero_session_athlete_still_gets_flagged_by_engagement():
    """Known to Fitr, trained nothing: must stay flagged, not be excused."""
    athletes = [{"name": "A", "jst_id": "1"}]
    pr = [{"Athlete Name": "A", "Benchmark Name": "Row",
           "Date": (dt.date.today() - dt.timedelta(days=120)).isoformat(), "Value": "1"}]
    act = analytics.activity_from_data_records(
        [{"Full Name": "A", "Sessions (14d)": "0", "Last Trained": ""}])
    out = analytics.engagement_check(pr, athletes, threshold_days=28, activity_by_name=act)
    assert out[0]["flag"] is True
