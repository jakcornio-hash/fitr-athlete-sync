"""Athletes being billed while not training.

Found while fixing MRR: a dozen athletes who joined years ago and have never
logged a session were sitting inside the revenue figure, and two on a Missed
Payment status were being counted at full price. A £ total hides all of that;
these produce names a coach can act on.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics

TODAY = dt.date(2026, 7, 31)


def _athlete(name, status="All Good", tier="Standard", join="01/01/2024"):
    return {"Full Name": name, "Fitr Status": status,
            "Programming Tier": tier, "Join Date": join}


def _log(name, date_str):
    return {"Athlete Name": name, "Date": date_str}


def _run(data, prs, **kw):
    kw.setdefault("monthly_value_fn", lambda r: 54.99)
    return analytics.revenue_anomalies(data, prs, today=TODAY, **kw)


# ── the three reasons ─────────────────────────────────────────────────────────

def test_never_logged_is_flagged():
    rows = _run([_athlete("Harry Hanford")], [])
    assert len(rows) == 1
    assert rows[0]["reason"] == "Never logged a session"
    assert rows[0]["last_logged"] == "never"
    assert rows[0]["monthly_value"] == 54.99


def test_long_dormant_is_flagged_with_the_day_count():
    rows = _run([_athlete("Lee Brown")], [_log("Lee Brown", "2025-07-21")])
    assert len(rows) == 1
    assert "No session in" in rows[0]["reason"]
    assert rows[0]["days_since"] == (TODAY - dt.date(2025, 7, 21)).days


def test_missed_payment_is_flagged_even_when_training():
    """Billing has failed but they are still being counted at full price."""
    rows = _run([_athlete("Tomas Dempsey", status="Missed Payment")],
                [_log("Tomas Dempsey", "2026-07-30")])
    assert len(rows) == 1
    assert rows[0]["reason"] == "Missed payment"


# ── who must NOT be flagged ───────────────────────────────────────────────────

def test_active_athlete_is_not_flagged():
    rows = _run([_athlete("Nick Flint")], [_log("Nick Flint", "2026-07-29")])
    assert rows == []


def test_gone_athletes_are_not_a_billing_question():
    """They are already off the roster; flagging them is noise."""
    gone = {analytics.normalise_client_name("Old Client")}
    rows = _run([_athlete("Old Client")], [], gone_norm=gone)
    assert rows == []


def test_dormant_threshold_is_respected():
    recent = (TODAY - dt.timedelta(days=45)).isoformat()
    assert _run([_athlete("A")], [_log("A", recent)]) == []
    assert len(_run([_athlete("A")], [_log("A", recent)], dormant_days=30)) == 1


def test_blank_name_rows_are_skipped():
    assert _run([_athlete("")], []) == []


# ── ordering and shape ────────────────────────────────────────────────────────

def test_missed_payments_come_first():
    data = [_athlete("Dormant Person"), _athlete("Broke Billing", status="Missed Payment")]
    prs = [_log("Dormant Person", "2024-01-01"), _log("Broke Billing", "2026-07-30")]
    rows = _run(data, prs)
    assert rows[0]["name"] == "Broke Billing"


def test_longest_silent_first_among_dormant():
    data = [_athlete("Recent"), _athlete("Ancient")]
    prs = [_log("Recent", "2026-01-01"), _log("Ancient", "2024-01-01")]
    rows = _run(data, prs)
    assert [r["name"] for r in rows] == ["Ancient", "Recent"]


def test_latest_log_wins_not_the_first_seen():
    rows = _run([_athlete("A")],
                [_log("A", "2024-01-01"), _log("A", "2026-07-30")])
    assert rows == []


def test_one_row_per_athlete_with_the_worst_reason():
    """A missed payment who also never logged appears once, as missed payment."""
    rows = _run([_athlete("Both", status="Missed Payment")], [])
    assert len(rows) == 1
    assert rows[0]["reason"] == "Missed payment"
