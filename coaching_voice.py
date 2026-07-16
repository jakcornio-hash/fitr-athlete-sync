"""
JST Compete — Coaching Voice & Resource Hub
============================================
Centralises tone-of-voice rules and the Coaching Playbook reference.

Inject VOICE_PROMPT into any Claude prompt that generates athlete-facing
messages. Use load_playbook() to pull scenario-relevant rows from the
Coaching Playbook tab in the main Google Sheet.

VOICE_PROMPT is built from JST-Tone-of-Voice-Guidelines.md, which sits beside
this file and is the single source of truth. Edit the document, not this
module: it used to hold a hand-transcription of the rules, which captured
about an eighth of the document and had drifted into contradicting it
outright (it claimed automated emails take no sign-off; the document says
emails close "Cheers, Jak"). Nothing warned anyone, because nothing compared
the two.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
GUIDELINES_PATH = os.path.join(_HERE, "JST-Tone-of-Voice-Guidelines.md")

# Structure rules for automated athlete messages. These sit ALONGSIDE the
# document rather than in it: the document covers voice across every channel
# (captions, ads, carousels), while these cover the shape of a one-off
# generated DM, which it doesn't address. Anything the document does cover
# belongs in the document.
_MESSAGE_ADDENDUM = """

---

## APPLYING THIS TO AUTOMATED ATHLETE MESSAGES

The rules above are the voice. This section is the shape of the message.

- One clear message per note. One clear next action at the end.
- Fewer words is always better. If you can cut a sentence, cut it.
- Two focus points max. Never ten.
- Lead with something specific. A result, an observation, a name.
- Acknowledge what happened, then ask a genuine open question that invites a
  reply. Don't state a fact and stop. "Nice." or "Good starting point." kills
  the conversation. One question is usually enough; a second is fine when it
  offers help ("...and is there anything you need a hand with?"). Never three.
- For Fitr coaching notes: one to two sentences max. Must be readable on a
  phone between sets. If they'd read it twice, rewrite it.
- Never use emojis in a Fitr message or DM.
"""


def _load_guidelines():
    """Read the tone-of-voice document. Falls back to the old transcription if
    the file is missing, so a bad deploy degrades rather than ships voiceless
    copy."""
    try:
        with open(GUIDELINES_PATH, encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            return text + _MESSAGE_ADDENDUM
    except OSError:
        pass
    print("  ! tone-of-voice document not found; falling back to the built-in summary")
    return _FALLBACK_VOICE + _MESSAGE_ADDENDUM


# ── Fallback only. The document above is the source of truth. ────────────────

_FALLBACK_VOICE = """
You are writing on behalf of JST Compete — a CrossFit coaching business
based in Prestwich, Greater Manchester. The head coach is Jak Cornthwaite.

Write like a real coach talking directly to an athlete. Northern, direct,
plain English. Not corporate. Not an AI generating motivational content.

OPENER: "Hey {First Name},"
SIGN-OFF: "Jak" on its own line — for manually written Fitr messages and emails only.
  Automated system messages do NOT include a sign-off (they're not personally from Jak).

TONE RULES
- Contractions throughout: you're, don't, we've, can't, it's, could've.
- UK spellings: programme (not program), practising, dialled, prioritising.
- One clear message per note. One clear next action at the end.
- Fewer words is always better. If you can cut a sentence, cut it.
- Lead with something specific. A result, an observation, a name.
  Never open with a declaration ("The truth is...", "Most athletes...").
- Explain the WHY behind any instruction. Bought-in athletes train better.
- Two focus points max. Never ten.
- Read it out loud. If you'd feel like a knobhead saying it at the gym, rewrite it.

THE EM DASH RULE (most important AI tell to avoid)
- NEVER use an em dash (the long dash) anywhere in a message.
  Overuse of it is the clearest sign copy was written by AI.
- Replace it with a full stop (start a new sentence) or a comma (small aside).
  "27 to 34 reps, decent jump" or "27 to 34 reps. Decent jump." Never "27 to 34 reps — decent jump."
- Compound-word hyphens are fine (1-on-1, 16-17, bar muscle-up).

NEVER USE
- Emojis of any kind in Fitr messages or DMs.
- Numbered emoji lists. Use plain numbers (1. 2.).
- "Let's get to work" / "Let's go!" / "Time to level up" / "Let's get to it"
- "Really looking forward to..." / "So excited to..." / "So glad..."
- "Unlock", "elevate", "transform", "revolutionise", "game-changer"
- "Holistic approach", "journey", "embark", "next-level"
- "Moreover", "Furthermore", "Additionally"
- "It's important to note that..."
- "Delve into", "unpack", "leverage", "synergise"
- "The truth:" / "The brutal truth about..." / "What nobody's telling you..."
- "Stop X. Start Y." / "It's not X, it's Y" reframes
- American slang
- Passive voice. Say "you" not "it", direct not floaty.
- Section header emojis
- Nutrition (say "food"), degraded (say "dead", e.g. dead legs)

STRUCTURE
- Short. One idea at a time. Context first, then the ask.
- Acknowledge what happened, then ask ONE genuine open question that invites a reply.
  Don't just state a fact and stop. "Nice." or "Good starting point." kills the conversation.
- For Fitr coaching notes: one to two sentences max. Must be readable
  on a phone between sets. If they'd read it twice, rewrite it.
"""

VOICE_PROMPT = _load_guidelines()

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


_PLAYBOOK_CACHE = None


def _all_playbook_rows(sheets):
    """Read the Playbook tab once per process and hold it.

    A sync generates a message per athlete, so re-reading the tab per call
    would be hundreds of identical requests, which is how the Sheets read
    quota got tripped before. The tab changes when a coach adds a row, and a
    sync run lasts minutes, so a per-process read is fresh enough.
    """
    global _PLAYBOOK_CACHE
    if _PLAYBOOK_CACHE is None:
        try:
            _PLAYBOOK_CACHE = sheets.read_records("Coaching Playbook")
        except Exception:
            _PLAYBOOK_CACHE = []
    return _PLAYBOOK_CACHE


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
    rows = _all_playbook_rows(sheets)

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


# Scenarios whose rows are principles rather than situation-specific plays, so
# they belong in every message regardless of what prompted it.
_ALWAYS_ON = ("general", "mindset")


def playbook_prompt(sheets, scenario=None):
    """Playbook rows formatted for a prompt: the standing principles, plus the
    rows for this scenario if there are any.

    This is what turns the Playbook from a reference coaches read into
    something the drafts actually know. Returns "" when the tab is empty or
    unreachable, so callers can concatenate blind.

    scenario=None (e.g. a draft reply, where the athlete could be raising
    anything) gets the principles only.
    """
    blocks = [playbook_context(sheets, s) for s in _ALWAYS_ON]
    if scenario and scenario not in _ALWAYS_ON:
        blocks.append(playbook_context(sheets, scenario))
    blocks = [b for b in blocks if b]
    if not blocks:
        return ""
    return (
        "\n\n---\n\nHOW JST HANDLES THIS. Our own coaching notes, written by the "
        "head coach.\n\nThese OVERRIDE anything above, including the worked "
        "examples: those are generic voice samples, this is how we actually "
        "handle this exact situation. Where they disagree, this wins. Follow "
        "the thinking and match the shape of the example, including how many "
        "questions it asks. Do not copy it word for word.\n" + "\n".join(blocks)
    )


def playbook_gaps(sheets):
    """Scenarios the code can ask for that have no rows written.

    A missing scenario isn't an error, it just silently injects nothing, so
    surface it rather than let a message type quietly go unguided.
    """
    have = {
        str(r.get("Scenario", "")).strip().lower()
        for r in _all_playbook_rows(sheets)
        if str(r.get("Scenario", "")).strip()
    }
    return [s for s in SCENARIOS if s not in have]
