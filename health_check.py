"""Daily health check — catches the bugs that used to live here for months.

Every check in this file exists because the failure it looks for actually
happened and nobody noticed, in some cases for weeks:

- the Sync Log header drifted from what sync.py writes, so the dashboard's
  Sync tab raised a KeyError and took the Help tab down with it
- bespoke suppression was keyed off a column whose value is never "Bespoke",
  so it matched zero athletes and every individually-coached athlete kept
  getting automated messages
- the dashboard filtered its roster on a broader "cancelled" rule than the
  sync, so 20 current athletes were invisible on every tab
- Coach Alerts section headings were written as formulas and rendered #ERROR!

The checks are deliberately read-only. Findings go out on the Slack and email
digest a coach already reads, because a log nobody opens is where these bugs
hid in the first place.

Run standalone for an ad-hoc look:

    python health_check.py            # data checks
    python health_check.py --pages    # also render every dashboard tab
"""
import datetime as dt

import config

TODAY = dt.date.today()

# Severity levels, worst first.
FAIL = "fail"
WARN = "warn"


class Finding:
    __slots__ = ("severity", "area", "title", "detail")

    def __init__(self, severity, area, title, detail=""):
        self.severity = severity
        self.area = area
        self.title = title
        self.detail = detail

    def __repr__(self):
        return f"<{self.severity.upper()} {self.area}: {self.title}>"

    def line(self):
        return f"{self.title}{(' — ' + self.detail) if self.detail else ''}"


# ── the schema contract ───────────────────────────────────────────────────────
# Columns the code reads by name. A missing one is a crash or a silently empty
# feature, so it is a failure rather than a warning.
#
# "always_populated" lists columns where a completely empty column means a
# feature is dead rather than merely unused — the bespoke-suppression failure
# mode. Columns that are legitimately sparse are not listed.

EXPECTED_COLUMNS = {
    config.TAB_DATA: {
        "required": ["Full Name", "Email", "Programming Tier", "Subscription Plan",
                     "Fitr Status", "Coaching Notes", "Join Date", "North Star Goal"],
        "always_populated": ["Full Name", "Programming Tier", "Fitr Status"],
    },
    config.TAB_BENCHMARKS: {
        "required": ["JST ID", "Name", "Fitr ID", "Last Scraped"],
        "always_populated": ["Name", "Fitr ID"],
    },
    config.TAB_PR_LOG: {
        # "Athlete Note" is the athlete's own comment on the result. Optional
        # per row, so not always_populated, but it must keep its header — while
        # it was blank, 396 athlete comments were unreadable by name.
        "required": ["Date", "Athlete Name", "Benchmark Name", "Value",
                     "Athlete Note"],
        "always_populated": ["Date", "Athlete Name", "Benchmark Name", "Value"],
    },
    config.TAB_SYNC_LOG: {
        "required": ["Run Date", "Total Athletes", "New PR Log rows",
                     "Challenge scores added", "Conversations summarised",
                     "Recovery merged", "Notes updated", "Athletes auto-onboarded",
                     "Athlete Emails Sent", "Notes"],
        "always_populated": ["Run Date", "Total Athletes"],
    },
    config.TAB_MESSAGE_LOG: {
        # Replied / Reply Date are written later by mark_message_replied, which
        # looks them up by name and gives up silently if they are absent.
        "required": ["Date", "Athlete Name", "Message Type", "Room ID",
                     "Replied", "Reply Date"],
        "always_populated": ["Date", "Athlete Name", "Message Type"],
    },
    config.TAB_COMPETITIONS: {
        "required": ["Athlete Name", "Competition Name", "Date", "Type"],
        "always_populated": ["Athlete Name", "Competition Name", "Date"],
    },
    "Active Roster": {
        "required": ["Full Name"],
        "always_populated": ["Full Name"],
    },
    config.TAB_COACHES: {
        "required": ["Programme", "Slack Channel", "Active"],
        "always_populated": ["Programme", "Slack Channel"],
    },
}

# Tabs written only by this system, where a drifted header is a bug rather than
# a coach's edit. Checked even when the tab has no rows yet.
MACHINE_OWNED_TABS = (config.TAB_SYNC_LOG, config.TAB_MESSAGE_LOG,
                      config.TAB_PENDING_MESSAGES)

# Days without a logged session before a billed athlete needs a human look.
# One definition in config, shared with the dashboard's Finance tab.
REVENUE_DORMANT_DAYS = int(getattr(config, "REVENUE_DORMANT_DAYS", 90))

# A pending draft older than this has plainly not been worked.
PENDING_STALE_DAYS = 3
# More than this queued at once means the list is being generated, not worked.
PENDING_BACKLOG_LIMIT = 25


def _parse_date(s):
    s = str(s or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(s[:len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    return None


# ── checks ────────────────────────────────────────────────────────────────────

def check_sheet_schemas(sheets):
    """Missing, duplicate or blank headers, and columns that are entirely empty."""
    out = []
    for tab, spec in EXPECTED_COLUMNS.items():
        try:
            values = sheets.read_values(tab)
        except Exception as exc:
            out.append(Finding(FAIL, "sheets", f"Cannot read the '{tab}' tab", str(exc)[:160]))
            continue
        if not values:
            out.append(Finding(FAIL, "sheets", f"The '{tab}' tab is empty",
                               "expected a header row"))
            continue

        header = [str(h).strip() for h in values[0]]
        rows = values[1:]

        missing = [c for c in spec["required"] if c not in header]
        if missing:
            out.append(Finding(
                FAIL, "sheets", f"'{tab}' is missing {len(missing)} expected column(s)",
                ", ".join(missing)))

        named = [h for h in header if h]
        dupes = sorted({h for h in named if named.count(h) > 1})
        if dupes:
            out.append(Finding(
                FAIL, "sheets", f"'{tab}' has duplicate header(s)",
                ", ".join(dupes) + " — readers keep the first and silently drop the rest"))

        blank_with_data = [
            i for i, h in enumerate(header)
            if not h and any(len(r) > i and str(r[i]).strip() for r in rows)
        ]
        if blank_with_data:
            out.append(Finding(
                WARN, "sheets", f"'{tab}' has {len(blank_with_data)} blank header(s) with data under them",
                f"column index {blank_with_data} — that data is unreadable by name"))

        if rows:
            for col in spec.get("always_populated", ()):
                if col not in header:
                    continue   # already reported as missing
                i = header.index(col)
                filled = sum(1 for r in rows if len(r) > i and str(r[i]).strip())
                if filled == 0:
                    out.append(Finding(
                        FAIL, "sheets", f"'{tab}'.'{col}' is completely empty",
                        f"{len(rows)} rows, none populated — anything reading this "
                        "column is silently doing nothing"))
    return out


def check_machine_tab_headers(sheets):
    """Machine-owned tabs whose header no longer matches the writer."""
    out = []
    for tab in MACHINE_OWNED_TABS:
        spec = EXPECTED_COLUMNS.get(tab)
        if not spec:
            continue
        try:
            values = sheets.read_values(tab)
        except Exception:
            continue    # absent tabs are covered by check_sheet_schemas
        if not values:
            continue
        header = [str(h).strip() for h in values[0]]
        while header and not header[-1]:
            header.pop()
        expected = spec["required"]
        if header != expected:
            out.append(Finding(
                WARN, "sheets", f"'{tab}' header does not match what the sync writes",
                f"sheet has {header}, writer expects {expected}"))
    return out


def check_suppression_rules_match_someone(data_records, bespoke_names, gone_norm):
    """A suppression rule matching nobody is how bespoke silently broke.

    Bespoke lives in Programming Tier. Keying it off Subscription Plan matched
    zero athletes for months while looking perfectly healthy in the logs.
    """
    out = []
    if not data_records:
        return out

    tiers = {str(r.get("Programming Tier", "")).strip().lower() for r in data_records}
    if "bespoke" not in tiers:
        out.append(Finding(
            FAIL, "suppression",
            "No athlete has Programming Tier 'Bespoke'",
            "bespoke suppression is matching nobody, so individually-coached "
            "athletes will receive automated messages"))
    elif not bespoke_names:
        out.append(Finding(
            FAIL, "suppression",
            "Bespoke athletes exist in _DATA but the sync matched none of them",
            "check the column the suppression set is built from"))

    if not gone_norm:
        out.append(Finding(
            WARN, "suppression", "The genuinely-gone list is empty",
            "no athlete is being excluded from messages; expected the CRM Exit "
            "Autopsy minus the Active Roster to leave some"))
    return out


def check_cancelled_not_on_lists(engagement_results, gone_norm, analytics_mod):
    """Athletes who have gone must not resurface on a coaching list."""
    out = []
    if not gone_norm or not engagement_results:
        return out
    leaked = sorted({
        e.get("name") for e in engagement_results
        if analytics_mod.normalise_client_name(e.get("name", "")) in gone_norm
    })
    if leaked:
        out.append(Finding(
            FAIL, "roster",
            f"{len(leaked)} cancelled athlete(s) are back on the engagement list",
            ", ".join(leaked[:8]) + ("…" if len(leaked) > 8 else "")))
    return out


def check_roster_agrees_with_dashboard(sheets, analytics_mod, gone_norm):
    """The dashboard and the sync must exclude the same people.

    They drifted once already: the dashboard filtered on the raw Exit Autopsy
    set and hid 20 athletes who were on the current Active Roster.
    """
    out = []
    try:
        pr_records = sheets.read_records(config.TAB_PR_LOG)
        data_records = sheets.read_records(config.TAB_DATA)
        exit_rows = sheets.load_exit_autopsy()
        roster = [str(r.get("Full Name", "")).strip()
                  for r in sheets.read_records("Active Roster")
                  if str(r.get("Full Name", "")).strip()]
    except Exception as exc:
        return [Finding(WARN, "roster", "Could not cross-check the roster", str(exc)[:140])]

    cancelled_lower, _ = analytics_mod.cancelled_athletes(exit_rows, pr_records)
    shared = analytics_mod.not_current_client_names(cancelled_lower, data_records, roster)

    raw_only = {analytics_mod.normalise_client_name(n) for n in cancelled_lower} - shared
    if raw_only:
        names = sorted({
            str(r.get("Full Name", "")).strip() for r in data_records
            if analytics_mod.normalise_client_name(r.get("Full Name", "")) in raw_only
        })
        out.append(Finding(
            WARN, "roster",
            f"{len(raw_only)} athlete(s) gave notice but are still current",
            "they stay on every list and in MRR by design (on the Active Roster): "
            + ", ".join(names[:6]) + ("…" if len(names) > 6 else "")))

    if gone_norm is not None and set(gone_norm) != set(shared):
        only_sync = len(set(gone_norm) - set(shared))
        only_shared = len(set(shared) - set(gone_norm))
        out.append(Finding(
            FAIL, "roster",
            "The sync and the shared roster rule disagree on who has gone",
            f"{only_sync} excluded only by the sync, {only_shared} only by the shared rule"))
    return out


def check_crm_says_gone_but_training(sheets, analytics_mod):
    """Athletes the CRM has as cancelled who are demonstrably still training.

    The code already handles this safely — they are treated as rejoined and
    kept in — so nothing breaks. But the CRM is wrong, and it stays wrong,
    because the only place this was ever mentioned was a line in the sync log
    that nobody reads.
    """
    try:
        exit_rows = sheets.load_exit_autopsy()
        pr_records = sheets.read_records(config.TAB_PR_LOG)
    except Exception:
        return []
    _, rejoined = analytics_mod.cancelled_athletes(exit_rows, pr_records)
    if not rejoined:
        return []
    names = ", ".join(sorted(rejoined)[:8])
    if len(rejoined) > 8:
        names += f" and {len(rejoined) - 8} more"
    return [Finding(
        WARN, "crm",
        f"{len(rejoined)} athlete(s) are marked cancelled in the CRM but are training again",
        f"They are correctly kept on the lists; the CRM Exit Autopsy is what needs "
        f"correcting: {names}")]


def check_duplicate_athlete_rows(sheets):
    """Two rows for one athlete means two half-profiles and split history."""
    try:
        rows = sheets.read_records(config.TAB_DATA)
    except Exception:
        return []
    seen = {}
    for r in rows:
        nm = str(r.get("Full Name", "")).strip()
        if nm:
            seen[nm] = seen.get(nm, 0) + 1
    dupes = sorted(nm for nm, n in seen.items() if n > 1)
    if not dupes:
        return []
    return [Finding(
        WARN, "sheets", f"{len(dupes)} athlete(s) have more than one row in _DATA",
        "profile edits and coaching notes will land on one row and not the "
        "other: " + ", ".join(dupes[:8]))]


def check_programming_tier_values(sheets):
    """Programming Tier drives message suppression, so junk in it is a risk."""
    try:
        rows = sheets.read_records(config.TAB_DATA)
    except Exception:
        return []
    allowed = {"", "standard", "bespoke", "semi-bespoke"}
    odd = {}
    for r in rows:
        v = str(r.get("Programming Tier", "")).strip()
        if v.lower() not in allowed:
            odd.setdefault(v, []).append(str(r.get("Full Name", "")).strip())
    if not odd:
        return []
    detail = "; ".join(f"{v[:45]!r} ({len(names)})" for v, names in list(odd.items())[:3])
    return [Finding(
        WARN, "sheets",
        f"{sum(len(n) for n in odd.values())} athlete(s) have an unrecognised Programming Tier",
        "this column decides who is exempt from automated messages, so anything "
        f"unexpected in it is worth correcting: {detail}")]


def check_training_signal(sheets):
    """The training-adherence columns must keep filling.

    This signal is pulled from an undocumented Fitr endpoint. If Fitr renames a
    field or changes how it paginates, the pull returns nothing and engagement
    quietly falls back to judging people on benchmark retests — which is the
    bug this replaced. That regression would be invisible: the dashboard would
    look fine and simply start flagging the wrong 87 athletes again.
    """
    out = []
    try:
        rows = sheets.read_records(config.TAB_DATA)
    except Exception:
        return out
    if not rows:
        return out
    if "Last Trained" not in (rows[0] or {}):
        return [Finding(
            WARN, "training", "The Last Trained column is missing from _DATA",
            "the Fitr training-adherence pull has not run yet, so engagement is "
            "still being judged on benchmark retests alone")]

    filled = sum(1 for r in rows if str(r.get("Last Trained", "")).strip())
    if filled == 0:
        out.append(Finding(
            FAIL, "training", "No athlete has a Last Trained date",
            "the Fitr adherence pull is returning nothing, so engagement flags "
            "have silently reverted to benchmark retests — expect a large jump "
            "in false 'inactive' flags"))
    elif filled < len(rows) * 0.25:
        out.append(Finding(
            WARN, "training",
            f"Only {filled} of {len(rows)} athletes have a Last Trained date",
            "coverage was around 78% of the roster when this was built; a sharp "
            "drop usually means Fitr changed its pagination"))
    return out


def check_disabled_integrations():
    """Stages whose config is unset, so they run and quietly do nothing.

    Every one of these reads a sheet ID that defaults to an empty string. With
    it unset the stage still "succeeds" — it just processes zero rows and logs
    nothing unusual, which is indistinguishable from there being no new data.
    Two of these had been off in production for the life of the workflow.
    """
    wired = [
        ("INTAKE_FORM_SHEET_ID", "new athlete intake form"),
        ("TSHIRT_FORM_SHEET_ID", "180-day t-shirt reward"),
        ("RECOVERY_SHEET_ID", "weekly recovery survey"),
        ("COMP_FORM_SHEET_ID", "competition planner form"),
        ("SLACK_WEBHOOK_URL", "Slack digest"),
        ("SMTP_PASSWORD", "email digest and athlete emails"),
    ]
    missing = [(name, what) for name, what in wired
               if not str(getattr(config, name, "") or "").strip()]
    if not missing:
        return []
    return [Finding(
        WARN, "config",
        f"{len(missing)} integration(s) are switched off because their config is unset",
        "; ".join(f"{what} ({name})" for name, what in missing)
        + " — these stages run and process nothing rather than failing")]


def check_pending_message_queue(sheets):
    """Drafts nobody is sending. This is now the only route to an athlete."""
    out = []
    try:
        rows = sheets.read_records(config.TAB_PENDING_MESSAGES)
    except Exception:
        # Tab appears on the first sync that queues anything. Not a problem.
        return out

    pending = [r for r in rows if str(r.get("Status", "")).strip().lower() == "pending"]
    if not pending:
        return out

    stale = []
    for r in pending:
        d = _parse_date(r.get("Date", ""))
        if d and (TODAY - d).days >= PENDING_STALE_DAYS:
            stale.append((r.get("Athlete Name", ""), (TODAY - d).days))

    if stale:
        oldest = max(days for _, days in stale)
        out.append(Finding(
            FAIL, "messages",
            f"{len(stale)} drafted message(s) have been waiting {PENDING_STALE_DAYS}+ days",
            f"oldest is {oldest} days old. Automatic sending is off, so these "
            "athletes have heard nothing: "
            + ", ".join(n for n, _ in stale[:6]) + ("…" if len(stale) > 6 else "")))
    elif len(pending) > PENDING_BACKLOG_LIMIT:
        out.append(Finding(
            WARN, "messages",
            f"{len(pending)} drafted messages are queued",
            "the list is growing faster than it is being worked"))
    return out


def check_message_log_replies(sheets):
    """The reply scanner writes back here. A column that never fills is dead code."""
    out = []
    try:
        rows = sheets.read_records(config.TAB_MESSAGE_LOG)
    except Exception:
        return out
    if len(rows) < 20:
        return out
    if "Replied" not in (rows[0] or {}):
        return out
    if not any(str(r.get("Replied", "")).strip() for r in rows):
        out.append(Finding(
            WARN, "messages",
            f"No reply has ever been recorded against {len(rows)} logged messages",
            "the Fitr reply scanner has never matched anything — reply rate "
            "reporting is meaningless until that is confirmed working"))
    return out


def check_revenue_anomalies(sheets, analytics_mod, data_records, gone_norm,
                            monthly_value_fn=None):
    """Athletes being billed while not training.

    Split by reason rather than lumped into one count, because the three mean
    different things: a failed payment is a billing job, a never-logged
    athlete is an onboarding failure, and a long silence is a coaching one.
    """
    out = []
    if not data_records:
        return out
    try:
        pr_records = sheets.read_records(config.TAB_PR_LOG)
    except Exception as exc:
        return [Finding(WARN, "revenue", "Could not check billing against training",
                        str(exc)[:140])]

    rows = analytics_mod.revenue_anomalies(
        data_records, pr_records, gone_norm=gone_norm,
        dormant_days=REVENUE_DORMANT_DAYS, monthly_value_fn=monthly_value_fn,
        activity_by_name=analytics_mod.activity_from_data_records(data_records))
    if not rows:
        return out

    groups = {}
    for r in rows:
        key = "Missed payment" if r["reason"] == "Missed payment" else (
            "No training on record" if r["reason"].startswith("No training")
            else f"No session in {REVENUE_DORMANT_DAYS}+ days")
        groups.setdefault(key, []).append(r)

    for label, items in groups.items():
        value = sum(i["monthly_value"] for i in items)
        names = ", ".join(i["name"] for i in items[:6])
        if len(items) > 6:
            names += f" and {len(items) - 6} more"
        severity = FAIL if label == "Missed payment" else WARN
        out.append(Finding(
            severity, "revenue",
            f"{len(items)} current athlete(s) — {label.lower()}",
            f"£{value:,.0f}/month of billing with no training behind it: {names}"))
    return out


def check_dashboard_pages(timeout=600):
    """Render every dashboard tab headlessly and report any that raise.

    Uses Streamlit's own AppTest so this is the real script against real data,
    not a mock. Each tab is wrapped by dashboard._render_tab, so one broken tab
    is recorded rather than aborting the run.
    """
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as exc:
        return [Finding(WARN, "dashboard", "Could not import Streamlit AppTest", str(exc)[:140])]

    try:
        at = AppTest.from_file("dashboard.py", default_timeout=timeout)
        at.run()
    except Exception as exc:
        return [Finding(FAIL, "dashboard", "The dashboard failed to start at all",
                        f"{type(exc).__name__}: {exc}"[:300])]

    out = []
    try:
        failures = at.session_state["_tab_render_failures"]
    except Exception:
        failures = {}
    for tab, err in (failures or {}).items():
        out.append(Finding(FAIL, "dashboard", f"The {tab} tab failed to load", str(err)[:200]))

    # A data load that failed is quieter than a tab that crashed: every tab
    # still renders, just with less behind it.
    try:
        warnings = at.session_state["_load_warnings"]
    except Exception:
        warnings = []
    for w in (warnings or []):
        out.append(Finding(FAIL, "dashboard", "Dashboard data failed to load", str(w)[:200]))

    for exc in at.exception:
        out.append(Finding(FAIL, "dashboard", "Unhandled dashboard exception",
                           str(exc.value)[:200]))
    return out


# ── runner ────────────────────────────────────────────────────────────────────

def run_health_check(sheets, analytics_mod, *, data_records=None, bespoke_names=None,
                     gone_norm=None, engagement_results=None, check_pages=False):
    """Run every check. Never raises — a broken check must not break the sync."""

    def monthly_value(rec):
        return analytics_mod.monthly_value(
            rec.get("Subscription Plan", ""),
            rec.get("Programming Tier", ""),
            fallbacks=getattr(config, "SUBSCRIPTION_FALLBACK_PRICES", {}),
            bespoke_value=getattr(config, "BESPOKE_MONTHLY_TO_JST", 40),
        )

    findings = []
    checks = [
        ("revenue anomalies", lambda: check_revenue_anomalies(
            sheets, analytics_mod, data_records, gone_norm, monthly_value)),
        ("sheet schemas", lambda: check_sheet_schemas(sheets)),
        ("machine tab headers", lambda: check_machine_tab_headers(sheets)),
        ("suppression rules", lambda: check_suppression_rules_match_someone(
            data_records, bespoke_names, gone_norm)),
        ("cancelled on lists", lambda: check_cancelled_not_on_lists(
            engagement_results, gone_norm, analytics_mod)),
        ("roster agreement", lambda: check_roster_agrees_with_dashboard(
            sheets, analytics_mod, gone_norm)),
        ("pending queue", lambda: check_pending_message_queue(sheets)),
        ("message log replies", lambda: check_message_log_replies(sheets)),
        ("crm rejoins", lambda: check_crm_says_gone_but_training(sheets, analytics_mod)),
        ("duplicate athlete rows", lambda: check_duplicate_athlete_rows(sheets)),
        ("programming tier values", lambda: check_programming_tier_values(sheets)),
        ("training signal", lambda: check_training_signal(sheets)),
        ("disabled integrations", check_disabled_integrations),
    ]
    if check_pages:
        checks.append(("dashboard pages", lambda: check_dashboard_pages()))

    for name, fn in checks:
        try:
            findings.extend(fn() or [])
        except Exception as exc:
            findings.append(Finding(
                WARN, "health-check", f"The '{name}' check itself failed",
                f"{type(exc).__name__}: {exc}"[:200]))
    return findings


def format_findings(findings):
    """(plain, slack) blocks for the digest, or ("", "") when all is well."""
    if not findings:
        return "", ""
    fails = [f for f in findings if f.severity == FAIL]
    warns = [f for f in findings if f.severity == WARN]

    plain, slack = [], []
    if fails:
        plain.append(f"🛑 BROKEN — needs fixing ({len(fails)})")
        slack.append(f"🛑 *BROKEN — needs fixing* ({len(fails)})")
        for f in fails:
            plain.append(f"  • {f.line()}")
            slack.append(f"  • *{f.title}*{(' — ' + f.detail) if f.detail else ''}")
    if warns:
        plain.append(f"⚠️ WORTH A LOOK ({len(warns)})")
        slack.append(f"⚠️ *WORTH A LOOK* ({len(warns)})")
        for f in warns:
            plain.append(f"  • {f.line()}")
            slack.append(f"  • {f.title}{(' — ' + f.detail) if f.detail else ''}")
    return "\n".join(plain), "\n".join(slack)


if __name__ == "__main__":
    import sys

    import analytics
    import sheets_client

    want_pages = "--pages" in sys.argv
    sh = sheets_client.SheetsClient()
    data = sh.read_records(config.TAB_DATA)
    bespoke = {str(r.get("Full Name", "")).strip() for r in data
               if str(r.get("Programming Tier", "")).strip().lower() == "bespoke"}
    pr = sh.read_records(config.TAB_PR_LOG)
    exits = sh.load_exit_autopsy()
    cancelled, _ = analytics.cancelled_athletes(exits, pr)
    roster = [str(r.get("Full Name", "")).strip()
              for r in sh.read_records("Active Roster")
              if str(r.get("Full Name", "")).strip()]
    gone = analytics.not_current_client_names(cancelled, data, roster)

    results = run_health_check(sh, analytics, data_records=data, bespoke_names=bespoke,
                               gone_norm=gone, check_pages=want_pages)
    text, _ = format_findings(results)
    print(text or "✅ All health checks passed.")
    sys.exit(1 if any(f.severity == FAIL for f in results) else 0)
