"""
JST Compete — Coaching Dashboard

Run locally:  streamlit run dashboard.py
Deploy:       Streamlit Community Cloud → connect GitHub repo → add secrets
"""
import datetime as dt
import json
import os
import re
import urllib.parse

import streamlit as st
import altair as alt
import pandas as pd

import analytics
import archetypes as arch_mod
import config
import message_templates as msg_tmpl
import recovery as rec_mod

_CHAT_DATE_RE = re.compile(r'\[(\d{4}-\d{2}-\d{2}) — chat\]')


st.set_page_config(
    page_title="JST Compete Coaching",
    page_icon="🏋️",
    layout="wide",
)

TODAY = dt.date.today()


# ── Programme helpers ─────────────────────────────────────────────────────────

def _prog_short(prog):
    """Shorten programme name for compact display.
    'JST Athlete - 2 Sessions Per Day' → 'JST Athlete 2x'
    """
    if not prog:
        return "—"
    return (prog
            .replace("2 Sessions Per Day", "2x/day")
            .replace("1 Session Per Day", "1x/day")
            .replace(" - ", " "))


def _programme_contact(prog):
    """Infer who should make outreach.

    JST Athlete tracks → 'JST' (Ed / head coach)
    Anything containing 'youth' → 'JST Youth'
    Everything else → the programme name itself (encodes the individual coach)
    """
    if not prog:
        return "—"
    if prog in config.JST_TRACKS:
        return "JST"
    if "youth" in prog.lower():
        return "JST Youth"
    return prog


# ── Auth ──────────────────────────────────────────────────────────────────────

# Sheet IDs are config, not secrets — hard-code them as Cloud fallbacks
_SHEET_ID = "1Fx4r3IrYeytoysX_hkarZ5I5SoQdq3ZSPZkS0Y2yDOc"
_RECOVERY_SHEET_ID = "1hSBGVWppOfzI1GUO-ZK74tgLbSye8apDpLn3b2QDoys"


@st.cache_resource
def get_sheets():
    """Return SheetsClient, using Streamlit secrets on Cloud or .env locally."""
    from sheets_client import SheetsClient
    # Support both key names: GOOGLE_SERVICE_ACCOUNT (our template) and
    # gcp_service_account (Streamlit's built-in gspread shorthand)
    sa_key = next(
        (k for k in ("GOOGLE_SERVICE_ACCOUNT", "gcp_service_account") if k in st.secrets),
        None,
    )
    if sa_key:
        sa = dict(st.secrets[sa_key])
        # Read IDs directly from secrets — don't rely on config module variable
        sheet_id = str(st.secrets.get("SHEET_ID", "") or config.SHEET_ID).strip()
        # Also propagate to config for other modules that read it
        if sheet_id:
            config.SHEET_ID = sheet_id
        if not config.RECOVERY_SHEET_ID:
            config.RECOVERY_SHEET_ID = str(st.secrets.get("RECOVERY_SHEET_ID", "")).strip()
        if not config.COMP_FORM_SHEET_ID:
            config.COMP_FORM_SHEET_ID = str(st.secrets.get("COMP_FORM_SHEET_ID", "")).strip()
        return SheetsClient(service_account_info=sa, sheet_id=sheet_id)
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

    competition_rows = []
    try:
        competition_rows = sheets.load_competitions()
    except Exception:
        pass

    return pr_records, athletes, rec_latest, data_records, archetype_rows, competition_rows


def run_analytics(pr_records, athletes, rec_latest, data_records=None, competition_rows=None):  # noqa: too-many-locals
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

    # Use the Competitions tab if available; fall back to _DATA columns
    if competition_rows:
        comp_results = analytics.comp_schedule(competition_rows=competition_rows)
    else:
        comp_results = analytics.comp_schedule(athletes=athletes, data_records=data_records)

    return trend_results, engagement_results, consistency_wins, rec_alert_rows, rec_by_name, comp_results


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

def page_self_assess():
    """Public-facing athlete self-assessment — shown when ?mode=self_assess&id=JST_ID."""
    jst_id = (st.query_params.get("id") or "").strip()
    if not jst_id:
        st.error("No athlete ID in URL. Ask your coach for your personal link.")
        return

    sheets = get_sheets()
    try:
        data_records = sheets.read_records(config.TAB_DATA)
    except Exception:
        st.error("Unable to load. Please try again later.")
        return

    athlete_name = None
    for rec in data_records:
        rid = str(rec.get("Athlete ID", "")).strip()
        if rid and rid == jst_id:
            athlete_name = str(rec.get("Full Name", "")).strip()
            break

    if not athlete_name:
        st.error("Link not recognised — ask your coach for a new one.")
        return

    st.title("JST Compete — Athlete Self-Assessment")
    st.markdown(
        f"Hi **{athlete_name}** — this takes about 4 minutes. "
        "Pick the option that feels most true, most of the time."
    )
    st.divider()

    questions = arch_mod.FORCED_CHOICE.get("questions", [])

    with st.form("self_assess_form", clear_on_submit=True):
        answers = []
        for i, q in enumerate(questions):
            st.markdown(f"**{i + 1}. {q['q_athlete']}**")
            opts = q.get("options", [])
            choice = st.radio(
                label=f"q_{i}",
                options=list(range(len(opts))),
                format_func=lambda idx, o=opts: o[idx]["athlete"],
                label_visibility="collapsed",
                key=f"self_q{i}",
            )
            answers.append(choice)

        if st.form_submit_button("Submit", type="primary"):
            result = arch_mod.score_forced_choice(answers)
            row = {
                "Athlete Name": athlete_name,
                "Assessor": "Athlete (Self)",
                "Instrument": "forced_choice",
                "Version": str(arch_mod.FORCED_CHOICE.get("version", 1)),
                "Taken At": TODAY.isoformat(),
                "Primary Archetype": result.get("primary", ""),
                "Profile JSON": json.dumps(result),
                "Raw Answers JSON": json.dumps(answers),
                "Notes": "",
            }
            sheets.write_archetype_assessment(row)

            primary_id = result.get("primary", "")
            arch = arch_mod.get_archetype(primary_id)
            st.success("Submitted — thank you!")
            if arch:
                st.markdown(f"## Your archetype: **{arch['name']}**")
                st.markdown(f"*{arch['athlete']['tagline']}*")
                st.divider()
                wc, tc = st.columns(2)
                with wc:
                    st.markdown("**What works for you:**")
                    for w in arch["athlete"].get("works", []):
                        st.markdown(f"- {w}")
                with tc:
                    st.markdown("**What tends to trip you up:**")
                    for t in arch["athlete"].get("trips", []):
                        st.markdown(f"- {t}")
                tell = arch["athlete"].get("tell_coach", "")
                if tell:
                    st.info(f"💡 **Tell your coach:** {tell}")


def page_alerts(engagement_results, trend_results, rec_alert_rows, consistency_wins, data_records=None):
    flagged = [e for e in engagement_results if e["flag"]]
    concerns = [
        (athlete, s)
        for athlete, signals in trend_results.items()
        for s in signals
        if s["trend"] == "declining" or s["peak_drop_flag"]
    ]

    data_by_name = {str(r.get("Full Name", "")).strip(): r for r in (data_records or [])}

    def _prog(name):
        return str(data_by_name.get(name, {}).get("Programme", "")).strip()

    # Metric row
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🔴 Recovery Flags", len(rec_alert_rows))
    c2.metric("⚠️ Inactive Athletes", len(flagged))
    c3.metric("📉 Performance Concerns", len(concerns))
    c4.metric("✅ Consistency Streaks", len(consistency_wins))

    st.divider()

    if rec_alert_rows:
        st.subheader("🔴 Recovery Flags")
        rec_rows = []
        for alert in rec_alert_rows:
            name = alert[0]
            prog = _prog(name)
            rec_rows.append({
                "Athlete": name,
                "Issue": alert[1],
                "Submitted": alert[2] if len(alert) > 2 else "",
                "Programme": _prog_short(prog),
                "Contact": _programme_contact(prog),
            })
        st.dataframe(pd.DataFrame(rec_rows), use_container_width=True, hide_index=True)
        st.write("")

    if flagged:
        st.subheader("⚠️ Engagement / Dropout Risk")
        rows = []
        for e in flagged:
            prog = _prog(e["name"])
            rows.append({
                "Athlete": e["name"],
                "JST ID": e["jst_id"],
                "Last Logged": e["last_logged"],
                "Days Inactive": e["days_since"] if e["days_since"] is not None else "Never",
                "Status": "Never logged" if e["last_logged"] == "never" else "Inactive",
                "Programme": _prog_short(prog),
                "Contact": _programme_contact(prog),
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


def _profile_completeness(name, profile, archetype_by_name, has_logged, has_recovery):
    """Return (done, total, [(label, bool)]) for the onboarding checklist."""
    checks = [
        ("Email",                bool(str(profile.get("Email", "")).strip())),
        ("Age",                  bool(str(profile.get("Age", "")).strip())),
        ("North Star Goal",      bool(str(profile.get("North Star Goal", "")).strip())),
        ("Communication Style",  bool(str(profile.get("Communication Style", "")).strip())),
        ("Programme assigned",   bool(str(profile.get("Programme", "")).strip())),
        ("Archetype assessed",   bool((archetype_by_name or {}).get(name))),
        ("Has logged results",   has_logged),
        ("Recovery survey",      has_recovery),
    ]
    done = sum(1 for _, v in checks if v)
    return done, len(checks), checks


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


def _archetype_panel(name, archetype_by_name, profile=None):
    """Display archetype profile, inline assessment form, and self-assessment link."""
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

    jst_id = str((profile or {}).get("Athlete ID", "")).strip()
    if jst_id:
        with st.expander("🔗 Share Self-Assessment Link with Athlete"):
            st.caption(
                "The athlete fills this in themselves — answers go directly to your dashboard. "
                "Paste your dashboard URL before the query string below."
            )
            st.code(f"?mode=self_assess&id={jst_id}", language=None)


def _athlete_profile_panel(name, data_by_name, pr_records, trend_results,
                           engagement_results, rec_by_name, archetype_by_name=None,
                           competition_rows=None, data_records=None):
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
        plan = str(profile.get("Subscription Plan", "")).strip()
        if plan:
            st.markdown(f"- **Plan:** {plan}")

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

        # Tier + Programme on one row
        ta, tb = st.columns(2)
        with ta:
            st.markdown("**Tier**")
            current_tier = str(profile.get("Tier", "")).strip()
            tier_opts = ["— not set —"] + config.JST_TIERS
            tier_idx = tier_opts.index(current_tier) if current_tier in tier_opts else 0
            sel_tier = st.selectbox(
                "Tier", tier_opts, index=tier_idx,
                label_visibility="collapsed", key=f"tier_sel_{name}",
            )
            if st.button("Save Tier", key=f"tier_save_{name}", type="secondary"):
                new_tier = "" if sel_tier == "— not set —" else sel_tier
                get_sheets().batch_update_by_name(
                    config.TAB_DATA, "Full Name", {name: {"Tier": new_tier}}
                )
                st.success(f"Tier: {new_tier or '(cleared)'}")
                st.cache_data.clear()

        with tb:
            st.markdown("**Programme**")
            current_prog = str(profile.get("Programme", "")).strip()
            prog_options = ["— not set —"] + config.JST_TRACKS
            prog_idx = prog_options.index(current_prog) if current_prog in prog_options else 0
            sel_prog = st.selectbox(
                "Programme", prog_options, index=prog_idx,
                label_visibility="collapsed", key=f"prog_sel_{name}",
            )
            new_prog = "" if sel_prog == "— not set —" else sel_prog
            if st.button("Save Programme", key=f"prog_save_{name}", type="secondary"):
                get_sheets().batch_update_by_name(
                    config.TAB_DATA, "Full Name", {name: {"Programme": new_prog}}
                )
                st.success(f"Saved: {new_prog or '(cleared)'}")
                st.cache_data.clear()

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

    # ── Profile completeness ──────────────────────────────────────────────────
    has_logged = any(str(r.get("Athlete Name", "")).strip() == name for r in pr_records)
    has_recovery = bool(rec_by_name.get(name))
    done, total, checks = _profile_completeness(name, profile, archetype_by_name, has_logged, has_recovery)
    if done < total:
        with st.expander(f"⚠️ Profile {done}/{total} complete — tap to see what's missing", expanded=False):
            for lbl, ok in checks:
                st.markdown(f"{'✅' if ok else '❌'} {lbl}")
    else:
        st.success(f"✅ Profile complete ({done}/{total})")

    st.divider()

    # ── Competition calendar ───────────────────────────────────────────────────
    st.markdown("**🏁 Competition Calendar**")

    # Filter this athlete's competitions from the Competitions tab
    athlete_comps = []
    if competition_rows:
        for cr in competition_rows:
            if str(cr.get("Athlete Name", "")).strip() == name:
                cd = _parse_date(str(cr.get("Date", "")).strip())
                if cd:
                    athlete_comps.append({
                        "comp_name": str(cr.get("Competition Name", "")).strip() or "Competition",
                        "comp_date": cd,
                        "comp_type": str(cr.get("Type", "A")).strip().upper() or "A",
                        "notes": str(cr.get("Notes", "")).strip(),
                    })
        athlete_comps.sort(key=lambda x: x["comp_date"])

    if athlete_comps:
        comp_rows_display = []
        for c in athlete_comps:
            days_out = (c["comp_date"] - TODAY).days
            phase, _ = analytics.comp_phase(days_out, comp_type=c["comp_type"])
            if days_out >= 0:
                w, d = divmod(days_out, 7)
                time_str = f"{w}w {d}d" if w else f"{d}d"
            else:
                time_str = f"{abs(days_out)}d ago"
            comp_rows_display.append({
                "Type": _COMP_TYPE_LABEL.get(c["comp_type"], c["comp_type"]),
                "Competition": c["comp_name"],
                "Date": c["comp_date"].strftime("%d %b %Y"),
                "Time Out": time_str,
                "Phase": f"{_PHASE_EMOJI.get(phase, chr(9898))} {phase}" if phase else "—",
            })
        st.dataframe(pd.DataFrame(comp_rows_display), use_container_width=True, hide_index=True)

        # Show full coaching panel for the nearest upcoming A competition
        next_a = next(
            (c for c in athlete_comps
             if c["comp_type"] == "A" and (c["comp_date"] - TODAY).days >= -14),
            None
        )
        if next_a:
            days_out = (next_a["comp_date"] - TODAY).days
            phase, action = analytics.comp_phase(days_out, comp_type="A")
            emoji = _PHASE_EMOJI.get(phase, chr(9898))
            cc1, cc2, cc3 = st.columns(3)
            if days_out >= 0:
                w, d = divmod(days_out, 7)
                cc1.metric(next_a["comp_name"], f"{w}w {d}d", help="Next A competition")
            else:
                cc1.metric(next_a["comp_name"], f"{abs(days_out)}d ago")
            cc2.markdown(f"**Phase**  \n{emoji} {phase}")
            if action:
                cc3.markdown(f"**Action**  \n⚡ {action}")
            msg = analytics.comp_message(name, next_a["comp_name"], days_out, comp_type="A")
            with st.expander("\U0001f4e8 Message Template — click to copy"):
                st.code(msg, language=None)
    else:
        # Fall back to legacy _DATA comp fields if no Competitions tab entries
        comp_name = str(profile.get("Next Competition", "")).strip()
        comp_date_str = str(profile.get("Competition Date", "")).strip()
        comp_date = _parse_date(comp_date_str) if comp_date_str else None
        if comp_date:
            days_out = (comp_date - TODAY).days
            phase, action = analytics.comp_phase(days_out)
            emoji = _PHASE_EMOJI.get(phase, chr(9898))
            cc1, cc2, cc3 = st.columns(3)
            if days_out >= 0:
                w, d = divmod(days_out, 7)
                cc1.metric(comp_name or "Competition", f"{w}w {d}d")
            else:
                cc1.metric(comp_name or "Competition", f"{abs(days_out)}d ago")
            cc2.markdown(f"**Phase**  \n{emoji} {phase}")
            if action:
                cc3.markdown(f"**Action**  \n⚡ {action}")
            msg = analytics.comp_message(name, comp_name, days_out)
            with st.expander("\U0001f4e8 Message Template — click to copy"):
                st.code(msg, language=None)
        else:
            st.caption("No competitions planned. Use the form below to add one.")

    # A/B/C guidance
    with st.expander("ℹ️ What are A, B, and C competitions?"):
        st.markdown("""
**\U0001f947 A Competition — Primary goal event**
Everything points to this. You run a full peak block: 10-week prep followed by a 2-week final peak. Training is structured around peaking for this date. Athletes should have 1–3 per year maximum.

**\U0001f948 B Competition — Secondary race**
Athletes race hard and get a real result, but training doesn't change around it. It's a test of current fitness within the existing training phase — great for benchmarking and race-day practice. Minor volume reduction in race week only.

**\U0001f949 C Competition — Training day**
Show up and compete, but treat it like a hard training session. No taper, no disruption to the programme. Good for staying sharp, building race experience, or breaking up a long training block.

*The A/B/C system comes from periodization theory — it's how elite endurance and CrossFit coaches structure a competitive year so athletes peak when it matters most.*
        """)

    # Add competition form
    with st.expander("➕ Add Competition"):
        with st.form(f"add_comp_form_{name}", clear_on_submit=True):
            new_comp_name = st.text_input("Competition Name", key=f"add_comp_name_{name}",
                                          placeholder="e.g. CrossFit Open 2027")
            new_comp_date = st.text_input(
                "Competition Date (DD/MM/YYYY)", key=f"add_comp_date_{name}",
                placeholder="e.g. 15/01/2027",
            )
            comp_type_choice = st.radio(
                "Competition Type",
                options=["A", "B", "C"],
                format_func=lambda t: {
                    "A": "\U0001f947 A — Primary goal. Full taper and peak programme.",
                    "B": "\U0001f948 B — Secondary race. Race hard, no programme change.",
                    "C": "\U0001f949 C — Training day. Compete, no taper.",
                }[t],
                horizontal=False,
                key=f"add_comp_type_{name}",
            )
            new_comp_notes = st.text_input("Notes (optional)", key=f"add_comp_notes_{name}",
                                           placeholder="e.g. Targeting podium / team event")
            if st.form_submit_button("Add to Competition Calendar", type="primary"):
                if new_comp_name.strip() and new_comp_date.strip():
                    parsed = _parse_date(new_comp_date.strip())
                    if parsed:
                        get_sheets().save_competition({
                            "Athlete Name": name,
                            "Competition Name": new_comp_name.strip(),
                            "Date": new_comp_date.strip(),
                            "Type": comp_type_choice,
                            "Notes": new_comp_notes.strip(),
                            "Synced At": TODAY.isoformat(),
                        })
                        st.success(f"Added {new_comp_name.strip()} ({comp_type_choice}) — {parsed.strftime('%d %b %Y')}")
                        st.cache_data.clear()
                    else:
                        st.error("Could not parse the date — use DD/MM/YYYY format.")
                else:
                    st.warning("Competition name and date are required.")

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
    _archetype_panel(name, archetype_by_name, profile=profile)
    st.divider()

    # ── Coaching notes timeline ───────────────────────────────────────────────
    notes_raw = str(profile.get("Coaching Notes", "")).strip()
    timeline = _parse_notes_timeline(notes_raw)
    st.markdown("**📝 Coaching Notes**")
    if timeline:
        kind_icons = {"chat": "💬", "result": "🏆", "recovery": "💤"}
        for entry in timeline:
            icon = kind_icons.get(entry["kind"], "📌")
            with st.expander(f"{icon} {entry['date']} — {entry['kind']}", expanded=False):
                st.write(entry["text"])
    else:
        st.caption("No notes yet.")

    with st.expander("✏️ Add Note"):
        with st.form(f"note_form_{name}", clear_on_submit=True):
            note_text = st.text_area(
                "Note", key=f"note_text_{name}",
                placeholder="e.g. Discussed competition prep — feeling strong about the next block",
            )
            note_kind = st.selectbox(
                "Type", ["note", "chat", "result", "recovery"],
                key=f"note_kind_{name}",
            )
            if st.form_submit_button("Save Note"):
                if note_text.strip():
                    line = f"[{TODAY.isoformat()} — {note_kind}] {note_text.strip()}"
                    current = str(profile.get("Coaching Notes", "")).strip()
                    new_notes = (current + "\n" + line).strip() if current else line
                    get_sheets().batch_update_by_name(
                        config.TAB_DATA, "Full Name", {name: {"Coaching Notes": new_notes}}
                    )
                    st.success("Note saved.")
                    st.cache_data.clear()
                else:
                    st.warning("Note is empty.")

    st.divider()

    # ── Programme peer comparison ─────────────────────────────────────────────
    programme = str(profile.get("Programme", "")).strip()
    if programme:
        peer_data = analytics.programme_peer_comparison(name, programme, pr_records, data_records or list(data_by_name.values()))

        if peer_data:
            st.markdown("**📊 Programme Peer Comparison**")
            st.caption(f"vs others on {programme}")
            _dir_icons = {"above": "🟢", "below": "🔴", "at": "⚪"}
            for p in peer_data[:6]:
                icon = _dir_icons.get(p["direction"], "⚪")
                short_bench = (
                    p["benchmark"]
                    .replace(" (kg)", "")
                    .replace(" (mm:ss)", "")
                    .replace("1RM ", "")
                )
                pct_txt = f"{p['percentile']}th percentile"
                peer_txt = f"median {p['peer_median']}"
                st.markdown(
                    f"{icon} **{short_bench}:** {p['athlete_value']} "
                    f"({pct_txt}, {peer_txt}, n={p['peer_count']})"
                )

    st.divider()

    # ── Journey timeline ───────────────────────────────────────────────────────
    with st.expander("📅 Full Journey Timeline", expanded=False):
        events = []

        # PR log entries
        for r in pr_records:
            if str(r.get("Athlete Name", "")).strip() != name:
                continue
            d = _parse_date(str(r.get("Date", "")))
            bench = str(r.get("Benchmark Name", "")).strip()
            val = str(r.get("Value", "")).strip()
            note = str(r.get("Note", "")).strip()
            if d and bench:
                label = f"{bench}: {val}"
                if note:
                    label += f" — \"{note}\""
                events.append({"date": d.isoformat(), "icon": "🏆", "kind": "result", "text": label})

        # Coaching notes (already parsed above)
        for entry in timeline:
            events.append({
                "date": entry["date"],
                "icon": {"chat": "💬", "result": "🏆", "recovery": "💤"}.get(entry["kind"], "📌"),
                "kind": entry["kind"],
                "text": entry["text"],
            })

        # Competition entries
        if competition_rows:
            for cr in competition_rows:
                if str(cr.get("Athlete Name", "")).strip() != name:
                    continue
                cd = _parse_date(str(cr.get("Date", "")).strip())
                if cd:
                    ct = str(cr.get("Type", "A")).strip()
                    badge = _COMP_TYPE_LABEL.get(ct, ct)
                    events.append({
                        "date": cd.isoformat(),
                        "icon": badge[:2],
                        "kind": "competition",
                        "text": f"{str(cr.get('Competition Name', '')).strip()} ({ct}-race)",
                    })

        if not events:
            st.caption("No events recorded yet.")
        else:
            events.sort(key=lambda x: x["date"], reverse=True)
            current_month = None
            for ev in events:
                try:
                    month = ev["date"][:7]  # YYYY-MM
                    month_label = dt.datetime.strptime(month, "%Y-%m").strftime("%B %Y")
                except (ValueError, TypeError):
                    month_label = ev["date"][:7]
                    month = month_label
                if month != current_month:
                    st.markdown(f"**{month_label}**")
                    current_month = month
                st.markdown(f"&nbsp;&nbsp;{ev['icon']} `{ev['date'][5:]}` &nbsp; {ev['text']}")


def page_athletes(pr_records, athletes, trend_results, engagement_results,
                  rec_by_name, data_records=None, archetype_by_name=None,
                  competition_rows=None):
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
        prog_full = str(data_by_name.get(nm, {}).get("Programme", "")).strip()
        prog_short = (
            prog_full
            .replace(" - 2 Sessions Per Day", " 2x")
            .replace(" - 1 Session Per Day", " 1x")
            or "—"
        )
        has_logged_nm = nm in last_logged
        has_rec_nm = bool(rec_by_name.get(nm))
        done_nm, total_nm, _ = _profile_completeness(
            nm, data_by_name.get(nm, {}), archetype_by_name, has_logged_nm, has_rec_nm
        )
        summary_rows.append({
            "Name": nm,
            "Programme": prog_short,
            "Profile": f"{done_nm}/{total_nm}",
            "Last Logged": last.isoformat() if last else "Never",
            "Days Since": days if days is not None else "—",
            "Trend": trend_label,
            "Recovery": rec_str,
            "Archetype": arch_primary or "—",
            "Logging": "📝 Nudge" if nudge else ("✅ Active" if (days is not None and days < 28) else "⚠️ Inactive"),
        })

    # ── Filters ───────────────────────────────────────────────────────────────
    all_programmes = sorted({r["Programme"] for r in summary_rows})
    all_tiers = sorted(filter(None, {
        str(data_by_name.get(a["name"], {}).get("Tier", "")).strip()
        for a in athletes
    }))
    all_archetypes = sorted(filter(None, {r["Archetype"] for r in summary_rows if r["Archetype"] != "—"}))
    all_statuses = sorted({r["Logging"] for r in summary_rows})

    fc1, fc2, fc3, fc4 = st.columns(4)
    f_prog = fc1.multiselect("Programme", all_programmes, placeholder="All")
    f_tier = fc2.multiselect("Tier", all_tiers, placeholder="All")
    f_arch = fc3.multiselect("Archetype", all_archetypes, placeholder="All")
    f_status = fc4.multiselect("Status", all_statuses, placeholder="All")

    filtered_rows = summary_rows
    if f_prog:
        filtered_rows = [r for r in filtered_rows if r["Programme"] in f_prog]
    if f_tier:
        filtered_rows = [
            r for r in filtered_rows
            if str(data_by_name.get(r["Name"], {}).get("Tier", "")).strip() in f_tier
        ]
    if f_arch:
        filtered_rows = [r for r in filtered_rows if r["Archetype"] in f_arch]
    if f_status:
        filtered_rows = [r for r in filtered_rows if r["Logging"] in f_status]

    total = len(summary_rows)
    shown = len(filtered_rows)
    if shown < total:
        st.caption(f"Showing {shown} of {total} athletes")

    df = pd.DataFrame(filtered_rows)

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
            competition_rows=competition_rows, data_records=data_records,
        )
    else:
        st.caption("No athlete selected.")


def page_trends(pr_records, athletes):
    athlete_names = sorted({a["name"] for a in athletes})

    col_sel, col_cmp = st.columns([2, 1])
    selected = col_sel.selectbox("Athlete", athlete_names, key="trend_main")
    compare_mode = col_cmp.checkbox("Compare with another athlete", key="trend_cmp_toggle")

    selected_b = None
    if compare_mode:
        other_names = [n for n in athlete_names if n != selected]
        if other_names:
            selected_b = st.selectbox("Compare with", other_names, key="trend_b")

    if not selected:
        return

    athlete_records = [
        r for r in pr_records
        if str(r.get("Athlete Name", "")).strip() == selected
    ]
    benchmarks = sorted({str(r.get("Benchmark Name", "")).strip() for r in athlete_records if r.get("Benchmark Name")})

    if not benchmarks:
        st.info(f"No PR Log entries found for {selected}.")
        return

    selected_bench = st.selectbox("Benchmark", benchmarks)

    def _get_points(name, bench):
        pts = []
        for r in pr_records:
            if str(r.get("Athlete Name", "")).strip() != name:
                continue
            if str(r.get("Benchmark Name", "")).strip() != bench:
                continue
            d = _parse_date(str(r.get("Date", "")))
            v = _numeric(str(r.get("Value", "")))
            if d and v is not None:
                pts.append({
                    "Date": pd.Timestamp(d),
                    "Value": v,
                    "Label": str(r.get("Value", "")),
                    "Athlete": name,
                })
        return pts

    points_a = _get_points(selected, selected_bench)

    if not points_a:
        st.info("No numeric values found for this benchmark.")
        return

    points_b = _get_points(selected_b, selected_bench) if selected_b else []
    df_all = pd.DataFrame(points_a + points_b).sort_values("Date")

    if points_b:
        chart = (
            alt.Chart(df_all)
            .mark_line(point=True)
            .encode(
                x=alt.X("Date:T", title="Date"),
                y=alt.Y("Value:Q", title=selected_bench, scale=alt.Scale(zero=False)),
                color=alt.Color("Athlete:N", legend=alt.Legend(title="Athlete")),
                tooltip=["Date:T", "Athlete:N", "Label:N"],
            )
            .properties(height=350)
        )
    else:
        chart = (
            alt.Chart(df_all)
            .mark_line(point=True, color="#1f77b4")
            .encode(
                x=alt.X("Date:T", title="Date"),
                y=alt.Y("Value:Q", title=selected_bench, scale=alt.Scale(zero=False)),
                tooltip=["Date:T", "Label:N"],
            )
            .properties(height=350)
        )
    st.altair_chart(chart, use_container_width=True)

    df_a = pd.DataFrame(points_a).sort_values("Date")
    col1, col2, col3 = st.columns(3)
    col1.metric(f"{selected} — Entries", len(df_a))
    col2.metric(f"{selected} — Best", df_a["Label"].iloc[df_a["Value"].argmax()])
    col3.metric(f"{selected} — Latest", df_a["Label"].iloc[-1])

    if points_b:
        df_b = pd.DataFrame(points_b).sort_values("Date")
        cb1, cb2, cb3 = st.columns(3)
        cb1.metric(f"{selected_b} — Entries", len(df_b))
        cb2.metric(f"{selected_b} — Best", df_b["Label"].iloc[df_b["Value"].argmax()])
        cb3.metric(f"{selected_b} — Latest", df_b["Label"].iloc[-1])


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


_PHASE_EMOJI = {
    # A competition phases
    "Post-Competition":      "🟣",
    "2-Week Peak Prep":      "🔴",
    "Switch → 2-Week Prep":  "🚨",
    "10-Week Prep":          "🟠",
    "Switch → 10-Week Prep": "🚨",
    "Pre-Peak":              "🟡",
    "Approaching":           "🟢",
    "Normal Training":       "⚪",
    # B competition phases
    "B — Race Week":         "🥈",
    "B — Final Prep":        "🥈",
    "B — Approaching":       "🥈",
    # C competition phases
    "C — Race Week":         "🥉",
    "C — Coming Up":         "🥉",
}

_COMP_TYPE_LABEL = {"A": "🥇 A", "B": "🥈 B", "C": "🥉 C"}


def page_competitions(comp_results, athletes, data_records, competition_rows=None):
    data_by_name = {str(r.get("Full Name", "")).strip(): r for r in (data_records or [])}
    all_names = {a["name"] for a in athletes}

    if comp_results:
        st.subheader("Upcoming & Recent Competitions")

        # Sort: A first within each type, then soonest
        a_comps = [c for c in comp_results if c.get("comp_type") == "A"]
        b_comps = [c for c in comp_results if c.get("comp_type") == "B"]
        c_comps = [c for c in comp_results if c.get("comp_type") == "C"]

        rows = []
        for c in comp_results:
            ct = c.get("comp_type", "A")
            emoji = _PHASE_EMOJI.get(c["phase"], chr(9898))
            type_badge = _COMP_TYPE_LABEL.get(ct, ct)
            if c["days_out"] >= 0:
                w, d = divmod(c["days_out"], 7)
                time_str = f"{w}w {d}d" if w else f"{d}d"
            else:
                time_str = f"{abs(c['days_out'])}d ago"
            prog = str(data_by_name.get(c["name"], {}).get("Programme", "")).strip()
            prog_short = prog.replace(" - 2 Sessions Per Day", " 2x").replace(" - 1 Session Per Day", " 1x")
            rows.append({
                "Type": type_badge,
                "": emoji,
                "Athlete": c["name"],
                "Competition": c["comp_name"],
                "Date": c["comp_date"].strftime("%d %b %Y"),
                "Out": time_str,
                "Phase": c["phase"],
                "Action": c["action"] or "—",
                "Programme": prog_short or "—",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # Summary counts
        if a_comps or b_comps or c_comps:
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("A-Race Competitions", len(a_comps))
            mc2.metric("B-Race Competitions", len(b_comps))
            mc3.metric("C-Race / Training Days", len(c_comps))
        st.divider()

    # Actionable panel: A-competition transitions (only A comps require programme switches)
    action_items = [c for c in comp_results if c["action"]]
    a_actions = [c for c in action_items if c.get("comp_type") == "A"]
    bc_actions = [c for c in action_items if c.get("comp_type") in ("B", "C")]

    if a_actions:
        st.subheader("⚡ A-Race: Actions Required Now")
        for c in a_actions:
            emoji = _PHASE_EMOJI.get(c["phase"], chr(9889))
            with st.expander(f"{emoji} {c['name']} — {c['action']}", expanded=True):
                col1, col2 = st.columns([1, 2])
                with col1:
                    st.metric(c["comp_name"] or "Competition", c["comp_date"].strftime("%d %b %Y"))
                    w, d = divmod(max(c["days_out"], 0), 7)
                    st.caption(f"{w}w {d}d out — {c['phase']}")
                with col2:
                    st.markdown("**Ready-to-send message:**")
                    st.code(c["message_template"], language=None)

    if bc_actions:
        st.subheader("B/C Race Reminders")
        for c in bc_actions:
            ct = c.get("comp_type", "B")
            badge = _COMP_TYPE_LABEL.get(ct, ct)
            emoji = _PHASE_EMOJI.get(c["phase"], chr(9898))
            with st.expander(f"{badge} {emoji} {c['name']} — {c['action']}", expanded=False):
                col1, col2 = st.columns([1, 2])
                with col1:
                    st.metric(c["comp_name"] or "Competition", c["comp_date"].strftime("%d %b %Y"))
                    w, d = divmod(max(c["days_out"], 0), 7)
                    st.caption(f"{w}w {d}d out — {c['phase']}")
                with col2:
                    st.code(c["message_template"], language=None)

    if action_items:
        st.divider()

    # Annual competition calendar
    if competition_rows:
        st.subheader("📅 Annual Competition Calendar")
        cal_rows = []
        for row in competition_rows:
            name = str(row.get("Athlete Name", "")).strip()
            comp_name = str(row.get("Competition Name", "")).strip()
            raw_date = str(row.get("Date", "")).strip()
            comp_type = str(row.get("Type", "A")).strip() or "A"
            d = _parse_date(raw_date)
            if d and name:
                cal_rows.append({
                    "Athlete": name,
                    "Competition": comp_name or "Unnamed",
                    "Date": d,
                    "Type": comp_type,
                })
        if cal_rows:
            cal_df = pd.DataFrame(cal_rows)
            cal_df["Date"] = pd.to_datetime(cal_df["Date"])
            # Sort athletes by earliest upcoming comp
            athlete_order = (
                cal_df[cal_df["Date"] >= pd.Timestamp(TODAY)]
                .groupby("Athlete")["Date"]
                .min()
                .sort_values()
                .index.tolist()
            )
            remaining = [a for a in sorted(cal_df["Athlete"].unique()) if a not in athlete_order]
            athlete_order = athlete_order + remaining

            color_scale = alt.Scale(
                domain=["A", "B", "C"],
                range=["#FFD700", "#C0C0C0", "#CD7F32"],
            )
            today_rule = (
                alt.Chart(pd.DataFrame({"today": [pd.Timestamp(TODAY)]}))
                .mark_rule(color="red", strokeDash=[4, 4], strokeWidth=1.5)
                .encode(x=alt.X("today:T"))
            )
            points = (
                alt.Chart(cal_df)
                .mark_point(size=120, filled=True, opacity=0.85)
                .encode(
                    x=alt.X("Date:T", title="Date", axis=alt.Axis(format="%b %Y")),
                    y=alt.Y("Athlete:N", sort=athlete_order, title=""),
                    color=alt.Color("Type:N", scale=color_scale, legend=alt.Legend(title="Type")),
                    shape=alt.Shape(
                        "Type:N",
                        scale=alt.Scale(domain=["A", "B", "C"], range=["triangle-up", "circle", "square"]),
                    ),
                    tooltip=["Athlete:N", "Competition:N", "Date:T", "Type:N"],
                )
            )
            chart = (today_rule + points).properties(
                height=max(200, len(athlete_order) * 30),
            ).interactive()
            st.altair_chart(chart, use_container_width=True)
            st.caption("🔴 dashed line = today  |  🥇 A-race  🥈 B-race  🥉 C-race")
        st.divider()

    # Athletes without any competition planned
    with_comp = {c["name"] for c in comp_results}
    no_comp = sorted(all_names - with_comp)
    if no_comp:
        with st.expander(f"{len(no_comp)} athletes with no competitions planned"):
            st.caption("Share your competition planner Typeform link so they can add their races.")
            st.write(", ".join(no_comp))


def page_programmes(athletes, pr_records, trend_results, data_records, load_results=None):
    from collections import defaultdict

    data_by_name = {str(r.get("Full Name", "")).strip(): r for r in (data_records or [])}

    last_logged = {}
    for r in pr_records:
        nm = str(r.get("Athlete Name", "")).strip()
        d = _parse_date(str(r.get("Date", "")))
        if nm and d and (nm not in last_logged or d > last_logged[nm]):
            last_logged[nm] = d

    by_prog = defaultdict(list)
    for a in athletes:
        prog = str(data_by_name.get(a["name"], {}).get("Programme", "")).strip()
        by_prog[prog or "— Unassigned —"].append(a["name"])

    load_by_name = load_results or {}

    rows = []
    for prog, names in sorted(by_prog.items(), key=lambda x: (x[0] == "— Unassigned —", x[0])):
        count = len(names)
        active = sum(1 for nm in names if nm in last_logged and (TODAY - last_logged[nm]).days < 28)
        days_list = [(TODAY - last_logged[nm]).days for nm in names if nm in last_logged]
        avg_days = round(sum(days_list) / len(days_list)) if days_list else None
        declining = sum(
            1 for nm in names
            for s in trend_results.get(nm, [])
            if s["trend"] == "declining"
        )
        acwr_vals = [load_by_name[nm]["acwr"] for nm in names if nm in load_by_name and load_by_name[nm]["acwr"] is not None]
        avg_acwr = round(sum(acwr_vals) / len(acwr_vals), 2) if acwr_vals else None
        spikes = sum(1 for nm in names if nm in load_by_name and load_by_name[nm]["status"] == "red")
        rows.append({
            "Programme": prog,
            "Athletes": count,
            "Active (28d)": f"{active}/{count} ({round(active / count * 100)}%)" if count else "—",
            "Avg Days Since Log": str(avg_days) if avg_days is not None else "never",
            "Avg ACWR": f"{avg_acwr:.2f}" if avg_acwr is not None else "—",
            "🔴 Load Spikes": str(spikes) if spikes else "—",
            "Declining Trends": str(declining) if declining else "—",
        })

    st.subheader("Programme Breakdown")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    assigned = [(p, len(n)) for p, n in by_prog.items() if p != "— Unassigned —"]
    if assigned:
        chart_df = pd.DataFrame(assigned, columns=["Programme", "Athletes"])
        chart = (
            alt.Chart(chart_df)
            .mark_bar(color="#1f77b4")
            .encode(
                x=alt.X("Athletes:Q", title="Athletes"),
                y=alt.Y("Programme:N", sort="-x", title=""),
                tooltip=["Programme:N", "Athletes:Q"],
            )
            .properties(height=max(200, len(assigned) * 38))
        )
        st.altair_chart(chart, use_container_width=True)

    unassigned = by_prog.get("— Unassigned —", [])
    if unassigned:
        st.markdown(f"**{len(unassigned)} not yet assigned:**  " + ", ".join(sorted(unassigned)))


def _outreach_send_buttons(msg):
    """Render email and WhatsApp send links below an outreach message."""
    encoded = urllib.parse.quote(msg)
    c1, c2, _ = st.columns([1, 1, 5])
    with c1:
        st.link_button(
            "✉️ Email", f"mailto:?subject={urllib.parse.quote('Training Update')}&body={encoded}",
        )
    with c2:
        st.link_button("💬 WhatsApp", f"https://wa.me/?text={encoded}")


def page_outreach(engagement_results, trend_results, rec_alert_rows, milestones,
                  consistency_wins, comp_results=None, archetype_by_name=None, data_records=None):
    """Prioritised list of every athlete who needs contact this week."""
    data_by_name = {str(r.get("Full Name", "")).strip(): r for r in (data_records or [])}

    def _prog(name):
        return str(data_by_name.get(name, {}).get("Programme", "")).strip()

    rows = []

    # 1. Recovery flags — most urgent
    for alert in rec_alert_rows:
        nm = alert[0]; p = _prog(nm)
        rows.append({
            "Priority": "🔴 Contact Today",
            "Athlete": nm,
            "Reason": alert[1],
            "Action": "Recovery check-in",
            "_order": 0,
            "_reason_type": "recovery_flag",
            "_ctx": {"issue": alert[1]},
            "_programme": p,
        })

    # 2. Milestones — quick win to celebrate
    for m in milestones:
        name, bench, val = m[0], m[1], m[2]
        prev = m[3] if len(m) > 3 else ""
        reason = (f"New result — {bench}: {val} (was {prev})" if prev and prev != "first entry"
                  else f"Logged this week — {bench}: {val}")
        p = _prog(name)
        rows.append({
            "Priority": "🏆 Celebrate",
            "Athlete": name,
            "Reason": reason,
            "Action": "Congratulate",
            "_order": 1,
            "_reason_type": "celebrate",
            "_ctx": {"result": reason},
            "_programme": p,
        })

    # 3. Consistency streaks — acknowledge
    for name, weeks in consistency_wins:
        p = _prog(name)
        rows.append({
            "Priority": "✅ Positive",
            "Athlete": name,
            "Reason": f"{weeks} consecutive weeks logging",
            "Action": "Acknowledge streak",
            "_order": 2,
            "_reason_type": "consistency",
            "_ctx": {"weeks": weeks},
            "_programme": p,
        })

    # 4. Long-term dropout (45+ days) — urgent re-engage
    for e in engagement_results:
        if not e["flag"]:
            continue
        days = e["days_since"]
        never = e["last_logged"] == "never"
        if never or (days and days >= 45):
            reason = "Never logged" if never else f"{days} days inactive"
            p = _prog(e["name"])
            rows.append({
                "Priority": "⚠️ Re-engage",
                "Athlete": e["name"],
                "Reason": reason,
                "Action": "Re-engagement message",
                "_order": 3,
                "_reason_type": "never_logged" if never else "re_engage",
                "_ctx": {} if never else {"days": days},
                "_programme": p,
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
                p = _prog(athlete)
                rows.append({
                    "Priority": "📉 Performance",
                    "Athlete": athlete,
                    "Reason": f"{s['benchmark']}: {', '.join(parts)}",
                    "Action": "Performance check-in",
                    "_order": 4,
                    "_reason_type": "performance_concern",
                    "_ctx": {"bench": s["benchmark"]},
                    "_programme": p,
                })

    # 6. Standard inactive (28-44 days)
    for e in engagement_results:
        if not e["flag"]:
            continue
        days = e["days_since"]
        if days and 28 <= days < 45:
            p = _prog(e["name"])
            rows.append({
                "Priority": "⚠️ Check In",
                "Athlete": e["name"],
                "Reason": f"{days} days inactive (last: {e['last_logged']})",
                "Action": "Check-in message",
                "_order": 5,
                "_reason_type": "re_engage",
                "_ctx": {"days": days},
                "_programme": p,
            })

    # 7. Competition prep — phase transitions
    for c in (comp_results or []):
        if not c["action"]:
            continue
        phase = c["phase"]
        ct = c.get("comp_type", "A")
        w, d = divmod(max(c["days_out"], 0), 7)
        time_str = f"{abs(c['days_out'])}d ago" if c["days_out"] < 0 else f"{w}w {d}d out"
        badge = _COMP_TYPE_LABEL.get(ct, ct)
        if ct == "A":
            if phase == "Post-Competition":
                priority, order = "🟣 Post-Comp", 0
            elif "Switch" in phase:
                priority, order = "🚨 Programme Switch", 1
            elif phase == "Pre-Peak":
                priority, order = "🏁 Comp Prep", 2
            else:
                priority, order = "🏁 Comp Prep", 3
        else:
            if phase == "Post-Competition":
                priority, order = "🟣 Post-Comp", 0
            else:
                priority, order = "🏁 Comp Prep", 3
        p = _prog(c["name"])
        rows.append({
            "Priority": priority,
            "Athlete": c["name"],
            "Reason": f"{badge} {c['comp_name']} — {time_str}",
            "Action": c["action"],
            "_order": order,
            "_reason_type": "post_comp" if phase == "Post-Competition" else None,
            "_ctx": {"comp": c["comp_name"]},
            "_comp_msg": c["message_template"],  # use pre-built comp message
            "_programme": p,
        })

    # 8. In contact but not logging — monthly nudge
    for e in engagement_results:
        if not e.get("nudge_flag"):
            continue
        days = e["days_since"]
        last_contact = e.get("last_contact", "recently")
        reason = (
            f"No results logged ({days} days) — last contact {last_contact}"
            if days else f"Never logged — but in contact (last {last_contact})"
        )
        p = _prog(e["name"])
        rows.append({
            "Priority": "📝 Remind to Log",
            "Athlete": e["name"],
            "Reason": reason,
            "Action": "Ask them to record their results",
            "_order": 6,
            "_reason_type": "nudge_to_log",
            "_ctx": {},
            "_programme": p,
        })

    rows.sort(key=lambda x: x["_order"])

    # ── This week's wins board ────────────────────────────────────────────────
    celebrate_rows = [r for r in rows if r["Priority"] == "🏆 Celebrate"]
    streak_rows = [r for r in rows if r["Priority"] == "✅ Positive"]
    if celebrate_rows or streak_rows:
        n_pr = len(celebrate_rows)
        n_st = len(streak_rows)
        label = (
            f"🏆 {n_pr} new result{'s' if n_pr != 1 else ''}"
            + (f" · ✅ {n_st} streak{'s' if n_st != 1 else ''}" if streak_rows else "")
        )
        with st.expander(label, expanded=True):
            for r in celebrate_rows:
                st.markdown(f"🏆 **{r['Athlete']}** — {r['Reason']}")
            if celebrate_rows and streak_rows:
                st.divider()
            for r in streak_rows:
                st.markdown(f"✅ **{r['Athlete']}** — {r['Reason']}")

    if not rows:
        st.success("Nothing to action this week — all athletes on track.")
        return

    # ── Summary table ────────────────────────────────────────────────────────
    display_cols = ["Priority", "Athlete", "Programme", "Contact", "Reason", "Action"]
    table_rows = []
    for r in rows:
        p = r.get("_programme", "")
        table_rows.append({
            "Priority": r["Priority"],
            "Athlete": r["Athlete"],
            "Programme": _prog_short(p),
            "Contact": _programme_contact(p),
            "Reason": r["Reason"],
            "Action": r["Action"],
        })
    df = pd.DataFrame(table_rows)

    st.caption(f"{len(rows)} athletes to contact this week")

    def _row_colour(row):
        colours = {
            "🔴 Contact Today":    "background-color: #ffe5e5",
            "🏆 Celebrate":        "background-color: #fff8e1",
            "✅ Positive":         "background-color: #e8f5e9",
            "⚠️ Re-engage":        "background-color: #fff3e0",
            "⚠️ Check In":         "background-color: #fff3e0",
            "📉 Performance":      "background-color: #fce4ec",
            "🟣 Post-Comp":        "background-color: #f3e5f5",
            "🚨 Programme Switch": "background-color: #fff3e0",
            "🏁 Comp Prep":        "background-color: #e8eaf6",
            "📝 Remind to Log":    "background-color: #e3f2fd",
        }
        colour = colours.get(row["Priority"], "")
        return [colour] * len(row)

    styled = df.style.apply(_row_colour, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True, height=500)

    # ── Bulk export ───────────────────────────────────────────────────────────
    st.divider()
    def _build_export(rows, archetype_by_name):
        import io
        buf = io.StringIO()
        buf.write(f"# JST Compete — Outreach List\n")
        buf.write(f"Generated: {dt.datetime.now().strftime('%d %b %Y %H:%M')}\n")
        buf.write(f"{len(rows)} athletes to contact\n\n---\n\n")
        for r in rows:
            name = r["Athlete"]
            arch_row = (archetype_by_name or {}).get(name)
            arch_id = str(arch_row.get("Primary Archetype", "")).strip() if arch_row else None
            reason_type = r.get("_reason_type")
            ctx = r.get("_ctx", {})
            comp_msg = r.get("_comp_msg")
            if comp_msg and not reason_type:
                msg = comp_msg
            elif reason_type:
                msg = msg_tmpl.generate_message(name, reason_type, ctx, arch_id)
            else:
                msg = ""
            prog = r.get("_programme", "")
            buf.write(f"## {name}\n")
            buf.write(f"**Priority:** {r['Priority']}  \n")
            if prog:
                buf.write(f"**Programme:** {prog} · Contact: {_programme_contact(prog)}  \n")
            buf.write(f"**Reason:** {r['Reason']}  \n")
            buf.write(f"**Action:** {r['Action']}  \n")
            if msg:
                buf.write(f"\n**Message:**\n\n> {msg}\n")
            buf.write("\n---\n\n")
        return buf.getvalue()

    export_md = _build_export(rows, archetype_by_name)
    st.download_button(
        label="📥 Export Outreach List",
        data=export_md,
        file_name=f"outreach_{dt.datetime.now().strftime('%Y%m%d')}.md",
        mime="text/markdown",
        help="Download all outreach items with archetype-aware messages as Markdown",
    )

    # ── Archetype-aware message generator ────────────────────────────────────
    st.divider()
    st.subheader("📨 Generate Message")
    st.caption(
        "Select any athlete from the outreach list — get a ready-to-send message "
        "personalised to their archetype and the reason for contact."
    )

    athlete_options = sorted(set(r["Athlete"] for r in rows))
    sel = st.selectbox("Athlete", ["— select —"] + athlete_options, key="outreach_msg_sel")

    if sel and sel != "— select —":
        arch_row = (archetype_by_name or {}).get(sel)
        arch_id = str(arch_row.get("Primary Archetype", "")).strip() if arch_row else None
        arch_def = arch_mod.get_archetype(arch_id) if arch_id else None
        arch_name = arch_def.get("name", arch_id.replace("_", " ").title()) if arch_def else None

        sel_prog = _prog(sel)
        if sel_prog:
            contact_label = _programme_contact(sel_prog)
            st.caption(f"Programme: **{sel_prog}** · Contact via: **{contact_label}**")

        if arch_name:
            cluster = msg_tmpl.archetype_cluster(arch_id)
            st.caption(f"Archetype: **{arch_name}** · Communication cluster: *{cluster}*")
        else:
            st.caption("No archetype assessed yet — showing generic message.")

        athlete_rows = [r for r in rows if r["Athlete"] == sel]
        for r in athlete_rows:
            reason_type = r.get("_reason_type")
            ctx = r.get("_ctx", {})
            comp_msg = r.get("_comp_msg")

            with st.expander(f"{r['Priority']} — {r['Reason']}", expanded=True):
                if comp_msg and not reason_type:
                    # Competition phase message — already fully built by analytics
                    st.code(comp_msg, language=None)
                    _outreach_send_buttons(comp_msg)
                elif reason_type:
                    msg = msg_tmpl.generate_message(sel, reason_type, ctx, arch_id)
                    st.code(msg, language=None)
                    _outreach_send_buttons(msg)
                    if arch_name:
                        coach_hints = (arch_def.get("coach", {}).get("coach_toward", []))[:2]
                        if coach_hints:
                            st.caption("Coaching cues for this archetype: " + " · ".join(coach_hints))
                else:
                    st.caption("No message template for this item.")


_LOAD_STATUS_BADGE = {
    "red":        "🔴 Spike",
    "amber_high": "🟡 High",
    "green":      "🟢 OK",
    "amber_low":  "🟡 Low",
    "low":        "⚪ Very Low",
    "insufficient": "—",
}
_LOAD_STATUS_COLOUR = {
    "red":        "background-color: #fce4ec",
    "amber_high": "background-color: #fff9c4",
    "green":      "background-color: #e8f5e9",
    "amber_low":  "background-color: #fff9c4",
    "low":        "",
    "insufficient": "",
}


def page_load(load_results):
    """Training load (ACWR proxy) overview — squad summary, athlete detail, programme breakdown."""
    from collections import defaultdict

    if not load_results:
        st.info("No PR log data yet — results will appear once athletes start logging.")
        return

    # ── Squad summary ─────────────────────────────────────────────────────────
    st.subheader("Squad Load Overview")
    st.caption(
        "Load proxy = PR log entries per week. "
        "ACWR = entries in last 7 days ÷ 4-week weekly average. "
        "🟢 Sweet spot 0.8–1.3 · 🟡 Amber 1.3–1.5 or 0.5–0.8 · 🔴 Danger >1.5"
    )

    rows = []
    for name, d in sorted(load_results.items()):
        acwr_str = f"{d['acwr']:.2f}" if d["acwr"] is not None else "—"
        prog = d["programme"]
        prog_short = prog.replace("Sessions Per Day", "x/day").replace(" - ", " ") if prog else "—"
        rows.append({
            "Athlete": name,
            "ACWR": acwr_str,
            "Status": _LOAD_STATUS_BADGE[d["status"]],
            "Acute (7d)": d["acute"],
            "Chronic (avg/wk)": d["chronic"],
            "Soreness": f"{d['soreness']:.0f}/10" if d["soreness"] is not None else "—",
            "Stress": f"{d['stress']:.0f}/10" if d["stress"] is not None else "—",
            "Programme": prog_short,
            "_status": d["status"],
        })

    df_summary = pd.DataFrame(rows)

    def _load_row_colour(row):
        colour = _LOAD_STATUS_COLOUR.get(row["_status"], "")
        return [colour] * len(row)

    display_cols = [c for c in df_summary.columns if c != "_status"]
    styled = df_summary[display_cols + ["_status"]].style.apply(_load_row_colour, axis=1)
    st.dataframe(
        styled,
        use_container_width=True, hide_index=True,
        column_config={"_status": None},
    )

    # ── Athlete drill-down ────────────────────────────────────────────────────
    st.divider()
    st.subheader("Athlete Load Detail")

    sel = st.selectbox("Select athlete", ["— select —"] + sorted(load_results.keys()), key="load_athlete_sel")

    if sel and sel != "— select —":
        d = load_results[sel]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ACWR", f"{d['acwr']:.2f}" if d["acwr"] is not None else "—")
        c2.metric("Acute (7d entries)", d["acute"])
        c3.metric("Chronic (avg/wk)", f"{d['chronic']:.1f}")
        c4.metric("Status", _LOAD_STATUS_BADGE[d["status"]])

        chart_df = pd.DataFrame({
            "Week": [w.strftime("%-d %b") for w in d["weeks"]],
            "Entries": d["weekly_loads"],
            "Chronic baseline": d["chronic_line"],
        })

        bars = (
            alt.Chart(chart_df)
            .mark_bar(color="#1f77b4", opacity=0.8)
            .encode(
                x=alt.X("Week:N", sort=None, title="Week of"),
                y=alt.Y("Entries:Q", title="Entries logged"),
                tooltip=["Week:N", "Entries:Q", alt.Tooltip("Chronic baseline:Q", format=".1f")],
            )
        )
        baseline = (
            alt.Chart(chart_df)
            .mark_line(color="#ff7f0e", strokeWidth=2, strokeDash=[4, 2])
            .encode(
                x=alt.X("Week:N", sort=None),
                y=alt.Y("Chronic baseline:Q", title=""),
            )
        )
        st.altair_chart((bars + baseline).properties(height=280), use_container_width=True)
        st.caption("🟠 dashed = 4-week rolling average (chronic baseline)")

        hints = []
        if d["soreness"] is not None:
            hints.append(f"Soreness {d['soreness']:.0f}/10")
        if d["stress"] is not None:
            hints.append(f"Stress {d['stress']:.0f}/10")
        if hints:
            st.caption("Latest recovery survey: " + " · ".join(hints))

        if d["programme"]:
            exp = d["expected_daily"]
            exp_str = f"  ({exp}x/day programme)" if exp else ""
            st.caption(f"Programme: {d['programme']}{exp_str}")

    # ── Load by programme ─────────────────────────────────────────────────────
    st.divider()
    st.subheader("Load by Programme")
    st.caption("Useful for spotting whole tracks that are over or under-loading relative to each other.")

    by_prog = defaultdict(list)
    for name, d in load_results.items():
        by_prog[d["programme"] or "— Unassigned —"].append(d)

    prog_rows = []
    for prog, group in sorted(by_prog.items()):
        acwr_vals = [g["acwr"] for g in group if g["acwr"] is not None]
        avg_acwr = round(sum(acwr_vals) / len(acwr_vals), 2) if acwr_vals else None
        prog_short = prog.replace("Sessions Per Day", "x/day").replace(" - ", " ") if prog else prog
        prog_rows.append({
            "Programme": prog_short,
            "Athletes": len(group),
            "Avg ACWR": f"{avg_acwr:.2f}" if avg_acwr is not None else "—",
            "🟢 OK": sum(1 for g in group if g["status"] == "green"),
            "🟡 Amber": sum(1 for g in group if g["status"] in ("amber_high", "amber_low")),
            "🔴 Spike": sum(1 for g in group if g["status"] == "red"),
            "— No data": sum(1 for g in group if g["status"] == "insufficient"),
        })
    st.dataframe(pd.DataFrame(prog_rows), use_container_width=True, hide_index=True)


def page_squad(athletes, engagement_results, rec_by_name,
               data_records=None, archetype_by_name=None, pr_records=None):
    """Card grid — every athlete at a glance, colour-coded by status."""

    data_by_name = {}
    for rec in (data_records or []):
        nm = str(rec.get("Full Name", "")).strip()
        if nm:
            data_by_name[nm] = rec

    last_logged = {}
    for r in (pr_records or []):
        nm = str(r.get("Athlete Name", "")).strip()
        d = _parse_date(str(r.get("Date", "")))
        if nm and d and (nm not in last_logged or d > last_logged[nm]):
            last_logged[nm] = d

    eng_map = {e["name"]: e for e in engagement_results}

    def _card_status(name):
        last = last_logged.get(name)
        days = (TODAY - last).days if last else None
        rec_row = rec_by_name.get(name)
        rec_urgent = False
        if rec_row:
            try:
                s = float(str(rec_row.get("Soreness", "")).strip() or 0)
                st_ = float(str(rec_row.get("Stress", "")).strip() or 0)
                rec_urgent = s >= 7 or st_ >= 7
            except (ValueError, TypeError):
                pass
        if rec_urgent:
            return "🔴", "Recovery alert"
        e = eng_map.get(name, {})
        if e.get("flag") and (days is None or days >= 45):
            return "🔴", "Inactive 45+ days" if days else "Never logged"
        if days is not None and days >= 28:
            return "🟡", f"{days}d inactive"
        if days is None:
            return "🟡", "No results logged"
        return "🟢", f"Active ({days}d ago)"

    def _status_bucket(emoji):
        return {"🟢": "🟢 Active", "🟡": "🟡 Check in"}.get(emoji, "🔴 Urgent")

    # Filters
    all_progs = sorted(filter(None, {
        str(data_by_name.get(a["name"], {}).get("Programme", "")).strip() or None
        for a in athletes
    }))
    fc1, fc2 = st.columns(2)
    prog_filter = fc1.multiselect("Programme", all_progs, placeholder="All programmes", key="squad_prog_filter")
    status_filter = fc2.multiselect(
        "Status", ["🟢 Active", "🟡 Check in", "🔴 Urgent"],
        placeholder="All statuses", key="squad_status_filter",
    )

    visible = []
    for a in athletes:
        nm = a["name"]
        prog = str(data_by_name.get(nm, {}).get("Programme", "")).strip()
        emoji, _ = _card_status(nm)
        if prog_filter and prog not in prog_filter:
            continue
        if status_filter and _status_bucket(emoji) not in status_filter:
            continue
        visible.append(a)

    if not visible:
        st.info("No athletes match the current filters.")
        return

    total_urgent = sum(1 for a in athletes if _card_status(a["name"])[0] == "🔴")
    total_check  = sum(1 for a in athletes if _card_status(a["name"])[0] == "🟡")
    total_active = sum(1 for a in athletes if _card_status(a["name"])[0] == "🟢")
    sm1, sm2, sm3 = st.columns(3)
    sm1.metric("🔴 Urgent", total_urgent)
    sm2.metric("🟡 Check in", total_check)
    sm3.metric("🟢 Active", total_active)
    st.divider()

    cols_per_row = 3
    for i in range(0, len(visible), cols_per_row):
        batch = visible[i : i + cols_per_row]
        cols = st.columns(cols_per_row)
        for j, athlete in enumerate(batch):
            with cols[j]:
                nm = athlete["name"]
                status_emoji, status_text = _card_status(nm)
                profile = data_by_name.get(nm, {})
                prog = str(profile.get("Programme", "")).strip()
                last = last_logged.get(nm)
                days = (TODAY - last).days if last else None
                arch_row = (archetype_by_name or {}).get(nm)
                arch_id = str(arch_row.get("Primary Archetype", "")).strip() if arch_row else None
                arch_def = arch_mod.get_archetype(arch_id) if arch_id else None
                arch_name = arch_def.get("name", "") if arch_def else ""
                rec_row = rec_by_name.get(nm)

                st.markdown(f"#### {status_emoji} {nm}")
                if prog:
                    st.caption(prog)
                if days is not None:
                    st.markdown(f"Last logged: **{last.strftime('%d %b')}** · {days}d ago")
                else:
                    st.markdown("Last logged: **Never**")
                if arch_name:
                    st.caption(f"🧠 {arch_name}")
                if rec_row:
                    try:
                        s   = float(str(rec_row.get("Soreness", "")).strip() or 0)
                        st_ = float(str(rec_row.get("Stress",   "")).strip() or 0)
                        if s >= 5 or st_ >= 5:
                            flag = "🔴" if (s >= 7 or st_ >= 7) else "🟡"
                            st.caption(f"{flag} Soreness {s:.0f} · Stress {st_:.0f}/10")
                    except (ValueError, TypeError):
                        pass

                with st.expander("✏️ Quick note"):
                    with st.form(f"sq_note_{i}_{j}", clear_on_submit=True):
                        note_text = st.text_area(
                            "Note", label_visibility="collapsed", key=f"sq_nt_{i}_{j}", height=68,
                            placeholder="Quick coaching note…",
                        )
                        note_kind = st.selectbox(
                            "Type", ["note", "chat", "result", "recovery"],
                            key=f"sq_nk_{i}_{j}",
                        )
                        if st.form_submit_button("Save", type="primary"):
                            if note_text.strip():
                                line = f"[{TODAY.isoformat()} — {note_kind}] {note_text.strip()}"
                                current = str(profile.get("Coaching Notes", "")).strip()
                                new_notes = (current + "\n" + line).strip() if current else line
                                get_sheets().batch_update_by_name(
                                    config.TAB_DATA, "Full Name",
                                    {nm: {"Coaching Notes": new_notes}},
                                )
                                st.success("Saved")
                                st.cache_data.clear()
                            else:
                                st.warning("Empty note")
                st.write("")


def page_week_planner(engagement_results, rec_alert_rows, comp_results,
                      consistency_wins, milestones, data_records=None):
    """Prioritised coaching focus for this week with inline quick notes."""

    data_by_name = {}
    for rec in (data_records or []):
        nm = str(rec.get("Full Name", "")).strip()
        if nm:
            data_by_name[nm] = rec

    st.caption(f"Week of {TODAY.strftime('%d %b %Y')} · Refresh to update")

    urgent = []
    this_week = []
    celebrate = []

    for alert in rec_alert_rows:
        urgent.append({"name": alert[0], "reason": alert[1], "note_kind": "recovery"})

    for c in (comp_results or []):
        if not c["action"]:
            continue
        ct = c.get("comp_type", "A")
        badge = _COMP_TYPE_LABEL.get(ct, ct)
        if ct == "A" and "Switch" in (c["phase"] or ""):
            urgent.append({
                "name": c["name"],
                "reason": f"🚨 Programme switch — {c['comp_name']}",
                "note_kind": "chat",
            })
        else:
            this_week.append({
                "name": c["name"],
                "reason": f"Comp prep: {c['phase']} — {badge} {c['comp_name']}",
                "note_kind": "chat",
            })

    for e in engagement_results:
        if not e["flag"]:
            continue
        days = e["days_since"]
        never = e["last_logged"] == "never"
        if never or (days and days >= 45):
            urgent.append({
                "name": e["name"],
                "reason": "Never logged" if never else f"{days}d inactive — re-engage",
                "note_kind": "chat",
            })
        elif days and 28 <= days < 45:
            this_week.append({
                "name": e["name"],
                "reason": f"{days}d inactive — check in",
                "note_kind": "chat",
            })

    for name, weeks in consistency_wins:
        celebrate.append({
            "name": name,
            "reason": f"{weeks} consecutive weeks logging",
            "note_kind": "chat",
        })

    for m in milestones:
        celebrate.append({
            "name": m[0],
            "reason": f"New result — {m[1]}: {m[2]}",
            "note_kind": "result",
        })

    m1, m2, m3 = st.columns(3)
    m1.metric("🔴 Do Today", len(urgent))
    m2.metric("📋 This Week", len(this_week))
    m3.metric("🏆 Celebrate", len(celebrate))
    st.divider()

    if not urgent and not this_week and not celebrate:
        st.success("All clear this week — no coaching actions needed.")
        return

    def _render_group(title, items):
        if not items:
            return
        st.subheader(title)
        for idx, item in enumerate(items):
            nm = item["name"]
            profile = data_by_name.get(nm, {})
            row_col, note_col = st.columns([3, 1])
            row_col.markdown(f"**{nm}** · {item['reason']}")
            with note_col:
                with st.expander("✏️ Note"):
                    with st.form(f"wk_{title[:4]}_{idx}_{nm}", clear_on_submit=True):
                        note_text = st.text_area(
                            "Notes", key=f"wk_nt_{title[:4]}_{nm}_{idx}",
                            height=64, placeholder="Quick note…",
                            label_visibility="collapsed",
                        )
                        if st.form_submit_button("Save"):
                            if note_text.strip():
                                kind = item["note_kind"]
                                line = f"[{TODAY.isoformat()} — {kind}] {note_text.strip()}"
                                current = str(profile.get("Coaching Notes", "")).strip()
                                new_notes = (current + "\n" + line).strip() if current else line
                                get_sheets().batch_update_by_name(
                                    config.TAB_DATA, "Full Name",
                                    {nm: {"Coaching Notes": new_notes}},
                                )
                                st.success("Saved")
                                st.cache_data.clear()
            st.divider()

    _render_group("🔴 Do today", urgent)
    _render_group("📋 This week", this_week)
    _render_group("🏆 Celebrate", celebrate)


# ─────────────────────────────────────────────────────────── CRM integration
def page_crm_onboarding():
    """Athletes in CRM with Fitr IDs not yet syncing (Benchmarks pipeline)."""
    st.markdown("## CRM Onboarding Pipeline")
    st.caption("Athletes in the CRM who have Fitr IDs but aren't syncing yet")

    try:
        sheets = st.session_state.get("sheets_client")
        if not sheets:
            from sheets_client import SheetsClient
            sheets = SheetsClient()

        # Load CRM data
        crm_athletes = {}
        for tab in ("Bespoke Athletes", "Junior + Youth"):
            try:
                rows = sheets.read_external_records(config.CRM_SHEET_ID, tab)
                for r in rows:
                    name = (r.get("Athlete Name") or "").strip()
                    coach = (r.get("Coach") or "").strip()
                    if name and coach:
                        crm_athletes[name.lower()] = (name, coach)
            except Exception as e:
                st.error(f"Error reading CRM tab {tab}: {e}")
                return

        # Load Benchmarks
        bench_vals = sheets.read_values(config.TAB_BENCHMARKS)
        if not bench_vals:
            st.info("No Benchmarks yet")
            return
        bench_header = bench_vals[0]
        bench_name_idx = bench_header.index("Name") if "Name" in bench_header else None
        if not bench_name_idx:
            st.error("Benchmarks tab missing 'Name' column")
            return

        bench_names_lower = {
            r[bench_name_idx].strip().lower()
            for r in bench_vals[1:]
            if bench_name_idx < len(r) and r[bench_name_idx].strip()
        }

        # Find CRM athletes NOT in Benchmarks
        pending = [
            (display, coach)
            for lower, (display, coach) in crm_athletes.items()
            if lower not in bench_names_lower
        ]
        pending.sort(key=lambda x: x[1])  # Sort by coach

        if not pending:
            st.success("✓ All CRM athletes are syncing!")
            return

        st.warning(f"{len(pending)} athletes ready to onboard")

        # Group by coach
        by_coach = {}
        for name, coach in pending:
            if coach not in by_coach:
                by_coach[coach] = []
            by_coach[coach].append(name)

        for coach in sorted(by_coach.keys()):
            with st.expander(f"**{coach}** ({len(by_coach[coach])} pending)"):
                for name in sorted(by_coach[coach]):
                    st.text(f"• {name}")

        st.info("💡 These athletes will be auto-onboarded when they appear in Fitr chat rooms.")

    except Exception as e:
        st.error(f"Error: {e}")


def page_coach_rosters():
    """Compare each coach's CRM roster vs. active syncing athletes."""
    st.markdown("## Coach Rosters (CRM vs. Fitr)")
    st.caption("Who each coach has in the CRM vs. who's actively syncing")

    try:
        sheets = st.session_state.get("sheets_client")
        if not sheets:
            from sheets_client import SheetsClient
            sheets = SheetsClient()

        # Load CRM
        coach_roster = {}
        for tab in ("Bespoke Athletes", "Junior + Youth"):
            try:
                rows = sheets.read_external_records(config.CRM_SHEET_ID, tab)
                for r in rows:
                    name = (r.get("Athlete Name") or "").strip()
                    coach = (r.get("Coach") or "").strip().lower()
                    if not name:
                        continue
                    coach_abbrev_map = {
                        "jamie w": "Jamie Warr", "jamie h": "Jamie Harrop",
                        "dcon": "Dan Connolly", "denis": "Denis Smith",
                        "ed": "Ed Cook", "jak": "Jak Cornthwaite",
                        "louis": "Louis Towers", "huw": "Huw Davis", "pete": "Pete Crudge",
                    }
                    full_coach = coach_abbrev_map.get(coach, coach)
                    if full_coach not in coach_roster:
                        coach_roster[full_coach] = []
                    coach_roster[full_coach].append(name)
            except Exception as e:
                st.warning(f"Could not read CRM tab {tab}: {e}")

        # Load syncing athletes
        data_recs = sheets.read_records(config.TAB_DATA)
        syncing_by_coach = {}
        for r in data_recs:
            name = (r.get("Full Name") or "").strip()
            prog = (r.get("Programme") or "").strip()
            if name and prog:
                if prog not in syncing_by_coach:
                    syncing_by_coach[prog] = []
                syncing_by_coach[prog].append(name)

        # Display comparison
        all_coaches = sorted(set(coach_roster.keys()) | set(syncing_by_coach.keys()))

        for coach in all_coaches:
            crm_set = set(coach_roster.get(coach, []))
            syncing_set = set(syncing_by_coach.get(coach, []))

            crm_count = len(crm_set)
            sync_count = len(syncing_set)
            missing = crm_set - syncing_set
            extra = syncing_set - crm_set

            status = "✅" if not missing and not extra else "⚠️"
            with st.expander(f"{status} **{coach}** — CRM: {crm_count} | Syncing: {sync_count}"):
                if missing:
                    st.error(f"In CRM but not syncing ({len(missing)}):")
                    for n in sorted(missing):
                        st.text(f"  • {n}")
                if extra:
                    st.warning(f"Syncing but not in CRM ({len(extra)}):")
                    for n in sorted(extra):
                        st.text(f"  • {n}")
                if not missing and not extra:
                    st.success("✓ Rosters match!")

    except Exception as e:
        st.error(f"Error: {e}")


def page_crm_discrepancies():
    """Flag athletes appearing in multiple places with mismatches."""
    st.markdown("## CRM Discrepancies")
    st.caption("Athletes appearing multiple times or with inconsistencies")

    try:
        sheets = st.session_state.get("sheets_client")
        if not sheets:
            from sheets_client import SheetsClient
            sheets = SheetsClient()

        # Load CRM (collect ALL occurrences)
        crm_entries = []
        for tab in ("Bespoke Athletes", "Junior + Youth"):
            try:
                rows = sheets.read_external_records(config.CRM_SHEET_ID, tab)
                for r in rows:
                    name = (r.get("Athlete Name") or "").strip()
                    coach = (r.get("Coach") or "").strip()
                    if name:
                        crm_entries.append((name, coach, tab))
            except Exception as e:
                st.warning(f"Could not read CRM tab {tab}: {e}")

        # Find duplicates (same athlete under different coaches)
        from collections import Counter
        name_counts = Counter(name for name, coach, tab in crm_entries)
        duplicates = {n: name_counts[n] for n in name_counts if name_counts[n] > 1}

        if duplicates:
            st.error(f"Athletes appearing multiple times in CRM ({len(duplicates)}):")
            for name in sorted(duplicates.keys()):
                entries = [e for e in crm_entries if e[0] == name]
                with st.expander(f"**{name}** ({len(entries)} entries)"):
                    for n, coach, tab in entries:
                        st.text(f"  • {coach} ({tab})")
        else:
            st.success("✓ No duplicate CRM entries")

        # Load _DATA for programme validation
        data_recs = sheets.read_records(config.TAB_DATA)
        data_by_name = {r.get("Full Name", "").strip(): r for r in data_recs}

        # Cross-check: CRM coach vs. _DATA Programme
        mismatches = []
        coach_abbrev_map = {
            "jamie w": "Jamie Warr", "jamie h": "Jamie Harrop",
            "dcon": "Dan Connolly", "denis": "Denis Smith",
            "ed": "Ed Cook", "jak": "Jak Cornthwaite",
            "louis": "Louis Towers", "huw": "Huw Davis", "pete": "Pete Crudge",
        }
        for name, coach, tab in crm_entries:
            full_coach = coach_abbrev_map.get(coach.lower(), coach) if coach else ""
            data_rec = data_by_name.get(name)
            if data_rec:
                data_prog = (data_rec.get("Programme") or "").strip()
                if full_coach and data_prog and full_coach != data_prog:
                    mismatches.append((name, full_coach, data_prog))

        if mismatches:
            st.warning(f"Coach assignment mismatches ({len(mismatches)}):")
            for name, crm_coach, data_prog in sorted(mismatches):
                st.text(f"  • {name}: CRM says {crm_coach!r}, _DATA says {data_prog!r}")

    except Exception as e:
        st.error(f"Error: {e}")


def page_help():
    """Coach guide — explains each tab and how to use the dashboard."""
    st.markdown("## Dashboard Guide")
    st.caption(
        "A quick reference for coaches. "
        "This dashboard pulls live data from Google Sheets every 15 minutes. "
        "To force a reload: Streamlit menu (⋮) → Rerun."
    )
    st.divider()

    tabs_info = [
        ("📋 Outreach List", """
Your daily starting point. Every athlete who needs contact appears here, sorted by urgency.

- **🔴 Contact Today** — Recovery flags. Address these before anything else.
- **🏆 Celebrate** — Athlete logged a new result this week. Send a quick win message.
- **✅ Positive** — Consistency streak. Acknowledge the discipline.
- **⚠️ Re-engage** — 45+ days without logging. Direct personal reach-out needed.
- **📉 Performance** — A benchmark is declining or well below peak.
- **⚠️ Check In** — 28–44 days without logging. Friendly nudge.
- **🏁 Comp Prep** — Competition approaching or just passed. Phase-specific action required.
- **📝 Remind to Log** — Athlete is in contact but not recording results.

**Generate Message** at the bottom gives a ready-to-send, archetype-aware message for each athlete.
Use **Export Outreach List** to download the full list with messages as Markdown.
"""),
        ("🚨 Alerts", """
Aggregate view of all flags across the squad.

- **Recovery Flags** — High soreness or stress on the latest survey.
- **Engagement / Dropout Risk** — Who hasn't logged in 28+ days.
- **Performance Concerns** — Benchmarks with a declining trend or a big drop from peak.
- **Consistency Wins** — Positive streaks worth acknowledging.

Use this tab to check the squad's health at a glance before planning your week.
"""),
        ("🃏 Squad", """
Card grid showing every athlete's current status.

- **🟢 Active** — Logged within the last 14 days, no recovery concerns.
- **🟡 Check in** — 15–44 days since logging, or a recovery concern.
- **🔴 Urgent** — 45+ days inactive, never logged, or high soreness/stress.

Filter by Programme or Status to focus on a subset.
The **Quick note** button on each card lets you log a coaching note without navigating away.
"""),
        ("👥 Athletes", """
Full roster with filters. Click any row to open the athlete's profile panel.

The profile shows: physical stats, programme, injuries, competitions, benchmark snapshots, recovery, archetype, and full coaching notes timeline.

**Archetype Assessment** — Run the 10-question forced-choice instrument inside a profile.
Share the **Self-Assessment Link** with the athlete so they can fill it in themselves.
**Add Note** — Log a chat, result, or recovery entry directly to the athlete's profile.
"""),
        ("🗓️ Week Plan", """
Your coaching week as a prioritised action list with inline note-taking.

- **🔴 Do today** — Recovery flags and programme switches that can't wait.
- **📋 This week** — Check-ins and comp prep reminders.
- **🏆 Celebrate** — PRs and consistency wins to acknowledge.

Each item has a quick note button so you can log a coaching interaction without switching tabs.
"""),
        ("🏁 Competitions", """
All athlete competitions in one place.

- **Upcoming & Recent** — Full squad comp schedule with phase and required action.
- **A-Race Actions** — Expanded view of athletes needing a programme switch or taper.
- **Annual Calendar** — Visual scatter plot of the whole competitive year.

Add a competition inside any athlete's profile (Athletes → click athlete → Competition Calendar → Add Competition).

**A / B / C competition types:**
- 🥇 **A** — Primary goal. Full 10-week prep + 2-week peak. 1–3 per year max.
- 🥈 **B** — Secondary race. Race hard, no programme change.
- 🥉 **C** — Training day. Compete without taper.
"""),
        ("📊 Programmes", """
Breakdown of athletes by programme track.

Shows headcount, active rate (logged in the last 28 days), average days since logging, and declining trends per programme. Spot which tracks have low engagement or need attention.

Assign or change an athlete's programme inside their profile in the Athletes tab.
"""),
        ("📈 Trends", """
Progress chart for any athlete and benchmark.

Select an athlete, then a benchmark, to see their results plotted over time.
Tick **Compare with another athlete** to overlay a second athlete on the same chart — useful for peer benchmarking within a programme group.
"""),
        ("💤 Recovery", """
Latest recovery survey responses for every athlete who has submitted one.

**Soreness** and **Stress** cells are highlighted red (≥ 7/10) or amber (≥ 5/10). These athletes appear in the Alerts and Outreach tabs.

Recovery surveys are collected via Typeform. The link is in your coach onboarding notes.
"""),
    ]

    for tab_name, content in tabs_info:
        with st.expander(tab_name, expanded=False):
            st.markdown(content.strip())

    st.divider()
    st.markdown("### Quick tips")
    st.markdown("""
- **Coaching notes** are stored as `[YYYY-MM-DD — kind] text` entries in the Google Sheet *_DATA* tab → Coaching Notes column. You can edit them directly in the sheet.
- **Self-assessment links** — Each athlete has a unique URL. Find it in their profile under Archetype → Share Self-Assessment Link.
- **Archetype messaging** — Outreach messages are tailored to the athlete's communication cluster. Athletes without an archetype get a generic message.
- **Adding competitions** — Athletes tab → click athlete → Competition Calendar → Add Competition.
- **Refreshing data** — Streamlit menu (⋮, top right) → Rerun, or wait up to 15 minutes for the automatic cache refresh.
    """)

    st.divider()
    st.markdown("### Setting up coach Slack notifications")
    st.markdown("""
When an individual coach's athlete logs a result or challenge, the sync script can automatically notify that coach in Slack. Setup takes about 5 minutes:

**1. Create a Slack app**
- Go to [api.slack.com/apps](https://api.slack.com/apps) → Create New App → From scratch
- Name it (e.g. *JST Compete*) and choose your Slack workspace
- Under *OAuth & Permissions* → *Bot Token Scopes*, add `chat:write`
- Click *Install to Workspace* and copy the **Bot User OAuth Token** (`xoxb-...`)

**2. Add the token to Streamlit secrets**
- In your Streamlit Cloud app → Settings → Secrets, add:
  ```
  SLACK_BOT_TOKEN = "xoxb-your-token-here"
  ```

**3. Create the Coaches tab in the Google Sheet**
- Add a new tab named exactly **Coaches**
- Add two columns: `Programme` and `Slack Channel`
- One row per coach. The `Programme` value must match the athlete's Programme field exactly (e.g. *Peter Crudd individual programming*). The `Slack Channel` value should be the channel ID (right-click a channel in Slack → Copy link — the ID is the part starting with `C`)
- JST Athlete tracks can also have a row here pointing to a general JST channel if you want those logged too

**4. Invite the bot to each channel**
- In each coach's Slack channel, type `/invite @JST Compete` (or whatever you named the bot)

From the next sync onwards, each time an athlete logs a result, their coach gets a message like:
> 🏋️ *Athlete Name* logged 1 new result:
> 🏆 *Fran*: 6:42
> _Peter Crudd individual programming_
    """)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if st.query_params.get("mode") == "self_assess":
        page_self_assess()
        return

    st.title("🏋️ JST Compete — Coaching Dashboard")
    st.caption(f"Data refreshes every 15 minutes · Last loaded: {dt.datetime.now().strftime('%H:%M')}")

    with st.spinner("Loading..."):
        pr_records, athletes, rec_latest, data_records, archetype_rows, competition_rows = load_all()
        trend_results, engagement_results, consistency_wins, rec_alert_rows, rec_by_name, comp_results = run_analytics(
            pr_records, athletes, rec_latest, data_records, competition_rows=competition_rows
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

        # Reload analytics if this session started before the module was last updated
        # (Streamlit hot-reload keeps old modules in sys.modules across dashboard.py reruns)
        if not hasattr(analytics, "load_analysis"):
            import importlib
            importlib.reload(analytics)
        load_results = analytics.load_analysis(pr_records, rec_by_name=rec_by_name, data_records=data_records)

    tabs = st.tabs([
        "📋 Outreach List", "🚨 Alerts", "🃏 Squad", "👥 Athletes",
        "🗓️ Week Plan", "🏁 Competitions", "📊 Programmes", "🏋️ Load",
        "📈 Trends", "💤 Recovery", "🌐 CRM", "❓ Help",
    ])

    with tabs[0]:
        page_outreach(engagement_results, trend_results, rec_alert_rows, milestones, consistency_wins, comp_results, archetype_by_name=archetype_by_name, data_records=data_records)
    with tabs[1]:
        page_alerts(engagement_results, trend_results, rec_alert_rows, consistency_wins, data_records=data_records)
    with tabs[2]:
        page_squad(athletes, engagement_results, rec_by_name, data_records=data_records, archetype_by_name=archetype_by_name, pr_records=pr_records)
    with tabs[3]:
        page_athletes(pr_records, athletes, trend_results, engagement_results, rec_by_name, data_records, archetype_by_name=archetype_by_name, competition_rows=competition_rows)
    with tabs[4]:
        page_week_planner(engagement_results, rec_alert_rows, comp_results, consistency_wins, milestones, data_records=data_records)
    with tabs[5]:
        page_competitions(comp_results, athletes, data_records, competition_rows=competition_rows)
    with tabs[6]:
        page_programmes(athletes, pr_records, trend_results, data_records, load_results=load_results)
    with tabs[7]:
        page_load(load_results)
    with tabs[8]:
        page_trends(pr_records, athletes)
    with tabs[9]:
        page_recovery(rec_by_name)
    with tabs[10]:
        page_crm_onboarding()
        st.divider()
        page_coach_rosters()
        st.divider()
        page_crm_discrepancies()
    with tabs[11]:
        page_help()


if __name__ == "__main__":
    main()
