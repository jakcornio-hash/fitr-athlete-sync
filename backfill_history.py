#!/usr/bin/env python3
"""
One-time historical PR backfill.

For each athlete in the Benchmarks tab, fetches all benchmark history
available from the Fitr API and writes any entries not already in the
PR Log. The weekly sync captures the last 7 days going forward; this
script fills in everything before that.

Run:  python backfill_history.py
      DRY_RUN=1 python backfill_history.py
"""
import datetime as dt
import sys
import time

import config
from fitr_client import FitrClient, FitrError
from sheets_client import SheetsClient

TODAY = dt.date.today()


def _parse_date(s):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(str(s).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _fmt_value(v_num, symbol):
    if v_num is None:
        return ""
    return f"{v_num} {symbol}".strip()


def fetch_all_values(fitr, benchmark_id, user_id):
    """Return all benchmark_value entries for one athlete+benchmark.
    The API returns up to 7 entries (oldest first, no usable pagination).
    Caller should also include last_value to cover the most recent entry."""
    try:
        result = fitr._get(
            f"/api/coach/benchmarks/{benchmark_id}/benchmark_values",
            {"user_id": user_id},
        )
        return result.get("items", []) if result else []
    except FitrError:
        return []


def main():
    if not config.SHEET_ID:
        raise RuntimeError("Missing required env var: SHEET_ID")

    print(f"== Historical PR backfill {TODAY} (dry_run={config.DRY_RUN}) ==")

    fitr = FitrClient()
    fitr.authenticate()
    print("Fitr: authenticated")

    sheets = SheetsClient()
    print("Sheets: connected")

    # Load existing PR Log for deduplication
    existing = sheets.read_records(config.TAB_PR_LOG)
    existing_keys = set()
    email_by_name = {}
    for r in existing:
        name = str(r.get("Athlete Name", "")).strip()
        bench = str(r.get("Benchmark Name", "")).strip()
        value = str(r.get("Value", "")).strip()
        date = str(r.get("Date", "")).strip()
        existing_keys.add((name.lower(), bench.lower(), value.lower(), date))
        if name and r.get("Email"):
            email_by_name.setdefault(name, str(r["Email"]).strip())
    print(f"Existing PR Log rows: {len(existing)}")

    # Load athletes
    bm_rows = sheets.read_values(config.TAB_BENCHMARKS)
    bm_header = bm_rows[0]
    athletes = []
    for r in bm_rows[1:]:
        rec = dict(zip(bm_header, r))
        name = rec.get("Name", "").strip()
        fitr_id = rec.get("Fitr ID", "").strip()
        if name and fitr_id and fitr_id.isdigit():
            athletes.append({"name": name, "fitr_id": int(fitr_id)})
    print(f"Athletes with Fitr IDs: {len(athletes)}")
    print()

    total_new = 0
    batch = []
    BATCH_SIZE = 200

    def _flush():
        nonlocal total_new
        if batch:
            sheets.append_rows(config.TAB_PR_LOG, batch)
            total_new += len(batch)
            batch.clear()

    for idx, a in enumerate(athletes, 1):
        name = a["name"]
        fitr_id = a["fitr_id"]
        email = email_by_name.get(name, "")

        try:
            benchmarks = fitr.benchmarks(fitr_id)
        except FitrError as e:
            print(f"  [{idx}/{len(athletes)}] {name}: benchmarks fetch failed — {e}")
            continue

        athlete_new = 0
        for b in benchmarks:
            lv = b.get("last_value") or {}
            if not lv:
                continue

            bench_name = b.get("name", "")
            bench_id = b.get("id")
            symbol = lv.get("symbol") or lv.get("units") or ""

            # Gather all historical entries + the last_value
            history = fetch_all_values(fitr, bench_id, fitr_id)
            time.sleep(0.15)

            # Build a unified set of entries
            seen_in_history = set()
            for entry in history:
                d = _parse_date(entry.get("date"))
                v_num = entry.get("value")
                entry_symbol = entry.get("symbol") or symbol
                if not d or v_num is None:
                    continue
                value_str = _fmt_value(v_num, entry_symbol)
                key = (name.lower(), bench_name.lower(), value_str.lower(), d.isoformat())
                seen_in_history.add((d, v_num))
                if key in existing_keys:
                    continue
                note = (entry.get("note") or "").strip()
                batch.append([
                    d.isoformat(), name, email, bench_name, value_str,
                    "Benchmark", "", "", note,
                ])
                existing_keys.add(key)
                athlete_new += 1

            # Add last_value if it's not already in the history batch
            lv_date = _parse_date(lv.get("date"))
            lv_v = lv.get("value")
            if lv_date and lv_v is not None:
                if (lv_date, lv_v) not in seen_in_history:
                    lv_value_str = _fmt_value(lv_v, symbol)
                    key = (name.lower(), bench_name.lower(), lv_value_str.lower(), lv_date.isoformat())
                    if key not in existing_keys:
                        note = (lv.get("note") or "").strip()
                        batch.append([
                            lv_date.isoformat(), name, email, bench_name, lv_value_str,
                            "Benchmark", "", "", note,
                        ])
                        existing_keys.add(key)
                        athlete_new += 1

            if len(batch) >= BATCH_SIZE:
                _flush()

        status = f"+{athlete_new} new" if athlete_new else "up to date"
        print(f"  [{idx}/{len(athletes)}] {name}: {len(benchmarks)} benchmarks — {status}")

    _flush()
    print()
    print(f"== Done — {total_new} new PR Log rows added ==")


if __name__ == "__main__":
    try:
        main()
    except (FitrError, RuntimeError) as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)
