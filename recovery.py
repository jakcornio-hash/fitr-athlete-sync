"""
Recovery survey merge.

Assumes a Typeform recovery survey writes responses into a "Recovery" tab
(via Typeform's native Google Sheets integration). This module reads the
latest response per athlete and returns a compact readiness string that the
main sync writes next to each athlete in _DATA.

Expected Recovery tab columns (rename in RECOVERY_COLS to match your form):
  Submitted At | Email | Sleep (hrs) | Soreness | Stress | Motivation |
  Bodyweight | Niggles/Injuries | Availability this week
"""
import config

RECOVERY_COLS = {
    "timestamp": "Submitted At",
    "email": "Email",
    "sleep": "Sleep (hrs)",
    "soreness": "Soreness",
    "stress": "Stress",
    "motivation": "Motivation",
    "bodyweight": "Bodyweight",
    "niggles": "Niggles/Injuries",
    "availability": "Availability this week",
}


def latest_by_email(sheets):
    """Return {lower_email: response_dict} keeping the most recent row per athlete."""
    try:
        rows = sheets.read_records(config.TAB_RECOVERY)
    except Exception:
        return {}  # tab not set up yet — recovery is optional
    out = {}
    for row in rows:
        email = str(row.get(RECOVERY_COLS["email"], "")).strip().lower()
        if not email:
            continue
        # rows are appended in order, so later rows overwrite earlier ones
        out[email] = row
    return out


def readiness_string(row):
    g = lambda k: str(row.get(RECOVERY_COLS[k], "")).strip()
    parts = []
    if g("sleep"):
        parts.append(f"sleep {g('sleep')}h")
    if g("soreness"):
        parts.append(f"soreness {g('soreness')}/10")
    if g("stress"):
        parts.append(f"stress {g('stress')}/10")
    if g("motivation"):
        parts.append(f"motivation {g('motivation')}/10")
    if g("bodyweight"):
        parts.append(f"BW {g('bodyweight')}")
    if g("niggles") and g("niggles").lower() not in ("no", "none", "n/a"):
        parts.append(f"niggles: {g('niggles')}")
    if g("availability"):
        parts.append(f"avail: {g('availability')}")
    when = g("timestamp")
    head = f"[{when} — recovery] " if when else "[recovery] "
    return head + ", ".join(parts) if parts else None
