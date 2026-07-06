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

    last_contact = last_contact_by_name or {}

    out = []
    for a in athletes:
        name = a["name"]
        last_log = last_logged.get(name)
        last_chat = last_contact.get(name)

        days_since_log = (TODAY - last_log).days if last_log else None
        days_since_contact = (TODAY - last_chat).days if last_chat else None

        log_inactive = days_since_log is None or days_since_log >= threshold_days
        contact_recent = days_since_contact is not None and days_since_contact < threshold_days

        # nudge_flag: not logging but coach is in contact — softer monthly prompt
        # flag: not logging AND no recent contact — genuine dropout, needs re-engagement
        nudge_flag = log_inactive and contact_recent
        flag = log_inactive and not contact_recent

        out.append({
            "name": name,
            "jst_id": a.get("jst_id", ""),
            "last_logged": last_log.isoformat() if last_log else "never",
            "last_contact": last_chat.isoformat() if last_chat else None,
            "days_since": days_since_log,
            "flag": flag,
            "nudge_flag": nudge_flag,
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


_STREAK_EXEMPT_WEEKDAYS = {3, 6}  # Thursday (recovery), Sunday (rest)
_STREAK_MILESTONES = {7, 14, 21, 30, 60, 90}


def daily_streak_check(pr_log_records, athletes, today=None):
    """Find athletes whose current streak of consecutive expected-training-days hits a milestone.

    Thursday and Sunday are exempt — absence on those days never breaks a streak.
    Only fires for athletes active within the last 3 days so stale streaks don't re-trigger.
    Returns [(name, streak_days), ...] where streak_days is in _STREAK_MILESTONES.
    """
    import datetime as _dt
    if today is None:
        today = TODAY

    log_dates_by_name = defaultdict(set)
    for rec in pr_log_records:
        name = str(rec.get("Athlete Name", "")).strip()
        d = _parse_date(str(rec.get("Date", "")))
        if name and d:
            log_dates_by_name[name].add(d)

    valid_names = {a["name"] for a in athletes}
    results = []

    for name in valid_names:
        log_dates = log_dates_by_name.get(name, set())
        if not log_dates:
            continue

        most_recent = max(log_dates)
        if (today - most_recent).days > 3:
            continue  # stale — don't re-fire old streaks

        # Walk backward from most recent log, counting required training days logged
        streak = 0
        d = most_recent
        for _ in range(400):
            if d.weekday() in _STREAK_EXEMPT_WEEKDAYS:
                d -= _dt.timedelta(days=1)
                continue
            if d in log_dates:
                streak += 1
                d -= _dt.timedelta(days=1)
            else:
                break  # missed a required training day — streak ends

        if streak in _STREAK_MILESTONES:
            results.append((name, streak))

    return results


def load_analysis(pr_log_records, rec_by_name=None, data_records=None, weeks_back=12):
    """
    Training load proxy per athlete, derived from PR-log entry frequency.

    ACWR (Acute : Chronic Workload Ratio):
      acute   = entries logged in the last 7 days
      chronic = average weekly entries over the preceding 28 days
      acwr    = acute / chronic  (None when chronic < 0.1 — insufficient baseline)

    Status bands:
      green       0.8 – 1.3   sweet-spot
      amber_high  1.3 – 1.5   load spike, monitor
      red         > 1.5       danger zone
      amber_low   0.5 – 0.8   under-loading
      low         < 0.5       very low
      insufficient             < 2 weeks of history in the 28-day window

    Returns {athlete_name: {
        "weeks":        [date, ...],    # Monday of each ISO week, oldest first
        "weekly_loads": [int, ...],     # entries in that week (parallel with weeks)
        "chronic_line": [float, ...],   # 4-week rolling avg at each week end (for chart)
        "acute":        int,
        "chronic":      float,
        "acwr":         float | None,
        "status":       str,
        "programme":    str,
        "expected_daily": int | None,   # 1 or 2, parsed from programme name
        "soreness":     float | None,
        "stress":       float | None,
    }}
    """
    from collections import defaultdict

    entries_by_athlete = defaultdict(list)
    for rec in pr_log_records:
        name = str(rec.get("Athlete Name", "")).strip()
        d = _parse_date(str(rec.get("Date", "")))
        if name and d:
            entries_by_athlete[name].append(d)

    data_by_name = {}
    for rec in (data_records or []):
        nm = str(rec.get("Full Name", "")).strip()
        if nm:
            data_by_name[nm] = rec

    def _expected_daily(prog_str):
        s = str(prog_str).lower()
        if "2 session" in s:
            return 2
        if "1 session" in s:
            return 1
        return None

    def _num(val):
        try:
            return float(str(val).strip())
        except (ValueError, TypeError):
            return None

    current_monday = TODAY - dt.timedelta(days=TODAY.weekday())
    week_starts = [current_monday - dt.timedelta(weeks=i) for i in range(weeks_back - 1, -1, -1)]

    results = {}

    for name, dates in entries_by_athlete.items():
        # Acute: entries in last 7 days
        acute = sum(1 for d in dates if d > TODAY - dt.timedelta(days=7))

        # Chronic: avg entries per week over the 4 complete weeks before this one
        chronic_counts = []
        for w in range(1, 5):
            wk_end = current_monday - dt.timedelta(weeks=w - 1)
            wk_start = wk_end - dt.timedelta(weeks=1)
            chronic_counts.append(sum(1 for d in dates if wk_start <= d < wk_end))
        chronic = sum(chronic_counts) / 4.0

        if chronic < 0.1:
            acwr = None
            status = "insufficient"
        else:
            acwr = round(acute / chronic, 2)
            if acwr > 1.5:
                status = "red"
            elif acwr > 1.3:
                status = "amber_high"
            elif acwr >= 0.8:
                status = "green"
            elif acwr >= 0.5:
                status = "amber_low"
            else:
                status = "low"

        # Per-week entry counts for the chart window
        weekly_loads = []
        for ws in week_starts:
            we = ws + dt.timedelta(weeks=1)
            weekly_loads.append(sum(1 for d in dates if ws <= d < we))

        # 4-week rolling chronic baseline per chart point
        chronic_line = []
        for ws in week_starts:
            we = ws + dt.timedelta(weeks=1)
            c_vals = []
            for w in range(1, 5):
                c_end = we - dt.timedelta(weeks=w - 1)
                c_start = c_end - dt.timedelta(weeks=1)
                c_vals.append(sum(1 for d in dates if c_start <= d < c_end))
            chronic_line.append(round(sum(c_vals) / 4.0, 2))

        rec_row = (rec_by_name or {}).get(name, {})
        data_row = data_by_name.get(name, {})
        prog = str(data_row.get("Programme", "")).strip()

        results[name] = {
            "weeks": week_starts,
            "weekly_loads": weekly_loads,
            "chronic_line": chronic_line,
            "acute": acute,
            "chronic": round(chronic, 2),
            "acwr": acwr,
            "status": status,
            "programme": prog,
            "expected_daily": _expected_daily(prog),
            "soreness": _num(rec_row.get("Soreness")),
            "stress": _num(rec_row.get("Stress")),
        }

    return results


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


# ── Competition prep ──────────────────────────────────────────────────────────

def comp_phase(days_out, comp_type="A"):
    """Classify a competition into a prep phase.

    Returns (phase_label, action_or_None).
    action is only set during transition windows where the coach needs to act.

    comp_type:
      A — Primary goal. Full taper. 10-week + 2-week peak programmes.
      B — Secondary race. Light taper (race week only). No programme switch.
      C — Training day. No taper, no disruption to training block.
    """
    ct = str(comp_type).upper() if comp_type else "A"

    if ct == "C":
        if days_out < -7:
            return None, None
        elif days_out < 0:
            return "Post-Competition", "Post-comp check-in — how did it go?"
        elif days_out <= 7:
            return "C — Race Week", None
        elif days_out <= 21:
            return "C — Coming Up", None
        return None, None

    elif ct == "B":
        if days_out < -7:
            return None, None
        elif days_out < 0:
            return "Post-Competition", "Post-comp check-in — how did it go?"
        elif days_out <= 7:
            return "B — Race Week", "Minor taper this week — reduce volume by ~20%"
        elif days_out <= 14:
            return "B — Final Prep", None
        elif days_out <= 21:
            return "B — Approaching", "B-race in 3 weeks — keep training as planned, heads-up to athlete"
        return None, None

    else:  # A competition — full taper phases
        if days_out < -14:
            return None, None
        elif days_out < 0:
            return "Post-Competition", "Post-comp check-in — how did it go?"
        elif days_out <= 14:
            return "2-Week Peak Prep", None
        elif days_out <= 22:
            return "Switch → 2-Week Prep", "Switch to 2-week peak programme now"
        elif days_out <= 70:
            return "10-Week Prep", None
        elif days_out <= 77:
            return "Switch → 10-Week Prep", "Switch to 10-week peak programme now"
        elif days_out <= 91:
            return "Pre-Peak", "Notify: switching to 10-week prep in ~2 weeks"
        elif days_out <= 112:
            return "Approaching", "Competition approaching — confirm peak timing"
        else:
            return "Normal Training", None


def comp_message(name, comp_label, days_out, comp_type="A"):
    """Return a ready-to-send coaching message appropriate for this phase and type."""
    first = name.split()[0]
    weeks = round(abs(days_out) / 7)
    label = comp_label or "your competition"
    ct = str(comp_type).upper() if comp_type else "A"

    if ct == "C":
        if days_out < 0:
            return (
                f"Hi {first}, how did {label} go? Good benchmark — "
                f"let's use that data to inform your training going forward."
            )
        elif days_out <= 7:
            return (
                f"Hi {first}, {label} is this week — race it as a training stimulus, "
                f"no taper. Great chance to test your fitness under race conditions."
            )
        else:
            return (
                f"Hi {first}, {label} is coming up in {weeks} week{'s' if weeks != 1 else ''} — "
                f"no changes to your training, just treat it as a hard training day."
            )

    elif ct == "B":
        if days_out < 0:
            return (
                f"Hi {first}, how did {label} go? Great practice run — "
                f"take a day or two to recover and then back into your main training block."
            )
        elif days_out <= 7:
            return (
                f"Hi {first}, {label} is this week — I've trimmed the volume slightly "
                f"so you go in feeling fresh. Race hard and enjoy it."
            )
        else:
            return (
                f"Hi {first}, {label} is {weeks} week{'s' if weeks != 1 else ''} away — "
                f"we'll keep your main training running as planned. "
                f"Think of it as a race-pace effort within your training block."
            )

    else:  # A competition
        if days_out < 0:
            return (
                f"Hi {first}, how did {label} go? "
                f"Brilliant effort — take a few days to recover properly and then "
                f"let's sit down and plan what's next. You've earned the rest."
            )
        elif days_out <= 14:
            return (
                f"Hi {first}, {label} is {days_out} day{'s' if days_out != 1 else ''} away. "
                f"You're in the final peak block — everything has been building to this. "
                f"Trust the process, stay sharp, and we'll have you firing on the day."
            )
        elif days_out <= 22:
            return (
                f"Hi {first}, 3 weeks out from {label} — time to switch to the "
                f"2-week peak programme. This block is about sharpening everything up "
                f"right before competition day. Stay focused and trust your training."
            )
        elif days_out <= 77:
            return (
                f"Hi {first}, you're {weeks} weeks out from {label} and deep into "
                f"the 10-week competition prep block. Keep the quality high — "
                f"we've planned this so you peak exactly when it counts."
            )
        elif days_out <= 91:
            return (
                f"Hi {first}, {label} is {weeks} weeks away. "
                f"In about 2 weeks I'll be switching you onto the 10-week peak competition "
                f"prep programme — keep training hard until then, we're building momentum."
            )
        else:
            return (
                f"Hi {first}, great to see you've got {label} in the calendar — "
                f"{weeks} weeks away. Plenty of time to build something special. "
                f"Stay consistent and we'll plan your peak timing as we get closer."
            )


def comp_schedule(athletes=None, data_records=None, competition_rows=None, today=None):
    """Return athletes with competitions, sorted soonest first.

    Each entry: {name, comp_name, comp_date, comp_type, days_out, weeks_out,
                 phase, action, message_template}

    Two data sources (use competition_rows when available):
      competition_rows — from the Competitions tab (multiple per athlete, has Type column)
      data_records + athletes — legacy fallback (_DATA Next Competition / Competition Date)
    """
    if today is None:
        today = dt.date.today()

    entries = []  # [(name, comp_name, comp_date, comp_type)]

    if competition_rows is not None:
        for row in competition_rows:
            name = str(row.get("Athlete Name", "")).strip()
            if not name:
                continue
            comp_name = str(row.get("Competition Name", "")).strip()
            comp_date_str = str(row.get("Date", "")).strip()
            raw_type = str(row.get("Type", "")).strip().upper()
            comp_type = raw_type if raw_type in ("A", "B", "C") else "A"
            comp_date = _parse_date(comp_date_str)
            if comp_date:
                entries.append((name, comp_name, comp_date, comp_type))
    else:
        # Legacy: one competition per athlete from _DATA
        data_by_name = {}
        for r in (data_records or []):
            nm = str(r.get("Full Name", "")).strip()
            if nm:
                data_by_name[nm] = r
        for a in (athletes or []):
            profile = data_by_name.get(a["name"], {})
            comp_date_str = str(profile.get("Competition Date", "")).strip()
            comp_name = str(profile.get("Next Competition", "")).strip()
            if not comp_date_str:
                continue
            comp_date = _parse_date(comp_date_str)
            if comp_date:
                entries.append((a["name"], comp_name, comp_date, "A"))

    results = []
    for name, comp_name, comp_date, comp_type in entries:
        days_out = (comp_date - today).days
        phase, action = comp_phase(days_out, comp_type=comp_type)
        if phase is None:
            continue
        results.append({
            "name": name,
            "comp_name": comp_name or "Competition",
            "comp_date": comp_date,
            "comp_type": comp_type,
            "days_out": days_out,
            "weeks_out": round(days_out / 7, 1) if days_out >= 0 else None,
            "phase": phase,
            "action": action,
            "message_template": comp_message(name, comp_name, days_out, comp_type=comp_type),
        })

    results.sort(key=lambda x: x["days_out"])
    return results


def programme_peer_comparison(name, programme, pr_records, data_records):
    """Compare one athlete's key benchmarks against others on the same programme.

    Returns a list of dicts:
      {benchmark, athlete_value, peer_median, peer_count, percentile, direction}

    Only benchmarks where the athlete has a value AND at least 2 peers also have
    values are included. direction is 'above' / 'below' / 'at' median.
    """
    if not programme or not name:
        return []

    # Build programme -> [names] map from _DATA
    prog_members = set()
    for r in (data_records or []):
        nm = str(r.get("Full Name", "")).strip()
        prog = str(r.get("Programme", "")).strip()
        if nm and prog == programme and nm != name:
            prog_members.add(nm)

    if not prog_members:
        return []

    # Get latest value per (athlete, benchmark) from PR log
    latest = {}  # {(nm, bench): value}
    for r in (pr_records or []):
        nm = str(r.get("Athlete Name", "")).strip()
        bench = str(r.get("Benchmark Name", "")).strip()
        date_str = str(r.get("Date", "")).strip()
        val_str = str(r.get("Value", "")).strip()
        if not nm or not bench:
            continue
        v = _parse_numeric(val_str)
        d = _parse_date(date_str)
        if v is None or d is None:
            continue
        key = (nm, bench)
        if key not in latest or d > latest[key][0]:
            latest[key] = (d, v)

    # Collect athlete's benchmarks
    athlete_benches = {
        bench: v
        for (nm, bench), (_, v) in latest.items()
        if nm == name
    }
    if not athlete_benches:
        return []

    results = []
    for bench, athlete_val in athlete_benches.items():
        peer_vals = [
            v for nm in prog_members
            if (v := (latest.get((nm, bench)) or (None, None))[1]) is not None
        ]
        if len(peer_vals) < 2:
            continue
        peer_vals_sorted = sorted(peer_vals)
        median = statistics.median(peer_vals_sorted)
        n = len(peer_vals_sorted)
        rank = sum(1 for v in peer_vals_sorted if v <= athlete_val)
        pct = round(rank / (n + 1) * 100)  # +1 to include the athlete

        if athlete_val > median * 1.02:
            direction = "above"
        elif athlete_val < median * 0.98:
            direction = "below"
        else:
            direction = "at"

        results.append({
            "benchmark": bench,
            "athlete_value": athlete_val,
            "peer_median": round(median, 1),
            "peer_count": n,
            "percentile": pct,
            "direction": direction,
        })

    results.sort(key=lambda x: -abs(x["percentile"] - 50))  # most divergent first
    return results


def churn_risk_score(name, engagement_results, trend_results,
                     rec_by_name=None, last_contact_by_name=None):
    """Composite churn risk score 0–100 for a single athlete.

    Factors (higher = more at risk):
      - Days since last log (major signal)
      - Declining benchmark trends
      - High soreness/stress or low motivation in recovery survey
      - No recent coach contact
    Returns {"score": int, "label": str, "factors": [str]}
    """
    eng = next((e for e in engagement_results if e["name"] == name), None)
    days = (eng or {}).get("days_since")
    never = (eng or {}).get("last_logged") == "never"

    score = 0
    factors = []

    # Days since log (0-60 points)
    if never:
        score += 60
        factors.append("Never logged")
    elif days is None:
        score += 50
        factors.append("No log data")
    elif days >= 60:
        score += 55
        factors.append(f"{days}d since last log")
    elif days >= 45:
        score += 45
        factors.append(f"{days}d since last log")
    elif days >= 28:
        score += 30
        factors.append(f"{days}d since last log")
    elif days >= 14:
        score += 15
        factors.append(f"{days}d since last log")
    elif days >= 7:
        score += 5

    # Declining benchmark trends (0-15 points)
    signals = trend_results.get(name, [])
    n_declining = sum(1 for s in signals if s["trend"] == "declining")
    n_below_peak = sum(1 for s in signals if s["peak_drop_flag"])
    if n_declining:
        pts = min(n_declining * 5, 15)
        score += pts
        factors.append(f"{n_declining} declining benchmark{'s' if n_declining > 1 else ''}")
    elif n_below_peak:
        score += min(n_below_peak * 3, 9)
        factors.append(f"{n_below_peak} below peak")

    # Recovery flags (0-15 points)
    rec_row = (rec_by_name or {}).get(name)
    if rec_row:
        def _n(v):
            try:
                return float(str(v).strip())
            except (ValueError, TypeError):
                return None
        s = _n(rec_row.get("Soreness"))
        st_ = _n(rec_row.get("Stress"))
        m = _n(rec_row.get("Motivation"))
        if s is not None and s >= 7:
            score += 8
            factors.append(f"High soreness ({s:.0f}/10)")
        if st_ is not None and st_ >= 7:
            score += 8
            factors.append(f"High stress ({st_:.0f}/10)")
        if m is not None and m <= 3:
            score += 8
            factors.append(f"Low motivation ({m:.0f}/10)")
        # Cap rec contribution at 15
        score = min(score, (score - max(0, score - 100)))

    # No coach contact (0-10 points)
    last_contact = (last_contact_by_name or {}).get(name)
    days_contact = (TODAY - last_contact).days if last_contact else None
    if days_contact is None and (days is None or days >= 28):
        score += 10
        factors.append("No recent coach contact")
    elif days_contact and days_contact >= 28:
        score += 5

    score = min(score, 100)

    if score >= 60:
        label = "🔴 Critical"
    elif score >= 35:
        label = "🟡 Elevated"
    elif score >= 15:
        label = "🟠 Moderate"
    else:
        label = "🟢 Low"

    return {"score": score, "label": label, "factors": factors}


def coach_capacity(athletes, pr_records, data_records, engagement_results, bespoke_coaches):
    """Per-coach summary: athlete count, active count, avg days since log, flagged count.

    bespoke_coaches: set of coach full-name strings (from _COACH_ABBREV values).
    Returns list of dicts sorted by athlete count desc.
    """
    data_by_name = {}
    for r in (data_records or []):
        nm = str(r.get("Full Name", "")).strip()
        if nm:
            data_by_name[nm] = r

    last_logged = {}
    for r in (pr_records or []):
        nm = str(r.get("Athlete Name", "")).strip()
        d = _parse_date(str(r.get("Date", "")))
        if nm and d and (nm not in last_logged or d > last_logged[nm]):
            last_logged[nm] = d

    eng_by_name = {e["name"]: e for e in (engagement_results or [])}

    by_coach = {}
    for a in athletes:
        nm = a["name"]
        prog = str(data_by_name.get(nm, {}).get("Programme", "")).strip()
        if prog not in bespoke_coaches:
            continue
        coach = prog
        by_coach.setdefault(coach, []).append(nm)

    rows = []
    for coach, names in sorted(by_coach.items()):
        active = sum(
            1 for nm in names
            if nm in last_logged and (TODAY - last_logged[nm]).days < 28
        )
        days_list = [(TODAY - last_logged[nm]).days for nm in names if nm in last_logged]
        avg_days = round(sum(days_list) / len(days_list)) if days_list else None
        flagged = sum(1 for nm in names if (eng_by_name.get(nm) or {}).get("flag"))
        rows.append({
            "Coach": coach,
            "Athletes": len(names),
            "Active (28d)": active,
            "Active %": f"{round(active / len(names) * 100)}%" if names else "—",
            "Avg Days Since Log": str(avg_days) if avg_days is not None else "—",
            "Needs Attention": flagged,
        })

    rows.sort(key=lambda x: -x["Athletes"])
    return rows


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


def pr_velocity(pr_records, min_points=2):
    """Improvement rate per (athlete, benchmark) as % per month.

    Returns {athlete_name: [
        {benchmark, rate_pct_per_month, first_date, last_date, data_points, direction}
    ]} sorted by abs(rate) desc per athlete.
    direction: 'improving' if > 0.3%/month, 'declining' if < -0.3%/month, else 'flat'.
    """
    series = {}
    for rec in pr_records:
        name = str(rec.get("Athlete Name", "")).strip()
        bench = str(rec.get("Benchmark Name", "")).strip()
        d = _parse_date(str(rec.get("Date", "")))
        v = _parse_numeric(str(rec.get("Value", "")))
        if name and bench and d and v is not None:
            series.setdefault((name, bench), []).append((d, v))

    results = {}
    for (athlete, bench), pts in series.items():
        pts.sort(key=lambda x: x[0])
        if len(pts) < min_points:
            continue
        first_date, _ = pts[0]
        last_date, _ = pts[-1]
        span_days = (last_date - first_date).days
        if span_days < 7:
            continue
        ys = [v for _, v in pts]
        if not ys or ys[0] <= 0:
            continue
        s = _slope(pts)
        entries_per_month = len(pts) / (span_days / 30.44)
        rate_pct_per_month = round(s * entries_per_month * 100, 2)
        direction = (
            "improving" if rate_pct_per_month > 0.3
            else "declining" if rate_pct_per_month < -0.3
            else "flat"
        )
        results.setdefault(athlete, []).append({
            "benchmark": bench,
            "rate_pct_per_month": rate_pct_per_month,
            "first_date": first_date,
            "last_date": last_date,
            "data_points": len(pts),
            "direction": direction,
        })

    for athlete in results:
        results[athlete].sort(key=lambda x: -abs(x["rate_pct_per_month"]))
    return results


_LB_CATEGORIES = {
    "Weightlifting": ["clean", "snatch", "jerk"],
    "Strength": ["squat", "deadlift", "press", "bench"],
    "Gymnastics": ["pull", "muscle", "hspu", "handstand", "ring dip", "toes-to-bar", "toes to bar", "ttb"],
    "Conditioning": ["row", "run", "bike", "assault", "erg", "400m", "800m", "1km", "1.2km", "2km", "mile", "sprint"],
}

_LB_LOWER_IS_BETTER = ["row", "run", "bike", "assault", "erg", "400m", "800m", "1km", "1.2km", "2km", "5km", "mile", "sprint"]


def leaderboard_data(pr_records):
    """Latest value per (athlete, benchmark) from all-time PR log.

    Returns {
        "latest": {(athlete, benchmark): {"value_str": str, "value_num": float, "date": date}},
        "all_benchmarks": sorted list of benchmark names logged by >= 2 athletes,
        "athletes": sorted list of all athlete names with at least one entry,
        "lower_is_better": {benchmark_name: bool},
        "category": {benchmark_name: category_name | None},
    }
    """
    latest = {}
    for rec in pr_records:
        name = str(rec.get("Athlete Name", "")).strip()
        bench = str(rec.get("Benchmark Name", "")).strip()
        d = _parse_date(str(rec.get("Date", "")))
        v_str = str(rec.get("Value", "")).strip()
        v_num = _parse_numeric(v_str)
        if name and bench and d and v_num is not None:
            key = (name, bench)
            if key not in latest or d > latest[key]["date"]:
                latest[key] = {"value_str": v_str, "value_num": v_num, "date": d}

    # Only keep benchmarks where >= 2 athletes have a value
    from collections import Counter
    bench_counts = Counter(b for _, b in latest.keys())
    shared_benchmarks = {b for b, c in bench_counts.items() if c >= 2}

    all_benchmarks = sorted(shared_benchmarks)
    all_athletes = sorted({a for a, _ in latest.keys()})

    lower_is_better = {}
    category_map = {}
    for bench in all_benchmarks:
        bl = bench.lower()
        lower_is_better[bench] = any(kw in bl for kw in _LB_LOWER_IS_BETTER)
        cat = None
        for cat_name, keywords in _LB_CATEGORIES.items():
            if any(kw in bl for kw in keywords):
                cat = cat_name
                break
        category_map[bench] = cat

    return {
        "latest": {k: v for k, v in latest.items() if k[1] in shared_benchmarks},
        "all_benchmarks": all_benchmarks,
        "athletes": all_athletes,
        "lower_is_better": lower_is_better,
        "category": category_map,
    }


def session_compliance(pr_records, data_records, weeks=4):
    """Compliance % per athlete: unique training days logged vs programme expectation.

    Expected sessions derived from programme name:
      "2 Sessions Per Day" → 14 sessions/week
      "1 Session Per Day"  →  7 sessions/week
      Otherwise            →  5 sessions/week (default)

    Returns {athlete_name: {"actual": int, "expected": int, "pct": int, "label": str}}
    """
    from collections import defaultdict

    data_by_name = {}
    for rec in (data_records or []):
        nm = str(rec.get("Full Name", "")).strip()
        if nm:
            data_by_name[nm] = rec

    cutoff = TODAY - dt.timedelta(weeks=weeks)

    log_days_by_name = defaultdict(set)
    for rec in pr_records:
        nm = str(rec.get("Athlete Name", "")).strip()
        d = _parse_date(str(rec.get("Date", "")))
        if nm and d and d > cutoff:
            log_days_by_name[nm].add(d)

    results = {}
    for name, days_set in log_days_by_name.items():
        prog = str(data_by_name.get(name, {}).get("Programme", "")).lower()
        if "2 session" in prog:
            expected_per_week = 14
        elif "1 session" in prog:
            expected_per_week = 7
        else:
            expected_per_week = 5

        expected_total = expected_per_week * weeks
        actual = len(days_set)
        pct = round(actual / expected_total * 100) if expected_total else 0

        if pct >= 80:
            label = f"✅ {pct}%"
        elif pct >= 50:
            label = f"🟡 {pct}%"
        else:
            label = f"🔴 {pct}%"

        results[name] = {
            "actual": actual,
            "expected": expected_total,
            "pct": pct,
            "label": label,
        }

    return results


def cohort_retention(pr_records, min_cohort_size=2):
    """Group athletes by month of first PR log entry, compute 30/60/90-day retention.

    Retention = % of cohort who logged again within N days of their first entry.
    Only computes a window if enough time has elapsed since cohort start.
    Returns list of {cohort (YYYY-MM), n, pct_30d, pct_60d, pct_90d} sorted newest first.
    """
    from collections import defaultdict

    first_log = {}
    log_dates_by_name = defaultdict(list)
    for rec in pr_records:
        name = str(rec.get("Athlete Name", "")).strip()
        d = _parse_date(str(rec.get("Date", "")))
        if name and d:
            if name not in first_log or d < first_log[name]:
                first_log[name] = d
            log_dates_by_name[name].append(d)

    cohorts = defaultdict(list)
    for name, fd in first_log.items():
        cohorts[fd.strftime("%Y-%m")].append(name)

    results = []
    for cohort_month in sorted(cohorts.keys(), reverse=True):
        names = cohorts[cohort_month]
        if len(names) < min_cohort_size:
            continue
        cohort_start = _parse_date(cohort_month + "-01")
        days_elapsed = (TODAY - cohort_start).days if cohort_start else 0

        def _retained(name, window_days):
            fd = first_log[name]
            cutoff = fd + dt.timedelta(days=window_days)
            return any(d > fd and d <= cutoff for d in log_dates_by_name[name])

        n = len(names)
        r30 = round(sum(1 for nm in names if _retained(nm, 30)) / n * 100) if days_elapsed >= 30 else None
        r60 = round(sum(1 for nm in names if _retained(nm, 60)) / n * 100) if days_elapsed >= 60 else None
        r90 = round(sum(1 for nm in names if _retained(nm, 90)) / n * 100) if days_elapsed >= 90 else None
        results.append({
            "cohort": cohort_month,
            "n": n,
            "pct_30d": r30,
            "pct_60d": r60,
            "pct_90d": r90,
        })

    return results


def training_load(pr_records, weeks=12):
    """Weekly training load per athlete: unique session days per calendar week.

    pr_records: list of dicts from the PR Log tab (keys: Athlete Name, Date, ...)
    weeks: how many calendar weeks to look back from today

    Returns:
        {athlete_name: [{"week": "YYYY-WW", "week_start": "YYYY-MM-DD", "sessions": int}, ...]}
        Sorted oldest-first. Only includes athletes with at least one log in the window.
    """
    cutoff = TODAY - dt.timedelta(days=weeks * 7)

    # Collect unique (athlete, date) pairs within the window
    days_by_athlete = {}
    for rec in pr_records:
        name = str(rec.get("Athlete Name", "")).strip()
        d = _parse_date(str(rec.get("Date", "")))
        if not name or not d:
            continue
        if d < cutoff:
            continue
        days_by_athlete.setdefault(name, set()).add(d)

    results = {}
    for name, day_set in days_by_athlete.items():
        # Group unique dates by ISO week
        week_days = {}
        for d in day_set:
            week_key = d.strftime("%G-W%V")
            week_start = d - dt.timedelta(days=d.weekday())
            week_days.setdefault(week_key, {"week_start": week_start, "dates": set()})
            week_days[week_key]["dates"].add(d)

        if not week_days:
            continue

        # Determine full range of weeks between first and last log
        all_week_starts = sorted(v["week_start"] for v in week_days.values())
        first_monday = all_week_starts[0]
        last_monday = all_week_starts[-1]

        # Build ordered list filling gaps with 0
        output = []
        current = first_monday
        while current <= last_monday:
            wk = current.strftime("%G-W%V")
            sessions = len(week_days[wk]["dates"]) if wk in week_days else 0
            output.append({
                "week": wk,
                "week_start": current.isoformat(),
                "sessions": sessions,
            })
            current += dt.timedelta(weeks=1)

        results[name] = output

    return results


def multi_benchmark_decline_alerts(trend_results, min_declining=3):
    """Athletes with min_declining+ benchmarks simultaneously declining.

    Returns list of (athlete_name, declining_count, [benchmark_names]) sorted by count desc.
    """
    alerts = []
    for athlete, signals in trend_results.items():
        declining = [s["benchmark"] for s in signals if s["trend"] == "declining"]
        if len(declining) >= min_declining:
            alerts.append((athlete, len(declining), declining))
    alerts.sort(key=lambda x: -x[1])
    return alerts


def duplicate_candidates(athletes, data_records, pr_records, threshold=0.82):
    """Detect suspiciously similar athlete names across Benchmarks, _DATA, and PR Log.

    Returns a list of dicts sorted by similarity descending:
        [{"name_a": str, "name_b": str, "score": float, "sources": str}]

    'sources' is a list of which data sources each name appears in, e.g.
        ["benchmarks", "pr_log"] for name_a and ["data"] for name_b
        — formatted as "name_a: benchmarks,pr_log | name_b: data"

    threshold: minimum SequenceMatcher ratio to flag (0.82 ~= one letter swap or
               missing initial). Exact matches are excluded.
    """
    import difflib

    # Collect name -> set of sources
    name_sources = {}

    for a in (athletes or []):
        nm = str(a.get("name", "")).strip()
        if nm:
            name_sources.setdefault(nm, set()).add("benchmarks")

    for rec in (data_records or []):
        nm = str(rec.get("Full Name", "")).strip()
        if nm:
            name_sources.setdefault(nm, set()).add("data")

    for rec in (pr_records or []):
        nm = str(rec.get("Athlete Name", "")).strip()
        if nm:
            name_sources.setdefault(nm, set()).add("pr_log")

    names = list(name_sources.keys())
    results = []
    seen = set()

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a = names[i]
            b = names[j]

            # Skip exact matches (case-insensitive)
            if a.lower() == b.lower():
                continue

            pair_key = (min(a, b), max(a, b))
            if pair_key in seen:
                continue
            seen.add(pair_key)

            score = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
            if score >= threshold:
                src_a = ",".join(sorted(name_sources[a]))
                src_b = ",".join(sorted(name_sources[b]))
                results.append({
                    "name_a": a,
                    "name_b": b,
                    "score": round(score, 4),
                    "sources": f"{a}: {src_a} | {b}: {src_b}",
                })

    results.sort(key=lambda x: -x["score"])
    return results


# ── Grandslam retention scoring ───────────────────────────────────────────────

def grandslam_score(athletes, pr_records, data_records, today=None):
    """Score each athlete for LTV potential and assign a journey stage.

    Whale score (0–100) combines four signals:
      Tenure     (0–25) — days since first log, 365 days = full score
      Recency    (0–25) — days since last log (≤14 = 25, ≤28 = 15, ≤44 = 5, else 0)
      Engagement (0–25) — sessions in last 90 days, 45 sessions = full score
      Plan tier  (0–25) — Bespoke=25, named plan=15, unknown=5

    Journey stages (checked in order):
      🏆 Elite      — Bespoke subscription (any tenure/activity)
      ☠️ Churned    — 60+ days since last log
      ⚠️ Drifting   — 28–59 days since last log
      💎 Lifer      — 180+ days tenure, logged within 27 days
      ⭐ Established — 90–179 days tenure, logged within 44 days
      🔥 Active     — 30–89 days tenure
      🌱 New        — < 30 days tenure

    Returns list of dicts sorted by whale_score descending:
        name, days_tenure, sessions_90d, days_since_log,
        plan, whale_score, journey_stage, status_label
    """
    if today is None:
        today = dt.date.today()

    first_log_by_nm = {}
    last_log_by_nm = {}
    sessions_90d = {}
    cutoff_90 = today - dt.timedelta(days=90)

    for r in (pr_records or []):
        nm = str(r.get("Athlete Name", "")).strip()
        if not nm:
            continue
        d = _parse_date(str(r.get("Date", "")))
        if not d:
            continue
        if nm not in first_log_by_nm or d < first_log_by_nm[nm]:
            first_log_by_nm[nm] = d
        if nm not in last_log_by_nm or d > last_log_by_nm[nm]:
            last_log_by_nm[nm] = d
        if d >= cutoff_90:
            sessions_90d[nm] = sessions_90d.get(nm, 0) + 1

    data_by_nm = {str(r.get("Full Name", "")).strip(): r for r in (data_records or [])}

    results = []
    for a in (athletes or []):
        nm = str(a.get("name", "")).strip() if isinstance(a, dict) else str(a).strip()
        if not nm:
            continue

        dr = data_by_nm.get(nm, {})
        plan = str(dr.get("Subscription Plan", "")).strip().lower()
        plan_score = 25 if plan == "bespoke" else (15 if plan else 5)

        first = first_log_by_nm.get(nm)
        days_tenure = (today - first).days if first else 0
        tenure_score = min(25, round(days_tenure / 365 * 25, 1))

        last = last_log_by_nm.get(nm)
        days_since = (today - last).days if last else 999
        if days_since <= 14:
            recency_score = 25
        elif days_since <= 28:
            recency_score = 15
        elif days_since <= 44:
            recency_score = 5
        else:
            recency_score = 0

        s90 = sessions_90d.get(nm, 0)
        engagement_score = min(25, round(s90 / 45 * 25, 1))

        whale_score = int(tenure_score + recency_score + engagement_score + plan_score)

        if plan == "bespoke":
            stage = "🏆 Elite"
            label = "Elite"
        elif days_since >= 60:
            stage = "☠️ Churned"
            label = "Inactive"
        elif days_since >= 28:
            stage = "⚠️ Drifting"
            label = "At Risk"
        elif days_tenure >= 180:
            stage = "💎 Lifer"
            label = "Lifer"
        elif days_tenure >= 90:
            stage = "⭐ Established"
            label = "Active"
        elif days_tenure >= 30:
            stage = "🔥 Active"
            label = "Active"
        else:
            stage = "🌱 New"
            label = "Active"

        results.append({
            "name": nm,
            "days_tenure": days_tenure,
            "sessions_90d": s90,
            "days_since_log": days_since,
            "plan": plan,
            "whale_score": whale_score,
            "journey_stage": stage,
            "status_label": label,
        })

    results.sort(key=lambda x: -x["whale_score"])
    return results




def activation_scores(pr_records, athletes):
    """Measure how broadly each athlete has engaged across available benchmarks.

    Activation pct = unique benchmarks logged by athlete /
                     total distinct benchmarks ever logged by anyone.

    Highly activated athletes are more invested and less likely to churn.

    Returns {athlete_name: {unique_benchmarks, total_benchmarks, activation_pct}}
    """
    all_benchmarks = set()
    athlete_benchmarks = {}

    for r in (pr_records or []):
        nm = str(r.get("Athlete Name", "")).strip()
        bench = str(r.get("Benchmark Name", "")).strip()
        if not nm or not bench:
            continue
        all_benchmarks.add(bench)
        athlete_benchmarks.setdefault(nm, set()).add(bench)

    total = len(all_benchmarks)
    results = {}
    for a in (athletes or []):
        nm = str(a.get("name", "")).strip() if isinstance(a, dict) else str(a).strip()
        if not nm:
            continue
        unique = len(athlete_benchmarks.get(nm, set()))
        pct = round(100 * unique / total) if total else 0
        results[nm] = {
            "unique_benchmarks": unique,
            "total_benchmarks": total,
            "activation_pct": pct,
        }

    return results


# ─────────────────────────── Gym Owner Referral Programme ───────────────────

def _safe_float(s):
    """Parse a string like '£11' or '11.00' to float, returning 0.0 on failure."""
    import re
    cleaned = re.sub(r"[£$€,\s]", "", str(s)).strip()
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def gym_credit_summary(gym_referrals, gym_directory, today=None):
    """Compute per-gym referral credits, net owed, and alerts.

    Returns list of dicts sorted by most active referrals first:
    {gym_name, gym_code, owner_name, owner_email, monthly_fee, coach,
     active_count, total_monthly_credit, net_owed, excess, pct_offset,
     cap_hit, referrals, alerts}
    """
    import datetime as _dt
    from collections import defaultdict

    if today is None:
        today = TODAY

    gym_by_code = {}
    for gym in gym_directory:
        code = str(gym.get("Gym Code", "")).strip().upper()
        if code:
            gym_by_code[code] = {
                "gym_name":    str(gym.get("Gym Name", "")).strip(),
                "gym_code":    code,
                "owner_name":  str(gym.get("Owner Name", "")).strip(),
                "owner_email": str(gym.get("Owner Email", "")).strip(),
                "tier":        str(gym.get("Tier", "")).strip(),
                "monthly_fee": _safe_float(str(gym.get("Monthly Fee", "0"))),
                "coach":       str(gym.get("Coach", "")).strip(),
            }

    refs_by_gym = defaultdict(list)
    for ref in gym_referrals:
        code = str(ref.get("Gym Code", "")).strip().upper()
        if code:
            refs_by_gym[code].append(ref)

    all_codes = set(gym_by_code.keys()) | set(refs_by_gym.keys())
    results = []

    for code in sorted(all_codes):
        gym_info = gym_by_code.get(code, {
            "gym_name": code, "gym_code": code,
            "owner_name": "", "owner_email": "",
            "tier": "", "monthly_fee": 0.0, "coach": "",
        })
        monthly_fee = gym_info["monthly_fee"]

        total_credit = 0.0
        active_count = 0
        alerts = []
        ref_rows = []

        for ref in refs_by_gym.get(code, []):
            status       = str(ref.get("Status", "")).strip()
            credit_end_s = str(ref.get("Credit End", "")).strip()
            monthly_cr   = _safe_float(str(ref.get("Monthly Credit", "0")))
            referred     = str(ref.get("Referred Member", "")).strip()
            product      = str(ref.get("Product", "")).strip()
            ref_id       = str(ref.get("Referral ID", "")).strip()

            credit_end    = _parse_date(credit_end_s) if credit_end_s else None
            credit_active = False

            if status == "Active":
                if credit_end is None or credit_end >= today:
                    credit_active = True
                    if credit_end is not None:
                        days_left = (credit_end - today).days
                        if days_left <= 30:
                            alerts.append({
                                "type": "expiring", "ref_id": ref_id,
                                "member": referred, "product": product,
                                "message": (
                                    f"{referred} ({product}) — credit expires "
                                    f"in {days_left} day(s)"
                                ),
                            })
                else:
                    alerts.append({
                        "type": "stale", "ref_id": ref_id,
                        "member": referred, "product": product,
                        "message": (
                            f"{referred} ({product}) credit window closed — "
                            "mark as Expired in Gym Referrals tab"
                        ),
                    })
            elif status == "Cancelled":
                alerts.append({
                    "type": "cancelled", "ref_id": ref_id,
                    "member": referred, "product": product,
                    "message": f"{referred} cancelled — credit stopped",
                })

            if credit_active:
                total_credit += monthly_cr
                active_count += 1

            ref_rows.append({
                **{k: str(v).strip() for k, v in ref.items()},
                "credit_active": credit_active,
            })

        net_owed   = max(0.0, monthly_fee - total_credit) if monthly_fee else None
        excess     = max(0.0, total_credit - monthly_fee) if monthly_fee else 0.0
        pct_offset = round(total_credit / monthly_fee * 100, 1) if monthly_fee else None
        cap_hit    = monthly_fee > 0 and total_credit >= monthly_fee

        if cap_hit:
            alerts.append({
                "type": "cap_hit", "ref_id": "", "member": "", "product": "",
                "message": (
                    f"Credit cap reached — {gym_info['gym_name']} owes £0 this month. "
                    f"£{excess:.2f} rolls forward."
                ),
            })

        results.append({
            **gym_info,
            "active_count":         active_count,
            "total_monthly_credit": total_credit,
            "net_owed":             net_owed,
            "excess":               excess,
            "pct_offset":           pct_offset,
            "cap_hit":              cap_hit,
            "referrals":            ref_rows,
            "alerts":               alerts,
        })

    results.sort(key=lambda x: x["active_count"], reverse=True)
    return results
