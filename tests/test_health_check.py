"""The health check is the thing that is supposed to notice next time, so it
needs to fail loudly on the exact shapes that got past everyone before.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
import health_check


class FakeSheets:
    """Minimal stand-in: {tab: [[header], [row], ...]}."""

    def __init__(self, tabs):
        self.tabs = tabs

    def read_values(self, tab):
        if tab not in self.tabs:
            raise RuntimeError(f"WorksheetNotFound: {tab}")
        return self.tabs[tab]

    def read_records(self, tab):
        vals = self.read_values(tab)
        if not vals:
            return []
        header = [str(h).strip() for h in vals[0]]
        return [{h: (r[i] if i < len(r) else "") for i, h in enumerate(header) if h}
                for r in vals[1:]]


def _findings_titles(findings):
    return " | ".join(f.title for f in findings)


# ── schema drift: the Sync Log bug ────────────────────────────────────────────

def test_missing_column_is_a_failure():
    """The exact Sync Log shape: writer emits 10 values, header still has 6."""
    sheets = FakeSheets({config.TAB_SYNC_LOG: [
        ["Run Date", "New PR Log rows", "Challenge scores added",
         "Conversations summarised", "New/unknown athletes seen", "Notes", "", "", "", ""],
        ["2026-07-31", "299", "6", "9", "26", "27", "39", "2", "0", "ok"],
    ]})
    found = health_check.check_sheet_schemas(sheets)
    sync_findings = [f for f in found if config.TAB_SYNC_LOG in f.title]
    assert any(f.severity == health_check.FAIL for f in sync_findings)
    assert "Total Athletes" in _findings_titles(found) + " ".join(f.detail for f in found)


def test_duplicate_headers_are_a_failure():
    sheets = FakeSheets({"Active Roster": [
        ["Full Name", "Full Name"], ["Amy R", "Amy R"],
    ]})
    found = health_check.check_sheet_schemas(sheets)
    assert any(f.severity == health_check.FAIL and "duplicate" in f.title.lower()
               for f in found)


def test_blank_header_with_data_is_flagged():
    sheets = FakeSheets({"Active Roster": [
        ["Full Name", ""], ["Amy R", "orphaned value"],
    ]})
    found = health_check.check_sheet_schemas(sheets)
    assert any("blank header" in f.title for f in found)


def test_completely_empty_column_is_a_failure():
    """A column the code reads that is never populated — how bespoke broke."""
    sheets = FakeSheets({config.TAB_DATA: [
        ["Full Name", "Email", "Programming Tier", "Subscription Plan",
         "Fitr Status", "Coaching Notes", "Join Date", "North Star Goal"],
        ["Amy R", "a@b.c", "", "Monthly", "All Good", "", "2026-01-01", ""],
        ["Ben H", "b@b.c", "", "Monthly", "All Good", "", "2026-01-01", ""],
    ]})
    found = health_check.check_sheet_schemas(sheets)
    assert any(f.severity == health_check.FAIL and "Programming Tier" in f.title
               for f in found)


def test_healthy_tab_produces_nothing():
    sheets = FakeSheets({"Active Roster": [["Full Name"], ["Amy R"], ["Ben H"]]})
    found = [f for f in health_check.check_sheet_schemas(sheets)
             if "Active Roster" in f.title]
    assert found == []


# ── suppression rules that match nobody ───────────────────────────────────────

def test_bespoke_matching_nobody_is_a_failure():
    """Keyed off the wrong column, this matched 0 athletes for months."""
    data = [{"Programming Tier": "Standard", "Full Name": "Amy R"}]
    found = health_check.check_suppression_rules_match_someone(data, set(), {"x"})
    assert any(f.severity == health_check.FAIL and "Bespoke" in f.title for f in found)


def test_bespoke_present_and_matched_is_clean():
    data = [{"Programming Tier": "Bespoke", "Full Name": "Amy R"}]
    found = health_check.check_suppression_rules_match_someone(data, {"Amy R"}, {"x"})
    assert [f for f in found if f.severity == health_check.FAIL] == []


def test_empty_gone_list_is_flagged():
    data = [{"Programming Tier": "Bespoke", "Full Name": "Amy R"}]
    found = health_check.check_suppression_rules_match_someone(data, {"Amy R"}, set())
    assert any("genuinely-gone list is empty" in f.title for f in found)


# ── cancelled athletes back on the lists ──────────────────────────────────────

class FakeAnalytics:
    @staticmethod
    def normalise_client_name(s):
        import re
        return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def test_cancelled_athlete_on_engagement_list_is_a_failure():
    engagement = [{"name": "Gone Person"}, {"name": "Current Person"}]
    gone = {"goneperson"}
    found = health_check.check_cancelled_not_on_lists(engagement, gone, FakeAnalytics)
    assert len(found) == 1
    assert found[0].severity == health_check.FAIL
    assert "Gone Person" in found[0].detail


def test_clean_engagement_list_produces_nothing():
    engagement = [{"name": "Current Person"}]
    found = health_check.check_cancelled_not_on_lists(engagement, {"goneperson"}, FakeAnalytics)
    assert found == []


# ── the pending queue nobody is working ───────────────────────────────────────

def _pending_tab(rows):
    header = ["Date", "Athlete Name", "Message Type", "Message", "Room ID", "Status"]
    return FakeSheets({config.TAB_PENDING_MESSAGES: [header] + rows})


def test_stale_pending_drafts_are_a_failure():
    old = (dt.date.today() - dt.timedelta(days=6)).isoformat()
    sheets = _pending_tab([[old, "Amy R", "congrats", "well done", "1", "pending"]])
    found = health_check.check_pending_message_queue(sheets)
    assert len(found) == 1
    assert found[0].severity == health_check.FAIL
    assert "Amy R" in found[0].detail


def test_fresh_pending_drafts_are_fine():
    today = dt.date.today().isoformat()
    sheets = _pending_tab([[today, "Amy R", "congrats", "well done", "1", "pending"]])
    assert health_check.check_pending_message_queue(sheets) == []


def test_sent_drafts_are_not_counted_as_stale():
    old = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    sheets = _pending_tab([[old, "Amy R", "congrats", "well done", "1", "sent"]])
    assert health_check.check_pending_message_queue(sheets) == []


def test_missing_pending_tab_is_not_an_error():
    """The tab only appears on the first sync that queues something."""
    assert health_check.check_pending_message_queue(FakeSheets({})) == []


# ── reporting ─────────────────────────────────────────────────────────────────

def test_format_findings_puts_failures_first():
    findings = [
        health_check.Finding(health_check.WARN, "sheets", "minor thing"),
        health_check.Finding(health_check.FAIL, "dashboard", "big thing", "detail"),
    ]
    plain, slack = health_check.format_findings(findings)
    assert plain.index("big thing") < plain.index("minor thing")
    assert "BROKEN" in plain and "WORTH A LOOK" in plain
    assert "big thing" in slack


def test_no_findings_renders_nothing():
    assert health_check.format_findings([]) == ("", "")


def test_a_broken_check_does_not_break_the_sync():
    class Exploding:
        def read_values(self, tab):
            raise RuntimeError("boom")

        def read_records(self, tab):
            raise RuntimeError("boom")

        def load_exit_autopsy(self):
            raise RuntimeError("boom")

    findings = health_check.run_health_check(Exploding(), FakeAnalytics)
    assert isinstance(findings, list)   # returned rather than raised


# ── data-hygiene checks ───────────────────────────────────────────────────────

def test_duplicate_athlete_row_is_flagged():
    sheets = FakeSheets({config.TAB_DATA: [
        ["Full Name"], ["Andreas Sinados"], ["Andreas Sinados"], ["Amy R"],
    ]})
    found = health_check.check_duplicate_athlete_rows(sheets)
    assert len(found) == 1
    assert "Andreas Sinados" in found[0].detail


def test_single_rows_are_not_flagged():
    sheets = FakeSheets({config.TAB_DATA: [["Full Name"], ["Amy R"], ["Ben H"]]})
    assert health_check.check_duplicate_athlete_rows(sheets) == []


def test_unrecognised_programming_tier_is_flagged():
    """Intake-form prose landing in the column that drives message suppression."""
    sheets = FakeSheets({config.TAB_DATA: [
        ["Full Name", "Programming Tier"],
        ["Amy R", "Standard"],
        ["Ben H", "Elite / Quarterfinalist (3+ years experience)"],
    ]})
    found = health_check.check_programming_tier_values(sheets)
    assert len(found) == 1
    assert "Elite" in found[0].detail


def test_known_tiers_and_blanks_are_accepted():
    sheets = FakeSheets({config.TAB_DATA: [
        ["Full Name", "Programming Tier"],
        ["A", "Standard"], ["B", "Bespoke"], ["C", ""], ["D", "bespoke"],
    ]})
    assert health_check.check_programming_tier_values(sheets) == []


def test_crm_rejoins_are_reported():
    class A:
        @staticmethod
        def cancelled_athletes(exit_rows, pr):
            return set(), ["Abi Evans", "Andy Saxon"]

    class S:
        def load_exit_autopsy(self):
            return []

        def read_records(self, tab):
            return []

    found = health_check.check_crm_says_gone_but_training(S(), A)
    assert len(found) == 1
    assert "Abi Evans" in found[0].detail


def test_no_rejoins_is_quiet():
    class A:
        @staticmethod
        def cancelled_athletes(exit_rows, pr):
            return set(), []

    class S:
        def load_exit_autopsy(self):
            return []

        def read_records(self, tab):
            return []

    assert health_check.check_crm_says_gone_but_training(S(), A) == []
