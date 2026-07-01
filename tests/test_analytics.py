"""Unit tests for analytics.py core functions."""
import sys
import os
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics


def _pr(name, bench, date_str, value):
    return {"Athlete Name": name, "Benchmark Name": bench, "Date": date_str, "Value": str(value)}


# ── comp_phase ────────────────────────────────────────────────────────────────

def test_comp_phase_peak():
    phase, action = analytics.comp_phase(10, "A")
    assert phase == "2-Week Peak Prep"
    assert action is None

def test_comp_phase_switch_to_prep():
    phase, action = analytics.comp_phase(20, "A")
    assert "Switch" in phase
    assert action is not None

def test_comp_phase_post():
    phase, action = analytics.comp_phase(-5, "A")
    assert phase == "Post-Competition"

def test_comp_phase_far_out():
    phase, action = analytics.comp_phase(300, "A")
    assert phase == "Normal Training"

def test_comp_phase_c_race_week():
    phase, action = analytics.comp_phase(3, "C")
    assert "C" in phase

def test_comp_phase_b_post():
    phase, _ = analytics.comp_phase(-3, "B")
    assert phase == "Post-Competition"


# ── churn_risk_score ──────────────────────────────────────────────────────────

def test_churn_risk_never_logged():
    eng = [{"name": "Alice", "last_logged": "never", "days_since": None, "flag": True, "nudge_flag": False}]
    result = analytics.churn_risk_score("Alice", eng, {})
    assert result["score"] >= 60
    assert result["label"] == "🔴 Critical"

def test_churn_risk_recently_active():
    eng = [{"name": "Bob", "last_logged": "2025-01-01", "days_since": 3, "flag": False, "nudge_flag": False}]
    result = analytics.churn_risk_score("Bob", eng, {})
    assert result["score"] < 15
    assert result["label"] == "🟢 Low"

def test_churn_risk_declining_trends():
    eng = [{"name": "Carol", "last_logged": "2025-01-01", "days_since": 3, "flag": False, "nudge_flag": False}]
    trends = {"Carol": [
        {"trend": "declining", "peak_drop_flag": False},
        {"trend": "declining", "peak_drop_flag": False},
    ]}
    result = analytics.churn_risk_score("Carol", eng, trends)
    assert result["score"] >= 10


# ── engagement_check ──────────────────────────────────────────────────────────

def test_engagement_check_flags_inactive():
    prs = [_pr("Alice", "Squat", "2020-01-01", 100)]
    athletes = [{"name": "Alice", "jst_id": "A1"}]
    results = analytics.engagement_check(prs, athletes, threshold_days=21)
    assert results[0]["flag"] is True

def test_engagement_check_active_not_flagged():
    today = dt.date.today()
    prs = [_pr("Bob", "Squat", today.isoformat(), 100)]
    athletes = [{"name": "Bob", "jst_id": "B1"}]
    results = analytics.engagement_check(prs, athletes, threshold_days=21)
    assert results[0]["flag"] is False

def test_engagement_check_never_logged():
    athletes = [{"name": "Dave", "jst_id": "D1"}]
    results = analytics.engagement_check([], athletes, threshold_days=21)
    assert results[0]["last_logged"] == "never"
    assert results[0]["flag"] is True


# ── consistency_check ─────────────────────────────────────────────────────────

def test_consistency_streak_detected():
    today = dt.date.today()
    mon = today - dt.timedelta(days=today.weekday())
    prs = []
    for w in range(5):
        d = mon - dt.timedelta(weeks=w)
        prs.append(_pr("Alice", "Squat", d.isoformat(), 100))
    athletes = [{"name": "Alice"}]
    wins = analytics.consistency_check(prs, athletes, min_consecutive_weeks=4)
    assert len(wins) == 1
    assert wins[0][0] == "Alice"
    assert wins[0][1] >= 4

def test_consistency_no_streak():
    prs = [
        _pr("Bob", "Squat", "2020-01-01", 100),
        _pr("Bob", "Squat", "2020-03-01", 100),
    ]
    athletes = [{"name": "Bob"}]
    wins = analytics.consistency_check(prs, athletes, min_consecutive_weeks=4)
    assert wins == []


# ── pr_velocity ───────────────────────────────────────────────────────────────

def test_pr_velocity_improving():
    prs = [
        _pr("Alice", "Squat", "2024-01-01", 100),
        _pr("Alice", "Squat", "2024-02-01", 105),
        _pr("Alice", "Squat", "2024-03-01", 110),
    ]
    result = analytics.pr_velocity(prs)
    assert "Alice" in result
    alice = result["Alice"]
    bench_names = [r["benchmark"] for r in alice]
    assert "Squat" in bench_names
    squat = next(r for r in alice if r["benchmark"] == "Squat")
    assert squat["direction"] == "improving"
    assert squat["rate_pct_per_month"] > 0

def test_pr_velocity_not_enough_points():
    prs = [_pr("Alice", "Squat", "2024-01-01", 100)]
    result = analytics.pr_velocity(prs, min_points=2)
    assert "Alice" not in result


# ── cohort_retention ─────────────────────────────────────────────────────────

def test_cohort_retention_basic():
    prs = [
        _pr("Alice", "Squat", "2024-01-10", 100),
        _pr("Alice", "Squat", "2024-02-10", 105),  # retained at 30d
        _pr("Bob", "Squat", "2024-01-15", 80),
        # Bob has no follow-up → not retained
    ]
    result = analytics.cohort_retention(prs, min_cohort_size=2)
    jan_cohort = next((r for r in result if r["cohort"] == "2024-01"), None)
    assert jan_cohort is not None
    assert jan_cohort["n"] == 2
    if jan_cohort["pct_30d"] is not None:
        assert 0 <= jan_cohort["pct_30d"] <= 100


# ── training_load ─────────────────────────────────────────────────────────────

def test_training_load_basic():
    """Weekly counts across two athletes."""
    import datetime as dt
    # 3 logs Mon + 1 log Wed same week = 2 unique days
    monday = "2025-01-06"
    wednesday = "2025-01-08"
    next_monday = "2025-01-13"
    records = [
        {"Athlete Name": "Alice", "Date": monday, "Benchmark Name": "Back Squat", "Value": "100"},
        {"Athlete Name": "Alice", "Date": monday, "Benchmark Name": "Snatch", "Value": "70"},
        {"Athlete Name": "Alice", "Date": wednesday, "Benchmark Name": "Row", "Value": "7:00"},
        {"Athlete Name": "Alice", "Date": next_monday, "Benchmark Name": "Back Squat", "Value": "102"},
        {"Athlete Name": "Bob", "Date": monday, "Benchmark Name": "Deadlift", "Value": "150"},
    ]
    # Use a large weeks value so the Jan 2025 dates fall within the window
    result = analytics.training_load(records, weeks=9999)
    assert "Alice" in result
    assert "Bob" in result
    alice_sessions = {w["week"]: w["sessions"] for w in result["Alice"]}
    # Week containing Monday 6 Jan 2025 = 2025-W02
    assert alice_sessions.get("2025-W02") == 2
    assert alice_sessions.get("2025-W03") == 1
    bob_sessions = {w["week"]: w["sessions"] for w in result["Bob"]}
    assert bob_sessions.get("2025-W02") == 1


def test_training_load_empty():
    assert analytics.training_load([]) == {}


def test_training_load_deduplicates_same_day():
    """Multiple PRs on same day should count as 1 session."""
    records = [
        {"Athlete Name": "Alice", "Date": "2025-01-06", "Benchmark Name": "A", "Value": "1"},
        {"Athlete Name": "Alice", "Date": "2025-01-06", "Benchmark Name": "B", "Value": "2"},
        {"Athlete Name": "Alice", "Date": "2025-01-06", "Benchmark Name": "C", "Value": "3"},
    ]
    result = analytics.training_load(records, weeks=9999)
    alice_weeks = result.get("Alice", [])
    week = alice_weeks[0] if alice_weeks else {}
    assert week.get("sessions") == 1


# ── duplicate_candidates ──────────────────────────────────────────────────────

def test_duplicate_candidates_finds_near_match():
    athletes = [{"name": "John Smith"}, {"name": "Jon Smith"}]
    data_records = []
    pr_records = []
    result = analytics.duplicate_candidates(athletes, data_records, pr_records, threshold=0.80)
    assert len(result) >= 1
    pair = result[0]
    names = {pair["name_a"].lower(), pair["name_b"].lower()}
    assert "john smith" in names and "jon smith" in names
    assert pair["score"] >= 0.80


def test_duplicate_candidates_no_false_positives():
    athletes = [{"name": "Alice Jones"}, {"name": "Bob Smith"}, {"name": "Claire Wu"}]
    data_records = []
    pr_records = []
    result = analytics.duplicate_candidates(athletes, data_records, pr_records)
    assert result == []


def test_duplicate_candidates_exact_match_excluded():
    """Same name in two data sources — should not flag as duplicate."""
    athletes = [{"name": "Alice Jones"}]
    data_records = [{"Full Name": "Alice Jones"}]
    pr_records = [{"Athlete Name": "Alice Jones", "Date": "2025-01-01", "Benchmark Name": "A", "Value": "1"}]
    result = analytics.duplicate_candidates(athletes, data_records, pr_records)
    assert result == []


# ── leaderboard_data ──────────────────────────────────────────────────────────

def test_leaderboard_data_filters_single_athlete():
    """Benchmarks with only one athlete should not appear."""
    records = [
        {"Athlete Name": "Alice", "Date": "2025-01-01", "Benchmark Name": "Clean & Jerk", "Value": "80 kg"},
        {"Athlete Name": "Alice", "Date": "2025-01-01", "Benchmark Name": "Back Squat", "Value": "100 kg"},
        {"Athlete Name": "Bob",   "Date": "2025-01-01", "Benchmark Name": "Back Squat", "Value": "120 kg"},
    ]
    lb = analytics.leaderboard_data(records)
    # Clean & Jerk only has 1 athlete → should not appear
    assert "Clean & Jerk" not in (lb.get("all_benchmarks") or [])
    # Back Squat has 2 → should appear
    assert "Back Squat" in (lb.get("all_benchmarks") or [])


def test_leaderboard_data_empty():
    lb = analytics.leaderboard_data([])
    assert lb.get("athletes") == [] or lb.get("latest") == {}


if __name__ == "__main__":
    import traceback, sys
    passed = failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception as e:
                print(f"  FAIL  {name}: {e}")
                traceback.print_exc()
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
