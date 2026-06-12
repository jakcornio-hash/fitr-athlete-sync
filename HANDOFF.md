# HAND-OFF BRIEF (read me first)

This project was scaffolded in Claude Cowork. The code is written; what's left
is wiring in credentials and testing against the live accounts — which has to
happen on a machine that holds the secrets. Open this folder in **Claude Code
(desktop app → Code tab)** and work through the steps below. Most of it Claude
Code can do for you; the parts only *you* can do are flagged 🧑 YOU.

The goal: a `python sync.py` that updates the JST Compete — Athlete Profiles
sheet, then the same thing running automatically every week.

---

## Context Claude Code needs

- **Target sheet:** `JST Compete — Athlete Profiles`, ID `1Fx4r3IrYeytoysX_hkarZ5I5SoQdq3ZSPZkS0Y2yDOc`.
- **Tabs used:** `Benchmarks` (athlete → Fitr ID map, col C = Fitr ID, col E = Last Scraped),
  `PR Log` (append target), `_DATA` (has `Coaching Notes` column), optional `Recovery`,
  and `Sync Log` (auto-created).
- **Fitr API** (private, captured from the live coach app — may change):
  - `GET /api/coach/benchmarks?user_id={id}&page={n}` → benchmarks incl. `last_value.{date,value,symbol,note}`
  - `GET /api/score?kind=challenge&page={n}&per_page=15&q[s]=created_at desc` → challenge list
  - `GET /api/score/{id}/items?per_page=25&page={n}&sort=asc&order=best` → per-athlete scores
  - `GET /api/score/comments/?resource_type=score/score&resource_id={id}` → comments
  - `GET /api/chat/rooms?page={n}` → inbox conversations
  - Auth: `Authorization: bearer {token}`; login = `POST /api/users/sign_in` with
    `{user:{email,password}, client_id, client_secret}`.

---

## Step 0 — First test WITHOUT full login (5 min)

The fastest way to see it work end to end:

1. 🧑 YOU: in Chrome on `app.fitr.training`, open DevTools console, run
   `localStorage.getItem('access_token')`, copy the value.
2. `cp .env.example .env`; paste that token into `FITR_ACCESS_TOKEN`.
3. Do Step 1 (Google service account) so writes work.
4. `DRY_RUN=1 python sync.py` → confirms it pulls real data and prints what it
   *would* write. Then `python sync.py` for a real first pass.

The pasted token expires within the hour — fine for testing, not for the
schedule. Steps 2–4 make it self-sufficient.

---

## Step 1 — Google service account (so the script can write the sheet)

🧑 YOU (one-time, ~10 min):
1. Go to <https://console.cloud.google.com/> → create a project (e.g. "jst-sheets").
2. Enable the **Google Sheets API** for it.
3. **Create credentials → Service account**. Name it, create.
4. On the service account → **Keys → Add key → JSON**. A file downloads.
5. Put that file in this folder as `service_account.json`.
6. Open the file, copy the `client_email` (looks like `...@...iam.gserviceaccount.com`).
7. In Google Sheets, **Share** the Athlete Profiles sheet with that email as **Editor**.

Claude Code can verify with: `DRY_RUN=1 python -c "from sheets_client import SheetsClient; SheetsClient(); print('ok')"`

---

## Step 2 — Fitr login for automation (so it runs without a pasted token)

The login needs the Fitr app's OAuth `client_id` / `client_secret`. Capture them once:

🧑 YOU:
1. Log out of Fitr, open DevTools → **Network** tab, tick "Preserve log".
2. Log back in. Find the `POST /api/users/sign_in` request.
3. In its **Request payload** you'll see `client_id` and `client_secret`. Copy both.
4. Put `FITR_EMAIL`, `FITR_PASSWORD`, `FITR_CLIENT_ID`, `FITR_CLIENT_SECRET` in `.env`.
5. Remove the pasted `FITR_ACCESS_TOKEN` (or leave blank) so it uses the login path.

Claude Code: confirm with a live `python -c "from fitr_client import FitrClient; c=FitrClient(); c.authenticate(); print('auth ok')"`.
If the response shape differs from expected, adjust `_login()` in `fitr_client.py`
to read the token from wherever it actually appears.

---

## Step 3 — Anthropic key (for inbox summaries)

🧑 YOU: get a key from <https://console.anthropic.com/> → put in `ANTHROPIC_API_KEY`.
(Without it, summaries fall back to a trimmed raw excerpt — the pipeline still runs.)

---

## Step 4 — Validate against live data

Claude Code, please:
1. Run `DRY_RUN=1 python sync.py` and sanity-check the printed counts vs the sheet.
2. Confirm dedupe works: run it **twice** live — the second run should add ~0 PR Log rows.
3. Spot-check that Coaching Notes were appended (not overwritten) for a couple of athletes.
4. Verify `Last Scraped` updated and a `Sync Log` row appeared.

Things worth hardening while you're in there:
- Benchmark value scaling — `last_value` came back human-scaled in testing, but
  double-check a known PR (e.g. a 117.5 kg back squat) lands as `117.5 kg`, not grams.
- Challenge date: the list payload's `created_at` is the challenge's creation, not
  when each athlete logged. If you want true per-log dates, see whether
  `/api/score/{id}/items` entries carry a timestamp and use that.
- Chat recency: `/api/chat/rooms` returns newest first but the list items don't
  carry a clean per-message date — consider stopping the sweep once you hit a room
  whose `last_message` you've already summarised (persist seen message ids in `state.json`).

---

## Step 5 — Schedule it

Two options:

**A) GitHub Actions (recommended — runs in the cloud, laptop can be off).**
1. 🧑 YOU: push this folder to a **private** GitHub repo.
2. Repo → Settings → Secrets and variables → Actions → add:
   `SHEET_ID`, `FITR_EMAIL`, `FITR_PASSWORD`, `FITR_CLIENT_ID`, `FITR_CLIENT_SECRET`,
   `ANTHROPIC_API_KEY`, and `GOOGLE_SERVICE_ACCOUNT_JSON` (paste the whole JSON file contents).
3. The included `.github/workflows/weekly-sync.yml` then runs every Sunday 17:00 UTC,
   and you can trigger it manually from the **Actions** tab. Run it manually once to confirm.

**B) Local cron (simpler, but only runs when your Mac is on).**
```
0 18 * * 0  cd /path/to/fitr-athlete-sync && /usr/bin/python3 sync.py >> sync.log 2>&1
```

Once this is running, retire the Cowork "fitr-athlete-db-weekly-refresh" scheduled
task so the two don't both write.

---

## Step 6 (optional) — Recovery survey

1. 🧑 YOU: build the Typeform (sleep hrs, soreness 1–10, stress 1–10, motivation 1–10,
   bodyweight, niggles/injuries, availability), collecting the athlete's **email**.
2. In Typeform → Connect → **Google Sheets**, send responses to a new `Recovery` tab
   in the Athlete Profiles sheet.
3. Make the `Recovery` tab headers match `RECOVERY_COLS` in `recovery.py` (or edit that
   dict to match your form). The sync then merges each athlete's latest response into
   their Coaching Notes automatically.

---

## If something breaks

- `sync.py` is defensive: a failure on one athlete/challenge is logged and skipped,
  not fatal. A fatal error (auth, sheet access) prints `FATAL:` and exits non-zero.
- Re-running is safe — dedupe prevents double entries; notes are append-only.
- Start every debugging session with `DRY_RUN=1` so you never write junk while iterating.
