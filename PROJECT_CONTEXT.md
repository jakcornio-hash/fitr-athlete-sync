# fitr-athlete-sync — Project Context

This document brings you up to speed on what has already been built. Read it fully before making any changes.

---

## What this is

A Streamlit coaching dashboard + daily sync runner for **JST Compete**, a CrossFit coaching business. It pulls athlete data from the Fitr training platform API into Google Sheets, runs analytics, and automates coach notifications and athlete messaging.

**Repo location:** the working directory for the project.

**Live deployment:** Streamlit Cloud (secrets stored in the Streamlit Cloud secrets panel).  
**Daily automation:** GitHub Actions cron at 06:00 UTC (`sync.yml`).

---

## Security rules — never violate these

- Never paste tokens, API keys, or credentials in code or chat
- Slack bot tokens and service account keys live only in Streamlit Cloud secrets and GitHub Actions secrets
- A service account key was previously exposed (rotated). A GitHub PAT was previously embedded in the git remote (deleted).

---

## File overview

| File | Purpose |
|------|---------|
| `sync.py` | Daily runner — pulls Fitr data, writes to Sheets, fires all automated messages |
| `dashboard.py` | Streamlit UI — all pages and components |
| `analytics.py` | Pure analytics functions (no I/O) |
| `notifier.py` | Slack + email notification functions |
| `sheets_client.py` | Google Sheets wrapper (gspread) |
| `fitr_client.py` | Fitr API client (undocumented API, bearer token auth) |
| `config.py` | All env vars and constants |
| `archetypes.py` | 17 athlete archetypes (technician, skeptic, soldier, etc.) |
| `recovery.py` | Recovery survey parsing helpers |
| `summariser.py` | Claude API — summarises Fitr chat threads |
| `tests/test_analytics.py` | 17 unit tests (all passing) |

---

## Key technical patterns

- **Streamlit**: use `width='stretch'` (NOT the deprecated `use_container_width=True`)
- **Fitr API**: undocumented. Base URL `https://app.fitr.training`. Bearer token auth. `GET/POST /api/chat/messages` for reading/sending athlete messages.
- **Google Sheets**: `gspread`. `get_all_records()` safely returns empty strings for missing columns.
- **SMTP email**: Gmail via `SMTP_FROM` / `SMTP_PASSWORD` config vars.
- **Altair charts**: already imported as `alt` in dashboard.py.
- **Pandas**: already imported as `pd` in dashboard.py.

---

## config.py — key constants

```python
TAB_PR_LOG, TAB_BENCHMARKS, TAB_DATA, TAB_RECOVERY, TAB_SYNC_LOG,
TAB_COACH_ALERTS, TAB_COACHES, TAB_COMPETITIONS, TAB_CHURN_HISTORY,
TAB_MESSAGE_LOG

JST_TRACKS   # list of programme names
JST_TIERS    # ["Open", "Quarterfinals", "Semifinals", "Games"]

SUBSCRIPTION_PRICES = {
    "Bespoke": 300,
    "JST Athlete": 97,
    "Strength Bias": 97,
    "Engine Bias": 97,
    "Gymnastics Bias": 97,
    "Competition Ready": 97,
}

DRY_RUN      # set via env — skips all writes/messages
```

---

## Analytics functions (analytics.py)

| Function | Returns |
|----------|---------|
| `trend_analysis(pr_records)` | `{athlete: [{benchmark, trend, trend_pct, peak_drop_flag, ...}]}` |
| `engagement_check(pr_records, athletes, ...)` | `[{name, days_since, last_logged, flag, nudge_flag}]` |
| `churn_risk_score(name, engagement_results, trend_results, rec_by_name, last_contact_by_name)` | `{"score": int, "label": str, "factors": [str]}` — label is "🔴 Critical" (≥60), "🟡 Elevated" (≥35), "🟠 Moderate" (≥15), "🟢 Low" |
| `milestone_detection(bench_rows)` | `[(name, bench, value, prev)]` |
| `consistency_check(pr_records, athletes, min_consecutive_weeks)` | `[(name, weeks)]` |
| `comp_phase(days_out, comp_type)` | `(phase_str, action_str)` |
| `comp_message(name, comp_label, days_out, comp_type)` | message string |
| `comp_schedule(athletes, data_records, competition_rows, today)` | upcoming comps |
| `leaderboard_data(pr_records)` | `{latest, all_benchmarks, athletes, lower_is_better, category}` |
| `session_compliance(pr_records, data_records, weeks)` | `{athlete: {actual, expected, pct, label}}` |
| `pr_velocity(pr_records, min_points)` | `{athlete: [{benchmark, rate_pct_per_month, ...}]}` |
| `cohort_retention(pr_records, min_cohort_size)` | `[{cohort, n, pct_30d, pct_60d, pct_90d}]` |
| `programme_peer_comparison(name, programme, pr_records, data_records)` | `[{benchmark, athlete_value, percentile, peer_median, peer_count, direction}]` |
| `recovery_alerts(rec_by_name)` | `[[name, issue, submitted_date]]` |
| `build_coach_alerts_rows(...)` | rows for Coach Alerts tab |
| `coach_capacity(athletes, pr_records, data_records, engagement_results, bespoke_coaches)` | per-coach summary |
| `training_load(pr_records, weeks=12)` | `{athlete: [{"week": "YYYY-WW", "week_start": "YYYY-MM-DD", "sessions": int}]}` — unique logging days per ISO week |
| `duplicate_candidates(athletes, data_records, pr_records, threshold=0.82)` | `[{"name_a", "name_b", "score", "sources"}]` — fuzzy name pairs sorted by score desc |

---

## sheets_client.py — key methods

```python
# Standard reads
read_records(tab)           # → [dict, ...]
read_values(tab)            # → [[row], ...]
read_external_records(sheet_id, tab)

# Writes
append_rows(tab, rows)
overwrite_tab(tab, rows)
update_cells_by_rowmap(tab, col_letter, {row_num: value})
batch_update_by_name(tab, key_col, {name: {col: val}})
get_or_create(tab, headers)  # → worksheet

# Domain-specific
load_competitions()          # → [{Athlete Name, Competition Name, Date, Type, Notes, Result}]
save_competition(row)
update_competition_result(athlete_name, comp_name, result_text)
load_coaches()               # → {programme: slack_channel_id}
load_archetype_assessments() # → [{Athlete Name, Primary Archetype, Profile JSON, ...}]
write_archetype_assessment(row)

# Churn History tab
write_churn_snapshot(rows)   # rows: [{Date, Athlete Name, Score, Label, Factors}]
load_churn_history()         # → [{Date, Athlete Name, Score, Label, Factors}]

# Message Log tab
log_messages(rows)           # rows: [{Date, Athlete Name, Message Type, Room ID}]
load_pending_messages(max_age_days=4)   # → un-replied rows within age window
mark_message_replied(athlete_name, message_type, sent_date_str, reply_date_str)

# Draft Replies tab (TAB_DRAFT_REPLIES)
write_draft_reply(athlete_name, room_id, draft_text)  # upsert
load_draft_replies()         # → non-cleared rows [{Date, Athlete Name, Room ID, Draft Reply}]
clear_draft_reply(athlete_name)  # marks Cleared=Yes

# Intake form
load_intake_responses()      # → records from INTAKE_FORM_SHEET_ID / INTAKE_FORM_TAB
```

---

## fitr_client.py — key methods

```python
authenticate()
benchmarks(fitr_id)          # → [{name, last_value: {date, value, symbol, note}}]
challenges(pages)
challenge_scores(challenge_id)
challenge_comments(challenge_id)
chat_rooms()                 # → [{id, opponent: {id, full_name}, last_message_date, ...}]
chat_messages(room_id, max_messages)
send_chat_message(room_id, text)   # POST to undocumented /api/chat/messages
```

---

## notifier.py — key functions

```python
send_digest(date, engagement_results, trend_results, rec_alert_rows, milestones, consistency_wins)
send_coach_notifications(bench_rows, chal_rows, programme_by_name, coach_channel_map)
send_reengagement_alerts(engagement_results, programme_by_name, coach_channel_map)
send_weekly_coach_summary(athletes_by_coach, engagement_results, trend_results, milestones_by_name, coach_channel_map)
send_all_athlete_progress_emails(bench_rows, consistency_wins, competition_rows, email_by_name, archetype_by_name=None)
send_monthly_athlete_reports(data_recs, email_by_name, pr_records)  # fires on TODAY.day == 1; sends HTML email with branded header, sessions/results, goal section
```

### summariser.py — key functions

```python
summarise_conversation(athlete_name, messages_text, activity_date=None)  # → str | None
draft_reply(athlete_name, thread_text, profile_data=None)               # → str | None; returns None if SKIP
```

`draft_reply` uses `_REPLY_SYSTEM` prompt tuned for CrossFit coaching. Returns `None` if the last message in the thread is from the coach or there's nothing actionable. `profile_data` accepts keys: Programme, North Star Goal, Tier, Injury Status.

---

## sync.py — what main() does, in order

1. Authenticate Fitr + connect Sheets
2. Load athletes from Benchmarks tab
3. `collect_benchmarks()` — new PR rows since LOOKBACK_DAYS
4. `collect_challenges()` — new challenge scores
5. `collect_chat_summaries()` — AI-summarise recent Fitr conversations; also returns `pending_reply_candidates` dict of athletes whose last message was from them
6. Build `room_id_by_name` dict (Fitr chat room ID per athlete name)
7. `update_athlete_profiles()` — write Email/Age from Fitr into _DATA
8. Merge recovery survey responses
9. `sync_programme_from_recovery()` — update Programme in _DATA from survey
10. `sync_competition_from_typeform()` — sync comp calendar from Typeform
11. **Intake form sync** — `sync_intake_from_typeform()` maps Typeform onboarding responses into _DATA columns
12. **AI draft replies** — for each `pending_reply_candidates`, call `summariser.draft_reply()` and write to Draft Replies tab via `sheets.write_draft_reply()`
13. **Auto-congratulations** — send Fitr message on new PR (logs to messages_sent_log)
14. Write new rows to PR Log
15. Per-coach Slack notifications
16. Write coaching notes to _DATA
17. Update Last Scraped in Benchmarks
18. Run analytics (trend, engagement, milestones, consistency, recovery alerts)
19. Write Coach Alerts tab
20. **Churn risk snapshot** — write daily {athlete: score/label/factors} to Churn History tab
21. **Off-boarding** — send Fitr message if `days_since == 60 AND churn score >= 60`
22. Send digest (Slack + email)
23. Per-coach re-engagement alerts + weekly squad summaries
24. `auto_onboard_new_athletes()` — add CRM-matched athletes to Benchmarks + _DATA
25. **Athlete anniversaries** — send Fitr message at 90/180/270/365/730 days (logs to messages_sent_log)
26. **New athlete onboarding** — send Fitr welcome if first_log == TODAY (logs to messages_sent_log)
27. **Pre-competition messages** — send Fitr message at 70/21/7/1 days before comp (logs to messages_sent_log)
28. **Competition result auto-congratulations** — send Fitr message when a result is logged for a past comp (deduped via Message Log)
29. Weekly athlete progress emails (with archetype tips)
30. **Monthly reports** — if `TODAY.day == 1`, HTML email of previous month's summary to each active athlete
31. **Log automated messages** — write messages_sent_log to Message Log tab
32. **Check for replies** — scan Fitr chat for replies to pending messages, mark in Message Log
33. Write Sync Log row

---

## dashboard.py — tab structure

```
Tab 0:  📊 Overview
Tab 1:  👤 Athletes       (table + per-athlete profile panel)
Tab 2:  📈 Trends
Tab 3:  💤 Recovery
Tab 4:  📅 Competitions
Tab 5:  📋 Programmes
Tab 6:  📣 Outreach
Tab 7:  🔄 Load
Tab 8:  👥 Squad
Tab 9:  📆 Week Planner
Tab 10: 🏆 Leaderboard    (Overall + Weightlifting/Strength/Gymnastics/Conditioning sub-tabs)
Tab 11: ❓ Help
Tab 12: 🔗 CRM            (Lifecycle/Pipeline/Rosters/Discrepancies/Bulk Reassign/Revenue/Msg Effectiveness)
```

### Athlete profile panel sections (in order)

1. Header (name, age, tier, email)
2. Two-column stats (physical + athlete profile)
3. Profile completeness indicator
4. Competition calendar (table, log result form, phase panel, add comp form)
5. Benchmark snapshots
6. Latest recovery survey
7. Archetype panel (profile mix, coach cues, forced-choice assessment form, self-assessment link, **progress page share link**)
8. Coaching notes timeline + Add Note form (kinds: note/chat/result/recovery/**goal**)
9. **Goal Progress** — North Star Goal + goal-tagged notes
10. Programme peer comparison
11. **Churn Risk History** — sparkline from Churn History tab (shown if ≥3 data points)
12. **Weekly Training Load chart** — 12-week bar chart of unique logging days (`analytics.training_load`)
13. **AI Draft Reply panel** — Claude-drafted reply to latest athlete message, with "Mark as Done" button
14. Full Journey Timeline (collapsible)

### CRM tab sections

- **🔄 Lifecycle / 🚀 Pipeline / 👥 Rosters / ⚠️ Discrepancies / ✏️ Bulk Reassign**: existing
- **💰 Revenue**: MRR by plan, total MRR, avg per athlete, bar chart
- **📨 Msg Effectiveness**: reply rate by automated message type, bar chart
- **📊 Coach Stats**: per-coach squad size, active %, avg days since log, churn risk distribution
- **🔍 Duplicates**: fuzzy name pair detection using `analytics.duplicate_candidates()` with adjustable threshold slider

### Athlete-facing pages (no auth)

- `?mode=self_assess&id=JST_ID` — forced-choice archetype assessment (existing)
- `?mode=progress&id=JST_ID` — read-only progress view: goal, recent results, training load chart, competition calendar, programme/tier

---

## Pre-competition message rules

- `_COMP_MSG_DAYS = {70: ..., 21: ..., 7: ..., 1: ...}`
- A-comps only: 70d and 1d messages
- A + B comps: 21d and 7d messages
- C comps: no automated messages
- Exact day match (daily sync ensures exactly-once delivery)

---

## Archetype system

17 archetypes in `archetypes.py`. Key field used in emails: `arch["coach"]["programming_read"]`.  
Assessment stored in Sheets via `write_archetype_assessment()`. Primary archetype ID (e.g. `"technician"`) stored in "Primary Archetype" column.

---

## GitHub Actions

`.github/workflows/sync.yml` — runs `python sync.py` daily at 06:00 UTC.  
On failure: posts to `SLACK_WEBHOOK_URL`.  
Required secrets: `SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `FITR_ACCESS_TOKEN`, `FITR_EMAIL`, `FITR_PASSWORD`, `FITR_CLIENT_ID`, `FITR_CLIENT_SECRET`, `ANTHROPIC_API_KEY`, `SLACK_WEBHOOK_URL`, `SLACK_BOT_TOKEN`, `SMTP_FROM`, `SMTP_PASSWORD`, `SMTP_TO`, `RECOVERY_SHEET_ID`, `COMP_FORM_SHEET_ID`, `CRM_SHEET_ID`.

---

## Recovery survey link (included in onboarding message)

`https://jstcompete.typeform.com/to/Q1tL7MmR`
