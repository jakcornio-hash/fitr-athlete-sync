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


@st.cache_data(ttl=600, show_spinner=False)
def _load_churn_history_cached():
    return get_sheets().load_churn_history()


@st.cache_data(ttl=300, show_spinner=False)
def _load_draft_replies_cached():
    try:
        return get_sheets().load_draft_replies()
    except Exception:
        return []


@st.cache_data(ttl=1800, show_spinner=False)
def _load_recovery_all_cached():
    """All recovery submissions {lower_email: [rows]}. TTL=30min (large payload)."""
    try:
        return rec_mod.all_by_email(get_sheets())
    except Exception:
        return {}


@st.cache_resource(show_spinner="Connecting to Fitr…")
def get_fitr():
    """Authenticated FitrClient using Streamlit secrets (or config env vars)."""
    from fitr_client import FitrClient
    for key in ("FITR_ACCESS_TOKEN", "FITR_EMAIL", "FITR_PASSWORD",
                "FITR_CLIENT_ID", "FITR_CLIENT_SECRET"):
        val = str(st.secrets.get(key, "") or "").strip()
        if val:
            setattr(config, key, val)
    client = FitrClient()
    client.authenticate()
    return client


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
        st.dataframe(pd.DataFrame(rec_rows), width='stretch', hide_index=True)
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
        st.dataframe(df, width='stretch', hide_index=True)
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
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        st.write("")

    if consistency_wins:
        st.subheader("✅ Consistency Wins")
        df = pd.DataFrame(consistency_wins, columns=["Athlete", "Consecutive Weeks"])
        st.dataframe(df, width='stretch', hide_index=True)

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
        with st.expander("📊 Share Progress Page with Athlete"):
            st.caption(
                "A read-only view of the athlete's goal, recent results, training consistency, "
                "and competition calendar. Paste your dashboard URL before the query string below."
            )
            st.code(f"?mode=progress&id={jst_id}", language=None)


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

            # Programme switch wizard: suggest a programme based on comp proximity + archetype
            _prog_suggestion = None
            if competition_rows:
                _athlete_comps_wiz = [
                    cr for cr in competition_rows
                    if str(cr.get("Athlete Name", "")).strip() == name
                    and str(cr.get("Type", "")).strip().upper() == "A"
                ]
                for _cr in _athlete_comps_wiz:
                    _cd = _parse_date(str(_cr.get("Date", "")))
                    if _cd:
                        _days_out = (_cd - TODAY).days
                        if 15 <= _days_out <= 22:
                            _prog_suggestion = ("Switch to 2-Week Peak Prep",
                                                f"{_cr.get('Competition Name','')} is {_days_out}d away — time to peak.")
                            break
                        elif 70 <= _days_out <= 77:
                            _prog_suggestion = ("Switch to 10-Week Competition Prep",
                                                f"{_cr.get('Competition Name','')} is {round(_days_out/7)}w away — start your peak block.")
                            break
            if _prog_suggestion:
                st.info(f"💡 **Suggested switch:** {_prog_suggestion[0]}  \n{_prog_suggestion[1]}")

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
            result_val = ""
            if competition_rows:
                for cr in competition_rows:
                    if (str(cr.get("Athlete Name", "")).strip() == name
                            and str(cr.get("Competition Name", "")).strip() == c["comp_name"]):
                        result_val = str(cr.get("Result", "")).strip()
                        break
            comp_rows_display.append({
                "Type": _COMP_TYPE_LABEL.get(c["comp_type"], c["comp_type"]),
                "Competition": c["comp_name"],
                "Date": c["comp_date"].strftime("%d %b %Y"),
                "Time Out": time_str,
                "Phase": f"{_PHASE_EMOJI.get(phase, chr(9898))} {phase}" if phase else "—",
                "Result": result_val or "—",
            })
        st.dataframe(pd.DataFrame(comp_rows_display), width='stretch', hide_index=True)

        # Log results for past competitions that don't have one yet
        past_no_result = []
        if competition_rows:
            for cr in competition_rows:
                if str(cr.get("Athlete Name", "")).strip() != name:
                    continue
                cd = _parse_date(str(cr.get("Date", "")).strip())
                if cd and (TODAY - cd).days >= 0 and not str(cr.get("Result", "")).strip():
                    past_no_result.append(str(cr.get("Competition Name", "")).strip())
        if past_no_result:
            with st.expander(f"📋 Log Result ({len(past_no_result)} competition{'s' if len(past_no_result) > 1 else ''} awaiting result)"):
                for comp_nm in past_no_result:
                    with st.form(f"result_form_{name}_{comp_nm}", clear_on_submit=True):
                        st.markdown(f"**{comp_nm}**")
                        result_input = st.text_input(
                            "Result", key=f"result_input_{name}_{comp_nm}",
                            placeholder="e.g. 3rd place, 145kg total, 98th percentile",
                        )
                        if st.form_submit_button("Save Result", type="primary"):
                            if result_input.strip():
                                get_sheets().update_competition_result(name, comp_nm, result_input.strip())
                                st.success(f"Result saved for {comp_nm}")
                                st.cache_data.clear()
                            else:
                                st.warning("Enter a result before saving.")

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

    # ── Competitive record ────────────────────────────────────────────────────
    if competition_rows:
        past_with_result = [
            cr for cr in competition_rows
            if str(cr.get("Athlete Name", "")).strip() == name
            and str(cr.get("Result", "")).strip()
        ]
        if past_with_result:
            past_with_result.sort(key=lambda x: str(x.get("Date", "")))
            st.markdown("**🏆 Competitive Record**")
            cr_metrics = st.columns(min(len(past_with_result), 3))
            for idx, cr in enumerate(past_with_result[-3:]):
                cd = _parse_date(str(cr.get("Date", "")).strip())
                label = str(cr.get("Competition Name", "")).strip()[:28]
                result = str(cr.get("Result", "")).strip()
                date_str = cd.strftime("%b %Y") if cd else ""
                cr_metrics[idx % 3].metric(label, result, help=date_str)
            if len(past_with_result) > 3:
                with st.expander(f"All {len(past_with_result)} results"):
                    all_cr_rows = []
                    for cr in past_with_result:
                        cd = _parse_date(str(cr.get("Date", "")).strip())
                        all_cr_rows.append({
                            "Date": cd.strftime("%d %b %Y") if cd else "",
                            "Competition": str(cr.get("Competition Name", "")).strip(),
                            "Type": str(cr.get("Type", "")).strip(),
                            "Result": str(cr.get("Result", "")).strip(),
                        })
                    st.dataframe(pd.DataFrame(all_cr_rows), width='stretch', hide_index=True)

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

    # ── Manual PR entry ───────────────────────────────────────────────────────
    athlete_benches = sorted({
        str(r.get("Benchmark Name", "")).strip()
        for r in pr_records
        if str(r.get("Athlete Name", "")).strip() == name and r.get("Benchmark Name")
    })
    with st.expander("➕ Log a Result"):
        with st.form(f"manual_pr_{name}", clear_on_submit=True):
            pr_bench_options = athlete_benches or ["Back Squat 1RM (kg)", "Clean & Jerk 1RM (kg)",
                                                    "Snatch 1RM (kg)", "Deadlift 1RM (kg)",
                                                    "2k Row (mm:ss)", "1.2km Run (mm:ss)"]
            pr_bench = st.selectbox(
                "Benchmark", pr_bench_options + ["— Other (type below) —"],
                key=f"pr_bench_{name}",
            )
            pr_bench_custom = st.text_input(
                "Custom benchmark name (if not in list above)",
                key=f"pr_bench_custom_{name}",
                placeholder="e.g. Front Squat 1RM (kg)",
            )
            pr_value = st.text_input("Result", key=f"pr_value_{name}", placeholder="e.g. 120 or 7:42")
            pr_date = st.date_input("Date", value=TODAY, key=f"pr_date_{name}")
            pr_note = st.text_input("Note (optional)", key=f"pr_note_{name}",
                                    placeholder="e.g. competition, post-illness")
            if st.form_submit_button("Save Result", type="primary"):
                bench_final = pr_bench_custom.strip() if pr_bench_custom.strip() else (
                    pr_bench if pr_bench != "— Other (type below) —" else ""
                )
                if bench_final and pr_value.strip():
                    email = str(profile.get("Email", "")).strip()
                    new_row = [
                        pr_date.isoformat(), name, bench_final,
                        pr_value.strip(), email, pr_note.strip(),
                    ]
                    get_sheets().append_rows(config.TAB_PR_LOG, [new_row])
                    st.success(f"Saved — {bench_final}: {pr_value.strip()} ({pr_date})")
                    st.cache_data.clear()
                elif not bench_final:
                    st.warning("Choose or type a benchmark name.")
                else:
                    st.warning("Enter a result value.")
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

        # Recovery trend — show multi-submission sparklines if athlete has history
        try:
            email_key = str(profile.get("Email", "")).strip().lower()
            if email_key:
                rec_all = _load_recovery_all_cached()
                athlete_rec_history = rec_all.get(email_key, [])
                if len(athlete_rec_history) >= 3:
                    with st.expander(f"📉 Recovery trend ({len(athlete_rec_history)} submissions)"):
                        trend_pts = []
                        for rr in athlete_rec_history:
                            ts = str(rr.get(rec_mod.RECOVERY_COLS["timestamp"], "")).strip()
                            s_v = _numeric(str(rr.get("Soreness", "")).strip())
                            st_v = _numeric(str(rr.get("Stress", "")).strip())
                            mo_v = _numeric(str(rr.get("Motivation", "")).strip())
                            if ts and any(v is not None for v in [s_v, st_v, mo_v]):
                                trend_pts.append({
                                    "Date": ts[:10],
                                    "Soreness": s_v,
                                    "Stress": st_v,
                                    "Motivation": mo_v,
                                })
                        if len(trend_pts) >= 3:
                            tr_df = pd.DataFrame(trend_pts)
                            tr_df["Date"] = pd.to_datetime(tr_df["Date"], errors="coerce")
                            tr_df = tr_df.dropna(subset=["Date"]).sort_values("Date")
                            tr_melt = tr_df.melt(
                                id_vars="Date",
                                value_vars=["Soreness", "Stress", "Motivation"],
                                var_name="Metric", value_name="Score",
                            ).dropna(subset=["Score"])
                            rec_chart = (
                                alt.Chart(tr_melt)
                                .mark_line(point=True)
                                .encode(
                                    x=alt.X("Date:T", title="Date"),
                                    y=alt.Y("Score:Q", scale=alt.Scale(domain=[0, 10]), title="Score / 10"),
                                    color=alt.Color("Metric:N"),
                                    tooltip=["Date:T", "Metric:N", "Score:Q"],
                                )
                                .properties(height=200)
                            )
                            st.altair_chart(rec_chart, width='stretch')
        except Exception:
            pass

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
                "Type", ["note", "chat", "result", "recovery", "goal"],
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

    # ── Goal progress ─────────────────────────────────────────────────────────
    goal = str(profile.get("North Star Goal", "")).strip()
    goal_notes = [e for e in timeline if e.get("kind") == "goal"]
    if goal or goal_notes:
        st.markdown("**🎯 Goal Progress**")
        if goal:
            st.info(f"**North Star Goal:** {goal}")
        if goal_notes:
            for entry in goal_notes:
                with st.expander(f"🎯 {entry['date']}", expanded=False):
                    st.write(entry["text"])
        else:
            st.caption("No goal notes yet — add notes tagged 'goal' using the form above.")

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

    # ── Churn risk history ────────────────────────────────────────────────────
    try:
        churn_hist = _load_churn_history_cached()
        athlete_churn = sorted(
            [r for r in churn_hist if str(r.get("Athlete Name", "")).strip() == name],
            key=lambda x: str(x.get("Date", "")),
        )
        if len(athlete_churn) >= 3:
            st.markdown("**📉 Churn Risk History**")
            ch_df = pd.DataFrame(athlete_churn)[["Date", "Score", "Label"]].copy()
            ch_df["Score"] = pd.to_numeric(ch_df["Score"], errors="coerce")
            ch_chart = (
                alt.Chart(ch_df)
                .mark_line(point=True)
                .encode(
                    x=alt.X("Date:T", title="Date"),
                    y=alt.Y("Score:Q", scale=alt.Scale(domain=[0, 100]),
                             title="Churn Risk Score"),
                    tooltip=["Date:T", "Score:Q", "Label:N"],
                )
            )
            st.altair_chart(ch_chart, width='stretch')
            latest_snap = athlete_churn[-1]
            st.caption(
                f"Current: {latest_snap.get('Label', '—')} "
                f"({latest_snap.get('Score', '—')}/100)"
                + (f" — {latest_snap.get('Factors', '')}" if latest_snap.get("Factors") else "")
            )
    except Exception:
        pass

    # ── Training load chart ───────────────────────────────────────────────────
    try:
        tl_all = analytics.training_load(pr_records, weeks=12)
        tl_athlete = tl_all.get(name, [])
        if len(tl_athlete) >= 2:
            st.markdown("**🏃 Weekly Training Load (12 weeks)**")
            tl_df = pd.DataFrame(tl_athlete)
            tl_df["week_start"] = pd.to_datetime(tl_df["week_start"])
            tl_chart = (
                alt.Chart(tl_df)
                .mark_bar(color="#1f77b4", opacity=0.8)
                .encode(
                    x=alt.X("week_start:T", title="Week starting"),
                    y=alt.Y("sessions:Q", title="Training days logged", scale=alt.Scale(domain=[0, 7])),
                    tooltip=[alt.Tooltip("week_start:T", title="Week"), alt.Tooltip("sessions:Q", title="Days")],
                )
                .properties(height=200)
            )
            st.altair_chart(tl_chart, width='stretch')
            recent_sessions = [w["sessions"] for w in tl_athlete[-4:]]
            avg_4w = round(sum(recent_sessions) / len(recent_sessions), 1) if recent_sessions else 0
            st.caption(f"4-week avg: {avg_4w} training days/week")
            st.divider()
    except Exception:
        pass

    # ── Draft reply panel ─────────────────────────────────────────────────────
    try:
        all_drafts = _load_draft_replies_cached()
        athlete_draft = next(
            (r for r in all_drafts if str(r.get("Athlete Name", "")).strip() == name),
            None,
        )
        if athlete_draft:
            st.markdown("**✉️ AI-Drafted Reply**")
            st.caption(f"Generated {athlete_draft.get('Date', '')} — based on most recent Fitr message")
            draft_text = str(athlete_draft.get("Draft Reply", "")).strip()
            room_id = str(athlete_draft.get("Room ID", "")).strip()
            st.code(draft_text, language=None)
            fitr_messaging_on = st.session_state.get("fitr_messaging_on", False)
            d_col1, d_col2 = st.columns(2)
            with d_col1:
                if fitr_messaging_on and room_id:
                    if st.button("🚀 Send & Clear", key=f"draft_send_{name}", type="primary"):
                        try:
                            fitr_client = get_fitr()
                            fitr_client.send_chat_message(room_id, draft_text)
                            get_sheets().clear_draft_reply(name)
                            st.success("Sent and cleared!")
                            st.cache_data.clear()
                        except Exception as exc:
                            st.error(f"Send failed: {exc}")
                else:
                    st.caption("🔴 Toggle Fitr messaging ON in the sidebar to send directly.")
            with d_col2:
                if st.button("✅ Mark as Done", key=f"draft_done_{name}", type="secondary"):
                    get_sheets().clear_draft_reply(name)
                    st.success("Draft cleared.")
                    st.cache_data.clear()
            st.divider()
    except Exception:
        pass

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

    compliance_by_name = analytics.session_compliance(pr_records, data_records or [])

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
        risk = analytics.churn_risk_score(nm, engagement_results, trend_results, rec_by_name)
        comp_data = compliance_by_name.get(nm)
        summary_rows.append({
            "Name": nm,
            "Programme": prog_short,
            "Risk": risk["label"],
            "Compliance": comp_data["label"] if comp_data else "—",
            "Profile": f"{done_nm}/{total_nm}",
            "Last Logged": last.isoformat() if last else "Never",
            "Days Since": days if days is not None else "—",
            "Trend": trend_label,
            "Recovery": rec_str,
            "Archetype": arch_primary or "—",
            "Logging": "📝 Nudge" if nudge else ("✅ Active" if (days is not None and days < 28) else "⚠️ Inactive"),
        })

    # ── Search + Filters ──────────────────────────────────────────────────────
    search_query = st.text_input(
        "🔍 Search athlete", key="athletes_search",
        placeholder="Type a name to filter…",
        label_visibility="collapsed",
    )

    all_programmes = sorted({r["Programme"] for r in summary_rows})
    all_tiers = sorted(filter(None, {
        str(data_by_name.get(a["name"], {}).get("Tier", "")).strip()
        for a in athletes
    }))
    all_archetypes = sorted(filter(None, {r["Archetype"] for r in summary_rows if r["Archetype"] != "—"}))
    all_statuses = sorted({r["Logging"] for r in summary_rows})
    all_risks = ["🔴 Critical", "🟡 Elevated", "🟠 Moderate", "🟢 Low"]
    all_subscriptions = sorted(filter(None, {
        str(data_by_name.get(a["name"], {}).get("Subscription Plan", "")).strip()
        for a in athletes
    }))
    all_referrals = sorted(filter(None, {
        str(data_by_name.get(a["name"], {}).get("Referral Source", "")).strip()
        for a in athletes
    }))

    fc1, fc2, fc3, fc4, fc5 = st.columns(5)
    f_prog = fc1.multiselect("Programme", all_programmes, placeholder="All")
    f_tier = fc2.multiselect("Tier", all_tiers, placeholder="All")
    f_arch = fc3.multiselect("Archetype", all_archetypes, placeholder="All")
    f_status = fc4.multiselect("Status", all_statuses, placeholder="All")
    f_risk = fc5.multiselect("Risk", all_risks, placeholder="All")

    if all_subscriptions or all_referrals:
        fr1, fr2, _ = st.columns([2, 2, 1])
        f_sub = fr1.multiselect("Subscription", all_subscriptions, placeholder="All") if all_subscriptions else []
        f_ref = fr2.multiselect("Referral Source", all_referrals, placeholder="All") if all_referrals else []
    else:
        f_sub, f_ref = [], []

    filtered_rows = summary_rows
    if search_query:
        q = search_query.strip().lower()
        filtered_rows = [r for r in filtered_rows if q in r["Name"].lower()]
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
    if f_risk:
        filtered_rows = [r for r in filtered_rows if r["Risk"] in f_risk]
    if f_sub:
        filtered_rows = [
            r for r in filtered_rows
            if str(data_by_name.get(r["Name"], {}).get("Subscription Plan", "")).strip() in f_sub
        ]
    if f_ref:
        filtered_rows = [
            r for r in filtered_rows
            if str(data_by_name.get(r["Name"], {}).get("Referral Source", "")).strip() in f_ref
        ]

    total = len(summary_rows)
    shown = len(filtered_rows)
    if shown < total:
        st.caption(f"Showing {shown} of {total} athletes")

    df = pd.DataFrame(filtered_rows)

    st.caption("Click a row to open the full athlete profile.")
    event = st.dataframe(
        df, width='stretch', hide_index=True,
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

    col_sel, col_cmp = st.columns([2, 2])
    selected = col_sel.selectbox("Primary Athlete", athlete_names, key="trend_main")
    compare_names = col_cmp.multiselect(
        "Compare with (up to 4)",
        [n for n in athlete_names if n != selected],
        key="trend_cmp_names",
        placeholder="Add athletes to compare…",
    )
    if len(compare_names) > 4:
        st.caption("Showing first 4 comparison athletes.")
        compare_names = compare_names[:4]

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

    all_points = list(points_a)
    for cmp_name in compare_names:
        all_points.extend(_get_points(cmp_name, selected_bench))

    df_all = pd.DataFrame(all_points).sort_values("Date")

    if compare_names:
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
    st.altair_chart(chart, width='stretch')

    # Stats row per athlete
    all_compared = [selected] + list(compare_names)
    stat_cols = st.columns(len(all_compared))
    for col, nm in zip(stat_cols, all_compared):
        pts = _get_points(nm, selected_bench)
        if not pts:
            col.caption(f"{nm}: no data")
            continue
        df_nm = pd.DataFrame(pts).sort_values("Date")
        col.metric(f"{nm} — Best", df_nm["Label"].iloc[df_nm["Value"].argmax()])
        col.caption(f"{len(df_nm)} entries · Latest: {df_nm['Label'].iloc[-1]}")

    # ── PR Velocity ─────────────────────────────────────────────────────────────
    st.divider()
    st.markdown("**📈 Improvement Rate (all benchmarks)**")
    st.caption("% improvement per month, computed from all-time PR log data")
    velocity_data = analytics.pr_velocity(pr_records)
    vel_rows = velocity_data.get(selected, [])
    if vel_rows:
        vel_df = pd.DataFrame([{
            "Benchmark": r["benchmark"],
            "Rate (%/month)": r["rate_pct_per_month"],
            "Direction": ("↑ " if r["direction"] == "improving"
                          else "↓ " if r["direction"] == "declining" else "→ ") + r["direction"].title(),
            "Entries": r["data_points"],
            "From": r["first_date"].strftime("%b %Y"),
            "To": r["last_date"].strftime("%b %Y"),
        } for r in vel_rows])
        st.dataframe(vel_df, width='stretch', hide_index=True)
    else:
        st.caption("Not enough data points yet (need at least 2 entries per benchmark).")

    # ── Cohort retention ──────────────────────────────────────────────────────
    st.divider()
    st.markdown("**👥 Cohort Retention**")
    st.caption("% of athletes still logging at 30, 60, and 90 days — by the month they first appeared")
    cohort_data = analytics.cohort_retention(pr_records, min_cohort_size=2)
    if cohort_data:
        cohort_rows = []
        for c in cohort_data:
            cohort_rows.append({
                "Cohort": str(c.get("cohort", "")),
                "Athletes": c.get("n", 0),
                "30d %": c.get("pct_30d", 0),
                "60d %": c.get("pct_60d", 0),
                "90d %": c.get("pct_90d", 0),
            })
        cohort_df = pd.DataFrame(cohort_rows)
        st.dataframe(cohort_df, width='stretch', hide_index=True)

        # Grouped bar chart: 30d / 60d / 90d per cohort
        if len(cohort_rows) >= 2:
            melt_df = cohort_df.melt(
                id_vars="Cohort", value_vars=["30d %", "60d %", "90d %"],
                var_name="Window", value_name="Retention %",
            )
            cohort_chart = (
                alt.Chart(melt_df)
                .mark_bar()
                .encode(
                    x=alt.X("Cohort:N", title="Starting cohort"),
                    y=alt.Y("Retention %:Q", scale=alt.Scale(domain=[0, 100])),
                    color=alt.Color("Window:N", legend=alt.Legend(title="")),
                    xOffset="Window:N",
                    tooltip=["Cohort:N", "Window:N", "Retention %:Q"],
                )
                .properties(height=260)
            )
            st.altair_chart(cohort_chart, width='stretch')
    else:
        st.caption("Not enough data yet — need cohorts of ≥2 athletes with 90+ days of history.")


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

    styled = df.style.map(lambda v: _colour(v, "Soreness"), subset=["Soreness"])
    styled = styled.map(lambda v: _colour(v, "Stress"), subset=["Stress"])
    styled = styled.map(lambda v: _colour(v, "Motivation"), subset=["Motivation"])

    st.dataframe(styled, width='stretch', hide_index=True)


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
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

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
            st.altair_chart(chart, width='stretch')
            st.caption("🔴 dashed line = today  |  🥇 A-race  🥈 B-race  🥉 C-race")
        st.divider()

    # Past competitions needing results logged
    if competition_rows:
        needs_result = [
            row for row in competition_rows
            if str(row.get("Athlete Name", "")).strip() in all_names
            and not str(row.get("Result", "")).strip()
            and _parse_date(str(row.get("Date", "")).strip()) is not None
            and (TODAY - _parse_date(str(row.get("Date", "")).strip())).days >= 0  # type: ignore[operator]
        ]
        if needs_result:
            with st.expander(f"📋 {len(needs_result)} past competition{'s' if len(needs_result) > 1 else ''} awaiting results"):
                for row in sorted(needs_result, key=lambda r: r.get("Date", ""), reverse=True):
                    athlete_nm = str(row.get("Athlete Name", "")).strip()
                    comp_nm = str(row.get("Competition Name", "")).strip()
                    comp_dt = _parse_date(str(row.get("Date", "")).strip())
                    st.caption(f"{athlete_nm} — {comp_nm} ({comp_dt.strftime('%d %b %Y') if comp_dt else '?'})")
                st.info("Open an athlete's profile to log their result.")

    # Athletes without any competition planned
    with_comp = {c["name"] for c in comp_results}
    no_comp = sorted(all_names - with_comp)
    if no_comp:
        with st.expander(f"{len(no_comp)} athletes with no competitions planned"):
            st.caption("Share your competition planner Typeform link so they can add their races.")
            st.write(", ".join(no_comp))


def page_programmes(athletes, pr_records, trend_results, data_records, load_results=None, engagement_results=None):
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
    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

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
        st.altair_chart(chart, width='stretch')

    unassigned = by_prog.get("— Unassigned —", [])
    if unassigned:
        st.markdown(f"**{len(unassigned)} not yet assigned:**  " + ", ".join(sorted(unassigned)))

    # ── Coach capacity view ───────────────────────────────────────────────────
    st.divider()
    st.subheader("Coach Capacity")
    st.caption("Bespoke athlete load per coach — who has capacity and who's stretched")

    bespoke_coaches = set(_COACH_ABBREV.values())
    capacity_rows = analytics.coach_capacity(
        athletes, pr_records, data_records,
        engagement_results=(engagement_results or []),
        bespoke_coaches=bespoke_coaches,
    )
    if capacity_rows:
        cap_df = pd.DataFrame(capacity_rows)
        st.dataframe(cap_df, width='stretch', hide_index=True)

        # Bar chart of athletes per coach
        cap_chart = (
            alt.Chart(cap_df)
            .mark_bar(color="#5c85d6")
            .encode(
                x=alt.X("Athletes:Q", title="Athletes"),
                y=alt.Y("Coach:N", sort="-x", title=""),
                tooltip=["Coach:N", "Athletes:Q", "Active (28d):Q", "Needs Attention:Q"],
            )
            .properties(height=max(160, len(capacity_rows) * 34))
        )
        st.altair_chart(cap_chart, width='stretch')
    else:
        st.info("No bespoke athletes assigned yet.")

    # ── Referral Source breakdown ─────────────────────────────────────────────
    referral_counts = {}
    for a in athletes:
        ref = str(data_by_name.get(a["name"], {}).get("Referral Source", "")).strip()
        if ref:
            referral_counts[ref] = referral_counts.get(ref, 0) + 1
    if referral_counts:
        st.divider()
        st.subheader("Referral Sources")
        st.caption("How athletes found JST Compete — from the Athlete Data sheet")
        ref_df = pd.DataFrame(
            sorted(referral_counts.items(), key=lambda x: -x[1]),
            columns=["Source", "Athletes"],
        )
        ref_chart = (
            alt.Chart(ref_df)
            .mark_bar(color="#2ca02c")
            .encode(
                x=alt.X("Athletes:Q", title="Athletes"),
                y=alt.Y("Source:N", sort="-x", title=""),
                tooltip=["Source:N", "Athletes:Q"],
            )
            .properties(height=max(160, len(referral_counts) * 34))
        )
        st.altair_chart(ref_chart, width='stretch')
        total_with_ref = sum(referral_counts.values())
        total_athletes = len(athletes)
        if total_athletes:
            st.caption(f"{total_with_ref} of {total_athletes} athletes have a referral source recorded ({round(total_with_ref / total_athletes * 100)}%).")

        # Referral → engagement quality table
        if engagement_results:
            st.markdown("**Referral Source → Engagement Quality**")
            st.caption("% of athletes from each source who are currently active (logged in last 14d)")
            eng_by_name = {e["name"]: e for e in engagement_results}
            ref_quality = {}
            for a in athletes:
                ref = str(data_by_name.get(a["name"], {}).get("Referral Source", "")).strip()
                if not ref:
                    continue
                e = eng_by_name.get(a["name"], {})
                days = e.get("days_since")
                active = days is not None and days <= 14
                ref_quality.setdefault(ref, {"total": 0, "active": 0})
                ref_quality[ref]["total"] += 1
                if active:
                    ref_quality[ref]["active"] += 1
            if ref_quality:
                quality_df = pd.DataFrame([
                    {
                        "Source": ref,
                        "Athletes": v["total"],
                        "Active (14d)": v["active"],
                        "Active %": f"{round(v['active'] / v['total'] * 100)}%" if v["total"] else "—",
                    }
                    for ref, v in sorted(ref_quality.items(), key=lambda x: -x[1]["total"])
                ])
                st.dataframe(quality_df, width='stretch', hide_index=True)

    # ── Cohort retention ──────────────────────────────────────────────────────
    cohort_rows = analytics.cohort_retention(pr_records, min_cohort_size=2)
    if cohort_rows:
        st.divider()
        st.subheader("Cohort Retention")
        st.caption("Grouped by month of first PR log entry — % who logged again within 30/60/90 days")
        cohort_df = pd.DataFrame([
            {
                "Cohort": r["cohort"],
                "Athletes": r["n"],
                "30d %": f"{r['pct_30d']}%" if r["pct_30d"] is not None else "—",
                "60d %": f"{r['pct_60d']}%" if r["pct_60d"] is not None else "—",
                "90d %": f"{r['pct_90d']}%" if r["pct_90d"] is not None else "—",
            }
            for r in cohort_rows
        ])
        st.dataframe(cohort_df, width='stretch', hide_index=True)


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
    st.dataframe(styled, width='stretch', hide_index=True, height=500)

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
        width='stretch', hide_index=True,
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
        st.altair_chart((bars + baseline).properties(height=280), width='stretch')
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
    st.dataframe(pd.DataFrame(prog_rows), width='stretch', hide_index=True)


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

_COACH_ABBREV = {
    "jamie w": "Jamie Warr", "jamie h": "Jamie Harrop",
    "dcon": "Dan Connolly", "denis": "Denis Smith",
    "ed": "Ed Cook", "jak": "Jak Cornthwaite",
    "louis": "Louis Towers", "huw": "Huw Davis", "pete": "Pete Crudge",
}


@st.cache_data(ttl=600, show_spinner="Loading CRM data...")
def load_crm_data():
    """Load CRM athlete-coach map + sync log. Cached separately from main data."""
    sheets = get_sheets()
    crm_by_name = {}  # name_lower -> (display_name, full_coach)
    for tab in ("Bespoke Athletes", "Junior + Youth"):
        try:
            rows = sheets.read_external_records(config.CRM_SHEET_ID, tab)
            for r in rows:
                name = (r.get("Athlete Name") or "").strip()
                coach_raw = (r.get("Coach") or "").strip()
                if name:
                    full_coach = _COACH_ABBREV.get(coach_raw.lower(), coach_raw)
                    crm_by_name[name.lower()] = (name, full_coach)
        except Exception:
            pass
    sync_log = []
    try:
        sync_log = sheets.read_records(config.TAB_SYNC_LOG)
    except Exception:
        pass
    return crm_by_name, sync_log


@st.cache_data(ttl=600, show_spinner=False)
def _load_message_log_cached():
    try:
        return get_sheets().sh.worksheet(config.TAB_MESSAGE_LOG).get_all_records()
    except Exception:
        return []


def page_crm(athletes, engagement_results, data_records, pr_records=None):
    """Unified CRM integration tab: lifecycle, pipeline, rosters, discrepancies, bulk actions."""
    st.markdown("## CRM Integration")

    crm_by_name, sync_log = load_crm_data()

    # ── Sync status header ────────────────────────────────────────────────────
    if sync_log:
        last = sync_log[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Last Sync", str(last.get("Run Date", "—")))
        c2.metric("New PR Results", str(last.get("New PR Log rows", 0)))
        c3.metric("Conversations", str(last.get("Conversations summarised", 0)))
        c4.metric("Auto-onboarded", str(last.get("Athletes auto-onboarded", 0)))

        # Athlete growth chart from sync log
        growth_pts = [
            {"Date": pd.Timestamp(str(r.get("Run Date", ""))), "Athletes": int(r["Total Athletes"])}
            for r in sync_log
            if r.get("Total Athletes") and str(r.get("Run Date", "")).strip()
            and str(r["Total Athletes"]).strip().isdigit()
        ]
        if len(growth_pts) >= 2:
            growth_df = pd.DataFrame(growth_pts).sort_values("Date")
            growth_chart = (
                alt.Chart(growth_df)
                .mark_line(point=True, color="#1f77b4")
                .encode(
                    x=alt.X("Date:T", title="Sync Date"),
                    y=alt.Y("Athletes:Q", title="Total Athletes", scale=alt.Scale(zero=False)),
                    tooltip=["Date:T", "Athletes:Q"],
                )
                .properties(height=200, title="Athlete Count Over Time")
            )
            st.altair_chart(growth_chart, width='stretch')
        st.divider()

    crm_tabs = st.tabs([
        "🔄 Lifecycle", "🚀 Pipeline", "👥 Rosters", "⚠️ Discrepancies", "✏️ Bulk Reassign",
        "💰 Revenue", "📨 Msg Effectiveness", "📊 Coach Stats", "🔍 Duplicates",
    ])

    with crm_tabs[0]:
        _crm_lifecycle(athletes, engagement_results, data_records, crm_by_name)
    with crm_tabs[1]:
        _crm_pipeline(athletes, crm_by_name)
    with crm_tabs[2]:
        _crm_rosters(athletes, engagement_results, data_records, crm_by_name)
    with crm_tabs[3]:
        _crm_discrepancies(data_records, crm_by_name)
    with crm_tabs[4]:
        _crm_bulk_reassign(data_records)
    with crm_tabs[5]:
        _crm_revenue(data_records)
    with crm_tabs[6]:
        _crm_message_effectiveness()
    with crm_tabs[7]:
        _crm_coach_stats(athletes, engagement_results, data_records)
    with crm_tabs[8]:
        _crm_dedup(athletes, data_records, pr_records or [])


def _crm_message_effectiveness():
    """Show reply rates per automated message type from the Message Log tab."""
    st.markdown("### Automated Message Effectiveness")
    st.caption("Reply rates for automated Fitr messages sent by the daily sync.")
    rows = _load_message_log_cached()
    if not rows:
        st.info("No messages logged yet. The Message Log tab will populate after the next sync run.")
        return

    from collections import defaultdict
    totals = defaultdict(lambda: {"sent": 0, "replied": 0})
    for r in rows:
        msg_type = str(r.get("Message Type", "unknown")).strip()
        totals[msg_type]["sent"] += 1
        if str(r.get("Replied", "")).strip().lower() == "yes":
            totals[msg_type]["replied"] += 1

    table_rows = []
    for msg_type, counts in sorted(totals.items()):
        sent = counts["sent"]
        replied = counts["replied"]
        rate = round(replied / sent * 100, 1) if sent else 0
        table_rows.append({"Message Type": msg_type, "Sent": sent, "Replied": replied,
                           "Reply Rate (%)": rate})

    df = pd.DataFrame(table_rows)
    st.dataframe(df, width='stretch', hide_index=True)

    # Bar chart of reply rates
    if len(table_rows) >= 2:
        chart = (
            alt.Chart(df)
            .mark_bar()
            .encode(
                x=alt.X("Reply Rate (%):Q", scale=alt.Scale(domain=[0, 100])),
                y=alt.Y("Message Type:N", sort="-x"),
                tooltip=["Message Type:N", "Sent:Q", "Replied:Q", "Reply Rate (%):Q"],
            )
        )
        st.altair_chart(chart, width='stretch')

    total_sent = sum(r["Sent"] for r in table_rows)
    total_replied = sum(r["Replied"] for r in table_rows)
    overall_rate = round(total_replied / total_sent * 100, 1) if total_sent else 0
    st.caption(
        f"Overall: {total_sent} messages sent, {total_replied} replies received "
        f"({overall_rate}% reply rate)"
    )


def _crm_lifecycle(athletes, engagement_results, data_records, crm_by_name):
    """Full lifecycle table: CRM → Benchmarks → Fitr activity."""
    st.markdown("### Athlete Lifecycle")
    st.caption("Every bespoke athlete's journey — from CRM through to active training")

    bench_names_lower = {a["name"].lower() for a in athletes}
    eng_by_name = {e["name"].lower(): e for e in engagement_results}
    data_by_name = {
        (r.get("Full Name") or "").strip().lower(): r
        for r in data_records
        if (r.get("Full Name") or "").strip()
    }

    # Union of CRM names + bespoke athletes already syncing
    bespoke_coaches = set(_COACH_ABBREV.values())
    all_names = set(crm_by_name.keys())
    for r in data_records:
        name = (r.get("Full Name") or "").strip()
        prog = (r.get("Programme") or "").strip()
        if name and prog in bespoke_coaches:
            all_names.add(name.lower())

    rows = []
    for name_lower in sorted(all_names):
        crm_entry = crm_by_name.get(name_lower)
        display_name = crm_entry[0] if crm_entry else name_lower.title()
        crm_coach = crm_entry[1] if crm_entry else "—"

        in_bench = name_lower in bench_names_lower
        eng = eng_by_name.get(name_lower) or eng_by_name.get(display_name.lower())
        data_rec = data_by_name.get(name_lower)
        data_coach = (data_rec.get("Programme") or "").strip() if data_rec else "—"

        last_logged = (eng or {}).get("last_logged", "never") if in_bench else "not syncing"
        days = (eng or {}).get("days_since")

        if not in_bench:
            status = "⬜ Not syncing"
        elif last_logged == "never":
            status = "🆕 Onboarded"
        elif days is None:
            status = "🆕 Onboarded"
        elif days <= 14:
            status = "🟢 Active"
        elif days <= 44:
            status = "🟡 Check in"
        else:
            status = "🔴 Inactive"

        rows.append({
            "Athlete": display_name,
            "CRM Coach": crm_coach,
            "Fitr Programme": data_coach,
            "Syncing": "✅" if in_bench else "❌",
            "Last Logged": last_logged,
            "Days Since": str(days) if days is not None else ("—" if in_bench else "—"),
            "Status": status,
        })

    if not rows:
        st.info("No bespoke athletes found.")
        return

    df = pd.DataFrame(rows)

    # Filter controls
    col_f, col_s = st.columns([2, 2])
    with col_f:
        coaches = ["All coaches"] + sorted({r["CRM Coach"] for r in rows if r["CRM Coach"] != "—"})
        sel_coach = st.selectbox("Filter by coach", coaches, key="lc_coach_filter")
    with col_s:
        statuses = ["All statuses", "🟢 Active", "🟡 Check in", "🔴 Inactive", "🆕 Onboarded", "⬜ Not syncing"]
        sel_status = st.selectbox("Filter by status", statuses, key="lc_status_filter")

    if sel_coach != "All coaches":
        df = df[df["CRM Coach"] == sel_coach]
    if sel_status != "All statuses":
        df = df[df["Status"] == sel_status]

    st.dataframe(df, width='stretch', hide_index=True)

    # Summary counts
    status_counts = pd.Series([r["Status"] for r in rows]).value_counts()
    st.caption("  ·  ".join(f"{s}: {c}" for s, c in status_counts.items()))


def _crm_pipeline(athletes, crm_by_name):
    """Athletes in CRM not yet in Benchmarks."""
    st.markdown("### Onboarding Pipeline")
    st.caption("CRM athletes not yet syncing — will be auto-added when they appear in Fitr chat")

    bench_names_lower = {a["name"].lower() for a in athletes}
    pending = [
        (display, coach)
        for lower, (display, coach) in crm_by_name.items()
        if lower not in bench_names_lower
    ]
    pending.sort(key=lambda x: x[1])

    if not pending:
        st.success("✓ All CRM athletes are syncing!")
        return

    st.warning(f"{len(pending)} athletes not yet syncing")

    by_coach = {}
    for name, coach in pending:
        by_coach.setdefault(coach, []).append(name)

    for coach in sorted(by_coach.keys()):
        with st.expander(f"**{coach}** — {len(by_coach[coach])} pending"):
            for name in sorted(by_coach[coach]):
                st.text(f"• {name}")

    st.info("💡 To add manually: run `python onboard_bespoke_athletes.py` with their Fitr IDs.")


def _crm_rosters(athletes, engagement_results, data_records, crm_by_name):
    """Per-coach CRM roster vs. active Fitr athletes, with engagement signal."""
    st.markdown("### Coach Rosters")
    st.caption("CRM roster vs. actively syncing athletes, with engagement status")

    eng_by_name = {e["name"].lower(): e for e in engagement_results}
    bench_names_lower = {a["name"].lower() for a in athletes}

    syncing_by_coach = {}
    for r in data_records:
        name = (r.get("Full Name") or "").strip()
        prog = (r.get("Programme") or "").strip()
        if name and prog in set(_COACH_ABBREV.values()):
            syncing_by_coach.setdefault(prog, []).append(name)

    crm_by_coach = {}
    for lower, (display, coach) in crm_by_name.items():
        crm_by_coach.setdefault(coach, []).append(display)

    all_coaches = sorted(set(crm_by_coach.keys()) | set(syncing_by_coach.keys()))

    for coach in all_coaches:
        crm_set = {n.lower() for n in crm_by_coach.get(coach, [])}
        sync_set = {n.lower() for n in syncing_by_coach.get(coach, [])}
        missing = crm_set - sync_set
        extra = sync_set - crm_set
        icon = "✅" if not missing and not extra else "⚠️"

        with st.expander(
            f"{icon} **{coach}** — CRM: {len(crm_set)} | Syncing: {len(sync_set)}"
        ):
            if missing:
                st.error(f"In CRM but not syncing ({len(missing)}):")
                for n in sorted(missing):
                    st.text(f"  • {n}")
            if extra:
                st.warning(f"Syncing but not in CRM ({len(extra)}):")
                for n in sorted(extra):
                    st.text(f"  • {n}")

            # Show engagement summary for syncing athletes
            syncing_names = syncing_by_coach.get(coach, [])
            if syncing_names:
                active = sum(
                    1 for n in syncing_names
                    if (eng_by_name.get(n.lower()) or {}).get("days_since") is not None
                    and (eng_by_name.get(n.lower()) or {}).get("days_since") <= 14
                )
                flagged = sum(
                    1 for n in syncing_names
                    if (eng_by_name.get(n.lower()) or {}).get("flag")
                )
                st.caption(
                    f"Active (≤14d): {active}/{len(syncing_names)}   "
                    f"Needs contact: {flagged}"
                )

            if not missing and not extra:
                st.success("✓ Rosters match!")


def _crm_discrepancies(data_records, crm_by_name):
    """Flag CRM duplicates and coach assignment mismatches vs _DATA."""
    st.markdown("### Discrepancies")
    st.caption("CRM duplicates and coach assignment mismatches")

    from collections import Counter
    name_counts = Counter(lower for lower in crm_by_name.keys())
    # CRM is already deduped by name_lower in load_crm_data, so show raw list approach
    # Re-load raw to check for true name duplicates
    try:
        sheets = get_sheets()
        raw_entries = []
        for tab in ("Bespoke Athletes", "Junior + Youth"):
            try:
                rows = sheets.read_external_records(config.CRM_SHEET_ID, tab)
                for r in rows:
                    name = (r.get("Athlete Name") or "").strip()
                    coach = (r.get("Coach") or "").strip()
                    if name:
                        raw_entries.append((name, coach, tab))
            except Exception:
                pass

        raw_counts = Counter(n.lower() for n, c, t in raw_entries)
        dups = {n: raw_counts[n] for n in raw_counts if raw_counts[n] > 1}

        if dups:
            st.error(f"Athletes appearing multiple times in CRM ({len(dups)}):")
            for name_lower in sorted(dups.keys()):
                entries = [(n, c, t) for n, c, t in raw_entries if n.lower() == name_lower]
                with st.expander(f"**{entries[0][0]}** ({len(entries)} entries)"):
                    for n, coach, tab in entries:
                        st.text(f"  • {coach} ({tab})")
        else:
            st.success("✓ No duplicate CRM entries")

        # Coach assignment mismatches: CRM vs _DATA Programme
        data_by_name = {(r.get("Full Name") or "").strip(): r for r in data_records}
        mismatches = []
        for name, coach_raw, tab in raw_entries:
            full_coach = _COACH_ABBREV.get(coach_raw.lower(), coach_raw)
            data_rec = data_by_name.get(name)
            if data_rec:
                data_prog = (data_rec.get("Programme") or "").strip()
                if full_coach and data_prog and full_coach != data_prog:
                    mismatches.append((name, full_coach, data_prog))

        if mismatches:
            st.warning(f"Coach assignment mismatches ({len(mismatches)}):")
            rows_out = [
                {"Athlete": n, "CRM Coach": crm, "_DATA Programme": data}
                for n, crm, data in sorted(mismatches)
            ]
            st.dataframe(pd.DataFrame(rows_out), width='stretch', hide_index=True)
        else:
            st.success("✓ No coach assignment mismatches")

    except Exception as e:
        st.error(f"Error: {e}")


def _crm_bulk_reassign(data_records):
    """Bulk reassign Programme for multiple athletes at once."""
    st.markdown("### Bulk Programme Reassignment")
    st.caption("Move multiple athletes to a different coach or programme in one go")

    all_coaches = sorted(_COACH_ABBREV.values())
    all_progs = all_coaches + config.JST_TRACKS

    # Build name→current programme map
    data_by_name = {}
    for r in data_records:
        name = (r.get("Full Name") or "").strip()
        prog = (r.get("Programme") or "").strip()
        if name:
            data_by_name[name] = prog

    col_filter, col_new = st.columns([2, 2])
    with col_filter:
        filter_prog = st.selectbox(
            "Show athletes currently in", ["All"] + all_progs,
            key="bulk_filter_prog",
        )
    with col_new:
        new_prog = st.selectbox(
            "Reassign selected to", all_progs,
            key="bulk_new_prog",
        )

    filtered = {
        name: prog for name, prog in data_by_name.items()
        if filter_prog == "All" or prog == filter_prog
    }

    selected = st.multiselect(
        "Select athletes to reassign",
        options=sorted(filtered.keys()),
        format_func=lambda n: f"{n}  ({filtered.get(n, '—')})",
        key="bulk_selected",
    )

    if selected:
        st.info(f"Will move {len(selected)} athlete(s) → **{new_prog}**")
        if st.button("Apply Reassignment", type="primary", key="bulk_apply"):
            updates = {name: {"Programme": new_prog} for name in selected}
            get_sheets().batch_update_by_name(config.TAB_DATA, "Full Name", updates)
            st.success(f"✓ Updated {len(selected)} athletes → {new_prog}")
            st.cache_data.clear()


def _crm_revenue(data_records):
    """MRR breakdown by subscription plan."""
    st.markdown("### Revenue")
    st.caption("Monthly recurring revenue by subscription plan, based on _DATA 'Subscription Plan' column")

    # Build {plan: count} from data_records
    plan_counts = {}
    for r in (data_records or []):
        plan = str(r.get("Subscription Plan", "")).strip()
        if plan:
            plan_counts[plan] = plan_counts.get(plan, 0) + 1

    if not plan_counts:
        st.info("No subscription plan data found in _DATA. Add a 'Subscription Plan' column to your athlete sheet.")
        return

    # Compute MRR per plan
    plan_rows = []
    unpriced = []
    total_athletes = 0
    total_mrr = 0
    for plan, count in sorted(plan_counts.items(), key=lambda x: -x[1]):
        price = config.SUBSCRIPTION_PRICES.get(plan, 0)
        mrr = price * count
        total_athletes += count
        total_mrr += mrr
        plan_rows.append({
            "Plan": plan,
            "Athletes": count,
            "Price/mo (£)": price if price else "—",
            "MRR (£)": mrr,
            "_mrr_num": mrr,
            "_price": price,
        })
        if not price:
            unpriced.append(plan)

    avg_rpa = round(total_mrr / total_athletes, 2) if total_athletes else 0

    # Three summary metrics
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Athletes", total_athletes)
    m2.metric("Total MRR (£)", f"£{total_mrr:,.0f}")
    m3.metric("Avg Revenue per Athlete (£)", f"£{avg_rpa:,.2f}")

    st.divider()

    # Bar chart: x = MRR, y = Plan, sorted descending
    chart_rows = [r for r in plan_rows if r["_mrr_num"] > 0]
    if chart_rows:
        chart_df = pd.DataFrame([
            {"Plan": r["Plan"], "MRR (£)": r["_mrr_num"]}
            for r in sorted(chart_rows, key=lambda x: -x["_mrr_num"])
        ])
        bar = (
            alt.Chart(chart_df)
            .mark_bar(color="#1f77b4")
            .encode(
                x=alt.X("MRR (£):Q", title="MRR (£)"),
                y=alt.Y("Plan:N", sort="-x", title=""),
                tooltip=["Plan:N", "MRR (£):Q"],
            )
            .properties(height=max(160, len(chart_rows) * 38))
        )
        st.altair_chart(bar, width='stretch')

    # Summary table
    display_rows = []
    for r in plan_rows:
        display_rows.append({
            "Plan": r["Plan"],
            "Athletes": r["Athletes"],
            "Price/mo (£)": r["Price/mo (£)"],
            "MRR (£)": r["MRR (£)"],
        })
    st.dataframe(pd.DataFrame(display_rows), width='stretch', hide_index=True)

    if unpriced:
        st.caption(
            f"Plans without a configured price: {', '.join(unpriced)}. "
            "Update SUBSCRIPTION_PRICES in config.py to include unpriced plans."
        )


def page_progress():
    """Athlete-facing progress view — shown when ?mode=progress&id=JST_ID."""
    jst_id = (st.query_params.get("id") or "").strip()
    if not jst_id:
        st.error("No athlete ID in URL. Ask your coach for your personal progress link.")
        return

    sheets = get_sheets()
    try:
        data_records = sheets.read_records(config.TAB_DATA)
        pr_records = sheets.read_records(config.TAB_PR_LOG)
    except Exception:
        st.error("Unable to load data. Please try again later.")
        return

    # Find athlete by JST ID
    profile = None
    athlete_name = None
    for rec in data_records:
        rid = str(rec.get("Athlete ID", "")).strip()
        if rid and rid == jst_id:
            athlete_name = str(rec.get("Full Name", "")).strip()
            profile = rec
            break

    if not athlete_name or not profile:
        st.error("Link not recognised — ask your coach for a new one.")
        return

    st.title(f"🏋️ Your Progress — {athlete_name}")
    st.caption("Powered by JST Compete · Updated daily")
    st.divider()

    # ── Goal ──────────────────────────────────────────────────────────────────
    goal = str(profile.get("North Star Goal", "")).strip()
    if goal:
        st.markdown("### 🎯 Your Goal")
        st.info(goal)
        st.divider()

    # ── Summary stats ─────────────────────────────────────────────────────────
    athlete_prs = [r for r in pr_records if str(r.get("Athlete Name", "")).strip() == athlete_name]
    if athlete_prs:
        dates = [_parse_date(str(r.get("Date", ""))) for r in athlete_prs]
        dates = [d for d in dates if d]
        benchmarks_logged = len({str(r.get("Benchmark Name", "")).strip() for r in athlete_prs})
        last_log = max(dates).strftime("%d %b %Y") if dates else "—"
        days_active = (max(dates) - min(dates)).days if len(dates) >= 2 else 0

        c1, c2, c3 = st.columns(3)
        c1.metric("Total PR Entries", len(athlete_prs))
        c2.metric("Benchmarks Tracked", benchmarks_logged)
        c3.metric("Last Logged", last_log)
        st.divider()

    # ── Recent results ─────────────────────────────────────────────────────────
    if athlete_prs:
        st.markdown("### 📈 Recent Results")
        recent = sorted(
            athlete_prs,
            key=lambda r: str(r.get("Date", "")),
            reverse=True,
        )[:10]
        recent_rows = []
        for r in recent:
            recent_rows.append({
                "Date": str(r.get("Date", "")).strip(),
                "Benchmark": str(r.get("Benchmark Name", "")).strip(),
                "Result": str(r.get("Value", "")).strip(),
                "Note": str(r.get("Note", "")).strip(),
            })
        st.dataframe(pd.DataFrame(recent_rows), width='stretch', hide_index=True)
        st.divider()

    # ── Training load chart ───────────────────────────────────────────────────
    try:
        tl_all = analytics.training_load(pr_records, weeks=12)
        tl_data = tl_all.get(athlete_name, [])
        if len(tl_data) >= 2:
            st.markdown("### 🏃 Training Consistency (last 12 weeks)")
            tl_df = pd.DataFrame(tl_data)
            tl_df["week_start"] = pd.to_datetime(tl_df["week_start"])
            chart = (
                alt.Chart(tl_df)
                .mark_bar(color="#2196F3", opacity=0.85)
                .encode(
                    x=alt.X("week_start:T", title="Week"),
                    y=alt.Y("sessions:Q", title="Days logged", scale=alt.Scale(domain=[0, 7])),
                    tooltip=[alt.Tooltip("week_start:T", title="Week"), alt.Tooltip("sessions:Q", title="Days logged")],
                )
                .properties(height=220)
            )
            st.altair_chart(chart, width='stretch')
            avg_4w = sum(w["sessions"] for w in tl_data[-4:]) / min(4, len(tl_data))
            st.caption(f"4-week average: {avg_4w:.1f} training days per week")
            st.divider()
    except Exception:
        pass

    # ── Competition calendar ───────────────────────────────────────────────────
    try:
        competition_rows = sheets.load_competitions()
        athlete_comps = [
            cr for cr in competition_rows
            if str(cr.get("Athlete Name", "")).strip() == athlete_name
        ]
        if athlete_comps:
            st.markdown("### 🏁 Your Competition Calendar")
            comp_rows_display = []
            for cr in sorted(athlete_comps, key=lambda x: str(x.get("Date", ""))):
                cd = _parse_date(str(cr.get("Date", "")).strip())
                if not cd:
                    continue
                days_out = (cd - TODAY).days
                if days_out >= 0:
                    w, d = divmod(days_out, 7)
                    time_str = f"{w}w {d}d" if w else f"{d}d"
                else:
                    time_str = f"{abs(days_out)}d ago"
                result = str(cr.get("Result", "")).strip()
                comp_rows_display.append({
                    "Competition": str(cr.get("Competition Name", "")).strip(),
                    "Date": cd.strftime("%d %b %Y"),
                    "In": time_str,
                    "Type": str(cr.get("Type", "")).strip(),
                    "Result": result or "—",
                })
            st.dataframe(pd.DataFrame(comp_rows_display), width='stretch', hide_index=True)
            st.divider()
    except Exception:
        pass

    # ── Programme ─────────────────────────────────────────────────────────────
    programme = str(profile.get("Programme", "")).strip()
    tier = str(profile.get("Tier", "")).strip()
    if programme or tier:
        st.markdown("### 🎽 Your Programme")
        pc1, pc2 = st.columns(2)
        pc1.metric("Programme", programme or "—")
        pc2.metric("Tier", tier or "—")

    st.divider()
    st.caption("Questions about your data? Message your coach directly in Fitr.")


def _crm_coach_stats(athletes, engagement_results, data_records):
    """Per-coach retention dashboard."""
    st.markdown("### Coach Retention Stats")
    st.caption("Squad size, activity, and churn risk distribution per coach")

    data_by_name = {
        str(r.get("Full Name", "")).strip(): r
        for r in (data_records or [])
        if str(r.get("Full Name", "")).strip()
    }
    eng_by_name = {e["name"]: e for e in engagement_results}

    # Group athletes by programme/coach
    by_coach = {}
    for a in athletes:
        nm = a["name"]
        rec = data_by_name.get(nm, {})
        coach = str(rec.get("Programme", "")).strip() or "— Unassigned —"
        by_coach.setdefault(coach, []).append(nm)

    if not by_coach:
        st.info("No athlete programme data available.")
        return

    rows = []
    for coach, names in sorted(by_coach.items()):
        eng_data = [eng_by_name.get(nm, {}) for nm in names]
        n = len(names)
        active = sum(
            1 for e in eng_data
            if e.get("days_since") is not None and e.get("days_since", 9999) <= 14
        )
        inactive = sum(1 for e in eng_data if e.get("flag"))
        never = sum(1 for e in eng_data if e.get("last_logged") == "never")
        days_vals = [e["days_since"] for e in eng_data if e.get("days_since") is not None]
        avg_days = round(sum(days_vals) / len(days_vals), 1) if days_vals else None

        # Churn risk distribution
        risk_counts = {"🔴 Critical": 0, "🟡 Elevated": 0, "🟠 Moderate": 0, "🟢 Low": 0}
        for nm in names:
            risk = analytics.churn_risk_score(nm, engagement_results, {}, {})
            lbl = risk.get("label", "🟢 Low")
            if lbl in risk_counts:
                risk_counts[lbl] += 1

        pct_active = round(active / n * 100) if n else 0
        rows.append({
            "Coach / Programme": _prog_short(coach),
            "Athletes": n,
            "Active (≤14d)": f"{active} ({pct_active}%)",
            "Needs Contact": inactive,
            "Never Logged": never,
            "Avg Days Since Log": avg_days if avg_days is not None else "—",
            "🔴 Critical": risk_counts["🔴 Critical"],
            "🟡 Elevated": risk_counts["🟡 Elevated"],
            "🟢 Low": risk_counts["🟢 Low"],
        })

    rows.sort(key=lambda x: -(x["Needs Contact"]))
    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

    # Highlight any coach with ≥ 3 critical athletes
    urgent_coaches = [r for r in rows if r["🔴 Critical"] >= 3]
    if urgent_coaches:
        names_str = ", ".join(r["Coach / Programme"] for r in urgent_coaches)
        st.warning(f"⚠️ High churn risk concentration: {names_str}")

    # ── AI weekly coaching brief ───────────────────────────────────────────────
    st.divider()
    st.markdown("**🤖 AI Weekly Coaching Brief**")
    st.caption(
        "Generates a 5-bullet priority brief per coach using Claude. "
        "Click Generate to run — uses your Anthropic API key."
    )

    if "coaching_briefs" not in st.session_state:
        st.session_state.coaching_briefs = {}

    coach_options = sorted({r["Coach / Programme"] for r in rows if r["Coach / Programme"] != "— Unassigned —"})
    sel_brief_coach = st.selectbox(
        "Coach / Programme", ["— select —"] + coach_options, key="brief_coach_sel"
    )

    if sel_brief_coach and sel_brief_coach != "— select —":
        if st.button("Generate Brief", key="gen_brief_btn", type="primary"):
            # Build per-athlete summary lines for this coach
            coach_athlete_names = [
                nm for nm in by_coach.get(sel_brief_coach, [])
            ]
            brief_lines = []
            for nm in coach_athlete_names:
                e = eng_by_name.get(nm, {})
                risk = analytics.churn_risk_score(nm, engagement_results, {}, {})
                days_txt = f"{e['days_since']}d inactive" if e.get("days_since") is not None else "never logged"
                brief_lines.append(
                    f"{nm} — {risk.get('label', '—')} · {days_txt}"
                    + (f" · {e.get('last_logged', '')}" if e.get("last_logged") else "")
                )
            import summariser as _sum
            with st.spinner("Generating brief with Claude…"):
                brief_text = _sum.coaching_brief(sel_brief_coach, brief_lines)
            if brief_text:
                st.session_state.coaching_briefs[sel_brief_coach] = brief_text
            else:
                st.warning("No brief generated — check ANTHROPIC_API_KEY is set.")

        cached_brief = st.session_state.coaching_briefs.get(sel_brief_coach)
        if cached_brief:
            st.markdown(cached_brief)


def _crm_dedup(athletes, data_records, pr_records):
    """Duplicate / alias name detection across data sources."""
    st.markdown("### Duplicate & Alias Detection")
    st.caption("Suspiciously similar names across Benchmarks, _DATA, and PR Log — possible duplicates or aliases")

    with st.spinner("Scanning for similar names…"):
        candidates = analytics.duplicate_candidates(athletes, data_records, pr_records, threshold=0.82)

    if not candidates:
        st.success("✓ No duplicate or alias candidates found (threshold: 82% similarity)")
        return

    st.warning(f"{len(candidates)} suspicious name pairs found")

    threshold_slider = st.slider(
        "Similarity threshold", min_value=0.70, max_value=1.0, value=0.82, step=0.01,
        key="dedup_threshold",
        help="Higher = stricter (fewer results). 0.82 catches one-letter swaps and missing initials.",
    )

    filtered = [c for c in candidates if c["score"] >= threshold_slider]
    if not filtered:
        st.info("No matches at this threshold level.")
        return

    st.caption(f"Showing {len(filtered)} pair(s) at ≥{threshold_slider:.0%} similarity")

    rows = []
    for c in filtered:
        rows.append({
            "Name A": c["name_a"],
            "Name B": c["name_b"],
            "Similarity": f"{c['score']:.1%}",
            "Sources": c["sources"],
        })
    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)

    st.caption(
        "To resolve: manually check these pairs in Google Sheets. "
        "If they are the same person, rename one to match the other — "
        "then re-run the daily sync to merge records."
    )


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

**Risk column** — Composite churn risk score (🔴 Critical / 🟡 Elevated / 🟠 Moderate / 🟢 Low) based on days since last log, declining trends, recovery flags, and time since last coach contact. Use the Risk filter to instantly surface your highest-risk athletes.

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

**Programme Breakdown** — Headcount, active rate (logged in the last 28 days), average days since logging, load spikes, and declining trends per track. Spot which tracks have low engagement or need attention.

**Coach Capacity** — Bespoke athlete count, active athletes, average days since log, and number needing attention per coach. Use this to identify who's stretched and whether any coach has capacity for new clients.

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
        ("🌐 CRM", """
Five views for managing the coach-athlete mapping between the CRM and Fitr. Fitr is the single source of truth for athlete data; the CRM is used for coach-athlete mapping and onboarding.

**🔄 Lifecycle** — Full status table for every bespoke athlete: CRM coach, whether they're syncing, last logged date, and engagement status. Filter by coach or status. This is your "are they still with us?" view.

**🚀 Pipeline** — CRM athletes not yet in Benchmarks (not yet syncing). These will be auto-added when they appear in Fitr chat rooms, or manually via `onboard_bespoke_athletes.py`.

**👥 Rosters** — Per-coach comparison of CRM roster vs. actively syncing athletes. Flags gaps (in CRM but no Fitr data) and surprises (syncing but not in CRM), with an engagement summary per coach.

**⚠️ Discrepancies** — Duplicate CRM entries (same athlete under multiple coaches) and mismatches between CRM coach and the _DATA Programme field. Fix these by updating the CRM or running the backfill script.

**✏️ Bulk Reassign** — Move multiple athletes to a different coach or programme in one action. Filter by current programme, select athletes, pick the new assignment, and click Apply.

The **Sync Status** header at the top shows the last sync run date, new PR results pulled, conversations summarised, and any athletes auto-onboarded.
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


def page_leaderboard(pr_records, athletes):
    """Squad leaderboard — overall composite percentile + per-category ranked tables."""
    lb = analytics.leaderboard_data(pr_records)
    latest = lb["latest"]
    lower_is_better = lb["lower_is_better"]
    category_map = lb["category"]
    all_benchmarks = lb["all_benchmarks"]

    if not all_benchmarks:
        st.info("Not enough data yet — leaderboard requires at least 2 athletes to have logged the same benchmark.")
        return

    # ── helpers ────────────────────────────────────────────────────────────────
    def _ranked(bench):
        """Return [(rank, name, value_str, value_num, date), ...] for one benchmark."""
        rows = [
            (nm, latest[(nm, bench)]["value_str"],
             latest[(nm, bench)]["value_num"],
             latest[(nm, bench)]["date"])
            for nm in lb["athletes"]
            if (nm, bench) in latest
        ]
        rows.sort(key=lambda x: x[2], reverse=not lower_is_better.get(bench, False))
        return [(i + 1,) + r for i, r in enumerate(rows)]

    def _percentile_score(athlete_name):
        """Average percentile across all benchmarks this athlete has logged."""
        percs = []
        for bench in all_benchmarks:
            if (athlete_name, bench) not in latest:
                continue
            ranked = _ranked(bench)
            n = len(ranked)
            if n < 2:
                continue
            pos = next((r[0] for r in ranked if r[1] == athlete_name), None)
            if pos is None:
                continue
            # lower rank = better; percentile = (n - rank) / (n - 1) * 100
            percs.append((n - pos) / (n - 1) * 100)
        return round(sum(percs) / len(percs)) if percs else None

    def _category_benchmarks(cat_name):
        return [b for b in all_benchmarks if category_map.get(b) == cat_name]

    def _render_benchmark_table(bench):
        ranked = _ranked(bench)
        if not ranked:
            st.caption("No data.")
            return
        lib = lower_is_better.get(bench, False)
        st.caption(f"{'Lower is better (time)' if lib else 'Higher is better'} · {len(ranked)} athletes")
        df = pd.DataFrame([{
            "Rank": f"{'🥇' if r[0] == 1 else '🥈' if r[0] == 2 else '🥉' if r[0] == 3 else r[0]}",
            "Athlete": r[1],
            "Result": r[2],
            "Date": r[4].strftime("%d %b %Y"),
        } for r in ranked])
        st.dataframe(df, width='stretch', hide_index=True)

    def _render_category(cat_name):
        benches = _category_benchmarks(cat_name)
        if not benches:
            st.info(f"No {cat_name} benchmarks logged by 2+ athletes yet.")
            return
        sel_bench = st.selectbox(
            "Benchmark", benches, key=f"lb_bench_{cat_name}",
        )
        _render_benchmark_table(sel_bench)

        # Full category grid: rows = athletes, columns = benchmarks
        st.markdown("**Full category overview**")
        st.caption("Latest value per athlete — blanks mean no result logged")
        grid_rows = []
        for nm in lb["athletes"]:
            row = {"Athlete": nm}
            has_any = False
            for b in benches:
                val = latest.get((nm, b))
                row[b] = val["value_str"] if val else "—"
                if val:
                    has_any = True
            if has_any:
                grid_rows.append(row)
        if grid_rows:
            st.dataframe(pd.DataFrame(grid_rows), width='stretch', hide_index=True)

    # ── Tabs ───────────────────────────────────────────────────────────────────
    lb_tabs = st.tabs(["🏆 Overall", "🏋️ Weightlifting", "💪 Strength", "🤸 Gymnastics", "🏃 Conditioning"])

    with lb_tabs[0]:
        st.subheader("Overall Composite Score")
        st.caption("Average percentile across all benchmarks each athlete has logged vs the squad")
        overall_rows = []
        for nm in lb["athletes"]:
            score = _percentile_score(nm)
            if score is None:
                continue
            bench_count = sum(1 for b in all_benchmarks if (nm, b) in latest)
            medal = "🥇" if not overall_rows else ("🥈" if len(overall_rows) == 1 else "🥉" if len(overall_rows) == 2 else "")
            overall_rows.append({"": medal, "Athlete": nm, "Score": score, "Benchmarks": bench_count})
        overall_rows.sort(key=lambda x: -x["Score"])
        for i, r in enumerate(overall_rows):
            r[""] = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else ""
        if overall_rows:
            ov_df = pd.DataFrame(overall_rows)
            bar = (
                alt.Chart(ov_df)
                .mark_bar()
                .encode(
                    x=alt.X("Score:Q", title="Composite Percentile Score", scale=alt.Scale(domain=[0, 100])),
                    y=alt.Y("Athlete:N", sort="-x", title=""),
                    color=alt.Color("Score:Q", scale=alt.Scale(scheme="blues"), legend=None),
                    tooltip=["Athlete:N", "Score:Q", "Benchmarks:Q"],
                )
                .properties(height=max(200, len(overall_rows) * 30))
            )
            st.altair_chart(bar, width='stretch')
            st.dataframe(ov_df, width='stretch', hide_index=True)

        # Benchmark selector for drill-down
        st.divider()
        st.markdown("**Drill into any benchmark**")
        all_bench_sel = st.selectbox("Benchmark", all_benchmarks, key="lb_overall_bench")
        _render_benchmark_table(all_bench_sel)

    with lb_tabs[1]:
        st.subheader("Weightlifting")
        _render_category("Weightlifting")

    with lb_tabs[2]:
        st.subheader("Strength")
        _render_category("Strength")

    with lb_tabs[3]:
        st.subheader("Gymnastics")
        _render_category("Gymnastics")

    with lb_tabs[4]:
        st.subheader("Conditioning")
        _render_category("Conditioning")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if st.query_params.get("mode") == "self_assess":
        page_self_assess()
        return

    if st.query_params.get("mode") == "progress":
        page_progress()
        return

    # ── Sidebar controls ──────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### ⚡ Fitr Messaging")
        fitr_on = st.toggle(
            "Enable sending via Fitr",
            value=st.session_state.get("fitr_messaging_on", False),
            key="fitr_messaging_on",
            help=(
                "When ON, the Send button on AI draft replies posts directly to the athlete "
                "in Fitr. Toggle OFF to use copy-only mode (no messages sent)."
            ),
        )
        if fitr_on:
            st.success("🟢 Fitr messaging ON")
        else:
            st.warning("🔴 Fitr messaging OFF — copy only")

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
        "📈 Trends", "🏆 Leaderboard", "💤 Recovery", "🌐 CRM", "❓ Help",
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
        page_programmes(athletes, pr_records, trend_results, data_records, load_results=load_results, engagement_results=engagement_results)
    with tabs[7]:
        page_load(load_results)
    with tabs[8]:
        page_trends(pr_records, athletes)
    with tabs[9]:
        page_leaderboard(pr_records, athletes)
    with tabs[10]:
        page_recovery(rec_by_name)
    with tabs[11]:
        page_crm(athletes, engagement_results, data_records, pr_records=pr_records)
    with tabs[12]:
        page_help()


if __name__ == "__main__":
    main()
