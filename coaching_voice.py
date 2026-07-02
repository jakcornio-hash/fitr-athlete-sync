"""
JST Compete — Coaching Voice & Resource Hub
============================================
Centralises tone-of-voice rules and the Coaching Playbook reference.

Inject VOICE_PROMPT into any Claude prompt that generates athlete-facing
messages. Use load_playbook() to pull scenario-relevant rows from the
Coaching Playbook tab in the main Google Sheet.
"""

# ── Core voice prompt (inject into any message-generation prompt) ─────────────

VOICE_PROMPT = """
You are writing on behalf of JST Compete — a CrossFit coaching business
based in Prestwich, Greater Manchester. The head coach is Jak Cornthwaite.

Write like a real coach talking directly to an athlete. Northern, direct,
plain English. Not corporate. Not an AI generating motivational content.

OPENER: "Hey {First Name},"
SIGN-OFF: "Jak" on its own line for all Fitr / DM messages.

TONE RULES
- Contractions throughout: you're, don't, we've, can't, it's, could've.
- UK spellings: programme (not program), practising, dialled, prioritising.
- One clear message per note. One clear next action at the end.
- Fewer words is always better. If you can cut a sentence, cut it.
- Lead with something specific — a result, an observation, a name.
  Never open with a declaration ("The truth is…", "Most athletes…").
- Explain the WHY behind any instruction. Bought-in athletes train better.
- Two focus points max. Never ten.
- Read it out loud. If you'd feel like a knobhead saying it at the gym, rewrite it.

NEVER USE
- Emojis of any kind in Fitr messages or DMs.
- Numbered emoji lists (1️⃣ 2️⃣ 3️⃣) — use plain numbers (1. 2.).
- "Let's get to work" / "Let's go!" / "Time to level up" / "Let's get to it"
- "Really looking forward to…" / "So excited to…" / "So glad…"
- "Unlock", "elevate", "transform", "revolutionise", "game-changer"
- "Holistic approach", "journey", "embark", "next-level"
- "Moreover", "Furthermore", "Additionally"
- "It's important to note that…"
- "Delve into", "unpack", "leverage", "synergise"
- "The truth:" / "The brutal truth about…" / "What nobody's telling you…"
- "Stop X. Start Y." / "It's not X, it's Y" reframes
- American slang
- Passive voice — say "you" not "it", direct not floaty
- Section header emojis

STRUCTURE
- Short paragraphs. One idea per paragraph.
- Context first, then the ask.
- End with one clear next action (not three).
- For Fitr coaching notes: one to two sentences max. Must be readable
  on a phone between sets. If they'd read it twice, rewrite it.
"""

# ── Scenario tags for the Coaching Playbook ─────────────────────────────────
# Used as filter values when loading playbook rows.

SCENARIOS = [
    "new_pr",           # athlete just set a new personal best
    "first_result",     # first ever entry for a benchmark
    "goal_achieved",    # athlete hit their stated North Star goal
    "inactive_28d",     # 28–44 days without logging
    "inactive_60d",     # 60+ days — check-in / offboarding
    "never_logged",     # signed up but never logged a result
    "comp_10wk",        # 10 weeks out from a competition
    "comp_3wk",         # 3 weeks out
    "comp_race_week",   # race week
    "comp_day_before",  # day before competition
    "post_comp",        # day after competition
    "comp_result",      # result entered after competition
    "recovery_flag",    # recovery survey flagged an issue
    "onboarding",       # new athlete added to the system
    "anniversary",      # training anniversary milestone
    "consistency",      # consecutive weeks logging streak
    "missed_session",   # athlete mentioned missing a session
    "mindset",          # general mindset / motivation
    "weightlifting",    # weightlifting coaching cues / questions
    "gymnastics",       # gymnastics coaching cues / questions
    "conditioning",     # conditioning / engine coaching
    "general",          # general coaching advice / community
]


def get_voice_prompt():
    """Return the voice prompt string for injection into Claude prompts."""
    return VOICE_PROMPT


def load_playbook(sheets, scenario=None, limit=10):
    """Load rows from the Coaching Playbook tab.

    Args:
        sheets: GoogleSheets client instance.
        scenario: Optional scenario tag to filter by (e.g. 'new_pr').
                  If None, returns all rows up to limit.
        limit: Max rows to return (most recently added first).

    Returns:
        List of dicts with keys: Scenario, Subject, Notes, Example, Source.
    """
    try:
        rows = sheets.read_records("Coaching Playbook")
    except Exception:
        return []

    if scenario:
        rows = [r for r in rows if str(r.get("Scenario", "")).strip().lower() == scenario.lower()]

    # Return most recently added (bottom of sheet = newest)
    rows = rows[-limit:] if len(rows) > limit else rows
    return rows


def playbook_context(sheets, scenario):
    """Return a formatted string of playbook rows for injection into a prompt.

    Returns empty string if no rows found (so callers can safely concatenate).
    """
    rows = load_playbook(sheets, scenario=scenario, limit=5)
    if not rows:
        return ""

    lines = [f"\nCOACHING PLAYBOOK — {scenario.upper().replace('_', ' ')}:"]
    for r in rows:
        subject = str(r.get("Subject", "")).strip()
        notes = str(r.get("Notes", "")).strip()
        example = str(r.get("Example", "")).strip()
        if subject:
            lines.append(f"\n  Subject: {subject}")
        if notes:
            lines.append(f"  Notes: {notes}")
        if example:
            lines.append(f"  Example message: {example}")
    return "\n".join(lines)
