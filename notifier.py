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
                 rec_alert_rows, milestones, consistency_wins):
    """Returns (plain_text, slack_text)."""
    sections_plain = []
    sections_slack = []

    # ---- recovery flags ----
    if rec_alert_rows:
        lines_p = [f"  • {r[0]} — {r[1]} (submitted {r[2]})" for r in rec_alert_rows]
        lines_s = [f"  • *{r[0]}* — {r[1]} (submitted {r[2]})" for r in rec_alert_rows]
        sections_plain.append(("🔴 RECOVERY FLAGS", lines_p))
        sections_slack.append(("🔴 *RECOVERY FLAGS*", lines_s))

    # ---- engagement / dropout ----
    flagged = [e for e in engagement_results if e["flag"]]
    if flagged:
        lines_p, lines_s = [], []
        for e in flagged:
            if e["last_logged"] == "never":
                line = f"  • {e['name']} — never logged"
            else:
                line = f"  • {e['name']} — {e['days_since']} days inactive (last: {_fmt_date(e['last_logged'])})"
            lines_p.append(line)
            lines_s.append(line.replace(f"  • {e['name']}", f"  • *{e['name']}*", 1))
        sections_plain.append(("⚠️ ENGAGEMENT / DROPOUT", lines_p))
        sections_slack.append(("⚠️ *ENGAGEMENT / DROPOUT*", lines_s))

    # ---- performance concerns ----
    concerns_p, concerns_s = [], []
    for athlete, signals in sorted(trend_results.items()):
        for s in signals:
            if s["trend"] == "declining" or s["peak_drop_flag"]:
                parts = []
                if s["trend"] == "declining":
                    parts.append(f"declining ({s['trend_pct']:+.1f}%/entry)")
                if s["peak_drop_flag"]:
                    parts.append(f"{s['peak_drop_pct']:.0f}% below peak")
                line = f"  • {athlete} — {s['benchmark']}: {', '.join(parts)}"
                concerns_p.append(line)
                concerns_s.append(line.replace(f"  • {athlete}", f"  • *{athlete}*", 1))
    if concerns_p:
        sections_plain.append(("📉 PERFORMANCE CONCERNS", concerns_p))
        sections_slack.append(("📉 *PERFORMANCE CONCERNS*", concerns_s))

    # ---- milestones / new PBs ----
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
        sections_plain.append(("🏆 MILESTONES / NEW PBs", lines_p))
        sections_slack.append(("🏆 *MILESTONES / NEW PBs*", lines_s))

    # ---- consistency wins ----
    if consistency_wins:
        lines_p = [f"  • {n} — {w} consecutive weeks logging" for n, w in consistency_wins]
        lines_s = [f"  • *{n}* — {w} consecutive weeks logging" for n, w in consistency_wins]
        sections_plain.append(("✅ CONSISTENCY WINS", lines_p))
        sections_slack.append(("✅ *CONSISTENCY WINS*", lines_s))

    header = f"JST Compete — Weekly Coaching Digest | {date}"

    if not sections_plain:
        msg = f"{header}\n\nNothing to flag this week — all athletes on track."
        return msg, msg

    # plain text
    plain_parts = ["=" * 60, header, "=" * 60, ""]
    for heading, lines in sections_plain:
        plain_parts.append(f"{heading} ({len(lines)})")
        plain_parts.extend(lines)
        plain_parts.append("")
    plain_parts.append("Automated weekly digest — JST Compete Coaching Platform")
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

    bench_rows / chal_rows: new PR log rows [date, name, email, bench, value, type, ...]
    programme_by_name:  {athlete_name: programme_string}
    coach_channel_map:  {programme_string: slack_channel_id}  — from the Coaches sheet tab

    Only fires if SLACK_BOT_TOKEN is set and the athlete's programme has a
    channel mapping. Silently skips athletes with no channel configured.
    Returns count of messages sent.
    """
    if not config.SLACK_BOT_TOKEN:
        return 0

    from collections import defaultdict
    entries_by_athlete = defaultdict(list)
    for row in bench_rows:
        name = row[1] if len(row) > 1 else ""
        bench = row[3] if len(row) > 3 else ""
        value = row[4] if len(row) > 4 else ""
        if name:
            entries_by_athlete[name].append((bench, value, "Benchmark"))
    for row in chal_rows:
        name = row[1] if len(row) > 1 else ""
        bench = row[3] if len(row) > 3 else ""
        value = row[4] if len(row) > 4 else ""
        if name:
            entries_by_athlete[name].append((bench, value, "Challenge"))

    sent = 0
    for athlete, entries in sorted(entries_by_athlete.items()):
        prog = programme_by_name.get(athlete, "")
        channel = coach_channel_map.get(prog)
        if not channel:
            continue
        count = len(entries)
        lines = [f"🏋️ *{athlete}* logged {count} new result{'s' if count > 1 else ''}:"]
        for bench, value, kind in entries:
            icon = "🏆" if kind == "Benchmark" else "🎯"
            lines.append(f"  {icon} *{bench}*: {value}")
        if prog:
            lines.append(f"  _{prog}_")
        try:
            send_slack_message(channel, "\n".join(lines))
            sent += 1
        except Exception as e:
            print(f"  ! Slack notify failed for {athlete} → {channel}: {e}")

    return sent


def send_email(subject, plain_text):
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


def send_digest(date, engagement_results, trend_results,
                rec_alert_rows, milestones, consistency_wins):
    plain, slack_text = build_digest(
        date, engagement_results, trend_results,
        rec_alert_rows, milestones, consistency_wins,
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
