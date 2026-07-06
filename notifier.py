"""
Weekly coaching digest — Slack message + email after each sync run.

Covers: recovery flags, engagement/dropout, performance concerns,
        milestones (new PBs), consistency wins.
"""
import json
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

import analytics
import config

try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()


def _fmt_date(d):
    if hasattr(d, "strftime"):
        return d.strftime("%-d %b")
    try:
        import datetime as dt
        return dt.date.fromisoformat(str(d)).strftime("%-d %b")
    except Exception:
        return str(d)


def build_digest(date, engagement_results, trend_results,
                 rec_alert_rows, milestones, consistency_wins,
                 declining_singles=None):
    """Returns (plain_text, slack_text) — action-focused, fits ~1 hour of coaching work."""
    sections_plain = []
    sections_slack = []

    # ---- recovery flags — always show, usually small ----
    if rec_alert_rows:
        lines_p = [f"  • {r[0]} — {r[1]} (submitted {r[2]})" for r in rec_alert_rows]
        lines_s = [f"  • *{r[0]}* — {r[1]} (submitted {r[2]})" for r in rec_alert_rows]
        sections_plain.append(("🔴 RECOVERY FLAGS — message these athletes", lines_p))
        sections_slack.append(("🔴 *RECOVERY FLAGS* — message these athletes", lines_s))

    # ---- single results that came in worse than last time — flag, don't congratulate ----
    if declining_singles:
        lines_p, lines_s = [], []
        for name, bench, value, prev in declining_singles:
            line = f"  • {name} — {bench}: {value} (previous: {prev})"
            lines_p.append(line)
            lines_s.append(line.replace(f"  • {name}", f"  • *{name}*", 1))
        sections_plain.append(("📉 RESULT DOWN — not auto-messaged, worth a check-in", lines_p))
        sections_slack.append(("📉 *RESULT DOWN* — not auto-messaged, worth a check-in", lines_s))

    # ---- new PRs to celebrate ----
    if milestones:
        lines_p, lines_s = [], []
        for m in milestones:
            name, bench, val, prev = m
            if prev == "first entry":
                line = f"  • {name} — {bench}: {val} (first entry)"
            else:
                line = f"  • {name} — {bench}: {val} (was: {prev})"
            lines_p.append(line)
            lines_s.append(line.replace(f"  • {name}", f"  • *{name}*", 1))
        sections_plain.append(("🏆 CELEBRATE TODAY — send congrats", lines_p))
        sections_slack.append(("🏆 *CELEBRATE TODAY* — send congrats", lines_s))

    # ---- consistency streaks to acknowledge ----
    if consistency_wins:
        lines_p = [f"  • {n} — {w} consecutive weeks logging" for n, w in consistency_wins]
        lines_s = [f"  • *{n}* — {w} consecutive weeks logging" for n, w in consistency_wins]
        sections_plain.append(("✅ CONSISTENCY STREAKS — give a shout out", lines_p))
        sections_slack.append(("✅ *CONSISTENCY STREAKS* — give a shout out", lines_s))

    # ---- engagement: newly flagged this week (28–35 days) + approaching (14–27 days, top 5) ----
    newly_flagged, approaching = [], []
    for e in engagement_results:
        days = e.get("days_since")
        if not isinstance(days, int):
            continue
        if 28 <= days <= 35:
            newly_flagged.append(e)
        elif 14 <= days <= 27:
            approaching.append(e)

    approaching_top = sorted(approaching, key=lambda e: -e["days_since"])[:5]

    if newly_flagged:
        lines_p, lines_s = [], []
        for e in newly_flagged:
            line = f"  • {e['name']} — just hit {e['days_since']} days inactive"
            lines_p.append(line)
            lines_s.append(line.replace(f"  • {e['name']}", f"  • *{e['name']}*", 1))
        sections_plain.append(("⚠️ NEWLY INACTIVE THIS WEEK — reach out now", lines_p))
        sections_slack.append(("⚠️ *NEWLY INACTIVE THIS WEEK* — reach out now", lines_s))

    if approaching_top:
        lines_p, lines_s = [], []
        for e in approaching_top:
            line = f"  • {e['name']} — {e['days_since']} days inactive"
            lines_p.append(line)
            lines_s.append(line.replace(f"  • {e['name']}", f"  • *{e['name']}*", 1))
        sections_plain.append(("🟡 APPROACHING DROPOUT — worth a quick message (top 5)", lines_p))
        sections_slack.append(("🟡 *APPROACHING DROPOUT* — worth a quick message (top 5)", lines_s))

    # ---- multi-benchmark declines: top 5 athletes with 3+ benchmarks declining ----
    multi_decline = []
    for athlete, signals in trend_results.items():
        declining = [s["benchmark"] for s in signals if s["trend"] == "declining"]
        if len(declining) >= 3:
            multi_decline.append((athlete, len(declining), declining))
    multi_decline.sort(key=lambda x: -x[1])

    if multi_decline:
        lines_p, lines_s = [], []
        for name, count, benches in multi_decline[:5]:
            line = f"  • {name} — {count} benchmarks declining ({', '.join(benches[:3])}{'…' if len(benches) > 3 else ''})"
            lines_p.append(line)
            lines_s.append(line.replace(f"  • {name}", f"  • *{name}*", 1))
        sections_plain.append(("📉 MULTI-BENCHMARK DECLINE — check in on training quality", lines_p))
        sections_slack.append(("📉 *MULTI-BENCHMARK DECLINE* — check in on training quality", lines_s))

    header = f"JST Compete — Today's Coaching Actions | {date}"

    if not sections_plain:
        msg = f"{header}\n\nNothing to action today — all athletes on track. 💪"
        return msg, msg

    # plain text
    plain_parts = ["=" * 60, header, "=" * 60, ""]
    for heading, lines in sections_plain:
        plain_parts.append(f"{heading} ({len(lines)})")
        plain_parts.extend(lines)
        plain_parts.append("")
    plain_parts.append("JST Compete Coaching Platform")
    plain = "\n".join(plain_parts)

    # slack text
    slack_parts = [f"*{header}*", ""]
    for heading, lines in sections_slack:
        slack_parts.append(f"{heading} ({len(lines)})")
        slack_parts.extend(lines)
        slack_parts.append("")
    slack = "\n".join(slack_parts).rstrip()

    return plain, slack


def send_slack(slack_text):
    url = config.SLACK_WEBHOOK_URL
    if not url:
        print("  ! Slack not configured (SLACK_WEBHOOK_URL missing)")
        return
    payload = json.dumps({"text": slack_text}).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=10, context=_SSL_CONTEXT)


def send_slack_message(channel, text):
    """Post to a specific Slack channel using the Bot API (chat.postMessage).

    Requires SLACK_BOT_TOKEN (xoxb-...) in config/secrets.
    The bot must be invited to the target channel first.
    """
    token = config.SLACK_BOT_TOKEN
    if not token:
        return
    payload = json.dumps({"channel": channel, "text": text}).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    urllib.request.urlopen(req, timeout=10, context=_SSL_CONTEXT)


def send_coach_notifications(bench_rows, chal_rows, programme_by_name, coach_channel_map):
    """Send one Slack message per athlete to the relevant coach's channel.

    bench_rows / chal_rows: new PR log rows [date, name, email, bench, value, type, prev, ...]
    programme_by_name:  {athlete_name: programme_string}
    coach_channel_map:  {programme_string: slack_channel_id}  — from the Coaches sheet tab

    Improvements (bench_rows where prev is non-empty) are flagged as 📸 marketing captures.
    Only fires if SLACK_BOT_TOKEN is set and the athlete's programme has a channel mapping.
    Returns count of messages sent.
    """
    if not config.SLACK_BOT_TOKEN:
        return 0

    from collections import defaultdict
    entries_by_athlete = defaultdict(list)
    for row in bench_rows:
        name  = row[1] if len(row) > 1 else ""
        bench = row[3] if len(row) > 3 else ""
        value = row[4] if len(row) > 4 else ""
        prev  = row[6] if len(row) > 6 else ""
        if name:
            entries_by_athlete[name].append((bench, value, "Benchmark", str(prev).strip()))
    for row in chal_rows:
        name  = row[1] if len(row) > 1 else ""
        bench = row[3] if len(row) > 3 else ""
        value = row[4] if len(row) > 4 else ""
        if name:
            entries_by_athlete[name].append((bench, value, "Challenge", ""))

    sent = 0
    for athlete, entries in sorted(entries_by_athlete.items()):
        prog = programme_by_name.get(athlete, "")
        channel = coach_channel_map.get(prog)
        if not channel:
            continue
        count = len(entries)
        lines = [f"🏋️ *{athlete}* logged {count} new result{'s' if count > 1 else ''}:"]
        marketing_captures = []
        declines = []
        for bench, value, kind, prev in entries:
            icon = "🏆" if kind == "Benchmark" else "🎯"
            comparison = analytics.compare_result(bench, prev, value) if prev and kind == "Benchmark" else None
            if comparison == "improved":
                lines.append(f"  {icon} *{bench}*: {prev} → *{value}* 📸")
                marketing_captures.append(f"{bench}: {prev} → {value}")
            elif comparison == "declined":
                lines.append(f"  ⚠️ *{bench}*: {prev} → *{value}* (down — worth a check-in)")
                declines.append(f"{bench}: {prev} → {value}")
            elif prev and kind == "Benchmark":
                lines.append(f"  {icon} *{bench}*: {prev} → *{value}*")
            else:
                lines.append(f"  {icon} *{bench}*: {value}")
        if marketing_captures:
            lines.append(f"  _📸 Marketing capture{'s' if len(marketing_captures) > 1 else ''} — consider capturing the before/after story_")
        if declines:
            lines.append(f"  _⚠️ {len(declines)} result{'s' if len(declines) > 1 else ''} down from last time — not auto-messaged to the athlete_")
        if prog:
            lines.append(f"  _{prog}_")
        try:
            send_slack_message(channel, "\n".join(lines))
            sent += 1
        except Exception as e:
            print(f"  ! Slack notify failed for {athlete} → {channel}: {e}")

    return sent


def send_email(subject, plain_text):
    if config.DRY_RUN:
        print(f"[DRY_RUN] Would send email: {subject}")
        return
    if not config.SMTP_FROM or not config.SMTP_PASSWORD:
        print("  ! Email not configured (SMTP_FROM / SMTP_PASSWORD missing)")
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = config.SMTP_TO
    msg.set_content(plain_text)
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.ehlo()
        s.starttls()
        s.login(config.SMTP_FROM, config.SMTP_PASSWORD)
        s.send_message(msg)


def _send_email_to(smtp_from, smtp_password, to_addr, subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_addr
    msg.set_content(body)
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.ehlo()
        s.starttls()
        s.login(smtp_from, smtp_password)
        s.send_message(msg)


def _send_html_email_to(smtp_from, smtp_password, to_addr, subject, plain_body, html_body):
    """Send a multipart email with plain-text fallback and HTML version."""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_addr
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.ehlo()
        s.starttls()
        s.login(smtp_from, smtp_password)
        s.send_message(msg)


def _parse_date_email(s):
    import datetime as dt_mod
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return dt_mod.datetime.strptime(str(s).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _build_athlete_progress_email(name, new_prs, streak_weeks, next_comp, archetype_id=None):
    first = name.split()[0]
    lines = [
        f"Hi {first},",
        "",
        "Here's your weekly training snapshot from JST Compete:",
        "",
    ]
    if new_prs:
        lines.append("🏆 New results this week:")
        for bench, value in new_prs:
            lines.append(f"  • {bench}: {value}")
        lines.append("")
    if streak_weeks and streak_weeks >= 4:
        lines.append(f"✅ {streak_weeks} consecutive weeks logging — great discipline, keep it going!")
        lines.append("")
    if next_comp:
        comp_name, days_out = next_comp
        if days_out >= 0:
            w, d = divmod(days_out, 7)
            time_str = f"{w}w {d}d" if w else f"{d}d"
            lines.append(f"🏁 Next competition: {comp_name} — {time_str} away. Stay sharp.")
        else:
            lines.append(f"🏁 {comp_name} was {abs(days_out)}d ago. Great effort — debrief coming soon.")
        lines.append("")
    # Archetype-specific coaching tip
    if archetype_id:
        try:
            import archetypes as _arch
            arch = _arch.get_archetype(archetype_id)
            tip = (arch.get("coach") or {}).get("programming_read", "")
            if tip:
                lines.append(f"\U0001f4a1 Your coaching focus: {tip}")
                lines.append("")
        except Exception:
            pass
    lines.extend([
        "Every result you log makes your coaching sharper. Keep it up.",
        "",
        "— JST Compete Coaching Team",
    ])
    return "\n".join(lines)


def send_all_athlete_progress_emails(bench_rows, consistency_wins, competition_rows, email_by_name, archetype_by_name=None):
    """Email each athlete who has news this week: new PRs, a streak milestone, or an upcoming comp.

    bench_rows: new PR log rows [[date, name, ..., bench, value, ...], ...]
    consistency_wins: [(name, consecutive_weeks), ...]
    competition_rows: rows from Competitions tab [{Athlete Name, Competition Name, Date, ...}]
    email_by_name: {athlete_name: email_address}
    Returns count of emails sent.
    """
    if config.DRY_RUN:
        return 0
    if not config.SMTP_FROM or not config.SMTP_PASSWORD:
        return 0

    import datetime as dt_mod
    today = dt_mod.date.today()

    prs_by_name = {}
    for row in bench_rows:
        if len(row) < 5:
            continue
        name, bench, value = row[1], row[3], row[4]
        if name:
            prs_by_name.setdefault(name, []).append((bench, value))

    streak_by_name = dict(consistency_wins)

    comp_by_name = {}
    for row in competition_rows:
        name = str(row.get("Athlete Name", "")).strip()
        comp_name = str(row.get("Competition Name", "")).strip()
        comp_date = _parse_date_email(str(row.get("Date", "")).strip())
        if not name or not comp_date:
            continue
        days_out = (comp_date - today).days
        if -14 <= days_out <= 84:
            existing = comp_by_name.get(name)
            if existing is None or abs(days_out) < abs(existing[1]):
                comp_by_name[name] = (comp_name, days_out)

    all_names = set(email_by_name.keys()) | set(prs_by_name.keys())
    sent = 0
    for name in sorted(all_names):
        email = email_by_name.get(name)
        if not email:
            continue
        new_prs = prs_by_name.get(name, [])
        streak = streak_by_name.get(name)
        next_comp = comp_by_name.get(name)
        if not new_prs and (not streak or streak < 4) and not next_comp:
            continue
        arch_row = (archetype_by_name or {}).get(name)
        arch_id = str(arch_row.get("Primary Archetype", "")).strip() if arch_row else None
        body = _build_athlete_progress_email(name, new_prs, streak, next_comp, archetype_id=arch_id)
        subject = "Your weekly training snapshot — JST Compete"
        try:
            _send_email_to(config.SMTP_FROM, config.SMTP_PASSWORD, email, subject, body)
            sent += 1
        except Exception as exc:
            print(f"  ! Progress email failed for {name} ({email}): {exc}")

    return sent


def send_reengagement_alerts(engagement_results, programme_by_name, coach_channel_map):
    """Notify each coach in Slack about their athletes who haven't logged recently.

    Sends one consolidated message per coach (not one per athlete) so it doesn't
    flood channels. Only fires when SLACK_BOT_TOKEN is set and the programme has a
    channel mapping. Returns count of coaches notified.
    """
    if not config.SLACK_BOT_TOKEN:
        return 0

    from collections import defaultdict
    flagged = [e for e in engagement_results if e["flag"]]
    if not flagged:
        return 0

    by_coach = defaultdict(list)
    for e in flagged:
        prog = programme_by_name.get(e["name"], "")
        channel = coach_channel_map.get(prog)
        if channel:
            by_coach[channel].append(e)

    sent = 0
    for channel, athletes in by_coach.items():
        lines = [f"⚠️ *{len(athletes)} athlete{'s' if len(athletes) > 1 else ''} need a check-in:*"]
        for e in sorted(athletes, key=lambda x: -(x["days_since"] or 9999)):
            if e["last_logged"] == "never":
                detail = "never logged"
            else:
                detail = f"{e['days_since']}d inactive (last: {_fmt_date(e['last_logged'])})"
            # Include a draft message the coach can copy
            first = e["name"].split()[0]
            draft = (
                f"Hi {first}, just checking in — how's training going? "
                f"Drop me your latest results when you get a chance."
            )
            lines.append(f"  • *{e['name']}* — {detail}")
            lines.append(f"    _Draft:_ \"{draft}\"")
        try:
            send_slack_message(channel, "\n".join(lines))
            sent += 1
        except Exception as exc:
            print(f"  ! Re-engagement Slack notify failed → {channel}: {exc}")

    return sent


def send_weekly_coach_summary(athletes_by_coach, engagement_results, trend_results,
                               milestones_by_name, coach_channel_map):
    """Send each coach a brief squad summary: who's active, who needs attention, any new PRs.

    athletes_by_coach: {programme: [name, ...]}
    milestones_by_name: {name: [(bench, value), ...]}
    Returns count of coaches notified.
    """
    if not config.SLACK_BOT_TOKEN:
        return 0

    eng_by_name = {e["name"]: e for e in engagement_results}
    sent = 0

    for prog, names in sorted(athletes_by_coach.items()):
        channel = coach_channel_map.get(prog)
        if not channel or not names:
            continue

        active = [n for n in names if not (eng_by_name.get(n) or {}).get("flag")]
        flagged = [n for n in names if (eng_by_name.get(n) or {}).get("flag")]

        prs_this_week = []
        for n in names:
            for bench, val in (milestones_by_name.get(n) or []):
                prs_this_week.append(f"*{n}* — {bench}: {val}")

        concerns = []
        for n in names:
            signals = trend_results.get(n, [])
            for s in signals:
                if s["trend"] == "declining":
                    concerns.append(
                        f"*{n}* — {s['benchmark']} declining "
                        f"({s['trend_pct']:+.1f}%/entry)"
                    )

        coach_name = prog.split()[0] if prog else "Coach"
        lines = [f"*📋 {coach_name} — Weekly Squad Summary*", ""]
        lines.append(f"Active this week: {len(active)}/{len(names)} athletes")

        if prs_this_week:
            lines.append("")
            lines.append("🏆 *New results:*")
            lines.extend(f"  • {p}" for p in prs_this_week[:5])
            if len(prs_this_week) > 5:
                lines.append(f"  _...and {len(prs_this_week) - 5} more_")

        if flagged:
            lines.append("")
            lines.append(f"⚠️ *Needs contact ({len(flagged)}):*")
            for n in sorted(flagged, key=lambda x: -(eng_by_name.get(x, {}).get("days_since") or 9999)):
                e = eng_by_name.get(n, {})
                detail = "never logged" if e.get("last_logged") == "never" else f"{e.get('days_since')}d inactive"
                lines.append(f"  • *{n}* — {detail}")

        if concerns:
            lines.append("")
            lines.append(f"📉 *Performance concerns ({len(concerns)}):*")
            lines.extend(f"  • {c}" for c in concerns[:3])

        try:
            send_slack_message(channel, "\n".join(lines))
            sent += 1
        except Exception as exc:
            print(f"  ! Weekly summary Slack notify failed → {prog}: {exc}")

    return sent


def send_monthly_athlete_reports(data_recs, email_by_name, pr_records):
    """Email each athlete their monthly training summary on the 1st of the month.

    Skips athletes with no activity in the previous calendar month.
    Returns count of emails sent.
    """
    if config.DRY_RUN:
        return 0
    if not config.SMTP_FROM or not config.SMTP_PASSWORD:
        return 0

    import datetime as dt_mod
    today = dt_mod.date.today()
    last_month_end = today.replace(day=1) - dt_mod.timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    month_label = last_month_end.strftime("%B %Y")

    month_prs_by_name = {}
    for r in pr_records:
        nm = str(r.get("Athlete Name", "")).strip()
        d_str = str(r.get("Date", "")).strip()
        if nm and last_month_start.isoformat() <= d_str <= last_month_end.isoformat():
            month_prs_by_name.setdefault(nm, []).append(r)

    sent = 0
    for rec in data_recs:
        nm = str(rec.get("Full Name", "")).strip()
        if not nm:
            continue
        email = email_by_name.get(nm) or str(rec.get("Email", "")).strip()
        if not email:
            continue
        month_prs = month_prs_by_name.get(nm, [])
        if not month_prs:
            continue  # no activity — skip
        sessions = len({r.get("Date") for r in month_prs if r.get("Date")})
        benchmarks = [(str(r.get("Benchmark Name", "")), str(r.get("Value", "")))
                      for r in month_prs]
        prog = str(rec.get("Programme", "")).strip()
        goal = str(rec.get("North Star Goal", "")).strip()

        first = nm.split()[0]

        # ── plain text ────────────────────────────────────────────────────────
        plain_lines = [f"Hi {first},", "", f"Your training summary for {month_label}:", "",
                       f"Sessions logged: {sessions}"]
        if benchmarks:
            plain_lines += ["", "Results this month:"]
            for bench, val in benchmarks[:8]:
                plain_lines.append(f"  - {bench}: {val}")
            if len(benchmarks) > 8:
                plain_lines.append(f"  - ...and {len(benchmarks) - 8} more")
        if goal:
            plain_lines += ["", f"North Star Goal: {goal}"]
        if prog:
            plain_lines += ["", f"Programme: {prog}"]
        plain_lines += ["", "Every session you log makes your coaching sharper.",
                        "", "— JST Compete Coaching Team"]
        plain_body = "\n".join(plain_lines)

        # ── HTML ──────────────────────────────────────────────────────────────
        bench_rows_html = "".join(
            f"<tr><td style='padding:6px 12px;border-bottom:1px solid #f0f0f0'>{b}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #f0f0f0;font-weight:600'>{v}</td></tr>"
            for b, v in benchmarks[:8]
        )
        bench_section = (
            f"<h3 style='color:#1a1a1a;margin:24px 0 8px'>🏆 Results this month</h3>"
            f"<table style='width:100%;border-collapse:collapse;font-size:14px'>"
            f"{bench_rows_html}</table>"
            + (f"<p style='color:#888;font-size:13px'>...and {len(benchmarks)-8} more</p>"
               if len(benchmarks) > 8 else "")
        ) if benchmarks else ""
        goal_section = (
            f"<div style='background:#f0f7ff;border-left:4px solid #0066cc;"
            f"padding:12px 16px;margin:20px 0;border-radius:4px'>"
            f"<strong>🎯 North Star Goal</strong><br>{goal}</div>"
        ) if goal else ""
        prog_section = (
            f"<p style='color:#555;font-size:13px'>📋 Programme: <strong>{prog}</strong></p>"
        ) if prog else ""

        html_body = f"""<!DOCTYPE html>
<html><body style='font-family:Arial,sans-serif;max-width:560px;margin:0 auto;color:#1a1a1a'>
<div style='background:#0066cc;padding:24px 32px;border-radius:8px 8px 0 0'>
  <h1 style='color:#fff;margin:0;font-size:22px'>JST Compete</h1>
  <p style='color:#cce0ff;margin:4px 0 0;font-size:14px'>{month_label} Training Summary</p>
</div>
<div style='background:#fff;padding:24px 32px;border:1px solid #e8e8e8;border-top:none;border-radius:0 0 8px 8px'>
  <p>Hi {first},</p>
  <div style='display:flex;gap:16px;margin:20px 0'>
    <div style='text-align:center;background:#f8f9fa;border-radius:8px;padding:16px 24px;flex:1'>
      <div style='font-size:32px;font-weight:700;color:#0066cc'>{sessions}</div>
      <div style='font-size:13px;color:#666;margin-top:4px'>sessions logged</div>
    </div>
    <div style='text-align:center;background:#f8f9fa;border-radius:8px;padding:16px 24px;flex:1'>
      <div style='font-size:32px;font-weight:700;color:#0066cc'>{len(benchmarks)}</div>
      <div style='font-size:13px;color:#666;margin-top:4px'>results recorded</div>
    </div>
  </div>
  {bench_section}
  {goal_section}
  {prog_section}
  <hr style='border:none;border-top:1px solid #f0f0f0;margin:24px 0'>
  <p style='color:#555;font-size:14px'>Every session you log makes your coaching sharper.
  Keep up the consistency.</p>
  <p style='color:#888;font-size:13px'>— JST Compete Coaching Team</p>
</div>
</body></html>"""

        subject = f"Your {month_label} training summary — JST Compete"
        try:
            _send_html_email_to(config.SMTP_FROM, config.SMTP_PASSWORD, email,
                                subject, plain_body, html_body)
            sent += 1
        except Exception as exc:
            print(f"  ! Monthly report failed for {nm} ({email}): {exc}")
    return sent


def send_progress_page_email(smtp_from, smtp_password, to_addr, athlete_name, jst_id, dashboard_url):
    """Email an athlete a link to their read-only progress page."""
    first = athlete_name.split()[0]
    url = f"{dashboard_url.rstrip('/')}/?mode=progress&id={jst_id}"
    subject = f"Your JST Compete Progress Page — {first}"
    plain = (
        f"Hi {first},\n\n"
        f"Here's your personal progress page where you can see your results, "
        f"training consistency, and competition calendar:\n\n"
        f"{url}\n\n"
        f"Keep pushing!\n\nJST Compete"
    )
    html = (
        f"<p>Hi {first},</p>"
        f"<p>Here's your personal progress page where you can see your results, "
        f"training consistency, and competition calendar:</p>"
        f"<p><a href='{url}' style='font-size:16px;font-weight:bold;'>View Your Progress Page →</a></p>"
        f"<p>Keep pushing!<br><strong>JST Compete</strong></p>"
    )
    _send_html_email_to(smtp_from, smtp_password, to_addr, subject, plain, html)


def send_draft_reply_alerts(names, webhook_url=None):
    """Send a Slack notification when AI draft replies are ready for review.

    names: list of athlete names with pending draft replies.
    Uses the provided webhook_url or falls back to SLACK_WEBHOOK_URL.
    Returns True if sent successfully, False otherwise.
    """
    url = webhook_url or config.SLACK_WEBHOOK_URL
    if not url or not names:
        return False
    n = len(names)
    name_list = "\n".join(f"  • {nm}" for nm in sorted(names))
    text = (
        f"📨 *{n} AI draft repl{'y' if n == 1 else 'ies'} ready for review*\n\n"
        f"{name_list}\n\n"
        f"Open the dashboard → Athletes tab → select athlete → scroll to Draft Reply."
    )
    try:
        payload = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10, context=_SSL_CONTEXT)
        return True
    except Exception as e:
        print(f"  ! Draft reply Slack alert failed: {e}")
        return False


def send_digest(date, engagement_results, trend_results,
                rec_alert_rows, milestones, consistency_wins,
                declining_singles=None):
    plain, slack_text = build_digest(
        date, engagement_results, trend_results,
        rec_alert_rows, milestones, consistency_wins,
        declining_singles=declining_singles,
    )
    subject = f"JST Compete Coaching Digest — {date}"

    if config.DRY_RUN:
        print(f"[DRY_RUN] digest preview:\n{plain}\n")
        return

    try:
        send_slack(slack_text)
        print("  Slack digest sent")
    except Exception as e:
        print(f"  ! Slack send failed: {e}")
    try:
        send_email(subject, plain)
        print("  Email digest sent")
    except Exception as e:
        print(f"  ! Email send failed: {e}")


def send_gym_owner_credits(gym_summaries, month_label):
    """Send a monthly credit statement to each gym owner with an email on file.

    gym_summaries: output of analytics.gym_credit_summary()
    month_label:   e.g. "June 2026"
    Returns count of emails sent.
    """
    if not config.SMTP_FROM or not config.SMTP_PASSWORD:
        print("  ! SMTP not configured — gym owner credit emails skipped")
        return 0

    sent = 0
    for gym in gym_summaries:
        owner_email  = gym.get("owner_email", "").strip()
        if not owner_email:
            continue
        owner_name   = gym.get("owner_name", "").strip() or gym.get("gym_name", "")
        gym_name     = gym.get("gym_name", "")
        active_count = gym.get("active_count", 0)
        total_credit = gym.get("total_monthly_credit", 0.0)
        monthly_fee  = gym.get("monthly_fee", 0.0)
        net_owed     = gym.get("net_owed")
        excess       = gym.get("excess", 0.0)
        cap_hit      = gym.get("cap_hit", False)
        first_name   = owner_name.split()[0] if owner_name else "there"

        lines = [
            f"Hi {first_name},",
            "",
            f"Here's your JST referral credit summary for {month_label}.",
            "",
            f"Active referrals:      {active_count}",
            f"Monthly credit earned: £{total_credit:.2f}",
        ]
        if monthly_fee:
            lines.append(f"Your JST invoice:      £{monthly_fee:.2f}/month")
            if cap_hit:
                lines.append(f"Credit applied:        £{monthly_fee:.2f} (cap reached)")
                lines.append(f"Excess rolled forward: £{excess:.2f}")
                lines.append("Net owed this month:   £0.00")
            else:
                lines.append(f"Net owed this month:   £{net_owed:.2f}")

        active_refs = [r for r in gym.get("referrals", []) if r.get("credit_active")]
        if active_refs:
            lines += ["", "Active referrals:"]
            for ref in active_refs:
                lines.append(
                    f"  • {ref.get('Referred Member', '')} "
                    f"({ref.get('Product', '')}) — "
                    f"£{ref.get('Monthly Credit', '')} credit/month"
                )

        lines += ["", "Thanks for spreading the word.", "Jak & the JST team"]

        subject = f"JST referral credit — {month_label} — {gym_name}"
        body    = "\n".join(lines)

        if config.DRY_RUN:
            print(f"[DRY_RUN] gym credit email → {owner_email}: {subject}")
            sent += 1
            continue

        try:
            _send_email_to(config.SMTP_FROM, config.SMTP_PASSWORD, owner_email, subject, body)
            sent += 1
            print(f"  Gym credit email sent → {owner_email} ({gym_name})")
        except Exception as exc:
            print(f"  ! Gym credit email failed → {owner_email}: {exc}")

    return sent
