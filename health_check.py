"""Smoke test for the coaching dashboard and its data.

Every bug that has taken a dashboard tab down this year was silent: the page
raised, or a column read came back empty, and nobody knew until a coach happened
to look. This calls every page with real data and checks the data itself for the
specific shapes those failures took, so the system reports its own breakage
instead of waiting to be noticed.

Run standalone (`python health_check.py`) or from the daily sync via
run_health_check(), which returns (ok, [problems]).
"""
import inspect
import traceback

import config


def _data_checks(sheets):
    """Data-shape problems that produce a working page showing wrong or no data."""
    problems = []

    def _rows(tab):
        try:
            return sheets.read_records(tab)
        except Exception as exc:
            problems.append(f"Tab '{tab}' unreadable: {type(exc).__name__}: {exc}")
            return []

    data = _rows(config.TAB_DATA)
    if not data:
        problems.append("_DATA is empty — the dashboard would show no athletes at all")
        return problems

    # A column the code reads that is empty for everyone is how bespoke
    # suppression matched zero athletes for months while looking fine.
    for col, why in (("Programming Tier", "bespoke suppression"),
                     ("Full Name", "every name lookup"),
                     ("Fitr Status", "cancellation detection")):
        if col not in data[0]:
            problems.append(f"_DATA has no '{col}' column — {why} is broken")
        elif not any(str(r.get(col, "")).strip() for r in data):
            problems.append(f"_DATA column '{col}' is empty for every athlete — {why} silently does nothing")

    # Tabs the dashboard needs. Blank/duplicate headers used to kill a whole tab.
    for tab in (config.TAB_PR_LOG, config.TAB_SYNC_LOG, "Coaching Playbook",
                "Challenge Measures", "Active Roster"):
        if not _rows(tab):
            problems.append(f"Tab '{tab}' is empty or unreadable")

    # Cancelled athletes must not still be on the working roster.
    try:
        import analytics
        pr = _rows(config.TAB_PR_LOG)
        cancelled, _ = analytics.cancelled_athletes(sheets.load_exit_autopsy(), pr)
        roster = [str(r.get("Full Name", "")).strip()
                  for r in _rows("Active Roster") if str(r.get("Full Name", "")).strip()]
        gone = analytics.not_current_client_names(cancelled, data, roster)
        if len(gone) > len(data) * 0.6:
            problems.append(
                f"{len(gone)} of {len(data)} athletes counted as gone — that is too many, "
                "check the Active Roster was pasted in")
    except Exception as exc:
        problems.append(f"Cancellation check failed: {type(exc).__name__}: {exc}")

    # Drafts nobody is sending. Automatic sending is off, so an unworked queue
    # means athletes are hearing nothing at all.
    try:
        pending = [r for r in sheets.read_records(config.TAB_PENDING_MESSAGES)
                   if str(r.get("Status", "")).strip().lower() == "pending"]
        if len(pending) > 40:
            problems.append(
                f"{len(pending)} drafted messages are waiting to be sent. Nothing reaches "
                "an athlete until a coach sends them")
    except Exception:
        pass  # tab may not exist yet
    return problems


def _page_checks():
    """Call every dashboard page with real data and catch anything that raises."""
    problems = []
    try:
        import dashboard as dash
    except Exception as exc:
        return [f"dashboard.py will not even import: {type(exc).__name__}: {exc}"]

    try:
        (pr_records, athletes, rec_latest, data_records, archetype_rows,
         competition_rows, cancelled_names, gone_norm, warns) = dash.load_all()
    except Exception as exc:
        return [f"load_all() failed, so every page is down: {type(exc).__name__}: {exc}"]
    for w in (warns or []):
        problems.append(f"Data load warning: {w}")

    try:
        trends, engagement, wins, rec_alerts, rec_by_name, comps = dash.run_analytics(
            pr_records, athletes, rec_latest, data_records,
            competition_rows=competition_rows)
    except Exception as exc:
        return problems + [f"run_analytics() failed: {type(exc).__name__}: {exc}"]

    available = {
        "pr_records": pr_records, "athletes": athletes, "data_records": data_records,
        "trend_results": trends, "engagement_results": engagement,
        "consistency_wins": wins, "rec_alert_rows": rec_alerts,
        "rec_by_name": rec_by_name, "comp_results": comps,
        "competition_rows": competition_rows, "milestones": [],
        "grandslam_results": [], "cancelled_names": cancelled_names,
    }
    for name in sorted(n for n in dir(dash) if n.startswith("page_")):
        fn = getattr(dash, name)
        if not callable(fn):
            continue
        try:
            sig = inspect.signature(fn)
            kwargs, skip = {}, False
            for pname, p in sig.parameters.items():
                if pname in available:
                    kwargs[pname] = available[pname]
                elif p.default is inspect.Parameter.empty:
                    skip = True  # needs something we can't supply
            if skip:
                continue
            print(f"    checking {name}...", flush=True)
            fn(**kwargs)
        except BaseException as exc:
            # BaseException on purpose: a page calling st.stop() raises something
            # that isn't an Exception, and outside a Streamlit session that ended
            # the entire check silently with a success code.
            if exc.__class__.__name__ in ("StopException", "RerunException"):
                continue
            problems.append(f"{name}() raises {type(exc).__name__}: "
                            f"{str(exc)[:160]} | {traceback.format_exc().strip().splitlines()[-2].strip()[:120]}")
    return problems


def run_health_check(sheets):
    """Returns (ok, [problem strings]). Safe to call from the sync."""
    problems = []
    try:
        problems += _data_checks(sheets)
    except Exception as exc:
        problems.append(f"Data checks crashed: {type(exc).__name__}: {exc}")
    try:
        problems += _page_checks()
    except Exception as exc:
        problems.append(f"Page checks crashed: {type(exc).__name__}: {exc}")
    return (not problems), problems


if __name__ == "__main__":
    from sheets_client import SheetsClient
    ok, probs = run_health_check(SheetsClient())
    if ok:
        print("Health check: all clear")
    else:
        print(f"Health check found {len(probs)} problem(s):")
        for p in probs:
            print(f"  - {p}")
