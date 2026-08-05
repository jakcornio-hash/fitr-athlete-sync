"""One failed stage must cost one stage, not the rest of the run.

On 5 August a 429 from Sheets landed inside auto-onboard, roughly two thirds
of the way through main(). The process ended there, so everything after it was
skipped in silence: the anniversaries, the new athlete welcomes, the
competition messages, the monthly check-in, the message log, and the Sync Log
row that tells the dashboard the run happened at all.

The stages are independent of one another. Only the process was not.

The structural tests below are the ones that matter over time: they read
sync.py itself, so a stage added later without isolation, or a value that
outlives its stage without a default, fails the suite rather than waiting for
a bad morning.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import sync

SRC = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "sync.py")).read()
TREE = ast.parse(SRC)
MAIN = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "main")


def setup_function():
    sync.STAGE_FAILURES.clear()


# ── the context manager ───────────────────────────────────────────────────────

def test_a_failing_stage_does_not_propagate():
    with sync.stage("a"):
        raise RuntimeError("boom")
    # reaching here at all is the assertion


def test_the_next_stage_still_runs():
    ran = []
    with sync.stage("a"):
        raise RuntimeError("boom")
    with sync.stage("b"):
        ran.append("b")
    assert ran == ["b"]


def test_the_failure_is_recorded_with_its_cause():
    with sync.stage("auto-onboard new athletes"):
        raise RuntimeError("Quota exceeded for quota metric 'Read requests'")
    assert len(sync.STAGE_FAILURES) == 1
    name, detail = sync.STAGE_FAILURES[0]
    assert name == "auto-onboard new athletes"
    assert "RuntimeError" in detail
    assert "Quota exceeded" in detail


def test_a_clean_stage_records_nothing():
    with sync.stage("a"):
        pass
    assert sync.STAGE_FAILURES == []


def test_keyboard_interrupt_is_not_swallowed():
    """Ctrl-C and SystemExit must still stop the run."""
    try:
        with sync.stage("a"):
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        return
    raise AssertionError("KeyboardInterrupt was swallowed")


# ── the shape of main(), enforced ─────────────────────────────────────────────

def _stage_labels():
    return [n.items[0].context_expr.args[0].value
            for n in ast.walk(MAIN)
            if isinstance(n, ast.With)
            and isinstance(n.items[0].context_expr, ast.Call)
            and getattr(n.items[0].context_expr.func, "id", "") == "stage"]


def test_every_stage_after_the_digest_is_isolated():
    """The stages that ran after the point of failure on 5 August."""
    labels = _stage_labels()
    for expected in ("auto-onboard new athletes", "athlete anniversaries",
                     "new athlete onboarding", "pre-competition messages",
                     "post-competition messages", "weekly progress emails",
                     "monthly Fitr check-in", "message log and replies",
                     "sync log", "flush pending drafts"):
        assert expected in labels, f"stage {expected!r} is not isolated"


def test_the_sync_log_row_is_written_inside_its_own_stage():
    """It is the record that the run happened, so it must survive a failure
    anywhere above it and must not take the draft queue down with it."""
    labels = _stage_labels()
    assert labels.index("sync log") < labels.index("flush pending drafts")


def test_nothing_that_outlives_a_stage_can_be_unbound():
    """The trap in wrapping stages: a value set in one and read in another.

    If a stage fails, its assignments never happen, so anything a later stage
    reads has to have been given a default first, or the isolation just moves
    the crash three stages down.
    """
    with_nodes = [n for n in ast.walk(MAIN)
                  if isinstance(n, ast.With)
                  and isinstance(n.items[0].context_expr, ast.Call)
                  and getattr(n.items[0].context_expr.func, "id", "") == "stage"]
    first_stage_line = min(n.lineno for n in with_nodes)

    # names assigned only inside stage bodies
    assigned_in_stage, assigned_before = {}, set()
    for node in ast.walk(MAIN):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if node.lineno < first_stage_line:
                assigned_before.add(node.id)
            else:
                owner = min((w for w in with_nodes
                             if w.lineno <= node.lineno <= w.end_lineno),
                            key=lambda w: w.end_lineno - w.lineno, default=None)
                if owner is not None:
                    assigned_in_stage.setdefault(node.id, set()).add(owner.lineno)

    leaks = []
    for node in ast.walk(MAIN):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            name = node.id
            if name in assigned_before or name not in assigned_in_stage:
                continue
            owner = min((w for w in with_nodes
                         if w.lineno <= node.lineno <= w.end_lineno),
                        key=lambda w: w.end_lineno - w.lineno, default=None)
            here = owner.lineno if owner is not None else None
            if here not in assigned_in_stage[name]:
                leaks.append((name, node.lineno))
    assert not leaks, (
        "read in a stage that does not assign them, and with no default set "
        f"before the first stage: {sorted(set(n for n, _ in leaks))}")


def test_the_defaults_are_actually_there():
    """Named explicitly so removing one is a deliberate act."""
    for name in ("onboarded", "first_log_by_name", "competition_rows",
                 "sent_comp_keys", "emails_sent"):
        assert f"\n    {name} = " in SRC, f"{name} has no default before the stages"


# ── reporting ─────────────────────────────────────────────────────────────────

class FakeSheets:
    def __init__(self):
        self.rows = []

    def ensure_headers(self, title, headers):
        return None

    def append_rows(self, title, rows):
        self.rows.extend(rows)
        return len(rows)


def test_failures_are_written_to_the_health_log_and_slack(monkeypatch):
    posted = []
    monkeypatch.setattr(sync.notifier, "send_slack", lambda t: posted.append(t))
    sheets = FakeSheets()
    with sync.stage("monthly Fitr check-in"):
        raise RuntimeError("429")
    sync.report_stage_failures(sheets)

    assert any("monthly Fitr check-in" in str(r) for r in sheets.rows)
    assert any(r[1] == "fail" for r in sheets.rows)
    assert posted and "monthly Fitr check-in" in posted[0]


def test_a_slack_outage_does_not_stop_the_health_log(monkeypatch):
    def boom(_):
        raise OSError("slack down")
    monkeypatch.setattr(sync.notifier, "send_slack", boom)
    sheets = FakeSheets()
    with sync.stage("sync log"):
        raise RuntimeError("nope")
    sync.report_stage_failures(sheets)   # must not raise
    assert any("sync log" in str(r) for r in sheets.rows)


def test_a_dry_run_never_posts_to_the_coach_channel(monkeypatch):
    """send_slack has no dry-run guard of its own."""
    posted = []
    monkeypatch.setattr(sync.notifier, "send_slack", lambda t: posted.append(t))
    monkeypatch.setattr(sync.config, "DRY_RUN", True)
    with sync.stage("weekly progress emails"):
        raise RuntimeError("nope")
    sync.report_stage_failures(FakeSheets())
    assert posted == []
