"""Recovery alerts judged on a run of weeks, not a single response.

"Single days lie. Weekly averages tell the truth" — the Client Tracker
Playbook's first rule, and the one this system was breaking: recovery_alerts
read only the latest submission, so one rough week and a month-long slide
produced an identical flag. The second is the one that precedes someone
quitting.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics


def _row(soreness=None, stress=None, motivation=None, avail="", ts="2026-08-01"):
    r = {"Submitted At": ts, "Availability this week": avail}
    if soreness is not None:
        r["Soreness"] = str(soreness)
    if stress is not None:
        r["Stress"] = str(stress)
    if motivation is not None:
        r["Motivation"] = str(motivation)
    return r


def _issues(latest, history=None):
    hist = {"A": history} if history else None
    return [a[1] for a in analytics.recovery_alerts({"A": latest}, history_by_name=hist)]


# ── behaviour without history is unchanged ────────────────────────────────────

def test_single_red_flag_still_fires_without_history():
    assert any("High soreness (8/10)" in i for i in _issues(_row(soreness=8)))


def test_healthy_scores_produce_nothing():
    assert _issues(_row(soreness=2, stress=3, motivation=9)) == []


def test_low_motivation_fires():
    assert any("Low motivation (2/10)" in i for i in _issues(_row(motivation=2)))


def test_injury_mention_fires():
    assert any("Injury flag" in i for i in _issues(_row(avail="carrying a niggle")))


# ── with history: a run reads differently from a one-off ──────────────────────

def test_one_bad_week_is_not_called_persistent():
    hist = [_row(soreness=3), _row(soreness=2), _row(soreness=8)]
    got = _issues(_row(soreness=8), hist)
    assert any("High soreness (8/10)" in i for i in got)
    assert not any("weeks running" in i for i in got)


def test_three_bad_weeks_is_called_out_as_a_run():
    hist = [_row(soreness=8), _row(soreness=7), _row(soreness=9)]
    got = _issues(_row(soreness=9), hist)
    assert any("3 weeks running" in i for i in got), got


def test_a_recovered_athlete_is_not_flagged():
    """Bad for weeks, fine now. The flag should clear."""
    hist = [_row(soreness=9), _row(soreness=8), _row(soreness=2)]
    assert _issues(_row(soreness=2), hist) == []


# ── the early warning: catch the slide before it crosses ─────────────────────

def test_soreness_climbing_below_threshold_is_flagged_softly():
    hist = [_row(soreness=3), _row(soreness=5), _row(soreness=6)]
    got = _issues(_row(soreness=6), hist)
    assert any("worsening" in i and "3→5→6" in i for i in got), got
    assert not any("High soreness" in i for i in got)


def test_stable_scores_below_threshold_are_not_flagged():
    hist = [_row(soreness=5), _row(soreness=5), _row(soreness=5)]
    assert _issues(_row(soreness=5), hist) == []


def test_a_small_wobble_is_not_a_trend():
    """4 to 5 is noise, not a slide — must not generate a flag."""
    hist = [_row(soreness=4), _row(soreness=4), _row(soreness=5)]
    assert _issues(_row(soreness=5), hist) == []


def test_improving_motivation_is_not_flagged():
    hist = [_row(motivation=4), _row(motivation=6), _row(motivation=8)]
    assert _issues(_row(motivation=8), hist) == []


def test_falling_motivation_is_flagged_early():
    hist = [_row(motivation=8), _row(motivation=6), _row(motivation=5)]
    got = _issues(_row(motivation=5), hist)
    assert any("worsening" in i for i in got), got


# ── ordering: a run outranks a fresh flag outranks an early warning ──────────

def test_persistent_problems_sort_above_new_ones():
    latest = {"Persistent": _row(soreness=9), "Fresh": _row(stress=8)}
    hist = {"Persistent": [_row(soreness=8), _row(soreness=9), _row(soreness=9)],
            "Fresh": [_row(stress=2), _row(stress=3), _row(stress=8)]}
    out = analytics.recovery_alerts(latest, history_by_name=hist)
    assert out[0][0] == "Persistent"


def test_return_shape_is_unchanged_for_existing_callers():
    """The digest and the action list index [0], [1] and [2]."""
    out = analytics.recovery_alerts({"A": _row(soreness=8)})
    assert all(len(a) == 3 for a in out)
