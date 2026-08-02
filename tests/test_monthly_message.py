"""The monthly note to an athlete.

Jak on the old version — "Andy, July done. You hit 4 results, 1RM Bench Press
at 130 kg stands out. 3 sessions logged. Solid month." — was that he wouldn't
quote four results and three sessions unless the number was worth writing home
about. Quoting a thin month advertises the thin month and makes the praise ring
hollow. Lead with the one real result and the consistency instead.

The session count was also simply wrong: it counted days on which a benchmark
was retested, not training sessions.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics


# ── no counts, ever ───────────────────────────────────────────────────────────

def test_no_result_or_session_counts_appear():
    msg = analytics.monthly_message("Andy", [("1RM Bench Press", "130 kg")], streak_months=9)
    for banned in ("4 results", "you hit", "sessions logged", "Solid month"):
        assert banned.lower() not in msg.lower(), f"{banned!r} is back in: {msg}"


def test_leads_with_the_actual_result():
    msg = analytics.monthly_message("Andy", [("1RM Bench Press", "130 kg")], streak_months=9)
    assert msg.startswith("Hey Andy, saw you logged 130 kg on the 1RM bench press last month.")


def test_the_consistency_story_is_told_when_real():
    msg = analytics.monthly_message("Andy", [("1RM Bench Press", "130 kg")], streak_months=9)
    assert "every month for the last 9 months" in msg


def test_ends_with_an_open_question():
    msg = analytics.monthly_message("Andy", [("1RM Bench Press", "130 kg")], streak_months=9)
    assert msg.rstrip().endswith("?")


def test_a_short_streak_is_not_claimed_as_consistency():
    msg = analytics.monthly_message("Amy", [("1RM Snatch", "60 kg")], streak_months=2)
    assert "consistency" not in msg
    assert "60 kg on the 1RM snatch" in msg


def test_silence_when_there_is_nothing_to_cite():
    """Better nothing than a hollow "solid month"."""
    assert analytics.monthly_message("Andy", [], streak_months=9) is None
    assert analytics.monthly_message("", [("1RM Snatch", "60 kg")]) is None
    assert analytics.monthly_message("Andy", [("1RM Snatch", "")]) is None


# ── tone rules ────────────────────────────────────────────────────────────────

def test_no_em_dash():
    """The number one AI tell in the tone-of-voice doc."""
    msg = analytics.monthly_message("Andy", [("1RM Bench Press", "130 kg")], streak_months=9)
    assert "—" not in msg and "–" not in msg


def test_no_banned_hype():
    msg = analytics.monthly_message("Andy", [("1RM Bench Press", "130 kg")], streak_months=12)
    for banned in ("unlock", "elevate", "transform", "journey", "game-changer",
                   "next-level", "dominate", "let's go", "moreover", "furthermore"):
        assert banned not in msg.lower()


def test_uses_contractions_and_no_signoff():
    msg = analytics.monthly_message("Andy", [("1RM Bench Press", "130 kg")], streak_months=9)
    assert "You've" in msg
    assert "Jak" not in msg          # automated messages carry no sign-off
    assert "How's" in msg


# ── benchmark names read like a coach said them ───────────────────────────────

def test_database_names_are_humanised():
    assert analytics.humanise_benchmark("1RM Bench Press") == "1RM bench press"
    assert analytics.humanise_benchmark("AMRAP 5 Minutes - Bar Muscle Ups") == \
        "5-minute AMRAP bar muscle-ups"
    assert analytics.humanise_benchmark("Max Kipping HSPU in 5 Minutes") == \
        "max kipping HSPU in 5 minutes"


def test_acronyms_survive():
    assert "HSPU" in analytics.humanise_benchmark("Max Strict HSPU")
    assert analytics.humanise_benchmark("2km Row").startswith("2km")


def test_blank_benchmark_is_safe():
    assert analytics.humanise_benchmark("") == ""
    assert analytics.humanise_benchmark(None) == ""


# ── the consistency streak ────────────────────────────────────────────────────

def _pr(name, date, bench="1RM Snatch"):
    return {"Athlete Name": name, "Date": date, "Benchmark Name": bench, "Value": "60"}


def test_streak_counts_back_from_last_month():
    today = dt.date(2026, 8, 1)
    rows = [_pr("A", "2026-07-10"), _pr("A", "2026-06-04"), _pr("A", "2026-05-20")]
    assert analytics.months_logging_streak(rows, "A", today=today) == 3


def test_a_gap_ends_the_streak():
    today = dt.date(2026, 8, 1)
    rows = [_pr("A", "2026-07-10"), _pr("A", "2026-05-20")]   # June missing
    assert analytics.months_logging_streak(rows, "A", today=today) == 1


def test_nothing_last_month_is_no_streak():
    today = dt.date(2026, 8, 1)
    assert analytics.months_logging_streak([_pr("A", "2026-06-01")], "A", today=today) == 0


def test_bodyweight_logs_do_not_build_a_streak():
    """Data, not results. They must not earn a consistency compliment."""
    today = dt.date(2026, 8, 1)
    rows = [_pr("A", "2026-07-10", bench="Bodyweight"),
            _pr("A", "2026-06-10", bench="Max Heart Rate")]
    assert analytics.months_logging_streak(rows, "A", today=today) == 0


def test_streak_matches_names_normalised():
    today = dt.date(2026, 8, 1)
    rows = [_pr("pat campbell jenner", "2026-07-10")]
    assert analytics.months_logging_streak(rows, "Pat Campbell-Jenner", today=today) == 1


def test_january_rolls_back_a_year():
    today = dt.date(2026, 1, 1)
    rows = [_pr("A", "2025-12-10"), _pr("A", "2025-11-10")]
    assert analytics.months_logging_streak(rows, "A", today=today) == 2


# ── multiple results worth shouting about ─────────────────────────────────────

def test_other_results_are_named_specifically():
    """Jak: "if there's multiple results to shout about let's recognise them
    too. List out the other specific results and what he got.\""""
    msg = analytics.monthly_message("Nick", [
        ("AMRAP 5 Minutes - Bar Muscle Ups", "56 reps"),
        ("1RM Clean (Full)", "100 kg"),
        ("1RM Back Squat", "180 kg"),
    ], streak_months=29)
    assert "You also hit 100 kg on the 1RM clean (full) and 180 kg on the 1RM back squat." in msg


def test_three_others_are_comma_joined_with_and():
    msg = analytics.monthly_message("Nick", [
        ("1RM Snatch", "80 kg"), ("1RM Clean", "100 kg"),
        ("2km Row", "7:20"), ("1RM Back Squat", "180 kg"),
    ])
    assert "100 kg on the 1RM clean, 7:20 on the 2km row and 180 kg on the 1RM back squat" in msg


def test_a_repeated_benchmark_is_listed_once():
    """Nick logged the 1RM clean five times in July."""
    msg = analytics.monthly_message("Nick", [
        ("1RM Clean", "95 kg"), ("1RM Clean", "98 kg"), ("1RM Clean", "100 kg"),
    ])
    assert msg.count("1RM clean") == 1
    assert "100 kg" in msg      # the most recent is kept


def test_a_big_month_gets_a_tail_count_because_it_is_worth_stating():
    results = [(f"Lift {i}", f"{i} kg") for i in range(1, 9)]
    msg = analytics.monthly_message("Nick", results)
    assert "That's on top of 4 other results." in msg


def test_one_leftover_result_is_not_announced_as_a_number():
    """"on top of 1 other result" is not a number worth writing home about."""
    results = [(f"Lift {i}", f"{i} kg") for i in range(1, 6)]
    msg = analytics.monthly_message("Nick", results)
    assert "on top of" not in msg


def test_a_pb_leads_even_when_logged_later():
    msg = analytics.monthly_message("Amy", [
        ("1RM Snatch", "60 kg", False),
        ("1RM Deadlift", "150 kg", True),
    ])
    assert msg.startswith("Hey Amy, saw you logged 150 kg on the 1RM deadlift last month.")


def test_a_single_result_still_reads_as_before():
    msg = analytics.monthly_message("Tom", [("1RM Overhead Press", "85 kg")], streak_months=8)
    assert "You also hit" not in msg
    assert "on top of" not in msg
    assert msg.startswith("Hey Tom, saw you logged 85 kg on the 1RM overhead press last month.")
