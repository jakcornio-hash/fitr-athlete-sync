"""Stay under the Sheets quota instead of recovering from breaching it.

On 5 August the daily sync died:

    gspread.exceptions.APIError: [429]: Quota exceeded for quota metric
    'Read requests' and limit 'Read requests per minute per user'

It died at the auto-onboard stage, roughly two thirds of the way through
main(), so every stage after it was skipped: the anniversaries, the new
athlete welcomes, the competition messages, the monthly check-in, the message
log, and the Sync Log row that tells the dashboard the run happened at all.

The retry ladder was already there and did not save it. Waiting 62 seconds
cannot clear a burst that put several minutes of quota into one minute, so the
only real fix is to not build the burst. Requests are now booked into a rolling
one-minute window before they are sent.

Clocks are injected here rather than slept through, so the tests are instant
and deterministic.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import sheets_client


class Clock:
    """A fake clock whose sleep() advances time instead of passing it."""

    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds

    def tick(self, seconds):
        self.t += seconds


def setup_function():
    sheets_client.reset_throttle_state()


def _burst(clock, n, kind="read"):
    for _ in range(n):
        sheets_client._await_quota_slot(kind, now=clock.now, sleep=clock.sleep)


# ── the limit itself ──────────────────────────────────────────────────────────

def test_a_run_under_the_limit_never_waits():
    c = Clock()
    _burst(c, sheets_client._QUOTA_PER_MINUTE)
    assert c.slept == []


def test_one_request_past_the_limit_waits_for_a_slot():
    c = Clock()
    _burst(c, sheets_client._QUOTA_PER_MINUTE)
    _burst(c, 1)
    assert len(c.slept) == 1
    assert c.slept[0] == 60.0        # the oldest was issued at t+0


def test_the_ceiling_leaves_headroom_under_googles_60():
    """A coach with the dashboard open shares this quota."""
    assert sheets_client._QUOTA_PER_MINUTE < 60


def test_the_burst_that_killed_the_sync_is_now_paced():
    """400 reads back to back: the shape that produced the live 429."""
    c = Clock()
    _burst(c, 400)
    # Nothing may exceed the limit in any single window.
    assert len(sheets_client._request_times["read"]) <= sheets_client._QUOTA_PER_MINUTE
    # And it took real time rather than a burst: 400 reads at 50/min is 7 min.
    assert c.t - 1000.0 >= 420


def test_old_requests_age_out_of_the_window():
    c = Clock()
    _burst(c, sheets_client._QUOTA_PER_MINUTE)
    c.tick(61)
    _burst(c, sheets_client._QUOTA_PER_MINUTE)
    assert c.slept == []


def test_a_slow_stage_never_accumulates_a_debt():
    """Reads spread thinly over an hour must not be throttled at all."""
    c = Clock()
    for _ in range(200):
        _burst(c, 1)
        c.tick(2)
    assert c.slept == []


# ── reads and writes are separate quotas ──────────────────────────────────────

def test_writes_do_not_consume_the_read_allowance():
    c = Clock()
    _burst(c, sheets_client._QUOTA_PER_MINUTE, kind="write")
    _burst(c, sheets_client._QUOTA_PER_MINUTE, kind="read")
    assert c.slept == []


def test_the_method_decides_which_quota():
    assert sheets_client._kind("get") == "read"
    assert sheets_client._kind("GET") == "read"
    assert sheets_client._kind("post") == "write"
    assert sheets_client._kind("put") == "write"


# ── reporting ─────────────────────────────────────────────────────────────────

def test_a_clear_run_reports_nothing():
    c = Clock()
    _burst(c, 10)
    assert sheets_client.throttle_summary() == ""


def test_a_throttled_run_says_so_with_numbers():
    c = Clock()
    _burst(c, sheets_client._QUOTA_PER_MINUTE + 5)
    summary = sheets_client.throttle_summary()
    assert "Sheets throttle" in summary
    assert f"{sheets_client._QUOTA_PER_MINUTE + 5} reads" in summary
