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
