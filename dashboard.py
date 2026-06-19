"""
JST Compete — Coaching Dashboard

Run locally:  streamlit run dashboard.py
Deploy:       Streamlit Community Cloud → connect GitHub repo → add secrets
"""
import datetime as dt
import os
import re

import streamlit as st
import altair as alt
import pandas as pd

import analytics
import config
import recovery as rec_mod

_CHAT_DATE_RE = re.compile(r'\[(\d{4}-\d{2}-\d{2}) — chat\]')


st.set_page_config(
    page_title="JST Compete Coaching",
    page_icon="🏋️",
    layout="wide",
)

TODAY = dt.date.today()


# ── Auth ──────────────────────────────────────────────────────────────────────

# Sheet IDs are config, not secrets — hard-code them as Cloud fallbacks
_SHEET_ID = "1Fx4r3IrYeytoysX_hkarZ5I5SoQdq3ZSPZkS0Y2yDOc"
_RECOVERY_SHEET_ID = "1hSBGVWppOfzI1GUO-ZK74tgLbSye8apDpLn3b2QDoys"


@st.cache_resource
def get_sheets():
    """Return SheetsClient, using Streamlit secrets on Cloud or .env locally."""
    from sheets_client import SheetsClient
    if "gcp_service_account" in st.secrets:
        sa = dict(st.secrets["gcp_service_account"])
        config.SHEET_ID = config.SHEET_ID or _SHEET_ID
        config.RECOVERY_SHEET_ID = config.RECOVERY_SHEET_ID or _RECOVERY_SHEET_ID
        return SheetsClient(service_account_info=sa)
    return SheetsClient()  # local dev — uses .env + service_account.json


# ── Data ──────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=900, show_spinner="Loading athlete data...")
def load_all():
    sheets = get_sheets()

    pr_records = sheets.read_records(config.TAB_PR_LOG)
    bm_values = sheets.read_values(config.TAB_BENCHMARKS)

    # Build athlete list same way sync.py does
    header = bm_values[0] if bm_values else []
    athletes = []
    for i, r in enumerate(bm_values[1:], start=2):
        rec = dict(zip(header, r))
        name = (rec.get("Name") or "").strip()
        fitr_id = (rec.get("Fitr ID") or "").strip()
        if name and fitr_id:
            athletes.append({
                "name": name,
                "jst_id": (rec.get("JST ID") or "").strip(),
                "fitr_id": fitr_id,
                "row": i,
            })

    data_records = []
    try:
        data_records = sheets.read_records(config.TAB_DATA)
    except Exception:
        pass

    rec_latest = {}
    try:
        if config.RECOVERY_SHEET_ID:
            rec_latest = rec_mod.latest_by_email(sheets)
    except Exception:
        pass

    return pr_records, athletes, rec_latest, data_records


def run_analytics(pr_records, athletes, rec_latest, data_records=None):
    trend_results = analytics.trend_analysis(pr_records)

    # Parse most recent chat date per athlete from Coaching Notes in _DATA
    last_contact_by_name = {}
    for rec in (data_records or []):
        name = str(rec.get("Full Name", "")).strip()
        notes = str(rec.get("Coaching Notes", "")).strip()
        if not name or not notes:
            continue
        dates = [_parse_date(m) for m in _CHAT_DATE_RE.findall(notes)]
        dates = [d for d in dates if d]
        if dates:
            last_contact_by_name[name] = max(dates)

    engagement_results = analytics.engagement_check(
        pr_records, athletes,
        threshold_days=config.ENGAGEMENT_THRESHOLD_DAYS,
        last_contact_by_name=last_contact_by_name,
    )
    consistency_wins = analytics.consistency_check(pr_records, athletes)

    # Build rec_by_name from latest recovery (email → name mapping via PR log)
    email_by_name = {}
    for r in pr_records:
        nm = str(r.get("Athlete Name", "")).strip()
        em = str(r.get("Email", "")).strip().lower()
        if nm and em:
            email_by_name.setdefault(nm, em)
    email_to_name = {v: k for k, v in email_by_name.items()}
    rec_by_name = {
        email_to_name[em]: row
        for em, row in rec_latest.items()
        if em.lower() in email_to_name
    }
    rec_alert_rows = analytics.recovery_alerts(rec_by_name)

    return trend_results, engagement_results, consistency_wins, rec_alert_rows, rec_by_name


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_date(s):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(str(s).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _numeric(s):
    import re
    if not s:
        return None
    m = re.match(r"^(\d+):(\d{2})(?::(\d{2}))?$", str(s).strip())
    if m:
        a, b, c = m.groups()
        return int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
    m = re.search(r"[-+]?\d*\.?\d+", str(s))
    return float(m.group()) if m else None


# ── Pages ─────────────────────────────────────────────────────────────────────

def page_alerts(engagement_results, trend_results, rec_alert_rows, consistency_wins):
    flagged = [e for e in engagement_results if e["flag"]]
    concerns = [
        (athlete, s)
        for athlete, signals in trend_results.items()
        for s in signals
        if s["trend"] == "declining" or s["peak_drop_flag"]
    ]

    # Metric row
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🔴 Recovery Flags", len(rec_alert_rows))
    c2.metric("⚠️ Inactive Athletes", len(flagged))
    c3.metric("📉 Performance Concerns", len(concerns))
    c4.metric("✅ Consistency Streaks", len(consistency_wins))

    st.divider()

    if rec_alert_rows:
        st.subheader("🔴 Recovery Flags")
        df = pd.DataFrame(rec_alert_rows, columns=["Athlete", "Issue", "Submitted"])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.write("")

    if flagged:
        st.subheader("⚠️ Engagement / Dropout Risk")
        rows = []
        for e in flagged:
            rows.append({
                "Athlete": e["name"],
                "JST ID": e["jst_id"],
                "Last Logged": e["last_logged"],
                "Days Inactive": e["days_since"] if e["days_since"] is not None else "Never",
                "Status": "Never logged" if e["last_logged"] == "never" else "Inactive",
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.write("")

    if concerns:
        st.subheader("📉 Performance Concerns")
        rows = []
        for athlete, s in sorted(concerns, key=lambda x: x[0]):
            rows.append({
                "Athlete": athlete,
                "Benchmark": s["benchmark"],
                "Trend": f"Declining ({s['trend_pct']:+.1f}%/entry)" if s["trend"] == "declining" else "Flat",
                "Below Peak": f"-{s['peak_drop_pct']:.0f}%" if s["peak_drop_flag"] else "—",
                "Last Value": str(s["last_value"]),
                "Last Date": s["last_date"].isoformat() if s["last_date"] else "",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.write("")

    if consistency_wins:
        st.subheader("✅ Consistency Wins")
        df = pd.DataFrame(consistency_wins, columns=["Athlete", "Consecutive Weeks"])
        st.dataframe(df, use_container_width=True, hide_index=True)

    if not any([rec_alert_rows, flagged, concerns]):
        st.success("Nothing to flag this week — all athletes on track.")


def page_athletes(pr_records, athletes, trend_results, engagement_results, rec_by_name):
    search = st.text_input("Search athlete", placeholder="Start typing a name...")

    # Build summary per athlete
    last_logged = {}
    for r in pr_records:
        nm = str(r.get("Athlete Name", "")).strip()
        d = _parse_date(str(r.get("Date", "")))
        if nm and d and (nm not in last_logged or d > last_logged[nm]):
            last_logged[nm] = d

    eng_map = {e["name"]: e for e in engagement_results}

    rows = []
    for a in athletes:
        nm = a["name"]
        if search and search.lower() not in nm.lower():
            continue
        e = eng_map.get(nm, {})
        last = last_logged.get(nm)
        days = (TODAY - last).days if last else None

        # trend summary for this athlete
        signals = trend_results.get(nm, [])
        declining = sum(1 for s in signals if s["trend"] == "declining")
        improving = sum(1 for s in signals if s["trend"] == "improving")
        trend_label = (
            f"📉 {declining} declining" if declining
            else f"📈 {improving} improving" if improving
            else "—"
        )

        # recovery
        rec_row = rec_by_name.get(nm)
        rec_str = rec_mod.readiness_string(rec_row) if rec_row else "—"
        if rec_str and len(rec_str) > 60:
            rec_str = rec_str[:60] + "…"

        rows.append({
            "Name": nm,
            "JST ID": a["jst_id"],
            "Last Logged": last.isoformat() if last else "Never",
            "Days Since": days if days is not None else "—",
            "Trend": trend_label,
            "Recovery": rec_str,
        })

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True, height=600)
    else:
        st.info("No athletes match your search.")


def page_trends(pr_records, athletes):
    athlete_names = sorted({a["name"] for a in athletes})
    selected = st.selectbox("Athlete", athlete_names)

    if not selected:
        return

    # Get benchmarks for this athlete
    athlete_records = [
        r for r in pr_records
        if str(r.get("Athlete Name", "")).strip() == selected
    ]
    benchmarks = sorted({str(r.get("Benchmark Name", "")).strip() for r in athlete_records if r.get("Benchmark Name")})

    if not benchmarks:
        st.info(f"No PR Log entries found for {selected}.")
        return

    selected_bench = st.selectbox("Benchmark", benchmarks)

    bench_records = [
        r for r in athlete_records
        if str(r.get("Benchmark Name", "")).strip() == selected_bench
    ]

    points = []
    for r in bench_records:
        d = _parse_date(str(r.get("Date", "")))
        v = _numeric(str(r.get("Value", "")))
        if d and v is not None:
            points.append({"Date": pd.Timestamp(d), "Value": v, "Label": str(r.get("Value", ""))})

    if not points:
        st.info("No numeric values found for this benchmark.")
        return

    df = pd.DataFrame(points).sort_values("Date")

    chart = (
        alt.Chart(df)
        .mark_line(point=True, color="#1f77b4")
        .encode(
            x=alt.X("Date:T", title="Date"),
            y=alt.Y("Value:Q", title=selected_bench, scale=alt.Scale(zero=False)),
            tooltip=["Date:T", "Label:N"],
        )
        .properties(height=350)
    )
    st.altair_chart(chart, use_container_width=True)

    col1, col2, col3 = st.columns(3)
    col1.metric("Entries", len(df))
    col2.metric("Best", df["Label"].iloc[df["Value"].argmax()])
    col3.metric("Latest", df["Label"].iloc[-1])


def page_recovery(rec_by_name):
    if not rec_by_name:
        if not config.RECOVERY_SHEET_ID:
            st.info("Recovery sheet not configured (RECOVERY_SHEET_ID not set).")
        else:
            st.info("No recovery survey responses yet.")
        return

    rows = []
    for nm, row in sorted(rec_by_name.items()):
        def g(col):
            return str(row.get(col, "")).strip()
        rows.append({
            "Athlete": nm,
            "Submitted": g("Submitted At"),
            "Sleep": g("Sleep (hrs)"),
            "Soreness": g("Soreness"),
            "Stress": g("Stress"),
            "Motivation": g("Motivation"),
            "Bodyweight": g("Bodyweight"),
            "Niggles": g("Niggles/Injuries"),
            "Availability": g("Availability this week"),
        })

    df = pd.DataFrame(rows)

    def _colour(val, col):
        try:
            v = float(val)
            if col in ("Soreness", "Stress") and v >= 7:
                return "background-color: #ffcccc"
            if col == "Motivation" and v <= 3:
                return "background-color: #ffcccc"
            if col in ("Soreness", "Stress") and v >= 5:
                return "background-color: #fff3cc"
        except (ValueError, TypeError):
            pass
        return ""

    styled = df.style.applymap(lambda v: _colour(v, "Soreness"), subset=["Soreness"])
    styled = styled.applymap(lambda v: _colour(v, "Stress"), subset=["Stress"])
    styled = styled.applymap(lambda v: _colour(v, "Motivation"), subset=["Motivation"])

    st.dataframe(styled, use_container_width=True, hide_index=True)


def page_outreach(engagement_results, trend_results, rec_alert_rows, milestones, consistency_wins):
    """Prioritised list of every athlete who needs contact this week."""
    rows = []

    # 1. Recovery flags — most urgent
    for alert in rec_alert_rows:
        rows.append({
            "Priority": "🔴 Contact Today",
            "Athlete": alert[0],
            "Reason": alert[1],
            "Action": "Recovery check-in",
            "_order": 0,
        })

    # 2. Milestones — quick win to celebrate
    for m in milestones:
        name, bench, val = m[0], m[1], m[2]
        prev = m[3] if len(m) > 3 else ""
        reason = (f"New result — {bench}: {val} (was {prev})" if prev and prev != "first entry"
                  else f"Logged this week — {bench}: {val}")
        rows.append({
            "Priority": "🏆 Celebrate",
            "Athlete": name,
            "Reason": reason,
            "Action": "Congratulate",
            "_order": 1,
        })

    # 3. Consistency streaks — acknowledge
    for name, weeks in consistency_wins:
        rows.append({
            "Priority": "✅ Positive",
            "Athlete": name,
            "Reason": f"{weeks} consecutive weeks logging",
            "Action": "Acknowledge streak",
            "_order": 2,
        })

    # 4. Long-term dropout (45+ days) — urgent re-engage
    for e in engagement_results:
        if not e["flag"]:
            continue
        days = e["days_since"]
        never = e["last_logged"] == "never"
        if never or (days and days >= 45):
            reason = "Never logged" if never else f"{days} days inactive"
            rows.append({
                "Priority": "⚠️ Re-engage",
                "Athlete": e["name"],
                "Reason": reason,
                "Action": "Re-engagement message",
                "_order": 3,
            })

    # 5. Performance concerns
    for athlete, signals in sorted(trend_results.items()):
        for s in signals:
            if s["trend"] == "declining" or s["peak_drop_flag"]:
                parts = []
                if s["trend"] == "declining":
                    parts.append(f"declining ({s['trend_pct']:+.1f}%/entry)")
                if s["peak_drop_flag"]:
                    parts.append(f"{s['peak_drop_pct']:.0f}% below peak")
                rows.append({
                    "Priority": "📉 Performance",
                    "Athlete": athlete,
                    "Reason": f"{s['benchmark']}: {', '.join(parts)}",
                    "Action": "Performance check-in",
                    "_order": 4,
                })

    # 6. Standard inactive (28-44 days)
    for e in engagement_results:
        if not e["flag"]:
            continue
        days = e["days_since"]
        if days and 28 <= days < 45:
            rows.append({
                "Priority": "⚠️ Check In",
                "Athlete": e["name"],
                "Reason": f"{days} days inactive (last: {e['last_logged']})",
                "Action": "Check-in message",
                "_order": 5,
            })

    # 7. In contact but not logging — monthly nudge
    for e in engagement_results:
        if not e.get("nudge_flag"):
            continue
        days = e["days_since"]
        last_contact = e.get("last_contact", "recently")
        reason = (
            f"No results logged ({days} days) — last contact {last_contact}"
            if days else f"Never logged — but in contact (last {last_contact})"
        )
        rows.append({
            "Priority": "📝 Remind to Log",
            "Athlete": e["name"],
            "Reason": reason,
            "Action": "Ask them to record their results",
            "_order": 6,
        })

    rows.sort(key=lambda x: x["_order"])
    for r in rows:
        del r["_order"]

    if not rows:
        st.success("Nothing to action this week — all athletes on track.")
        return

    st.caption(f"{len(rows)} athletes to contact this week")
    df = pd.DataFrame(rows)

    def _row_colour(row):
        colours = {
            "🔴 Contact Today": "background-color: #ffe5e5",
            "🏆 Celebrate":     "background-color: #fff8e1",
            "✅ Positive":      "background-color: #e8f5e9",
            "⚠️ Re-engage":     "background-color: #fff3e0",
            "⚠️ Check In":      "background-color: #fff3e0",
            "📉 Performance":   "background-color: #fce4ec",
            "📝 Remind to Log": "background-color: #e3f2fd",
        }
        colour = colours.get(row["Priority"], "")
        return [colour] * len(row)

    styled = df.style.apply(_row_colour, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True, height=600)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    st.title("🏋️ JST Compete — Coaching Dashboard")
    st.caption(f"Data refreshes every 15 minutes · Last loaded: {dt.datetime.now().strftime('%H:%M')}")

    with st.spinner("Loading..."):
        pr_records, athletes, rec_latest, data_records = load_all()
        trend_results, engagement_results, consistency_wins, rec_alert_rows, rec_by_name = run_analytics(
            pr_records, athletes, rec_latest, data_records
        )
        # Recent results this week = things to celebrate; dedupe per athlete+benchmark
        _week_ago = TODAY - dt.timedelta(days=7)
        _seen_milestones = set()
        milestones = []
        for _r in pr_records:
            _d = _parse_date(str(_r.get("Date", "")))
            if not _d or _d < _week_ago:
                continue
            _nm = str(_r.get("Athlete Name", "")).strip()
            _bn = str(_r.get("Benchmark Name", "")).strip()
            _val = str(_r.get("Value", "")).strip()
            if not _nm or not _bn or (_nm, _bn) in _seen_milestones:
                continue
            _seen_milestones.add((_nm, _bn))
            milestones.append((_nm, _bn, _val))

    tabs = st.tabs(["📋 Outreach List", "🚨 Alerts", "👥 Athletes", "📈 Trends", "💤 Recovery"])

    with tabs[0]:
        page_outreach(engagement_results, trend_results, rec_alert_rows, milestones, consistency_wins)
    with tabs[1]:
        page_alerts(engagement_results, trend_results, rec_alert_rows, consistency_wins)
    with tabs[2]:
        page_athletes(pr_records, athletes, trend_results, engagement_results, rec_by_name)
    with tabs[3]:
        page_trends(pr_records, athletes)
    with tabs[4]:
        page_recovery(rec_by_name)


if __name__ == "__main__":
    main()
