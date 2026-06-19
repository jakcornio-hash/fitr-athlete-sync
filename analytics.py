"""
Post-processing analytics on top of PR Log data.

Produces two types of coaching signal:
  - Trend detection: is an athlete improving, flat, or declining on each benchmark?
  - Engagement flags: athletes who haven't logged anything in ENGAGEMENT_THRESHOLD_DAYS.
"""
import datetime as dt
import re
import statistics

TODAY = dt.date.today()
_TREND_WINDOW_DAYS = 56      # 8 weeks of history to analyse
_MIN_POINTS = 3              # need at least this many entries to call a trend
_IMPROVING_THRESHOLD = 0.005 # normalised slope > 0.5%/entry = improving
_DECLINING_THRESHOLD = -0.005
_PEAK_DROP_THRESHOLD = 0.10  # 10% below all-time peak = flag


def _parse_numeric(value_str):
    """Extract a float from '117.5 kg', '45 reps', '3:42', '1:02:30', etc."""
    if not value_str:
        return None
    s = str(value_str).strip()
    # mm:ss or h:mm:ss
    m = re.match(r'^(\d+):(\d{2})(?::(\d{2}))?$', s)
    if m:
        a, b, c = m.groups()
        return int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
    m = re.search(r'[-+]?\d*\.?\d+', s)
    return float(m.group()) if m else None


def _parse_date(s):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(str(s).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _slope(points):
    """Linear slope through (index, value) pairs, normalised by mean value."""
    n = len(points)
    if n < 2:
        return 0.0
    xs = list(range(n))
    ys = [v for _, v in points]
    xm, ym = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    den = sum((x - xm) ** 2 for x in xs)
    raw_slope = (num / den) if den else 0.0
    return raw_slope / ym if ym else 0.0


def trend_analysis(pr_log_records):
    """
    Analyse PR Log for trend per (athlete, benchmark).

    Returns {athlete_name: [signal_dict, ...]} where each signal_dict has:
      benchmark, trend (improving/flat/declining/insufficient_data),
      trend_pct, peak, last_value, last_date, peak_drop, peak_drop_flag
    """
    cutoff = TODAY - dt.timedelta(days=_TREND_WINDOW_DAYS)

    series = {}
    for rec in pr_log_records:
        name = str(rec.get("Athlete Name", "")).strip()
        bench = str(rec.get("Benchmark Name", "")).strip()
        value = str(rec.get("Value", "")).strip()
        date_str = str(rec.get("Date", "")).strip()
        if not name or not bench:
            continue
        d = _parse_date(date_str)
        v = _parse_numeric(value)
        if d and v is not None:
            series.setdefault((name, bench), []).append((d, v))

    results = {}
    for (athlete, bench), pts in series.items():
        pts.sort(key=lambda x: x[0])
        all_vals = [v for _, v in pts]
        peak = max(all_vals)
        last_date, last_val = pts[-1]
        recent = [(d, v) for d, v in pts if d >= cutoff]

        signal = {
            "benchmark": bench,
            "data_points": len(pts),
            "last_date": last_date,
            "last_value": last_val,
            "peak": peak,
        }

        if len(recent) >= _MIN_POINTS:
            s = _slope(recent)
            if s > _IMPROVING_THRESHOLD:
                signal["trend"] = "improving"
            elif s < _DECLINING_THRESHOLD:
                signal["trend"] = "declining"
            else:
                signal["trend"] = "flat"
            signal["trend_pct"] = round(s * 100, 1)
        else:
            signal["trend"] = "insufficient_data"
            signal["trend_pct"] = None

        drop = (peak - last_val) / peak if peak > 0 else 0.0
        signal["peak_drop_pct"] = round(drop * 100, 1)
        signal["peak_drop_flag"] = drop >= _PEAK_DROP_THRESHOLD

        results.setdefault(athlete, []).append(signal)

    return results


def engagement_check(pr_log_records, athletes, threshold_days=21, last_contact_by_name=None):
    """
    Return sorted list of {name, jst_id, last_logged, days_since, flag}
    — most-overdue athletes first, never-logged athletes at the top.

    last_contact_by_name: optional {name: date} of most recent coach contact
    (from Fitr chat). When provided, an athlete who hasn't logged but was
    contacted recently won't be flagged.
    """
    last_logged = {}
    for rec in pr_log_records:
        name = str(rec.get("Athlete Name", "")).strip()
        date_str = str(rec.get("Date", "")).strip()
        if not name:
            continue
        d = _parse_date(date_str)
        if d and (name not in last_logged or d > last_logged[name]):
            last_logged[name] = d

    # Merge chat contact dates — a recent conversation suppresses the flag
    for name, contact_date in (last_contact_by_name or {}).items():
        if contact_date and (name not in last_logged or contact_date > last_logged[name]):
            last_logged[name] = contact_date

    out = []
    for a in athletes:
        name = a["name"]
        last = last_logged.get(name)
        days = (TODAY - last).days if last else None
        out.append({
            "name": name,
            "jst_id": a.get("jst_id", ""),
            "last_logged": last.isoformat() if last else "never",
            "days_since": days,
            "flag": days is None or days >= threshold_days,
        })

    out.sort(key=lambda x: (0 if x["days_since"] is None else 1, -(x["days_since"] or 99999)))
    return out


def milestone_detection(new_bench_rows):
    """
    From newly-added benchmark rows, identify results that changed vs previous.
    new_bench_rows: [[date, name, email, bench, value, type, prev, link, note], ...]
    Returns [[name, bench, new_value, prev_value], ...]
    prev_value is "first entry" when no prior result existed.
    """
    milestones = []
    for row in new_bench_rows:
        if len(row) < 7:
            continue
        name, bench, value, prev = row[1], row[3], row[4], row[6]
        if not prev:
            milestones.append([name, bench, value, "first entry"])
            continue
        v_num = _parse_numeric(value)
        p_num = _parse_numeric(prev)
        if v_num is not None and p_num is not None and v_num != p_num:
            milestones.append([name, bench, value, prev])
    return milestones


def consistency_check(pr_log_records, athletes, min_consecutive_weeks=4):
    """
    Find athletes on a logging streak of min_consecutive_weeks or more consecutive weeks.
    Returns [(name, consecutive_weeks), ...] sorted by streak length desc.
    """
    from collections import defaultdict

    weeks_by_athlete = defaultdict(set)
    for rec in pr_log_records:
        name = str(rec.get("Athlete Name", "")).strip()
        date_str = str(rec.get("Date", "")).strip()
        d = _parse_date(date_str)
        if name and d:
            weeks_by_athlete[name].add(d.isocalendar()[:2])  # (year, week_num)

    valid_names = {a["name"] for a in athletes}
    wins = []
    for name, week_set in weeks_by_athlete.items():
        if name not in valid_names:
            continue
        weeks = sorted(week_set)
        if len(weeks) < min_consecutive_weeks:
            continue
        consecutive = 1
        for i in range(len(weeks) - 1, 0, -1):
            y1, w1 = weeks[i]
            y2, w2 = weeks[i - 1]
            d1 = dt.date.fromisocalendar(y1, w1, 1)
            d2 = dt.date.fromisocalendar(y2, w2, 1)
            if (d1 - d2).days == 7:
                consecutive += 1
            else:
                break
        if consecutive >= min_consecutive_weeks:
            wins.append((name, consecutive))

    wins.sort(key=lambda x: -x[1])
    return wins


def recovery_alerts(recovery_by_name):
    """
    Flag athletes with concerning recovery survey scores.

    recovery_by_name: {athlete_name: raw_row_dict} from sheets (Typeform column headers).
    Returns list of [athlete, issue, submitted_at].
    Thresholds: soreness >= 7, stress >= 7, motivation <= 3, injury mention in availability.
    """
    def _num(val):
        try:
            return float(str(val).strip())
        except (ValueError, TypeError):
            return None

    alerts = []
    for name in sorted(recovery_by_name):
        row = recovery_by_name[name]
        issues = []
        s = _num(row.get("Soreness"))
        if s is not None and s >= 7:
            issues.append(f"High soreness ({s:.0f}/10)")
        st = _num(row.get("Stress"))
        if st is not None and st >= 7:
            issues.append(f"High stress ({st:.0f}/10)")
        m = _num(row.get("Motivation"))
        if m is not None and m <= 3:
            issues.append(f"Low motivation ({m:.0f}/10)")
        avail = str(row.get("Availability this week", "")).strip()
        if any(w in avail.lower() for w in ("injur", "carrying", "niggle")):
            issues.append(f"Injury flag: {avail}")
        ts = str(row.get("Submitted At", "")).strip()
        for issue in issues:
            alerts.append([name, issue, ts])
    return alerts


def build_coach_alerts_rows(engagement_results, trend_results,
                            recovery_by_name=None, milestones=None,
                            consistency_wins=None):
    """
    Build a list-of-lists ready to write into the Coach Alerts tab.
    Sections: Recovery Alerts, Milestones, Consistency, Engagement, Performance.
    """
    rows = []

    # ---- recovery section ----
    rec_alerts = recovery_alerts(recovery_by_name or {})
    rows.append(["== RECOVERY ALERTS ==", f"{len(rec_alerts)} flags from latest survey"])
    rows.append(["Athlete", "Issue", "Submitted At"])
    rows.extend(rec_alerts if rec_alerts else [["(none — no concerning recovery scores)"]])
    rows.append([])

    # ---- engagement section ----
    flagged = [e for e in engagement_results if e["flag"]]
    rows.append(["== ENGAGEMENT ALERTS ==", f"{len(flagged)} athletes inactive 21+ days"])
    rows.append(["Athlete", "JST ID", "Last Logged", "Days Inactive", "Status"])
    for e in flagged:
        days_str = str(e["days_since"]) if e["days_since"] is not None else "—"
        status = "❌ Never logged" if e["last_logged"] == "never" else "⚠️ Inactive"
        rows.append([e["name"], e["jst_id"], e["last_logged"], days_str, status])
    if not flagged:
        rows.append(["(none — all athletes active)"])
    rows.append([])

    # ---- performance concerns section ----
    concerns = []
    for athlete, signals in trend_results.items():
        for s in signals:
            if s["trend"] == "declining" or s["peak_drop_flag"]:
                trend_label = (
                    f"📉 Declining ({s['trend_pct']:+.1f}%/entry)"
                    if s["trend"] == "declining" else "Flat"
                )
                drop_str = f"-{s['peak_drop_pct']:.1f}%" if s["peak_drop_flag"] else "ok"
                concerns.append([
                    athlete,
                    s["benchmark"],
                    trend_label,
                    str(s["last_value"]),
                    str(s["peak"]),
                    drop_str,
                    s["last_date"].isoformat() if s["last_date"] else "",
                ])

    rows.append(["== PERFORMANCE CONCERNS ==", f"{len(concerns)} declining or below-peak benchmarks"])
    rows.append(["Athlete", "Benchmark", "Trend", "Last Value", "Peak", "% From Peak", "Last Date"])
    rows.extend(concerns if concerns else [["(none — no declining benchmarks detected)"]])
    rows.append([])

    # ---- milestones section ----
    ms = milestones or []
    rows.append(["== MILESTONES / NEW PBs ==", f"{len(ms)} new results this week"])
    rows.append(["Athlete", "Benchmark", "New Value", "Previous"])
    for m in ms:
        rows.append(m)
    if not ms:
        rows.append(["(none this week)"])
    rows.append([])

    # ---- consistency section ----
    cw = consistency_wins or []
    rows.append(["== CONSISTENCY WINS ==", f"{len(cw)} athletes on a streak"])
    rows.append(["Athlete", "Consecutive Weeks"])
    for name, weeks in cw:
        rows.append([name, weeks])
    if not cw:
        rows.append(["(none — no 4+ week streaks detected)"])

    return rows
