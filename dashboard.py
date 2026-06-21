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
import message_templates as msg_tmpl
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
    # Support both key names: GOOGLE_SERVICE_ACCOUNT (our template) and
    # gcp_service_account (Streamlit's built-in gspread shorthand)
    sa_key = next(
        (k for k in ("GOOGLE_SERVICE_ACCOUNT", "gcp_service_account") if k in st.secrets),
        None,
    )
    if sa_key:
        sa = dict(st.secrets[sa_key])
        # Pull Sheet IDs from secrets if not already set via env
        if not config.SHEET_ID and "SHEET_ID" in st.secrets:
            config.SHEET_ID = st.secrets["SHEET_ID"]
        if not config.RECOVERY_SHEET_ID and "RECOVERY_SHEET_ID" in st.secrets:
            config.RECOVERY_SHEET_ID = st.secrets["RECOVERY_SHEET_ID"]
        if not config.COMP_FORM_SHEET_ID and "COMP_FORM_SHEET_ID" in st.secrets:
            config.COMP_FORM_SHEET_ID = st.secrets["COMP_FORM_SHEET_ID"]
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


def page_programmes(athletes, pr_records, trend_results, data_records):
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
        rows.append({
            "Programme": prog,
            "Athletes": count,
            "Active (28d)": f"{active}/{count} ({round(active / count * 100)}%)" if count else "—",
            "Avg Days Since Log": avg_days if avg_days is not None else "never",
            "Declining Trends": declining or "—",
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


def page_outreach(engagement_results, trend_results, rec_alert_rows, milestones,
                  consistency_wins, comp_results=None, archetype_by_name=None):
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
            "_reason_type": "recovery_flag",
            "_ctx": {"issue": alert[1]},
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
            "_reason_type": "celebrate",
            "_ctx": {"result": reason},
        })

    # 3. Consistency streaks — acknowledge
    for name, weeks in consistency_wins:
        rows.append({
            "Priority": "✅ Positive",
            "Athlete": name,
            "Reason": f"{weeks} consecutive weeks logging",
            "Action": "Acknowledge streak",
            "_order": 2,
            "_reason_type": "consistency",
            "_ctx": {"weeks": weeks},
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
                "_reason_type": "never_logged" if never else "re_engage",
                "_ctx": {} if never else {"days": days},
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
                    "_reason_type": "performance_concern",
                    "_ctx": {"bench": s["benchmark"]},
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
                "_reason_type": "re_engage",
                "_ctx": {"days": days},
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
        rows.append({
            "Priority": priority,
            "Athlete": c["name"],
            "Reason": f"{badge} {c['comp_name']} — {time_str}",
            "Action": c["action"],
            "_order": order,
            "_reason_type": "post_comp" if phase == "Post-Competition" else None,
            "_ctx": {"comp": c["comp_name"]},
            "_comp_msg": c["message_template"],  # use pre-built comp message
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
        rows.append({
            "Priority": "📝 Remind to Log",
            "Athlete": e["name"],
            "Reason": reason,
            "Action": "Ask them to record their results",
            "_order": 6,
            "_reason_type": "nudge_to_log",
            "_ctx": {},
        })

    rows.sort(key=lambda x: x["_order"])

    if not rows:
        st.success("Nothing to action this week — all athletes on track.")
        return

    # ── Summary table ────────────────────────────────────────────────────────
    display_cols = ["Priority", "Athlete", "Reason", "Action"]
    df = pd.DataFrame([{k: r[k] for k in display_cols} for r in rows])

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
            buf.write(f"## {name}\n")
            buf.write(f"**Priority:** {r['Priority']}  \n")
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
                elif reason_type:
                    msg = msg_tmpl.generate_message(sel, reason_type, ctx, arch_id)
                    st.code(msg, language=None)
                    if arch_name:
                        coach_hints = (arch_def.get("coach", {}).get("coach_toward", []))[:2]
                        if coach_hints:
                            st.caption("Coaching cues for this archetype: " + " · ".join(coach_hints))
                else:
                    st.caption("No message template for this item.")


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

    tabs = st.tabs(["📋 Outreach List", "🚨 Alerts", "👥 Athletes", "🏁 Competitions", "📊 Programmes", "📈 Trends", "💤 Recovery"])

    with tabs[0]:
        page_outreach(engagement_results, trend_results, rec_alert_rows, milestones, consistency_wins, comp_results, archetype_by_name=archetype_by_name)
    with tabs[1]:
        page_alerts(engagement_results, trend_results, rec_alert_rows, consistency_wins)
    with tabs[2]:
        page_athletes(pr_records, athletes, trend_results, engagement_results, rec_by_name, data_records, archetype_by_name=archetype_by_name, competition_rows=competition_rows)
    with tabs[3]:
        page_competitions(comp_results, athletes, data_records, competition_rows=competition_rows)
    with tabs[4]:
        page_programmes(athletes, pr_records, trend_results, data_records)
    with tabs[5]:
        page_trends(pr_records, athletes)
    with tabs[6]:
        page_recovery(rec_by_name)


if __name__ == "__main__":
    main()
