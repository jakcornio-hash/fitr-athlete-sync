# fitr-athlete-sync — Project Context for Claude

Read this fully before making any changes. It covers what the project does, how it's built, key conventions, and recent changes that aren't obvious from reading the code.

---

## What this is

A Streamlit coaching dashboard + daily sync runner for **JST Compete**, a CrossFit coaching business based in Prestwich, Greater Manchester. The head coach is Jak Cornthwaite; Ed Cook handles athlete communications day-to-day.

The system:
- Pulls athlete training data from the Fitr API into Google Sheets
- Runs analytics (engagement, trends, recovery, competition phases)
- Sends automated Fitr messages and emails to athletes on triggers
- Provides a dashboard for coaches to manage communications and view the squad

**Live deployment:** Streamlit Cloud.
**Daily automation:** GitHub Actions cron at 06:00 UTC (`sync.yml`).
**Main Google Sheet:** `JST Compete — Athlete Profiles` (ID in `config.SHEET_ID`).

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
| `dashboard.py` | Streamlit UI — all 16 tabs and shared helper functions |
| `analytics.py` | Pure analytics functions (no I/O) |
| `notifier.py` | Slack + email notification functions |
| `sheets_client.py` | Google Sheets wrapper (gspread) |
| `fitr_client.py` | Fitr API client (undocumented API, bearer token auth) |
| `config.py` | All env vars and constants |
| `archetypes.py` | 17 athlete archetypes (technician, skeptic, soldier, etc.) |
| `recovery.py` | Recovery survey parsing helpers |
| `summariser.py` | Claude API — chat summaries, draft replies, weekly insights, annual reviews |
| `coaching_voice.py` | JST tone-of-voice rules + Coaching Playbook helpers |
| `message_templates.py` | Outreach message templates keyed by reason type and archetype |
| `setup_coaching_playbook.py` | One-time script to create/populate the Coaching Playbook tab in Sheets |
| `tests/test_analytics.py` | Unit tests for analytics functions |
| `docs/Ed_Dashboard_Guide.md` | Plain-English guide for Ed Cook — what the system does, his daily workflow |

---

## Key technical conventions

- **Streamlit**: use `width='stretch'` (NOT the deprecated `use_container_width=True`). Session state keys must be unique across the whole file.
- **Fitr API**: undocumented. Base URL `https://app.fitr.training`. Bearer token auth. Key endpoints: `GET /api/chat/rooms`, `GET /api/chat/messages`, `POST /api/chat/messages` (send), `GET /api/coach/benchmarks`.
- **Google Sheets**: `gspread`. `get_all_records()` returns empty strings for missing cells — always use `.get("Key", "")` not `.get("Key")`.
- **Altair charts**: already imported as `alt` in dashboard.py.
- **Pandas**: already imported as `pd` in dashboard.py.

---

## Bespoke athlete handling — critical

Athletes with `Subscription Plan == "Bespoke"` in the `_DATA` tab receive **zero** automated messages. This is non-negotiable — they pay for personal coaching, not system messages.

**How it works in sync.py:**
```python
bespoke_names = {
    nm for nm, r in data_by_name_all.items()
    if str(r.get("Subscription Plan", "")).strip().lower() == "bespoke"
}
```

Built after `programme_by_name` in `main()`. Passed as a parameter to `auto_onboard_new_athletes()` and checked with `if nm in bespoke_names: continue` before every automated message send (onboarding, congrats, offboarding, anniversary, comp messages, post-comp, etc.).

Email filtering also excludes bespoke athletes:
```python
non_bespoke_email_by_name = {k: v for k, v in email_by_name.items() if k not in bespoke_names}
```

**If you add a new automated message type, always add a bespoke guard.**

---

## Automated messages — all types

All messages are sent via `fitr.send_chat_message(room_id, msg)` and logged to the Message Log tab. None include a personal sign-off ("Jak" / "Ed") — they are system-generated, not personally authored.

| Type | When it fires | Location in sync.py |
|------|--------------|---------------------|
| Intake onboarding | When CRM athlete is added to Fitr for the first time | `auto_onboard_new_athletes()` ~line 700 |
| New PB / first result | When a new benchmark result is logged | Congrats loop ~line 1039 |
| North Star goal achieved | When logged result matches athlete's stated goal | Same congrats loop |
| First log | When `first_log_by_name[nm] == TODAY` | New athlete onboarding loop ~line 1283 |
| Training anniversary | At 90/180/270/365/730 days | Anniversary loop ~line 1254 |
| 60-day inactive | `days_since == 60` | Off-boarding loop ~line 1163 |
| Pre-comp (10 weeks) | 70 days before A-comp | `_COMP_MSG_DAYS` loop ~line 1305 |
| Pre-comp (3 weeks) | 21 days before A or B comp | Same |
| Pre-comp (race week) | 7 days before A or B comp | Same |
| Pre-comp (day before) | 1 day before A-comp | Same |
| Post-comp A/B | Day after A or B comp | Post-comp loop ~line 1413 |
| Post-comp C | Day after C comp | Same loop, shorter message |
| Comp result congrats | When a Result is entered for a comp within 14 days | Comp result loop ~line 1467 |

**Tone rules for all automated messages:**
- Start with "Hey [First name]," — no emoji, no exclamation mark
- No emojis anywhere in the message
- No numbered emoji lists (1️⃣ 2️⃣) — use plain numbers (1. 2.) if listing
- No banned phrases: "Let's get to work", "Let's go!", "unlock", "elevate", "transform", "journey", "embark", "Really looking forward to...", "Moreover", "Furthermore"
- UK spelling: programme, practising, dialled, prioritising
- Contractions throughout: you're, don't, we've
- No personal sign-off (messages are system-generated, not from Jak personally)

---

## coaching_voice.py

New module (added Round 11). Single source of truth for JST tone rules.

```python
coaching_voice.VOICE_PROMPT       # string — inject into Claude prompts for athlete-facing generation
coaching_voice.SCENARIOS          # list of valid scenario tag values for the Coaching Playbook
coaching_voice.get_voice_prompt() # returns VOICE_PROMPT
coaching_voice.load_playbook(sheets, scenario=None, limit=10)  # → list of dicts
coaching_voice.playbook_context(sheets, scenario)              # → formatted string for prompt injection
```

`summariser.py` imports `coaching_voice` and prepends `VOICE_PROMPT` to `_REPLY_SYSTEM` (draft replies) and `_ANNUAL_REVIEW_SYSTEM` (annual reviews). Coach-facing prompts (`_SYSTEM`, `_BRIEF_SYSTEM`, `_WEEKLY_INSIGHT_SYSTEM`) do not use it — they're not athlete-facing.

---

## Coaching Playbook (Google Sheet tab)

Tab name: `"Coaching Playbook"`. Columns: `Scenario | Subject | Notes | Example | Source`.

- Pre-populated with 31 entries covering: coaching method, mindset, missed sessions, competition scenarios, weightlifting cues (snatch/clean/jerk), gymnastics cues, conditioning, recovery flags, athlete avatars (Grit/Grunt/Grime).
- `Scenario` values must match `coaching_voice.SCENARIOS` to be filterable in the dashboard.
- Coaches add rows via the Playbook tab form in the dashboard, or directly in Google Sheets.
- `setup_coaching_playbook.py` clears and repopulates the tab if run again.

---

## summariser.py — key functions

```python
summarise_conversation(athlete_name, messages_text, activity_date=None)
    # → str | None — 2-4 sentence coaching-relevant summary; None if SKIP
    # Coach-facing. Does not use VOICE_PROMPT.

draft_reply(athlete_name, thread_text, profile_data=None)
    # → str | None — draft Fitr DM reply to athlete's last message
    # Injects VOICE_PROMPT. Returns None if SKIP.
    # profile_data keys: Programme, North Star Goal, Tier, Injury Status

coaching_brief(coach_name, athlete_lines)
    # → str | None — 5-bullet weekly brief for a coach

weekly_athlete_insight(athlete_name, pr_lines, rec_lines, goal, programme)
    # → str | None — 2-3 sentence insight for the coach dashboard

analyse_competition_result(athlete_name, comp_name, result, post_comp_response, pr_lines, programme, goal)
    # → str | None — coaching analysis of comp result + reflection

annual_athlete_review(athlete_name, months_training, pr_summary, comp_summary, goal, programme)
    # → str | None — personalised annual review for athlete (email)
    # Injects VOICE_PROMPT.
```

---

## config.py — key constants

```python
SHEET_ID                     # main Google Sheet ID
GOOGLE_SERVICE_ACCOUNT_FILE  # path to service_account.json
CRM_SHEET_ID                 # CRM sheet ID
COMP_FORM_SHEET_ID           # Typeform comp responses sheet ID
INTAKE_FORM_SHEET_ID         # Typeform intake responses sheet ID
RECOVERY_SHEET_ID            # Recovery survey sheet ID

TAB_PR_LOG, TAB_BENCHMARKS, TAB_DATA, TAB_RECOVERY, TAB_SYNC_LOG,
TAB_COACH_ALERTS, TAB_COACHES, TAB_COMPETITIONS, TAB_CHURN_HISTORY,
TAB_MESSAGE_LOG, TAB_DRAFT_REPLIES

JST_TRACKS          # list of programme names
ANTHROPIC_MODEL     # Claude model ID for API calls
DRY_RUN             # bool — skips all Fitr sends and Sheet writes when True
TEST_ATHLETES       # list — only these athletes get emails/messages in test runs
```

---

## Analytics functions (analytics.py)

| Function | Returns |
|----------|---------|
| `trend_analysis(pr_records)` | `{athlete: [{benchmark, trend, trend_pct, peak_drop_flag, peak_drop_pct, ...}]}` |
| `engagement_check(pr_records, athletes, ...)` | `[{name, days_since, last_logged, flag, nudge_flag, last_contact}]` |
| `churn_risk_score(name, engagement, trends, rec, last_contact)` | `{score, label, factors}` — label: "🔴 Critical" (≥60), "🟡 Elevated" (≥35), "🟠 Moderate" (≥15), "🟢 Low" |
| `milestone_detection(bench_rows)` | `[(name, bench, value, prev)]` |
| `consistency_check(pr_records, athletes, min_weeks)` | `[(name, weeks)]` |
| `comp_phase(days_out, comp_type)` | `(phase_str, action_str)` |
| `comp_schedule(athletes, data_records, competition_rows, today)` | list of upcoming comp dicts |
| `leaderboard_data(pr_records)` | `{latest, all_benchmarks, athletes, lower_is_better, category}` |
| `load_analysis(pr_records, rec_by_name, data_records)` | `{athlete: {acwr, status, acute, chronic, soreness, stress, programme}}` |
| `recovery_alerts(rec_by_name)` | `[[name, issue, submitted_date]]` |
| `training_load(pr_records, weeks=12)` | `{athlete: [{week, week_start, sessions}]}` — unique logging days per ISO week |
| `duplicate_candidates(athletes, data_records, pr_records, threshold=0.82)` | fuzzy name pairs |

---

## sheets_client.py — key methods

```python
read_records(tab)           # → [dict, ...]
read_values(tab)            # → [[row], ...]
read_external_records(sheet_id, tab)
append_rows(tab, rows)
overwrite_tab(tab, rows)
update_cells_by_rowmap(tab, col_letter, {row_num: value})
batch_update_by_name(tab, key_col, {name: {col: val}})
get_or_create(tab, headers)

load_competitions()         # → [{Athlete Name, Competition Name, Date, Type, Notes, Result}]
save_competition(row)
update_competition_result(athlete_name, comp_name, result_text)
load_coaches()              # → {programme: slack_channel_id}
load_archetype_assessments()
write_archetype_assessment(row)
write_churn_snapshot(rows)
load_churn_history()
log_messages(rows)          # → write to Message Log tab
load_pending_messages(max_age_days=4)
mark_message_replied(athlete_name, message_type, sent_date_str, reply_date_str)
write_draft_reply(athlete_name, room_id, draft_text)
load_draft_replies()
clear_draft_reply(athlete_name)
load_intake_responses()
```

---

## dashboard.py — tab structure (17 tabs)

```
Tab  0:  ✅ Actions        — Ed's daily checkable task queue (page_action_list)
Tab  1:  📋 Outreach List  — Full overview table + export (page_outreach)
Tab  2:  🚨 Alerts         — Aggregate flags view (page_alerts)
Tab  3:  🃏 Squad          — Card grid by status (page_squad)
Tab  4:  👥 Athletes       — Full roster + per-athlete profile panel (page_athletes)
Tab  5:  🗓️ Week Plan       — Prioritised coaching week (page_week_planner)
Tab  6:  🏁 Competitions   — Squad comp schedule + calendar (page_competitions)
Tab  7:  📊 Programmes     — Track breakdown + coach capacity (page_programmes)
Tab  8:  🏋️ Load           — ACWR proxy training load (page_load)
Tab  9:  📈 Trends         — Per-athlete benchmark charts (page_trends)
Tab 10:  🏆 Leaderboard    — Composite percentile rankings (page_leaderboard)
Tab 11:  💤 Recovery       — Latest recovery survey table (page_recovery)
Tab 12:  🌐 CRM            — Coach-athlete mapping (page_crm)
Tab 13:  📚 Playbook       — Coaching reference hub (page_coaching_playbook)
Tab 14:  💎 Grandslam      — Retention pipeline + whale board (page_grandslam)
Tab 15:  ⚙️ Sync           — Sync health + message log (page_sync_health)
Tab 16:  ❓ Help           — Guide for coaches (page_help)
```

### page_action_list — Ed's daily task list

Calls `_build_outreach_rows()` (shared with `page_outreach`), filters to non-auto-sent items (`ed_rows`), and renders each as a card with:
- Pre-drafted message in an editable text area
- Email / WhatsApp / Fitr send buttons
- "Mark Done" button that removes the item from the active queue for the session

Done state stored in `st.session_state` keyed by:
```python
def _item_key(r):
    raw = f"{r['Athlete']}|{r['Priority']}|{r['Reason']}"
    return "action_done_" + hashlib.md5(raw.encode()).hexdigest()[:10]
```
Resets each browser session. Undo button available in the Completed section.

### _build_outreach_rows() — shared helper

`_build_outreach_rows(engagement_results, trend_results, rec_alert_rows, milestones, consistency_wins, comp_results, data_records)` → list of row dicts.

Both `page_action_list` and `page_outreach` call this. Row dicts have keys:
`Priority, Athlete, Reason, Action, _order, _reason_type, _ctx, _programme, _auto, _comp_msg (optional)`.

`_auto = True` for priorities in `{"🏆 Celebrate", "🟣 Post-Comp", "🏁 Comp Prep", "⚠️ Re-engage"}` — shown in Outreach List's "Auto-sent by system" section; excluded from Ed's Actions queue.

### Fitr send buttons in dashboard

Two helpers used throughout:
```python
_outreach_send_buttons(msg)         # renders Email + WhatsApp link buttons
_fitr_send_widget(name, msg, idx=0) # per-athlete Fitr send toggle + button
```

`_fitr_send_widget` only renders if the global `fitr_messaging_on` session_state toggle is True (set in sidebar). Also requires a per-athlete toggle keyed `f"fitr_allow_outreach_{name}"` to prevent accidental sends.

### page_coaching_playbook

Reads `"Coaching Playbook"` tab. Filters by scenario dropdown and free-text search. Each entry is an expander with Subject, Notes, Example, and Source. Add-entry form appends via `ws.append_row()`.

---

## sync.py — main() execution order

1. Authenticate Fitr + connect Sheets
2. Load athletes from Benchmarks tab
3. Pull new benchmark results (`collect_benchmarks`)
4. Pull challenge scores (`collect_challenges`)
5. AI-summarise Fitr chat threads (`collect_chat_summaries`) — returns `pending_reply_candidates`
6. Build `room_id_by_name`
7. Update athlete profiles in `_DATA` from Fitr
8. Merge recovery survey responses
9. Sync programme from recovery survey
10. Sync competition calendar from Typeform (`sync_competition_from_typeform`)
11. Sync intake form responses (`sync_intake_from_typeform`)
12. Write AI draft replies to Draft Replies tab
13. **Send new-PR congrats** via Fitr (skips bespoke athletes)
14. Write new rows to PR Log
15. Per-coach Slack notifications
16. Write coaching notes to `_DATA`
17. Update Last Scraped in Benchmarks
18. Run analytics (trend, engagement, milestones, consistency, recovery alerts)
19. Write Coach Alerts tab
20. Write daily churn risk snapshot
21. **Send 60-day inactive check-in** (only if `days_since == 60`; skips bespoke)
22. Send Slack digest + progress emails
23. Per-coach re-engagement alerts + weekly squad summaries
24. **Build `bespoke_names` set** from `_DATA` Subscription Plan column
25. `auto_onboard_new_athletes()` — CRM → Benchmarks + Fitr welcome message (skips bespoke)
26. **Send training anniversary messages** (skips bespoke)
27. **Send first-log onboarding messages** (skips bespoke)
28. **Send pre-competition messages** (`_COMP_MSG_DAYS`)
29. **Send competition result congrats** (deduped via Message Log)
30. Send weekly progress emails (excludes bespoke)
31. Monthly reports if `TODAY.day == 1` (excludes bespoke)
32. Write `messages_sent_log` to Message Log tab
33. Scan Fitr chat for replies to pending messages
34. Write Sync Log row

---

## Pre-competition message rules

`_COMP_MSG_DAYS = {70: ..., 21: ..., 7: ..., 1: ...}` in sync.py.

- 70d and 1d: A-comps only
- 21d and 7d: A and B comps
- C-comps: no automated messages at any distance
- Exact day match only (daily sync ensures once-only delivery)

---

## Typeform / fuzzy name matching

`sync_competition_from_typeform()` maps raw athlete names from Typeform to known athletes using `difflib.get_close_matches`. **Cutoff is 0.6** (lowered from 0.75 to catch near-matches). If no match is found, the row is skipped.

Unmatched names from Typeform are not yet surfaced in the dashboard — a planned improvement is to show them in the Slack digest.

---

## _fmt_value — time benchmark formatting

`_fmt_value(v)` in sync.py converts Fitr `last_value` dicts to human-readable strings. Fitr returns time benchmarks with `symbol='mm:ss'` and `units='second'` (value in seconds as int). Converts to `M:SS` or `H:MM:SS`.

```python
is_time = sym.lower() in ("secs", "sec", "seconds", "s", "mm:ss") or units in ("second", "seconds")
if is_time:
    total = int(round(val))
    mins, secs = divmod(total, 60)
    if mins >= 60:
        hrs, mins = divmod(mins, 60)
        return f"{hrs}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"
```

A backfill script corrected 1,953 existing PR Log rows that were stored as `"1200 mm:ss"` instead of `"20:00"`.

---

## notifier.py — key functions

```python
send_digest(date, engagement_results, trend_results, rec_alert_rows, milestones, consistency_wins)
send_coach_notifications(bench_rows, chal_rows, programme_by_name, coach_channel_map)
send_reengagement_alerts(engagement_results, programme_by_name, coach_channel_map)
send_weekly_coach_summary(athletes_by_coach, engagement_results, trend_results, milestones_by_name, coach_channel_map)
send_all_athlete_progress_emails(bench_rows, consistency_wins, competition_rows, email_by_name, archetype_by_name=None)
send_monthly_athlete_reports(data_recs, email_by_name, pr_records)  # fires if TODAY.day == 1
```

---

## Fitr API client (fitr_client.py)

```python
fitr = FitrClient(email, password, client_id, client_secret, access_token)
fitr.get_athletes()                          # → [{id, name, email, programme, ...}]
fitr.get_benchmarks(athlete_id)              # → [{benchmark, last_value, history}]
fitr.get_chat_rooms()                        # → [{id, athlete_name, ...}]
fitr.get_chat_messages(room_id, limit=50)    # → [{sender, text, created_at}]
fitr.send_chat_message(room_id, text)        # → bool (True if sent)
fitr.get_athlete_profile(athlete_id)         # → {name, email, programme, joined_at, ...}
```

Auth: bearer token stored in `config.FITR_ACCESS_TOKEN`. If it expires, re-auth via email/password + client credentials.

---

## GitHub Actions

`.github/workflows/sync.yml` — `python sync.py` daily at 06:00 UTC.
On failure: posts to `SLACK_WEBHOOK_URL`.

Required GitHub secrets:
`SHEET_ID, GOOGLE_SERVICE_ACCOUNT_JSON, FITR_ACCESS_TOKEN, FITR_EMAIL, FITR_PASSWORD, FITR_CLIENT_ID, FITR_CLIENT_SECRET, ANTHROPIC_API_KEY, SLACK_WEBHOOK_URL, SLACK_BOT_TOKEN, SMTP_FROM, SMTP_PASSWORD, SMTP_TO, RECOVERY_SHEET_ID, COMP_FORM_SHEET_ID, CRM_SHEET_ID`

---

## Recovery survey

Weekly submission via Typeform. Captures soreness, stress, and motivation (1–10). Purpose: load management — gives coaches a weekly signal on under-recovery before making training decisions. Flags soreness/stress ≥ 7 as red, ≥ 5 as amber in the dashboard. Athletes with red flags appear at the top of Ed's Actions queue as "🔴 Contact Today".

---

## What changed in recent sessions (Rounds 10–11)

**Round 10:**
- Competition result loop (Fitr congrats when result logged for recent comp)
- Per-athlete Fitr send buttons in the dashboard (`_fitr_send_widget`)
- Recovery pattern insights in athlete profiles

**Round 11:**
- `coaching_voice.py` — tone rules module, `VOICE_PROMPT`, Coaching Playbook helpers
- All 9 automated Fitr message types rewritten: no emojis, no sign-offs, no banned phrases, UK spelling
- `summariser.py` — `VOICE_PROMPT` injected into `_REPLY_SYSTEM` and `_ANNUAL_REVIEW_SYSTEM`
- `setup_coaching_playbook.py` — created Coaching Playbook tab with 31 pre-populated entries
- Dashboard: new `📚 Playbook` tab (search, add-entry form, tone quick reference)
- Dashboard: new `✅ Actions` tab (first tab) — Ed's checkable daily task queue
- `_build_outreach_rows()` extracted as shared helper (removes duplication between Actions and Outreach List)
- Bespoke athlete suppression added to all remaining message types that were missing it
- Fuzzy name match cutoff lowered 0.75 → 0.6 for Typeform sync
- `_fmt_value` fixed to convert seconds → `M:SS` for time benchmarks
- PR Log backfill: 1,953 rows corrected from `"1200 mm:ss"` → `"20:00"` format
- `docs/Ed_Dashboard_Guide.md` — plain-English onboarding guide for Ed Cook

---

## Known issues / planned improvements

- Unmatched Typeform submissions not yet surfaced in Slack digest or dashboard
- SMTP from `enquiries@jstcompete.com` intermittently returns 535 errors — MailerLite considered as alternative
- Coaching Playbook needs more entries for comp-specific scenarios and programme links (Grit/Grunt/Grime URLs)
