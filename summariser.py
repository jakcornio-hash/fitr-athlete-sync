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
    "You summarise fitness-coaching conversations for an athlete CRM. "
    "In 1-3 sentences capture only coaching-relevant signal: injuries/niggles, "
    "motivation or life-load, competition plans, programme feedback, billing/admin "
    "issues, or risk of churn. Be factual and brief. If nothing coaching-relevant, "
    "reply exactly: SKIP."
)


def _client():
    if Anthropic is None or not config.ANTHROPIC_API_KEY:
        return None
    return Anthropic(api_key=config.ANTHROPIC_API_KEY)


def summarise_conversation(athlete_name, messages_text):
    """messages_text: the recent exchange as plain text. Returns summary or None."""
    client = _client()
    if client is None:
        excerpt = " ".join(messages_text.split())[:240]
        return excerpt or None
    try:
        resp = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=160,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Athlete: {athlete_name}\n\nRecent exchange:\n{messages_text}",
            }],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if not text or text.upper().startswith("SKIP"):
            return None
        return text
    except Exception as e:  # never let summarisation kill the run
        print(f"  ! summariser error for {athlete_name}: {e}")
        return None
