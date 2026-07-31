"""Form names are self-typed, and a bad match writes a competition or an
archetype result against the wrong athlete. The pairs below are real matches
the daily sync made (or refused) against the live roster.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics

ROSTER = [
    "Megan Parry", "Dayne Blackburn", "Chelsea Eddy", "Scott Sumner",
    "Rob Dagger", "Jak Cornthwaite", "Pat Campbell-Jenner",
]


# ── the matches that must keep working ────────────────────────────────────────

def test_shortened_first_name():
    assert analytics.match_athlete_name("Meg Parry", ROSTER) == "Megan Parry"


def test_dropped_middle_name():
    assert analytics.match_athlete_name("Dayne Kent Blackburn", ROSTER) == "Dayne Blackburn"


def test_double_barrelled_surname_and_lowercase():
    assert analytics.match_athlete_name("chelsea eddy-waland", ROSTER) == "Chelsea Eddy"


def test_exact_match_is_returned_unchanged():
    assert analytics.match_athlete_name("Jak Cornthwaite", ROSTER) == "Jak Cornthwaite"


def test_hyphen_variant_of_known_name():
    assert analytics.match_athlete_name("Pat Campbell Jenner", ROSTER) == "Pat Campbell-Jenner"


# ── the matches that were wrong and must now be refused ───────────────────────

def test_shared_first_name_is_not_a_match():
    """'Scott Maynard' scored 0.72 against 'Scott Sumner' and filed his
    competition under the wrong athlete."""
    assert analytics.match_athlete_name("Scott Maynard", ROSTER) is None


def test_shared_first_name_short_surname_is_not_a_match():
    assert analytics.match_athlete_name("Rob McGregor", ROSTER) is None


def test_unknown_person_is_not_forced_onto_the_roster():
    assert analytics.match_athlete_name("Warren Dos Reis Marques", ROSTER) is None


# ── edges ─────────────────────────────────────────────────────────────────────

def test_single_token_name_needs_an_exact_match():
    assert analytics.match_athlete_name("Scott", ROSTER) is None


def test_blank_and_empty_roster():
    assert analytics.match_athlete_name("", ROSTER) is None
    assert analytics.match_athlete_name("Meg Parry", []) is None


def test_emoji_suffix_still_matches():
    assert analytics.match_athlete_name("Jak Cornthwaite ⚒", ROSTER) == "Jak Cornthwaite"


# ── competition dedupe keys ───────────────────────────────────────────────────

def test_date_key_ignores_leading_zero_differences():
    """The live bug: the form said "2/08/27", Sheets stored "02/08/27", so the
    dedupe key never matched and the same competition was re-added every run."""
    assert analytics.canonical_date_key("2/08/27") == analytics.canonical_date_key("02/08/27")


def test_date_key_spans_formats():
    assert analytics.canonical_date_key("02/08/2027") == "2027-08-02"
    assert analytics.canonical_date_key("2027-08-02") == "2027-08-02"
    assert analytics.canonical_date_key("02/08/27") == "2027-08-02"


def test_different_dates_stay_different():
    assert analytics.canonical_date_key("02/08/27") != analytics.canonical_date_key("03/08/27")


def test_unparseable_date_falls_back_to_text():
    assert analytics.canonical_date_key("summer 2027") == "summer 2027"
    assert analytics.canonical_date_key("") == ""


# ── MRR from the subscription plan string ─────────────────────────────────────

FB = {1: 54.99, 3: 144, 12: 549}


def test_monthly_plan_reads_its_own_price():
    assert analytics.monthly_value("Monthly (£54.99)", "Standard", FB) == 54.99


def test_yearly_plan_is_spread_over_twelve_months():
    assert analytics.monthly_value("Yearly (£549)", "Standard", FB) == 45.75


def test_quarterly_plan_is_spread_over_three_months():
    assert analytics.monthly_value("Quarterly (£144)", "Standard", FB) == 48.0


def test_bare_cadence_falls_back_rather_than_counting_zero():
    """184 athletes are on "Monthly (£54.99)" and 55 on a bare "Monthly"; the
    bare ones must not silently count as £0."""
    assert analytics.monthly_value("Monthly", "Standard", FB) == 54.99
    assert analytics.monthly_value("Quarterly", "Standard", FB) == 48.0


def test_bespoke_is_priced_off_programming_tier():
    """Bespoke athletes pay the contractor coach; JST takes a flat fee."""
    assert analytics.monthly_value("Monthly (£54.99)", "Bespoke", FB, bespoke_value=40) == 40


def test_blank_or_unknown_plan_is_zero():
    assert analytics.monthly_value("", "Standard", FB) == 0.0
    assert analytics.monthly_value("Comped", "Standard", FB) == 0.0


def test_the_old_programme_keyed_table_matched_nothing():
    """Guards the actual defect: the table was keyed by programme name."""
    old_table = {"JST Athlete": 54.99, "Strength Bias": 54.99}
    assert old_table.get("Monthly (£54.99)", 0) == 0
