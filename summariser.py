"""
Turn a Fitr conversation (or athlete result note) into a short, dated,
coaching-relevant summary using the Claude API.

If no ANTHROPIC_API_KEY is configured, falls back to a trimmed raw excerpt
so the pipeline still runs.
"""
import config

try:
    from anthropic import Anthropic
except ImportError:  # anthropic not installed yet
    Anthropic = None

_SYSTEM = (
    "You summarise recent Fitr activity for an athlete CRM. "
    "Context may include text messages and/or workout completion logs (section title, "
    "workout description, athlete's completion note). "
    "In 1-3 sentences capture only coaching-relevant signal: workout compliance, "
    "injuries/niggles, motivation or life-load, competition plans, programme feedback, "
    "or risk of churn. Be specific — mention the workout section or exercise if relevant. "
    "If nothing coaching-relevant, reply exactly: SKIP."
)


def _client():
    if Anthropic is None or not config.ANTHROPIC_API_KEY:
        return None
    return Anthropic(api_key=config.ANTHROPIC_API_KEY)


def summarise_conversation(athlete_name, messages_text, activity_date=None):
    """
    messages_text: plain text built from last_message (text + attachment context).
    activity_date: optional datetime.date of the last message.
    Returns summary string or None.
    """
    client = _client()
    if client is None:
        excerpt = " ".join(messages_text.split())[:240]
        return excerpt or None
    date_line = f"Activity date: {activity_date.isoformat()}\n" if activity_date else ""
    try:
        resp = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=180,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Athlete: {athlete_name}\n"
                    f"{date_line}"
                    f"\nRecent activity:\n{messages_text}"
                ),
            }],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if not text or text.upper().startswith("SKIP"):
            return None
        return text
    except Exception as e:  # never let summarisation kill the run
        print(f"  ! summariser error for {athlete_name}: {e}")
        return None
