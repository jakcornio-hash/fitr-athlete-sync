"""
Turn a Fitr conversation (or athlete result note) into a short, dated,
coaching-relevant summary using the Claude API.

If no ANTHROPIC_API_KEY is configured, falls back to a trimmed raw excerpt
so the pipeline still runs.
"""
import config
import coaching_voice

try:
    from anthropic import Anthropic
except ImportError:  # anthropic not installed yet
    Anthropic = None

_VOICE = coaching_voice.VOICE_PROMPT

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


_ATHLETE_MSG_SYSTEM = _VOICE + """

--- YOUR TASK ---
Write ONE short automated Fitr message to a single athlete, in Jak's voice.

Shape: acknowledge what happened (specific, brief), then ask a genuine open
question that invites a reply. Never just state a fact and stop. One question
is usually enough; a second is fine when it offers help. Never three.

Hard rules:
- Short and readable on a phone. 2 to 4 short sentences. Where the playbook
  below gives an example for this situation, match its length and shape.
- No sign-off. A "Hey {First}," opener is optional.
- NEVER use an em dash. Use a comma or a full stop.
- No emojis. No hype stacking ("well done!!"). Let the numbers land.
- Humanise raw benchmark names. "AMRAP 5 Minutes - Bar Muscle Ups" becomes
  "5-minute AMRAP bar muscle-up test". "1RM Push Press" becomes "1RM push press".
- Output ONLY the message text. No preamble, no quotes.

Examples (situation -> message). These are voice samples only, for situations
the playbook doesn't cover yet. A playbook example below always beats these:
it is written for that exact situation, these are not. As the playbook fills
up, these retire.

PB, Gerard improved "AMRAP 5 Minutes - Bar Muscle Ups" from 27 to 34 reps.
-> Good work Gerard on the 5-minute AMRAP bar muscle-up test. 27 to 34 reps is a decent jump. Why do you reckon it's gone up so much?

Multiple PBs, Andy hit "1RM Bench Press" 130 kg.
-> That's a big boy bench, Andy. 130kg. Was that one out of the blue or have you been building to it?
"""


def _kill_em_dashes(text):
    """Belt-and-braces: turn any em/en dash into a comma so none ever ship."""
    import re as _re
    return _re.sub(r"\s*[—–]\s*", ", ", text)


def athlete_message(situation, fallback, playbook=""):
    """Generate one on-voice automated athlete message.

    situation: plain one-line description of what happened, e.g.
      'PB, Gerard improved "1RM Back Squat" from 110kg to 120kg.'
    fallback:  a safe, tone-compliant message used if the API is unavailable
               or returns something unusable. Never returns None.
    playbook:  optional Coaching Playbook rows from
               coaching_voice.playbook_prompt(), so the message reflects how
               JST actually handles this rather than a generic guess.
    """
    client = _client()
    if client is None:
        return fallback
    try:
        resp = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=120,
            system=_ATHLETE_MSG_SYSTEM + (playbook or ""),
            messages=[{"role": "user", "content": f"{situation}\n->"}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        text = _kill_em_dashes(text.strip().strip('"').strip())
        # sanity: non-empty, single message, not truncated garbage
        if not text or len(text) > 400 or "\n\n" in text:
            return fallback
        return text
    except Exception as e:
        print(f"  ! athlete_message error: {e}")
        return fallback


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
    _VOICE
    + "\n\n---\n\n"
    "You are drafting a Fitr DM reply from a JST Compete coach to an athlete's message. "
    "The conversation transcript is chronological. The LAST message is the athlete's most recent message.\n\n"
    "Draft a reply that:\n"
    "1. Acknowledges what the athlete said specifically\n"
    "2. Gives actionable coaching guidance or reassurance\n"
    "3. Asks one follow-up question if relevant\n\n"
    "Keep it under 100 words. No markdown. Follow the tone rules above exactly.\n\n"
    "SIGN-OFF: do not add one. End on the last line of the message itself. The coach "
    "who sends this adds their own name, and it is not always Jak, so signing it here "
    "would put the wrong coach's name on it.\n\n"
    "If the last message in the thread is clearly from the coach (not the athlete), reply exactly: SKIP.\n"
    "If there is nothing meaningful to reply to, reply exactly: SKIP."
)


_WEEKLY_INSIGHT_SYSTEM = (
    "You are generating a 2-3 sentence weekly coaching insight for a CrossFit coach at JST Compete. "
    "The coach reads this when reviewing an athlete's profile. "
    "Cover: what the training or recovery data shows this week, any meaningful trend or signal, "
    "and one specific coaching priority or cue. Be specific — reference actual benchmarks, "
    "numbers, or dates. Sound like a knowledgeable coach, not a report. "
    "Under 80 words. Plain text, no markdown, no bullet points. "
    "If data is insufficient, reply exactly: SKIP."
)


def weekly_athlete_insight(athlete_name, pr_lines, rec_lines, goal, programme):
    """Generate a 2-3 sentence weekly coaching insight for one athlete.

    pr_lines: list of strings like "2026-06-28: Back Squat 1RM — 120kg (prev: 115kg)"
    rec_lines: list of strings like "2026-06-28: Soreness 6, Stress 4, Motivation 8 (score: 6.7/10)"
    Returns insight string or None.
    """
    client = _client()
    if client is None:
        return None
    pr_block = "\n".join(f"  - {l}" for l in pr_lines) if pr_lines else "  (no results logged this week)"
    rec_block = "\n".join(f"  - {l}" for l in rec_lines) if rec_lines else "  (no recovery surveys)"
    try:
        resp = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=150,
            system=_WEEKLY_INSIGHT_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Athlete: {athlete_name}\n"
                    f"Programme: {programme or '—'}\n"
                    f"North Star Goal: {goal or '—'}\n"
                    f"\nResults logged (last 14 days):\n{pr_block}\n"
                    f"\nRecovery surveys (last 14 days):\n{rec_block}\n"
                    "\nWrite the weekly coaching insight."
                ),
            }],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if not text or text.upper().startswith("SKIP"):
            return None
        return text
    except Exception as e:
        print(f"  ! weekly_insight error for {athlete_name}: {e}")
        return None


_COMP_ANALYSIS_SYSTEM = (
    "You are a CrossFit coach at JST Compete analysing an athlete's post-competition data. "
    "Write a 3-4 sentence coaching analysis covering: "
    "how the result compares to recent training benchmarks (overperformed, underperformed, or expected), "
    "what the athlete's reflection reveals about their mindset or readiness, "
    "and one specific recommendation for the next training block. "
    "Be specific — reference actual numbers and dates. Under 100 words. Plain text, no markdown. "
    "Start directly with the analysis — no preamble like 'Based on...' or 'Looking at...'."
)


def analyse_competition_result(athlete_name, comp_name, result, post_comp_response,
                                pr_lines, programme, goal):
    """Generate a coaching analysis for a competition result + post-comp reflection.

    Returns analysis string or None.
    """
    client = _client()
    if client is None:
        return None
    pr_block = "\n".join(f"  - {l}" for l in pr_lines) if pr_lines else "  (no recent benchmarks available)"
    try:
        resp = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=200,
            system=_COMP_ANALYSIS_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Athlete: {athlete_name}\n"
                    f"Programme: {programme or '—'}\n"
                    f"North Star Goal: {goal or '—'}\n"
                    f"Competition: {comp_name}\n"
                    f"Result: {result or '(not yet logged)'}\n"
                    f"\nAthlete's post-comp reflection:\n{post_comp_response}\n"
                    f"\nRecent training benchmarks (last 8 weeks):\n{pr_block}\n"
                    "\nWrite the coaching analysis."
                ),
            }],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        return text or None
    except Exception as e:
        print(f"  ! comp_analysis error for {athlete_name}: {e}")
        return None


_ANNUAL_REVIEW_SYSTEM = (
    _VOICE
    + "\n\n---\n\n"
    "You are writing a personalised annual training review for a JST Compete athlete. "
    "This is sent directly to the athlete as a coaching email from Jak.\n\n"
    "Include: a specific headline achievement from the year (benchmark or competition result), "
    "their consistency and training volume, key benchmark progress, "
    "how they've progressed toward their North Star Goal, "
    "and one specific thing to build on next year.\n\n"
    "Address the athlete by first name. Open with 'Hey [First Name],' then something specific "
    "about their year — not a generic greeting. Under 220 words. Plain text, no markdown. "
    "Sign off as Jak."
)


def annual_athlete_review(athlete_name, months_training, pr_summary, comp_summary, goal, programme):
    """Generate a personalised annual review email for an athlete.

    Returns review string or None.
    """
    client = _client()
    if client is None:
        return None
    try:
        resp = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=400,
            system=_ANNUAL_REVIEW_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"Athlete: {athlete_name}\n"
                    f"Months training with JST Compete: {months_training}\n"
                    f"Programme: {programme or '—'}\n"
                    f"North Star Goal: {goal or '—'}\n"
                    f"\nBenchmark progress this year:\n{pr_summary}\n"
                    f"\nCompetition history this year:\n{comp_summary}\n"
                    "\nWrite the annual review."
                ),
            }],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        return text or None
    except Exception as e:
        print(f"  ! annual_review error for {athlete_name}: {e}")
        return None


def draft_reply(athlete_name, thread_text, profile_data=None, playbook=""):
    """Draft a coaching reply to an athlete's most recent message.

    thread_text: plain-text transcript built by format_thread() — chronological,
                 newest message is LAST.
    profile_data: optional dict with keys like Programme, North Star Goal,
                  Tier, Injury Status pulled from _DATA for this athlete.
    playbook: optional Coaching Playbook rows from
              coaching_voice.playbook_prompt(). A reply answers whatever the
              athlete raised, so there's no scenario to look up: this gets the
              standing principles rather than situation-specific plays.

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
            system=_REPLY_SYSTEM + (playbook or ""),
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
        # Same belt-and-braces as athlete_message. Without it the em dash rule
        # was prompt-only here and the model ignored it in 9 of 16 live drafts,
        # which is the single clearest AI tell we have.
        return _kill_em_dashes(text)
    except Exception as e:
        print(f"  ! draft_reply error for {athlete_name}: {e}")
        return None
