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
    "You summarise a coaching conversation thread for an athlete CRM. "
    "The thread is in chronological order, formatted as [date] Author: message. "
    "In 2-4 sentences capture only coaching-relevant signal from the full thread: "
    "injuries/niggles, motivation or life-load, compliance with training, competition plans, "
    "programme feedback, any concerns raised, or risk of churn. "
    "Be specific — reference exact issues, exercises, or dates if relevant. "
    "Focus on what a coach needs to act on or be aware of. "
    "If nothing coaching-relevant in the thread, reply exactly: SKIP."
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


_BRIEF_SYSTEM = (
    "You are generating a concise weekly coaching brief for a CrossFit coach at JST Compete. "
    "The coach will read this at the start of their week to know exactly where to focus. "
    "Format the output as exactly 5 bullet points (use • prefix), each under 25 words. "
    "Cover: who needs urgent contact, who to celebrate, key performance or load signals, "
    "competition prep priority, and one squad-level pattern worth addressing. "
    "Be specific — name athletes, quote numbers. Do not use headings or preamble. "
    "If the squad data is sparse, say so briefly and focus on what is actionable."
)


def coaching_brief(coach_name, athlete_lines):
    """Generate a 5-bullet weekly coaching brief for one coach.

    athlete_lines: list of plain-text strings, one per athlete with status summary
                   e.g. ["Alice — 🔴 Critical (45d inactive, declining snatch)", ...]
    Returns the brief text, or None on failure / no API key.
    """
    client = _client()
    if client is None:
        return None
    squad_text = "\n".join(f"- {line}" for line in athlete_lines) if athlete_lines else "(no athletes)"
    try:
        resp = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=350,
            system=_BRIEF_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Coach: {coach_name}\n"
                    f"Squad ({len(athlete_lines)} athletes):\n{squad_text}\n\n"
                    "Write the weekly coaching brief."
                ),
            }],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        return text or None
    except Exception as e:
        print(f"  ! coaching_brief error for {coach_name}: {e}")
        return None


_REPLY_SYSTEM = (
    "You are drafting a reply from a CrossFit coach (JST Compete) to an athlete's message. "
    "The conversation transcript is chronological. The LAST message is the athlete's most recent message. "
    "Draft a warm, direct, coach-to-athlete reply that:\n"
    "1. Acknowledges what the athlete said specifically\n"
    "2. Gives actionable coaching guidance or reassurance\n"
    "3. Asks one follow-up question if relevant\n"
    "Keep it under 100 words. Write in plain text, no markdown. Sound like a real coach, not a form letter. "
    "If the last message in the thread is clearly from the coach (not the athlete), reply exactly: SKIP. "
    "If there is nothing meaningful to reply to, reply exactly: SKIP."
)


def draft_reply(athlete_name, thread_text, profile_data=None):
    """Draft a coaching reply to an athlete's most recent message.

    thread_text: plain-text transcript built by format_thread() — chronological,
                 newest message is LAST.
    profile_data: optional dict with keys like Programme, North Star Goal,
                  Tier, Injury Status pulled from _DATA for this athlete.

    Returns a draft reply string, or None if no reply is warranted.
    """
    if profile_data is None:
        profile_data = {}
    client = _client()
    if client is None:
        return None
    try:
        resp = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=200,
            system=_REPLY_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Athlete: {athlete_name}\n"
                    f"Programme: {profile_data.get('Programme', '—')}\n"
                    f"Goal: {profile_data.get('North Star Goal', '—')}\n"
                    f"Tier: {profile_data.get('Tier', '—')}\n"
                    f"Injury status: {profile_data.get('Injury Status', '—')}\n"
                    f"\nConversation (chronological, most recent last):\n{thread_text}"
                ),
            }],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if not text or text.upper().startswith("SKIP"):
            return None
        return text
    except Exception as e:
        print(f"  ! draft_reply error for {athlete_name}: {e}")
        return None
