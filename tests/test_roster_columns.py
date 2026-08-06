"""The Active Roster keeps only names, and throws away the useful half.

The tab is one column, 252 names. The Fitr client list it is pasted from
carries each athlete's plan, membership status and plan start day, and all of
it is dropped on the way in. That paste is the only monthly snapshot of who was
a client, taken by hand from the system of record, so those columns are worth
keeping the moment they arrive.

Headers are matched by keyword, not exact string: the export's headings are not
ours to control, and a rename must degrade to "column absent" rather than break
the roster read that keeps cancelled athletes off every list.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics

TODAY = dt.date(2026, 8, 6)


# ── today's tab: names only ───────────────────────────────────────────────────

def test_a_name_only_roster_still_reads():
    rows = analytics.roster_rows([{"Full Name": "Amy Reed"}, {"Full Name": "Ben Sample"}])
    assert [r["name"] for r in rows] == ["Amy Reed", "Ben Sample"]
    assert rows[0]["start_day"] is None
    assert rows[0]["status"] == ""


def test_a_name_only_roster_reports_what_is_missing():
    present = analytics.roster_columns_present([{"Full Name": "Amy Reed"}])
    assert present == {"start_day": False, "status": False, "plan": False}


def test_blank_names_are_dropped():
    rows = analytics.roster_rows([{"Full Name": "Amy Reed"}, {"Full Name": "  "}])
    assert len(rows) == 1


def test_an_empty_tab_is_safe():
    assert analytics.roster_rows([]) == []
    assert analytics.roster_rows(None) == []


def test_a_tab_with_no_name_column_yields_nothing():
    """Better empty than inventing names out of some other column."""
    assert analytics.roster_rows([{"Plan": "JST Athlete", "Status": "active"}]) == []


# ── the richer paste, once someone keeps the columns ──────────────────────────

FULL = [{"Full Name": "Amy Reed", "Plan": "JST Athlete (Access All Areas)",
         "Membership Status": "active", "Start Day": "03/06/2026"}]


def test_the_extra_columns_are_used_when_present():
    r = analytics.roster_rows(FULL, today=TODAY)[0]
    assert r["name"] == "Amy Reed"
    assert r["start_day"] == dt.date(2026, 6, 3)
    assert r["status"] == "active"
    assert r["plan"] == "JST Athlete (Access All Areas)"


def test_presence_is_reported():
    assert analytics.roster_columns_present(FULL) == {
        "start_day": True, "status": True, "plan": True}


def test_header_spellings_that_should_all_work():
    for header in ("Start Day", "start_day", "Join Date", "Joined", "Member Since"):
        rows = analytics.roster_rows([{"Full Name": "Amy Reed", header: "03/06/2026"}],
                                     today=TODAY)
        assert rows[0]["start_day"] == dt.date(2026, 6, 3), header


def test_status_header_spellings():
    for header in ("Status", "Membership Status", "Fitr Status", "State"):
        rows = analytics.roster_rows([{"Full Name": "Amy Reed", header: "active"}])
        assert rows[0]["status"] == "active", header


def test_an_athlete_name_header_does_not_get_read_as_the_plan():
    """"Athlete Name" contains "athlete", which is also a plan keyword. If the
    name column were matched twice the plan would come out as the name."""
    rows = analytics.roster_rows([{"Athlete Name": "Amy Reed", "Plan": "JST Athlete"}])
    assert rows[0]["name"] == "Amy Reed"
    assert rows[0]["plan"] == "JST Athlete"


def test_the_future_dates_in_the_live_data_are_still_rejected():
    """The same 2044 values that are in plan.start_day will come through a
    paste of the same field."""
    rows = analytics.roster_rows(
        [{"Full Name": "Amy Reed", "Start Day": "23/07/2044"}], today=TODAY)
    assert rows[0]["start_day"] is None


def test_an_unparseable_date_does_not_lose_the_athlete():
    rows = analytics.roster_rows(
        [{"Full Name": "Amy Reed", "Start Day": "n/a"}], today=TODAY)
    assert rows[0]["name"] == "Amy Reed"
    assert rows[0]["start_day"] is None


def test_a_partial_paste_is_fine():
    """Status kept, start day not."""
    rows = analytics.roster_rows([{"Full Name": "Amy Reed", "Status": "active"}])
    assert rows[0]["status"] == "active"
    assert rows[0]["start_day"] is None
    present = analytics.roster_columns_present([{"Full Name": "Amy Reed", "Status": "active"}])
    assert present["status"] is True and present["start_day"] is False


# ── it feeds the anchor ───────────────────────────────────────────────────────

def test_roster_start_days_can_anchor_first_seen():
    """The point of keeping the column: an athlete with no logged result still
    gets a date instead of dropping out of every cohort."""
    rows = analytics.roster_rows(FULL, today=TODAY)
    starts = {r["name"]: r["start_day"] for r in rows if r["start_day"]}
    out = analytics.first_seen_dates([], starts, today=TODAY)
    assert out["Amy Reed"]["first_seen"] == dt.date(2026, 6, 3)


# ── the health check that reports it ──────────────────────────────────────────

import health_check


class _Sheets:
    def __init__(self, roster, data):
        self._roster, self._data = roster, data

    def read_records(self, tab):
        return self._roster if tab == "Active Roster" else self._data


def _findings(roster, data):
    return health_check.check_join_date_is_not_trusted(_Sheets(roster, data), analytics)


NAME_ONLY = [{"Full Name": "Amy Reed"}, {"Full Name": "Ben Sample"}]
ANCHORED = [{"Full Name": "Amy Reed", "First Seen": "2026-03-01"},
            {"Full Name": "Ben Sample", "First Seen": "2026-04-02"}]


def test_a_name_only_paste_is_reported_with_what_is_missing():
    out = _findings(NAME_ONLY, ANCHORED)
    titles = [f.title for f in out]
    assert "The Active Roster paste is keeping names only" in titles
    detail = next(f.detail for f in out if f.title.startswith("The Active Roster"))
    for expected in ("plan start day", "membership status", "plan name"):
        assert expected in detail


def test_a_full_paste_raises_nothing_about_columns():
    roster = [{"Full Name": "Amy Reed", "Start Day": "01/03/2026",
               "Status": "active", "Plan": "JST Athlete"}]
    data = [{"Full Name": "Amy Reed", "First Seen": "2026-03-01"}]
    assert not any(f.title.startswith("The Active Roster") for f in _findings(roster, data))


def test_no_first_seen_at_all_is_reported():
    out = _findings(NAME_ONLY, [{"Full Name": "Amy Reed"}, {"Full Name": "Ben Sample"}])
    assert any("No athlete has a First Seen date yet" == f.title for f in out)


def test_partial_first_seen_coverage_is_reported():
    data = [{"Full Name": f"Athlete {i}", "First Seen": "2026-03-01" if i < 5 else ""}
            for i in range(20)]
    roster = [{"Full Name": f"Athlete {i}"} for i in range(20)]
    out = _findings(roster, data)
    assert any("Only 5 of 20" in f.title for f in out)


def test_full_coverage_is_not_reported():
    assert not any("First Seen" in f.title for f in _findings(NAME_ONLY, ANCHORED))


def test_an_empty_roster_does_not_produce_a_false_alarm():
    """A failed roster read must not read as "nobody has an anchor"."""
    assert _findings([], ANCHORED) == []


def test_the_check_never_raises_on_a_broken_sheet():
    class Broken:
        def read_records(self, tab):
            raise RuntimeError("429")
    assert health_check.check_join_date_is_not_trusted(Broken(), analytics) == []


def test_roster_athletes_with_no_data_row_are_reported():
    """Found because First Seen resolved for more athletes than it could write.
    34 current clients had no _DATA row, so they were invisible to the squad
    views, the action list and the billing check, and nothing said so."""
    roster = [{"Full Name": "Amy Reed"}, {"Full Name": "Ghost Client"}]
    data = [{"Full Name": "Amy Reed", "First Seen": "2026-03-01"}]
    out = _findings(roster, data)
    f = next(f for f in out if "no _DATA row" in f.title)
    assert f.severity == health_check.FAIL
    assert "Ghost Client" in f.detail
    assert "billing check" in f.detail


def test_a_fully_present_roster_reports_no_gap():
    assert not any("no _DATA row" in f.title for f in _findings(NAME_ONLY, ANCHORED))


def test_the_missing_row_check_matches_names_normalised():
    roster = [{"Full Name": "Pat Campbell-Jenner"}]
    data = [{"Full Name": "pat campbell jenner", "First Seen": "2026-03-01"}]
    assert not any("no _DATA row" in f.title for f in _findings(roster, data))
