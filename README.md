# Fitr → Athlete Database Sync

Keeps the **"JST Compete — Athlete Profiles"** Google Sheet updated automatically
from Fitr: logged benchmark results, challenge scores (with notes), and a
summary of recent inbox conversations. Optional weekly recovery-survey merge.

This is the standalone, robust version of the weekly refresh — it talks to
Fitr's API and the Google Sheets API directly (no browser, no UI typing).

## What it does each run

1. Reads the **Benchmarks** tab to map athletes → Fitr IDs, and **PR Log** for dedupe.
2. Pulls each athlete's benchmarks → appends results from the last 7 days to **PR Log**.
3. Pulls recent challenge leaderboards → appends new scores (Type = `Challenge`).
4. Sweeps the Fitr **inbox** → AI-summarises recent chats → appends a dated line to
   each athlete's **Coaching Notes** in `_DATA` (append-only, never overwrites).
5. Merges the latest **Recovery** survey response per athlete (if that tab exists).
6. Updates **Last Scraped** and writes a **Sync Log** row.

## Files

| File | Purpose |
|------|---------|
| `sync.py` | Orchestrator — run this. |
| `fitr_client.py` | Fitr API client (validated endpoints). |
| `sheets_client.py` | Google Sheets read/write via service account. |
| `summariser.py` | Claude-powered conversation summaries. |
| `recovery.py` | Recovery-survey merge. |
| `config.py` | Loads settings from `.env`. |
| `.github/workflows/weekly-sync.yml` | Cloud schedule (GitHub Actions). |
| `HANDOFF.md` | **Start here** — credential setup, step by step. |

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill it in (see HANDOFF.md)
DRY_RUN=1 python sync.py      # pulls + prints, writes nothing
python sync.py                # live
```

See **HANDOFF.md** for the credential setup (Google service account, Fitr login,
Anthropic key) and for putting it on a weekly schedule.

## Known limitation

Full chat *threads* arrive over a websocket, not the REST API, so the inbox
summary uses each conversation's most recent message. That covers the bulk of
coaching signal; deeper history stays a manual "open Fitr" moment.
