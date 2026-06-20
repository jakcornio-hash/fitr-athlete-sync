"""
JST Compete — Coaching Dashboard

Run locally:  streamlit run dashboard.py
Deploy:       Streamlit Community Cloud → connect GitHub repo → add secrets
"""
import datetime as dt
import json
import os
import re

import streamlit as st
import altair as alt
import pandas as pd

import analytics
import archetypes as arch_mod
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

    archetype_rows = []
    try:
        archetype_rows = sheets.load_archetype_assessments()
    except Exception:
        pass

    return pr_records, athletes, rec_latest, data_records, archetype_rows


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


def _parse_notes_timeline(notes_str):
    """Parse a Coaching Notes cell into [{date, kind, text}, ...] newest-first."""
    entries = []
    if not notes_str:
        return entries
    current_date = current_kind = None
    current_lines = []

    def _flush():
        if current_date and current_lines:
            entries.append({
                "date": current_date,
                "kind": current_kind or "note",
                "text": " ".join(current_lines).strip(),
            })

    header_re = re.compile(r'^\[(\d{4}-\d{2}-\d{2}) — ([^\]]+)\]\s*(.*)')
    for line in notes_str.split("\n"):
        m = header_re.match(line.strip())
        if m:
            _flush()
            current_date = m.group(1)
            current_kind = m.group(2).strip()
            current_lines = [m.group(3).strip()] if m.group(3).strip() else []
        else:
            if current_date and line.strip():
                current_lines.append(line.strip())
    _flush()
    entries.sort(key=lambda x: x["date"], reverse=True)
    return entries


def _archetype_assessment_form(name):
    """Inline forced-choice assessment — coach voice, saves to Sheets on submit."""
    questions = arch_mod.FORCED_CHOICE.get("questions", [])
    with st.form(key=f"arch_assess_{name}", clear_on_submit=True):
        st.caption("Select the option that best describes this athlete. 10 questions, ~4 minutes.")
        answers = []
        for i, q in enumerate(questions):
            st.markdown(f"**{i + 1}. {q['q_coach']}**")
            opts = q.get("options", [])
            choice = st.radio(
                label=f"q_{i}",
                options=list(range(len(opts))),
                format_func=lambda idx, o=opts: o[idx]["coach"],
                label_visibility="collapsed",
                key=f"arch_{name}_q{i}",
            )
            answers.append(choice)
        notes = st.text_input(
            "Notes (optional)", key=f"arch_{name}_notes",
            placeholder="e.g. Based on 6-month observation",
        )
        if st.form_submit_button("Save Assessment"):
            result = arch_mod.score_forced_choice(answers)
            row = {
                "Athlete Name": name,
                "Assessor": "Coach",
                "Instrument": "forced_choice",
                "Version": str(arch_mod.FORCED_CHOICE.get("version", 1)),
                "Taken At": TODAY.isoformat(),
                "Primary Archetype": result.get("primary", ""),
                "Profile JSON": json.dumps(result),
                "Raw Answers JSON": json.dumps(answers),
                "Notes": notes,
            }
            get_sheets().write_archetype_assessment(row)
            primary_name = arch_mod.get_archetype(result.get("primary", "")).get("name", result.get("primary", "").title())
            st.success(f"Saved — Primary archetype: **{primary_name}**")
            st.cache_data.clear()


def _archetype_panel(name, archetype_by_name):
    """Display archetype profile and offer inline assessment form."""
    st.markdown("**🧠 Conscious Coaching Archetype**")
    assessment = (archetype_by_name or {}).get(name)

    if assessment:
        primary_id = str(assessment.get("Primary Archetype", "")).strip()
        taken_at   = str(assessment.get("Taken At", "")).strip()
        arch = arch_mod.get_archetype(primary_id)

        if arch and primary_id:
            lbl_col, date_col = st.columns([3, 1])
            with lbl_col:
                st.markdown(f"**{arch.get('name', primary_id.title())}** — {arch['coach']['tagline']}")
            with date_col:
                if taken_at:
                    st.caption(f"Assessed {taken_at[:10]}")

            # Profile mix
            try:
                profile = json.loads(assessment.get("Profile JSON") or "{}").get("profile", [])
            except (json.JSONDecodeError, TypeError):
                profile = []
            if profile:
                st.markdown("**Profile mix:**")
                for entry in profile[:5]:
                    aid = entry.get("archetype", "")
                    pct = entry.get("pct", 0)
                    a_def = arch_mod.get_archetype(aid)
                    label = (a_def.get("name") if a_def else None) or aid.replace("_", " ").title()
                    st.progress(pct / 100, text=f"{label} — {pct}%")

            # Coach cues
            coach = arch.get("coach", {})
            toward = coach.get("coach_toward", [])
            avoid  = coach.get("avoid", [])
            prog   = coach.get("programming_read", "")
            if toward or avoid:
                cl, cr = st.columns(2)
                with cl:
                    st.markdown("**Coach toward:**")
                    for cue in toward:
                        st.markdown(f"- {cue}")
                with cr:
                    st.markdown("**Avoid:**")
                    for cue in avoid:
                        st.markdown(f"- {cue}")
            if prog:
                st.info(f"**Programming read:** {prog}")

    label = "📊 Update Assessment" if assessment else "📊 Run First Assessment"
    with st.expander(label, expanded=not assessment):
        _archetype_assessment_form(name)


def _athlete_profile_panel(name, data_by_name, pr_records, trend_results,
                           engagement_results, rec_by_name, archetype_by_name=None):
    """Render the full profile panel for one athlete."""
    profile = data_by_name.get(name, {})

    # ── Header row ────────────────────────────────────────────────────────────
    h1, h2, h3, h4 = st.columns([3, 1, 1, 2])
    h1.markdown(f"### {name}")
    age = str(profile.get("Age", "")).strip()
    h2.metric("Age", age if age else "—")
    tier = str(profile.get("Tier", "")).strip()
    h3.metric("Tier", tier if tier else "—")
    email = str(profile.get("Email", "")).strip()
    h4.markdown(f"**Email**  \n{email or '—'}")

    st.divider()

    # ── Two-column stats ──────────────────────────────────────────────────────
    left, right = st.columns(2)

    with left:
        st.markdown("**Physical**")
        height = str(profile.get("Height (cm)", "")).strip()
        weight = str(profile.get("Weight (kg)", "")).strip()
        dob    = str(profile.get("DOB", "")).strip()
        t_age  = str(profile.get("Training Age (yrs)", "")).strip()
        sleep  = str(profile.get("Sleep Avg (hrs)", "")).strip()
        equip  = str(profile.get("Equipment Access", "")).strip()
        for label, val in [
            ("Height", f"{height} cm" if height else "—"),
            ("Weight", f"{weight} kg" if weight else "—"),
            ("Training Age", f"{t_age} yrs" if t_age else "—"),
            ("Sleep Avg", f"{sleep} hrs" if sleep else "—"),
            ("Equipment", equip or "—"),
        ]:
            st.markdown(f"- **{label}:** {val}")

        st.markdown("")
        st.markdown("**Competition**")
        comp = str(profile.get("Next Competition", "")).strip()
        comp_date = str(profile.get("Competition Date", "")).strip()
        plan = str(profile.get("Subscription Plan", "")).strip()
        for label, val in [
            ("Next Competition", comp or "—"),
            ("Competition Date", comp_date or "—"),
            ("Plan", plan or "—"),
        ]:
            st.markdown(f"- **{label}:** {val}")

    with right:
        st.markdown("**Athlete Profile**")
        goal = str(profile.get("North Star Goal", "")).strip()
        comms = str(profile.get("Communication Style", "")).strip()
        prog_tier = str(profile.get("Programming Tier", "")).strip()
        occ = str(profile.get("Occupation", "")).strip()
        occ_type = str(profile.get("Occupation Type", "")).strip()
        for label, val in [
            ("Goal", goal or "—"),
            ("Comms Style", comms or "—"),
            ("Programming Tier", prog_tier or "—"),
            ("Occupation", f"{occ} ({occ_type})" if occ and occ_type else occ or "—"),
        ]:
            st.markdown(f"- **{label}:** {val}")

        st.markdown("")
        inj_status = str(profile.get("Injury Status", "")).strip()
        inj_notes  = str(profile.get("Injury Notes", "")).strip()
        if inj_status or inj_notes:
            colour = "🔴" if inj_status.lower() not in ("", "none", "ok", "healthy", "clear") else "🟢"
            st.markdown(f"**{colour} Injury Status:** {inj_status or '—'}")
            if inj_notes:
                st.markdown(f"> {inj_notes}")
        else:
            st.markdown("**🟢 Injury Status:** —")

    st.divider()

    # ── Benchmark snapshots ───────────────────────────────────────────────────
    bench_cols = [
        "Back Squat 1RM (kg)", "Front Squat 1RM (kg)", "Clean & Jerk 1RM (kg)",
        "Snatch 1RM (kg)", "Deadlift 1RM (kg)", "Strict Press 1RM (kg)",
        "Max Pull-ups", "2k Row (mm:ss)", "1.2km Run (mm:ss)",
    ]
    snap_vals = {c: str(profile.get(c, "")).strip() for c in bench_cols if profile.get(c)}
    if snap_vals:
        st.markdown("**🏋️ Benchmark Snapshots**")
        cols = st.columns(min(len(snap_vals), 4))
        for i, (col, val) in enumerate(snap_vals.items()):
            short = col.replace(" (kg)", "").replace(" (mm:ss)", "").replace("1RM ", "")
            cols[i % 4].metric(short, val)
        st.divider()

    # ── Latest recovery ───────────────────────────────────────────────────────
    rec_row = rec_by_name.get(name)
    if rec_row:
        st.markdown("**💤 Latest Recovery Survey**")
        rc1, rc2, rc3 = st.columns(3)
        def _num(v):
            try: return float(str(v).strip())
            except: return None
        s = _num(rec_row.get("Soreness"))
        st_ = _num(rec_row.get("Stress"))
        m = _num(rec_row.get("Motivation"))
        rc1.metric("Soreness", f"{s:.0f}/10" if s is not None else "—",
                   delta_color="inverse" if s and s >= 7 else "normal")
        rc2.metric("Stress", f"{st_:.0f}/10" if st_ is not None else "—",
                   delta_color="inverse" if st_ and st_ >= 7 else "normal")
        rc3.metric("Motivation", f"{m:.0f}/10" if m is not None else "—")
        avail = str(rec_row.get("Availability this week", "")).strip()
        if avail:
            st.markdown(f"**Availability:** {avail}")
        submitted = str(rec_row.get("Submitted At", "")).strip()
        if submitted:
            st.caption(f"Survey submitted: {submitted}")
        st.divider()

    # ── Archetype ─────────────────────────────────────────────────────────────
    _archetype_panel(name, archetype_by_name)
    st.divider()

    # ── Coaching notes timeline ───────────────────────────────────────────────
    notes_raw = str(profile.get("Coaching Notes", "")).strip()
    timeline = _parse_notes_timeline(notes_raw)
    if timeline:
        st.markdown("**📝 Coaching Notes**")
        kind_icons = {"chat": "💬", "result": "🏆", "recovery": "💤"}
        for entry in timeline:
            icon = kind_icons.get(entry["kind"], "📌")
            with st.expander(f"{icon} {entry['date']} — {entry['kind']}", expanded=False):
                st.write(entry["text"])
    else:
        st.markdown("**📝 Coaching Notes**")
        st.caption("No notes yet.")


def page_athletes(pr_records, athletes, trend_results, engagement_results,
                  rec_by_name, data_records=None, archetype_by_name=None):
    # Build per-name lookup from _DATA
    data_by_name = {}
    for rec in (data_records or []):
        nm = str(rec.get("Full Name", "")).strip()
        if nm:
            data_by_name[nm] = rec

    # Build summary table
    last_logged = {}
    for r in pr_records:
        nm = str(r.get("Athlete Name", "")).strip()
        d = _parse_date(str(r.get("Date", "")))
        if nm and d and (nm not in last_logged or d > last_logged[nm]):
            last_logged[nm] = d

    eng_map = {e["name"]: e for e in engagement_results}

    summary_rows = []
    for a in athletes:
        nm = a["name"]
        last = last_logged.get(nm)
        days = (TODAY - last).days if last else None
        signals = trend_results.get(nm, [])
        declining = sum(1 for s in signals if s["trend"] == "declining")
        improving = sum(1 for s in signals if s["trend"] == "improving")
        trend_label = (
            f"📉 {declining} declining" if declining
            else f"📈 {improving} improving" if improving
            else "—"
        )
        e = eng_map.get(nm, {})
        nudge = e.get("nudge_flag", False)
        rec_row = rec_by_name.get(nm)
        rec_str = rec_mod.readiness_string(rec_row) if rec_row else "—"
        if rec_str and len(rec_str) > 50:
            rec_str = rec_str[:50] + "…"
        arch_row = (archetype_by_name or {}).get(nm)
        arch_primary = ""
        if arch_row:
            aid = str(arch_row.get("Primary Archetype", "")).strip()
            arch_def = arch_mod.get_archetype(aid)
            arch_primary = arch_def.get("name", aid.replace("_", " ").title()) if arch_def else aid.replace("_", " ").title()
        summary_rows.append({
            "Name": nm,
            "Age": str(data_by_name.get(nm, {}).get("Age", "")).strip() or "—",
            "Tier": str(data_by_name.get(nm, {}).get("Tier", "")).strip() or "—",
            "Last Logged": last.isoformat() if last else "Never",
            "Days Since": days if days is not None else "—",
            "Trend": trend_label,
            "Recovery": rec_str,
            "Archetype": arch_primary or "—",
            "Logging": "📝 Nudge" if nudge else ("✅ Active" if (days is not None and days < 28) else "⚠️ Inactive"),
        })

    df = pd.DataFrame(summary_rows)

    st.caption("Click a row to open the full athlete profile.")
    event = st.dataframe(
        df, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row",
    )

    selected_rows = event.selection.rows if hasattr(event, "selection") else []
    if selected_rows:
        selected_name = df.iloc[selected_rows[0]]["Name"]
        st.divider()
        _athlete_profile_panel(
            selected_name, data_by_name, pr_records, trend_results,
            engagement_results, rec_by_name, archetype_by_name=archetype_by_name,
        )
    else:
        st.caption("No athlete selected.")


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
        pr_records, athletes, rec_latest, data_records, archetype_rows = load_all()
        trend_results, engagement_results, consistency_wins, rec_alert_rows, rec_by_name = run_analytics(
            pr_records, athletes, rec_latest, data_records
        )
        # Build per-athlete archetype lookup: name -> latest assessment row
        archetype_by_name = {}
        for row in archetype_rows:
            nm = str(row.get("Athlete Name", "")).strip()
            if nm:
                existing = archetype_by_name.get(nm)
                if not existing or str(row.get("Taken At", "")) > str(existing.get("Taken At", "")):
                    archetype_by_name[nm] = row
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
        page_athletes(pr_records, athletes, trend_results, engagement_results, rec_by_name, data_records, archetype_by_name=archetype_by_name)
    with tabs[3]:
        page_trends(pr_records, athletes)
    with tabs[4]:
        page_recovery(rec_by_name)


if __name__ == "__main__":
    main()
