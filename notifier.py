"""
Weekly coaching digest — Slack message + email after each sync run.

Covers: recovery flags, engagement/dropout, performance concerns,
        milestones (new PBs), consistency wins.
"""
import json
import smtplib
import urllib.request
from email.message import EmailMessage

import config


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
    urllib.request.urlopen(req, timeout=10)


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
