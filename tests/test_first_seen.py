"""A start date that can be defended, because neither recorded one can.

Measured on the live data before writing any of this:

  _DATA!Join Date      of 253 athletes with both a join date and a logged
                       result, 165 (65%) logged their first result BEFORE the
                       recorded join date. Median gap 125 days. Nothing
                       recorded after 2026-05-25.

  Fitr plan.start_day  55% logged more than a week before it, median 32 days.
                       It is the current plan's start day, so it resets on
                       renewal. Values in the live data run to 2044-07-23.

  Together             of 181 athletes with both, 107 are identical, so the
                       sheet was copied from Fitr and shares its flaw rather
                       than corroborating it.

So the only defensible figure is a floor: an athlete cannot have logged a
result before arriving, so the earliest of (first logged result, plan start
day) is a date they were demonstrably here by. Named "first seen" so it is
never mistaken for a join date.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics

TODAY = dt.date(2026, 8, 6)


def _pr(name, date):
    return {"Athlete Name": name, "Date": date, "Benchmark Name": "1RM Snatch", "Value": "60"}


# ── the clamped parser ────────────────────────────────────────────────────────

def test_it_reads_fitrs_own_format():
    assert analytics.parse_fitr_start_day("03/06/2026", today=TODAY) == dt.date(2026, 6, 3)


def test_a_future_start_day_is_discarded_not_clamped():
    """The live data holds 2029, 2030 and 2044. Clamping one of those to today
    would invent a cohort; None is honest about not knowing."""
    for bad in ("23/07/2044", "01/01/2030", "15/08/2029"):
        assert analytics.parse_fitr_start_day(bad, today=TODAY) is None


def test_tomorrow_is_already_too_late():
    tomorrow = (TODAY + dt.timedelta(days=1)).strftime("%d/%m/%Y")
    assert analytics.parse_fitr_start_day(tomorrow, today=TODAY) is None


def test_today_is_fine():
    assert analytics.parse_fitr_start_day(TODAY.strftime("%d/%m/%Y"), today=TODAY) == TODAY


def test_an_absurdly_old_date_is_discarded():
    assert analytics.parse_fitr_start_day("01/01/1970", today=TODAY) is None


def test_the_oldest_real_records_survive():
    """Fitr's own history starts in 2019 and those are genuine."""
    assert analytics.parse_fitr_start_day("06/11/2019", today=TODAY) == dt.date(2019, 11, 6)


def test_blank_and_rubbish_are_safe():
    for v in ("", None, "   ", "not a date", "13/13/2026"):
        assert analytics.parse_fitr_start_day(v, today=TODAY) is None


def test_day_month_order_is_not_guessed_wrongly():
    """03/06/2026 is 3 June, not 6 March. Getting this backwards would shift
    cohorts by months and look plausible while doing it."""
    assert analytics.parse_fitr_start_day("03/06/2026", today=TODAY).month == 6


# ── roster scoping ────────────────────────────────────────────────────────────

def test_only_roster_names_survive():
    """Fitr returns 1707 clients across every coach on the account; the JST
    roster is 252. A percentage over the wrong denominator is not a metric."""
    out = analytics.restrict_to_roster(
        ["Amy Reed", "Someone Elses Athlete", "Ben Sample"], ["Amy Reed", "Ben Sample"])
    assert out == ["Amy Reed", "Ben Sample"]


def test_matching_survives_punctuation_and_case():
    assert analytics.restrict_to_roster(
        ["pat campbell jenner"], ["Pat Campbell-Jenner"]) == ["pat campbell jenner"]


def test_an_empty_roster_keeps_nobody():
    """A failed roster read must not widen the population to all of Fitr.
    Returning everything here would turn an outage into a wrong number."""
    assert analytics.restrict_to_roster(["Amy Reed"], []) == []
    assert analytics.restrict_to_roster(["Amy Reed"], None) == []


def test_blank_roster_entries_are_ignored():
    assert analytics.restrict_to_roster(["Amy Reed"], ["", "  ", "Amy Reed"]) == ["Amy Reed"]


# ── first seen ────────────────────────────────────────────────────────────────

def test_the_earlier_of_the_two_wins():
    out = analytics.first_seen_dates(
        [_pr("Amy Reed", "2026-03-01")],
        {"Amy Reed": "01/06/2026"}, today=TODAY)
    assert out["Amy Reed"]["first_seen"] == dt.date(2026, 3, 1)
    assert out["Amy Reed"]["source"] == "logged result"


def test_the_plan_start_wins_when_it_is_earlier():
    out = analytics.first_seen_dates(
        [_pr("Amy Reed", "2026-06-20")],
        {"Amy Reed": "01/06/2026"}, today=TODAY)
    assert out["Amy Reed"]["first_seen"] == dt.date(2026, 6, 1)
    assert out["Amy Reed"]["source"] == "plan start"


def test_agreement_is_recorded_as_such():
    """Worth distinguishing: a date both sources agree on carries more weight,
    and the dashboard should be able to say so."""
    out = analytics.first_seen_dates(
        [_pr("Amy Reed", "2026-06-01")],
        {"Amy Reed": "01/06/2026"}, today=TODAY)
    assert out["Amy Reed"]["source"] == "both agree"


def test_a_logged_result_alone_is_enough():
    out = analytics.first_seen_dates([_pr("Amy Reed", "2026-02-14")], {}, today=TODAY)
    assert out["Amy Reed"]["first_seen"] == dt.date(2026, 2, 14)


def test_a_plan_start_alone_is_enough():
    """239 of 252 roster athletes have a logged result. The rest still need a
    date or they vanish from every cohort silently."""
    out = analytics.first_seen_dates([], {"Amy Reed": "01/06/2026"}, today=TODAY)
    assert out["Amy Reed"]["first_seen"] == dt.date(2026, 6, 1)


def test_a_future_plan_start_does_not_become_the_anchor():
    out = analytics.first_seen_dates(
        [_pr("Amy Reed", "2026-03-01")], {"Amy Reed": "23/07/2044"}, today=TODAY)
    assert out["Amy Reed"]["first_seen"] == dt.date(2026, 3, 1)
    assert out["Amy Reed"]["source"] == "logged result"


def test_an_athlete_with_only_a_bad_start_day_is_absent():
    assert analytics.first_seen_dates([], {"Amy Reed": "23/07/2044"}, today=TODAY) == {}


def test_a_future_dated_log_is_ignored():
    assert analytics.first_seen_dates([_pr("Amy Reed", "2027-01-01")], {}, today=TODAY) == {}


def test_earliest_of_many_logs_is_used():
    out = analytics.first_seen_dates(
        [_pr("Amy Reed", "2026-05-01"), _pr("Amy Reed", "2026-01-09"),
         _pr("Amy Reed", "2026-03-01")], {}, today=TODAY)
    assert out["Amy Reed"]["first_seen"] == dt.date(2026, 1, 9)


def test_name_variants_are_one_athlete():
    out = analytics.first_seen_dates(
        [_pr("Pat Campbell-Jenner", "2026-04-01"), _pr("pat campbell jenner", "2026-02-01")],
        {}, today=TODAY)
    assert len(out) == 1
    assert list(out.values())[0]["first_seen"] == dt.date(2026, 2, 1)


def test_scoping_to_the_roster_is_applied():
    out = analytics.first_seen_dates(
        [_pr("Amy Reed", "2026-03-01"), _pr("Someone Else", "2026-03-01")],
        {}, today=TODAY, roster_names=["Amy Reed"])
    assert list(out) == ["Amy Reed"]


def test_no_roster_given_means_no_scoping():
    out = analytics.first_seen_dates(
        [_pr("Amy Reed", "2026-03-01"), _pr("Someone Else", "2026-03-01")], {}, today=TODAY)
    assert len(out) == 2


def test_empty_input_is_safe():
    assert analytics.first_seen_dates([], {}, today=TODAY) == {}
    assert analytics.first_seen_dates(None, None, today=TODAY) == {}


def test_it_is_never_called_a_join_date():
    """The naming is the safeguard. This is a floor, not a signup date, and the
    difference is the whole reason it exists."""
    out = analytics.first_seen_dates([_pr("Amy Reed", "2026-03-01")], {}, today=TODAY)
    assert "first_seen" in out["Amy Reed"]
    assert "join_date" not in out["Amy Reed"]
