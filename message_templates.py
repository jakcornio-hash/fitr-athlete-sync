"""
Archetype-aware coaching message templates (outreach drafts for coach review).

These are drafts a coach reads and copies from the dashboard, not auto-sends.
Voice follows the JST tone guidelines: Northern, direct, plain English, no em
dashes, no AI tells. Each message acknowledges first, then ends with one genuine
open question (the acknowledge/connect/action shape, never labelled).

Archetype clusters map to four primary drives:
  challenge  → mission, proving capacity
  logic      → mastery, understanding the why
  warmth     → connection, feeling seen
  autonomy   → status through agency
  social     → community, conversation
  belief     → building evidence of identity
"""

# Maps each archetype slug to a communication cluster
_CLUSTER = {
    "soldier":                 "challenge",
    "leader":                  "challenge",
    "wolverine":               "challenge",
    "technician":              "logic",
    "skeptic":                 "logic",
    "specialist":              "logic",
    "crusader":                "warmth",
    "novice":                  "warmth",
    "underdog_blue_collar":    "warmth",
    "self_sabotager":          "warmth",
    "hypochondriac":           "warmth",
    "politician":              "autonomy",
    "free_spirit":             "autonomy",
    "royal":                   "autonomy",
    "mouthpiece":              "social",
    "manipulator":             "social",
    "underdog_sleeping_giant": "belief",
}

# All valid reason types
REASON_TYPES = [
    "re_engage",
    "never_logged",
    "celebrate",
    "consistency",
    "performance_concern",
    "recovery_flag",
    "nudge_to_log",
    "post_comp",
]

# Reason types that represent concerns (merged into one card per athlete)
CONCERN_TYPES = frozenset({
    "recovery_flag", "performance_concern", "re_engage", "never_logged", "nudge_to_log",
})


def archetype_cluster(archetype_id):
    key = str(archetype_id or "").lower().replace(" ", "_")
    return _CLUSTER.get(key, "generic")


# ── ctx phrase helpers ────────────────────────────────────────────────────────

def _days_phrase(ctx):
    """'12 days', or 'a while' when the count is missing/non-numeric."""
    d = ctx.get("days")
    if d in (None, ""):
        return "a while"
    s = str(d)
    return f"{s} days" if s.isdigit() else s


def _weeks_phrase(ctx):
    """'6 weeks', or 'several weeks' when the count is missing/non-numeric."""
    w = ctx.get("weeks")
    if w in (None, ""):
        return "several weeks"
    s = str(w)
    return f"{s} weeks" if s.isdigit() else s


# ── Signal narrative helper ───────────────────────────────────────────────────

def _signal_narrative(signals):
    """Build a natural-language phrase listing multiple signals for one athlete."""
    parts = []
    for s in signals:
        rt = s.get("reason_type", "")
        ctx = s.get("ctx", {})
        if rt == "recovery_flag":
            parts.append(f"your recovery survey flagged {ctx.get('issue', 'some recovery concerns')}")
        elif rt == "performance_concern":
            parts.append(f"a dip in your {ctx.get('bench', 'numbers')}")
        elif rt == "re_engage":
            days = ctx.get("days", "")
            parts.append(f"it's been {days} days since your last log" if days else "you've gone quiet on results")
        elif rt == "never_logged":
            parts.append("you haven't logged any results yet")
        elif rt == "nudge_to_log":
            parts.append("I don't have recent results in the system")
        elif rt == "consistency":
            weeks = ctx.get("weeks", "")
            parts.append(f"{weeks} weeks straight of logging" if weeks else "strong consistency")
        elif rt == "celebrate":
            parts.append(ctx.get("result", "a great result"))

    if not parts:
        phrase = "a few things worth talking through"
    elif len(parts) == 1:
        phrase = parts[0]
    elif len(parts) == 2:
        phrase = f"{parts[0]} and {parts[1]}"
    else:
        phrase = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    # Sits as its own sentence in the combined templates, so capitalise the start.
    return phrase[:1].upper() + phrase[1:]


# ── Single-signal templates ───────────────────────────────────────────────────

def _build_templates():
    t = {}

    # ── re_engage ────────────────────────────────────────────────────────────
    t[("re_engage", "challenge")] = lambda f, ctx: (
        f"Hi {f}, it's been {_days_phrase(ctx)} without a result and I've noticed. "
        f"You're not one to go quiet for no reason. What's got in the way?"
    )
    t[("re_engage", "logic")] = lambda f, ctx: (
        f"Hi {f}, there's a {_days_phrase(ctx)} gap in your log. "
        f"Without the data I can't program you properly. What's been going on?"
    )
    t[("re_engage", "warmth")] = lambda f, ctx: (
        f"Hi {f}, it's been {_days_phrase(ctx)} since your last log and I've been thinking about you. "
        f"No pressure at all. How are things going outside of training?"
    )
    t[("re_engage", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, {_days_phrase(ctx)} without a result. No assumptions from me, life happens. "
        f"What's been going on, and what would actually help right now?"
    )
    t[("re_engage", "social")] = lambda f, ctx: (
        f"Hi {f}, feels like we've lost touch a bit, {_days_phrase(ctx)} since your last result. "
        f"What have you been up to?"
    )
    t[("re_engage", "belief")] = lambda f, ctx: (
        f"Hi {f}, {_days_phrase(ctx)} without a log doesn't wipe out the work, but I can't see it "
        f"without the record. What's been going on?"
    )
    t[("re_engage", "generic")] = lambda f, ctx: (
        f"Hi {f}, just checking in, it's been {_days_phrase(ctx)} since your last result. "
        f"How are you getting on?"
    )

    # ── never_logged ─────────────────────────────────────────────────────────
    t[("never_logged", "challenge")] = lambda f, ctx: (
        f"Hi {f}, you're in the programme but I've not got a result from you yet. "
        f"The first log doesn't need to be impressive, it just needs to exist. What did you hit this week?"
    )
    t[("never_logged", "logic")] = lambda f, ctx: (
        f"Hi {f}, I've not got any results for you yet. To set your programming right I need a baseline, "
        f"even one session's data is enough to start. What have you been working on?"
    )
    t[("never_logged", "warmth")] = lambda f, ctx: (
        f"Hi {f}, wanted to say a proper welcome. I've not seen a result from you yet and I want to make "
        f"sure you're settled in. No rush. What have you been training this week?"
    )
    t[("never_logged", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, no results from you yet, and there's no set way to start. "
        f"Log whatever you've been hitting when you're ready and we'll build from there. You set the pace."
    )
    t[("never_logged", "social")] = lambda f, ctx: (
        f"Hi {f}, not seen a result from you yet. Give me a shout when you're ready to start tracking "
        f"and we'll get you set up. What are you working with?"
    )
    t[("never_logged", "belief")] = lambda f, ctx: (
        f"Hi {f}, no results yet, but everyone starts here. Your first log is the start of the picture. "
        f"Whatever you hit this week, get it in and we'll build from there."
    )
    t[("never_logged", "generic")] = lambda f, ctx: (
        f"Hi {f}, I've not got any results from you yet. "
        f"Whenever you're ready, log your first session and we'll take it from there."
    )

    # ── celebrate ────────────────────────────────────────────────────────────
    t[("celebrate", "challenge")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('result', 'great result')}. That's what this programme's built for. "
        f"What do we go after next?"
    )
    t[("celebrate", "logic")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('result', 'great result')}. That's the number I've been watching for, "
        f"the direction's right. What do you want to build on from here?"
    )
    t[("celebrate", "warmth")] = lambda f, ctx: (
        f"Hi {f}, just saw your result, {ctx.get('result', 'really well done')}. "
        f"You've put the work in and it's showing. How did it feel?"
    )
    t[("celebrate", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, loved seeing {ctx.get('result', 'that result')}. You made that happen. "
        f"What do you want to go after next?"
    )
    t[("celebrate", "social")] = lambda f, ctx: (
        f"Hi {f}, had to reach out, {ctx.get('result', 'brilliant result')}. "
        f"How did it feel? I'd love to hear your version of it."
    )
    t[("celebrate", "belief")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('result', 'great result')}. Sit with that one, you earned it. "
        f"What do you want that result to mean for you?"
    )
    t[("celebrate", "generic")] = lambda f, ctx: (
        f"Hi {f}, great result, {ctx.get('result', 'well done')}. That consistency's paying off. "
        f"What do you want to build on next?"
    )

    # ── consistency ──────────────────────────────────────────────────────────
    t[("consistency", "challenge")] = lambda f, ctx: (
        f"Hi {f}, {_weeks_phrase(ctx)} of logging on the bounce. That's discipline, and it's the "
        f"foundation everything else gets built on. Keep it going."
    )
    t[("consistency", "logic")] = lambda f, ctx: (
        f"Hi {f}, {_weeks_phrase(ctx)} straight of data on the record. Your numbers are now solid "
        f"enough to see real trends. What's been keeping you so locked in?"
    )
    t[("consistency", "warmth")] = lambda f, ctx: (
        f"Hi {f}, {_weeks_phrase(ctx)} without a break. You might not feel it day to day, but that "
        f"adds up to something real. Really good to see."
    )
    t[("consistency", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, {_weeks_phrase(ctx)} in a row. Nobody made that happen but you, and it's adding up. "
        f"What's been working for you?"
    )
    t[("consistency", "social")] = lambda f, ctx: (
        f"Hi {f}, {_weeks_phrase(ctx)} straight, love to see it. "
        f"What's been keeping you so consistent?"
    )
    t[("consistency", "belief")] = lambda f, ctx: (
        f"Hi {f}, {_weeks_phrase(ctx)} logged in a row. Each one's a brick in the wall. "
        f"You're building something real here, don't stop now."
    )
    t[("consistency", "generic")] = lambda f, ctx: (
        f"Hi {f}, {_weeks_phrase(ctx)} of logging in a row, that's real discipline. "
        f"Keep the momentum going."
    )

    # ── performance_concern ───────────────────────────────────────────────────
    # ctx['bench'] is a descriptor like "Back Squat: declining (-2.3%/entry)",
    # so it goes in a parenthetical to keep the sentence grammatical.
    t[("performance_concern", "challenge")] = lambda f, ctx: (
        f"Hi {f}, I've been keeping an eye on your numbers and one's dipped ({ctx.get('bench', 'a benchmark')}). "
        f"I want to understand it before I touch anything. What's training actually been feeling like?"
    )
    t[("performance_concern", "logic")] = lambda f, ctx: (
        f"Hi {f}, there's a downward trend I want to flag ({ctx.get('bench', 'a benchmark')}). "
        f"Before I adjust the programme, what's your read? Sleep, load, anything outside training?"
    )
    t[("performance_concern", "warmth")] = lambda f, ctx: (
        f"Hi {f}, something I wanted to flag rather than let sit. One of your numbers has softened lately "
        f"({ctx.get('bench', 'a benchmark')}). Nothing to worry about. How's training been feeling?"
    )
    t[("performance_concern", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, one of your numbers has dipped ({ctx.get('bench', 'a benchmark')}) and I want your take "
        f"before I form mine. You know your body and load better than I do. What do you reckon's behind it?"
    )
    t[("performance_concern", "social")] = lambda f, ctx: (
        f"Hi {f}, one of your numbers has dipped a bit ({ctx.get('bench', 'a benchmark')}) and I'd rather "
        f"have a proper chat about it than change things from my end. How are things actually going?"
    )
    t[("performance_concern", "belief")] = lambda f, ctx: (
        f"Hi {f}, one of your numbers has dipped ({ctx.get('bench', 'a benchmark')}), but your capacity "
        f"hasn't gone anywhere. Something's got in the way and we'll find it. What's your read on the last few weeks?"
    )
    t[("performance_concern", "generic")] = lambda f, ctx: (
        f"Hi {f}, there's a dip I want to talk through ({ctx.get('bench', 'a benchmark')}). "
        f"Before I change anything, how's training been feeling lately?"
    )

    # ── recovery_flag ────────────────────────────────────────────────────────
    t[("recovery_flag", "challenge")] = lambda f, ctx: (
        f"Hi {f}, your recovery survey flagged {ctx.get('issue', 'something worth a look')}. "
        f"Recovery's as much a part of this as the sessions. What's the honest story behind it?"
    )
    t[("recovery_flag", "logic")] = lambda f, ctx: (
        f"Hi {f}, your recovery data came back with {ctx.get('issue', 'some concerns')} this week. "
        f"Before I adjust your load, what's going on with sleep, food, life stress?"
    )
    t[("recovery_flag", "warmth")] = lambda f, ctx: (
        f"Hi {f}, saw your recovery numbers, {ctx.get('issue', 'something flagged')} this week. "
        f"Wanted to reach out rather than wait. How are you really doing?"
    )
    t[("recovery_flag", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, your recovery data flagged {ctx.get('issue', 'something')} this week. "
        f"I won't make a call until I hear from you. What's going on, and what would help most?"
    )
    t[("recovery_flag", "social")] = lambda f, ctx: (
        f"Hi {f}, your recovery survey flagged {ctx.get('issue', 'some concerns')} and I wanted to reach out. "
        f"How are things going, not just training, everything?"
    )
    t[("recovery_flag", "belief")] = lambda f, ctx: (
        f"Hi {f}, your recovery data came back with {ctx.get('issue', 'something to look at')}. "
        f"Managing this is a skill, and the fact you're tracking it puts you ahead. What do you need from me?"
    )
    t[("recovery_flag", "generic")] = lambda f, ctx: (
        f"Hi {f}, your latest recovery survey flagged {ctx.get('issue', 'some concerns')}. "
        f"Just checking in, how are you doing?"
    )

    # ── nudge_to_log ──────────────────────────────────────────────────────────
    t[("nudge_to_log", "challenge")] = lambda f, ctx: (
        f"Hi {f}, I know you're doing the sessions, I've just not got the record of it. "
        f"Two minutes to log keeps my programming sharp for you. Get them in when you can."
    )
    t[("nudge_to_log", "logic")] = lambda f, ctx: (
        f"Hi {f}, we've been in contact but I've not got results in the system. Even rough numbers give me "
        f"something to work with. Can you log what you've been hitting?"
    )
    t[("nudge_to_log", "warmth")] = lambda f, ctx: (
        f"Hi {f}, gentle nudge. I know you're putting the sessions in and I want to see what you're doing. "
        f"Even rough numbers help. Whenever you get a moment."
    )
    t[("nudge_to_log", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, quick one. You decide what to track, but without data I can't program you accurately. "
        f"Whatever you've been hitting this week, get it in."
    )
    t[("nudge_to_log", "social")] = lambda f, ctx: (
        f"Hi {f}, hope all's good. Not had results coming through and I want to stay across what you're hitting. "
        f"Log what you can when you get a chance."
    )
    t[("nudge_to_log", "belief")] = lambda f, ctx: (
        f"Hi {f}, get your results logged when you can. Every session in the record is another brick in the "
        f"picture of what you're building. Don't let it disappear."
    )
    t[("nudge_to_log", "generic")] = lambda f, ctx: (
        f"Hi {f}, just a reminder to log your results when you can. "
        f"It helps me keep your programming on track."
    )

    # ── post_comp ────────────────────────────────────────────────────────────
    t[("post_comp", "challenge")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('comp', 'the comp')} done. Take a couple of days, then let's debrief. "
        f"What went to plan, what didn't, and what do you want to build toward next?"
    )
    t[("post_comp", "logic")] = lambda f, ctx: (
        f"Hi {f}, how did {ctx.get('comp', 'the comp')} go? Once you've recovered I'd like to run through it. "
        f"What went to plan, and what should we target next?"
    )
    t[("post_comp", "warmth")] = lambda f, ctx: (
        f"Hi {f}, hope {ctx.get('comp', 'the comp')} went well. Proud of you for getting out there. "
        f"Take a proper rest and let me know how it went when you're ready."
    )
    t[("post_comp", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, how did {ctx.get('comp', 'the comp')} go? Take the time you need to recover. "
        f"When you're ready, give me your take before I suggest anything."
    )
    t[("post_comp", "social")] = lambda f, ctx: (
        f"Hi {f}, can't wait to hear how {ctx.get('comp', 'the comp')} went. "
        f"Take a few days to recover, then let's catch up properly."
    )
    t[("post_comp", "belief")] = lambda f, ctx: (
        f"Hi {f}, whatever happened at {ctx.get('comp', 'the comp')}, you showed up and competed. "
        f"That's what counts. When you've recovered, what did you take from it?"
    )
    t[("post_comp", "generic")] = lambda f, ctx: (
        f"Hi {f}, how did {ctx.get('comp', 'the comp')} go? "
        f"Take a few days to recover, then let's debrief."
    )

    return t


_TEMPLATES = _build_templates()


# ── Combined (multi-signal) templates ────────────────────────────────────────

def _build_combined_templates():
    """Templates for athletes with multiple concurrent signals.

    Each lambda takes (first_name, signals_list) where signals_list is
    [{"reason_type": str, "ctx": dict}, ...]. Signals are woven into one
    narrative and closed with a single open question, never a list of issues.
    """
    t = {}

    t[("combined", "challenge")] = lambda f, signals: (
        f"Hi {f}, a few things have come up together that are worth a direct chat. "
        f"{_signal_narrative(signals)}. I'd rather hear it from you than guess. "
        f"What's your honest read on how training's been?"
    )
    t[("combined", "logic")] = lambda f, signals: (
        f"Hi {f}, a few things I want to flag together rather than in isolation. "
        f"{_signal_narrative(signals)}. On their own each is manageable, together they're worth "
        f"understanding before I touch the programme. What do you think is driving it?"
    )
    t[("combined", "warmth")] = lambda f, signals: (
        f"Hi {f}, a few things have come up at once I'd rather check in about than let sit. "
        f"{_signal_narrative(signals)}. Nothing major on its own, but I want to make sure you're okay. "
        f"How are you feeling about things at the moment?"
    )
    t[("combined", "autonomy")] = lambda f, signals: (
        f"Hi {f}, a few signals have come in together this week. "
        f"{_signal_narrative(signals)}. I'm not drawing conclusions yet, I'd rather hear your take. "
        f"What's going on, and what would be most useful from me?"
    )
    t[("combined", "social")] = lambda f, signals: (
        f"Hi {f}, a couple of things worth a proper catch-up about. "
        f"{_signal_narrative(signals)}. I'd rather have a real chat than adjust things from my end. "
        f"How are things actually going?"
    )
    t[("combined", "belief")] = lambda f, signals: (
        f"Hi {f}, a few things have surfaced together. "
        f"{_signal_narrative(signals)}. This is part of the process, not outside it. "
        f"Give me your honest take on where you're at so we build the right response. What's going on?"
    )
    t[("combined", "generic")] = lambda f, signals: (
        f"Hi {f}, a few things have come up I want to check in about. "
        f"{_signal_narrative(signals)}. Before I adjust anything I'd like your take. How are things going?"
    )

    return t


_COMBINED_TEMPLATES = _build_combined_templates()


# ── Public API ────────────────────────────────────────────────────────────────

def generate_message(name, reason_type, context=None, archetype_id=None):
    """Return a ready-to-send coaching message personalised to the athlete's archetype.

    Args:
        name: Athlete full name (first name extracted automatically).
        reason_type: Key from REASON_TYPES.
        context: Optional dict — keys vary by reason_type.
        archetype_id: Archetype slug or None for generic.

    Returns:
        str — message text ready to send.
    """
    first = (name or "").split()[0] or name
    cluster = archetype_cluster(archetype_id) if archetype_id else "generic"
    ctx = context or {}

    fn = _TEMPLATES.get((reason_type, cluster)) or _TEMPLATES.get((reason_type, "generic"))
    if fn:
        return fn(first, ctx)

    return f"Hi {first}, just checking in, hope training's going well."


def generate_combined_message(name, signals, archetype_id=None):
    """Generate a single message covering multiple concurrent signals for one athlete.

    Acknowledges, weaves all signals into one narrative, closes with one open
    question. Never produces a list of separate issues.

    Args:
        name: Athlete full name.
        signals: List of {"reason_type": str, "ctx": dict} dicts.
        archetype_id: Archetype slug or None for generic.

    Returns:
        str — message text ready to send.
    """
    if not signals:
        return generate_message(name, "re_engage", {}, archetype_id)
    if len(signals) == 1:
        s = signals[0]
        return generate_message(name, s["reason_type"], s.get("ctx", {}), archetype_id)

    first = (name or "").split()[0] or name
    cluster = archetype_cluster(archetype_id) if archetype_id else "generic"

    fn = _COMBINED_TEMPLATES.get(("combined", cluster)) or _COMBINED_TEMPLATES.get(("combined", "generic"))
    if fn:
        return fn(first, signals)

    return f"Hi {first}, a few things have come up I want to check in about. How are things going?"


# Map outreach priority labels to reason types
PRIORITY_TO_REASON = {
    "🔴 Contact Today":    "recovery_flag",
    "🏆 Celebrate":        "celebrate",
    "✅ Positive":         "consistency",
    "⚠️ Re-engage":        "re_engage",
    "⚠️ Check In":         "re_engage",
    "📉 Performance":      "performance_concern",
    "📝 Remind to Log":    "nudge_to_log",
    "🟣 Post-Comp":        "post_comp",
    "🚨 Programme Switch": "post_comp",
    "🏁 Comp Prep":        "post_comp",
}
