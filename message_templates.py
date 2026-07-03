"""
Archetype-aware coaching message templates.

Communication philosophy (Brett Bartholomew / JST):
  - A-C-A every message: Acknowledge first, Connect to a drive, Action/question last
  - Primacy effect: relational anchor before any technical content
  - Transformational not transactional: never lead with the problem
  - Always end with an open question — invite dialogue, not compliance
  - "Talking in colour": metaphor and story, not just data

Archetype clusters map to four primary drives:
  challenge  → Defend / Acquire (mission, proving capacity)
  logic      → Learn (mastery, understanding the why)
  warmth     → Bond (connection, feeling seen)
  autonomy   → Acquire / Defend (status through agency)
  social     → Bond (community, conversation)
  belief     → Learn / Bond (building evidence of identity)
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


# ── Signal narrative helper ───────────────────────────────────────────────────

def _signal_narrative(signals):
    """Build a natural-language phrase listing multiple signals for one athlete."""
    parts = []
    for s in signals:
        rt = s.get("reason_type", "")
        ctx = s.get("ctx", {})
        if rt == "recovery_flag":
            issue = ctx.get("issue", "some recovery concerns")
            parts.append(f"your recovery survey flagged {issue}")
        elif rt == "performance_concern":
            bench = ctx.get("bench", "your numbers")
            parts.append(f"your {bench} has dipped")
        elif rt == "re_engage":
            days = ctx.get("days", "")
            if days:
                parts.append(f"it's been {days} days since your last log")
            else:
                parts.append("you've gone quiet on results")
        elif rt == "never_logged":
            parts.append("you haven't logged any results yet")
        elif rt == "nudge_to_log":
            parts.append("I don't have recent results in the system")
        elif rt == "consistency":
            weeks = ctx.get("weeks", "")
            parts.append(f"{weeks} consecutive weeks of logging" if weeks else "strong consistency")
        elif rt == "celebrate":
            result = ctx.get("result", "a great result")
            parts.append(result)

    if not parts:
        return "a few things worth talking through"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


# ── Single-signal templates ───────────────────────────────────────────────────

def _build_templates():
    t = {}

    # ── re_engage ────────────────────────────────────────────────────────────
    # A: acknowledge the gap, not the athlete's failure
    # C: frame re-engaging as their identity / the mission / the data need
    # A: open question — what's in the way?

    t[("re_engage", "challenge")] = lambda f, ctx: (
        f"Hi {f}, it's been {ctx.get('days', 'a while')} days without a result from you — "
        f"and I've been watching. You don't strike me as someone who goes quiet without a reason. "
        f"What's got in the way, and what do you need to get back after it?"
    )
    t[("re_engage", "logic")] = lambda f, ctx: (
        f"Hi {f}, I've been looking at your record and we've got a {ctx.get('days', 'significant')}-day gap. "
        f"Without data I'm programming blind — I can't give you an accurate plan based on guesswork. "
        f"Before I adjust anything, I want to understand what's been going on. What's the situation?"
    )
    t[("re_engage", "warmth")] = lambda f, ctx: (
        f"Hi {f}, I just wanted to check in — it's been {ctx.get('days', 'a few weeks')} since your last log "
        f"and I've been thinking about you. No pressure at all, I genuinely just want to know you're okay. "
        f"How are things going outside of training?"
    )
    t[("re_engage", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, it's been {ctx.get('days', 'a while')} without a result — I'm not going to assume anything. "
        f"Totally fine if life got in the way. I'd rather hear it from you directly. "
        f"What's been happening, and what would actually help right now?"
    )
    t[("re_engage", "social")] = lambda f, ctx: (
        f"Hi {f}, feels like we've lost touch a bit — it's been {ctx.get('days', 'a few weeks')} since your last result. "
        f"Would love to catch up and hear what you've been up to. "
        f"What's been going on?"
    )
    t[("re_engage", "belief")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('days', 'a stretch of')} days without a log doesn't mean {ctx.get('days', 'days')} days without progress — "
        f"but without the record I can't build the evidence of what you're doing. "
        f"What's been going on? Let's get back to making it visible."
    )
    t[("re_engage", "generic")] = lambda f, ctx: (
        f"Hi {f}, just checking in — it's been {ctx.get('days', 'a while')} since your last result. "
        f"How are you getting on? Let me know what's been happening and if there's anything I can adjust to support you."
    )

    # ── never_logged ─────────────────────────────────────────────────────────
    t[("never_logged", "challenge")] = lambda f, ctx: (
        f"Hi {f}, you're in the programme but I don't have a result from you yet — and I want to fix that. "
        f"The first log doesn't need to be impressive, it just needs to exist. "
        f"What did you hit this week?"
    )
    t[("never_logged", "logic")] = lambda f, ctx: (
        f"Hi {f}, I don't have any results for you yet. "
        f"To give you accurate programming I need a baseline — even a single session's data is enough to start. "
        f"What have you been working on this week?"
    )
    t[("never_logged", "warmth")] = lambda f, ctx: (
        f"Hi {f}, just wanted to reach out properly and say welcome — I haven't seen a result from you yet "
        f"and I want to make sure you're settled in. No rush at all. "
        f"When you're ready to log your first session, I'll be watching — every athlete starts somewhere and I'm here from day one."
    )
    t[("never_logged", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, I don't have results from you yet — but there's no script for how this starts. "
        f"Log whatever you've been hitting when you're ready and we'll build from there. You set the pace."
    )
    t[("never_logged", "social")] = lambda f, ctx: (
        f"Hi {f}, haven't seen a result from you yet — give me a shout when you're ready to start tracking "
        f"and we'll get you set up properly. Excited to see what you're working with."
    )
    t[("never_logged", "belief")] = lambda f, ctx: (
        f"Hi {f}, no results yet — but that's fine, everyone starts here. "
        f"Your first log is the beginning of the evidence base. Whatever you hit this week, get it in. "
        f"We'll build the picture from there."
    )
    t[("never_logged", "generic")] = lambda f, ctx: (
        f"Hi {f}, I don't have any results from you yet. "
        f"Whenever you're ready, log your first session and we'll take it from there."
    )

    # ── celebrate ────────────────────────────────────────────────────────────
    # A: call out the result directly — they earned this moment
    # C: connect to what it means (identity, evidence, direction)
    # A: forward-facing question — what's next?

    t[("celebrate", "challenge")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('result', 'great result')} — "
        f"that's exactly what this programme is built for. "
        f"Now the question is, what do we go after next?"
    )
    t[("celebrate", "logic")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('result', 'great result')}. "
        f"That's the data point I've been watching for — the trend is confirmed and the direction is right. "
        f"What do you want to build on from here?"
    )
    t[("celebrate", "warmth")] = lambda f, ctx: (
        f"Hi {f}, I just saw your result — {ctx.get('result', 'really well done')}. "
        f"You've been putting the work in consistently and it's showing up exactly where it should. "
        f"Genuinely proud of that."
    )
    t[("celebrate", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, loved seeing {ctx.get('result', 'that result')} — you made that happen. "
        f"What do you want to build on next?"
    )
    t[("celebrate", "social")] = lambda f, ctx: (
        f"Hi {f}, had to reach out — {ctx.get('result', 'brilliant result')}! "
        f"How did it feel? I'd love to hear your version of it."
    )
    t[("celebrate", "belief")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('result', 'great result')}. "
        f"I want you to sit with that for a moment — that's evidence. "
        f"Evidence that the work is compounding and the capacity is real. What do you want that result to mean for you?"
    )
    t[("celebrate", "generic")] = lambda f, ctx: (
        f"Hi {f}, great result — {ctx.get('result', 'well done')}! "
        f"That consistency is paying off. What do you want to build on next?"
    )

    # ── consistency ──────────────────────────────────────────────────────────
    # A: acknowledge the discipline (this is underrated — most coaches miss it)
    # C: frame what that streak is building (data quality, compound effect)
    # A: keep going / curious question

    t[("consistency", "challenge")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('weeks', 'several')} consecutive weeks of logging — "
        f"that's not luck, that's discipline. It's the foundation everything else gets built on. "
        f"Keep going."
    )
    t[("consistency", "logic")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('weeks', 'several')} straight weeks of data on the record. "
        f"Your dataset is now strong enough to identify real trends — that consistency is doing serious work behind the scenes. "
        f"What's been keeping you so locked in?"
    )
    t[("consistency", "warmth")] = lambda f, ctx: (
        f"Hi {f}, just wanted to say — {ctx.get('weeks', 'several')} weeks without a break. "
        f"You might not notice it in the moment, but that discipline is accumulating into something real. "
        f"I'm watching and I'm impressed."
    )
    t[("consistency", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('weeks', 'several')} weeks in a row. "
        f"Nobody made that happen but you. That kind of consistency is entirely yours — and it's compounding."
    )
    t[("consistency", "social")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('weeks', 'several')} weeks straight — love to see it! "
        f"You're setting the standard. What's been keeping you so consistent? I'd love to know what's clicking."
    )
    t[("consistency", "belief")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('weeks', 'several')} consecutive weeks logged. Each one is a brick. "
        f"You're building something that can't be taken from you — don't stop now."
    )
    t[("consistency", "generic")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('weeks', 'several')} consecutive weeks of logging — that's real discipline. "
        f"Keep the momentum going."
    )

    # ── performance_concern ───────────────────────────────────────────────────
    # A: acknowledge their training effort, not the decline
    # C: frame the dip as something with a story behind it (not a failure)
    # A: ask for THEIR read before offering yours — curiosity over correction

    t[("performance_concern", "challenge")] = lambda f, ctx: (
        f"Hi {f}, I've been keeping a close eye on your data — your {ctx.get('bench', 'numbers')} have dipped "
        f"and I want to understand it before I touch anything. "
        f"Give me your honest read: what has training actually been feeling like lately?"
    )
    t[("performance_concern", "logic")] = lambda f, ctx: (
        f"Hi {f}, I've been running through your {ctx.get('bench', 'benchmark data')} and there's a downward trend I want to flag. "
        f"This is diagnostic, not a judgement — I want to understand what's driving it before I adjust the programme. "
        f"What's your analysis — sleep, accumulated load, anything outside training?"
    )
    t[("performance_concern", "warmth")] = lambda f, ctx: (
        f"Hi {f}, just something I've been looking at that I wanted to flag rather than let sit — "
        f"your {ctx.get('bench', 'numbers')} have softened a little recently. "
        f"Nothing to worry about, but I'd feel better checking in with you directly. "
        f"How has training been feeling lately?"
    )
    t[("performance_concern", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, your {ctx.get('bench', 'performance data')} has dipped and I want your take on it "
        f"before I form any opinions of my own. "
        f"You know your body and your load better than I do. What do you think is behind it?"
    )
    t[("performance_concern", "social")] = lambda f, ctx: (
        f"Hi {f}, just wanted to reach out — your {ctx.get('bench', 'performance')} has dipped slightly "
        f"and I'd rather have a proper conversation about it than just adjust things from my end. "
        f"How are things actually going?"
    )
    t[("performance_concern", "belief")] = lambda f, ctx: (
        f"Hi {f}, your {ctx.get('bench', 'numbers')} have dipped — but your capacity hasn't gone anywhere. "
        f"Something's got in the way and we need to find it. "
        f"What's your read on the last few weeks?"
    )
    t[("performance_concern", "generic")] = lambda f, ctx: (
        f"Hi {f}, I've been watching your {ctx.get('bench', 'numbers')} and there's a dip I want to talk through. "
        f"Before I change anything, I'd love to hear your take — how has training been feeling lately?"
    )

    # ── recovery_flag ────────────────────────────────────────────────────────
    # A: acknowledge that they flagged it (the act of tracking is worth noting)
    # C: connect to what recovery data actually means for their training/identity
    # A: open question — what's really going on?

    t[("recovery_flag", "challenge")] = lambda f, ctx: (
        f"Hi {f}, your recovery survey flagged {ctx.get('issue', 'something worth looking at')} — "
        f"and I take that data seriously. Recovery is as much part of the process as the sessions themselves. "
        f"What's the honest story behind those numbers?"
    )
    t[("recovery_flag", "logic")] = lambda f, ctx: (
        f"Hi {f}, your recovery data came back with {ctx.get('issue', 'some concerns')} this week. "
        f"Before I adjust your load, I want to understand what's driving it. "
        f"What's going on with sleep, nutrition, life stress outside training?"
    )
    t[("recovery_flag", "warmth")] = lambda f, ctx: (
        f"Hi {f}, I saw your recovery numbers this week — {ctx.get('issue', 'it flagged something')} came up "
        f"and I just wanted to reach out directly rather than wait. "
        f"How are you really going? This isn't about the training — I just want to make sure you're okay."
    )
    t[("recovery_flag", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, your recovery data flagged {ctx.get('issue', 'something')} this week. "
        f"I'm not going to make a call until I hear from you — "
        f"what do you think is going on, and what would actually help most right now?"
    )
    t[("recovery_flag", "social")] = lambda f, ctx: (
        f"Hi {f}, your recovery survey flagged {ctx.get('issue', 'some concerns')} and I wanted to reach out. "
        f"How are things actually going — not just training, everything? "
        f"Would love to have a proper catch-up."
    )
    t[("recovery_flag", "belief")] = lambda f, ctx: (
        f"Hi {f}, your recovery data came back with {ctx.get('issue', 'something to look at')}. "
        f"Managing this is one of the skills that separates long-term athletes from short-term ones — "
        f"and the fact you're tracking it means you're already ahead of most. "
        f"What do you need from me?"
    )
    t[("recovery_flag", "generic")] = lambda f, ctx: (
        f"Hi {f}, your latest recovery survey flagged {ctx.get('issue', 'some concerns')}. "
        f"Just checking in — how are you doing?"
    )

    # ── nudge_to_log ──────────────────────────────────────────────────────────
    # A: acknowledge the training is happening (don't imply they're lazy)
    # C: explain why the log matters for their coaching specifically
    # A: gentle ask, not a demand

    t[("nudge_to_log", "challenge")] = lambda f, ctx: (
        f"Hi {f}, I know you're doing the sessions — I just don't have the record of it. "
        f"Two minutes to log your results keeps my programming sharp for you. Get them in when you can."
    )
    t[("nudge_to_log", "logic")] = lambda f, ctx: (
        f"Hi {f}, we've been in contact but I don't have results in the system. "
        f"Without the data I'm effectively coaching blind — even rough numbers give me something to work with. "
        f"Can you take a few minutes to log what you've been hitting?"
    )
    t[("nudge_to_log", "warmth")] = lambda f, ctx: (
        f"Hi {f}, just a gentle nudge — I know you're putting the sessions in and I want to make sure "
        f"I can actually see what you're doing. Even rough numbers help me keep your programme on point. "
        f"Whenever you get a moment."
    )
    t[("nudge_to_log", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, quick one — you decide what to track, but without data in the system "
        f"I can't give you accurate programming. Whatever you've been hitting this week, get it in."
    )
    t[("nudge_to_log", "social")] = lambda f, ctx: (
        f"Hi {f}, hope all's good! I haven't had results coming through and I want to stay across what you're hitting. "
        f"Log what you can when you get a chance — love to see what you've been up to."
    )
    t[("nudge_to_log", "belief")] = lambda f, ctx: (
        f"Hi {f}, please log your results — I want to build the picture of what you're doing. "
        f"Every session logged is another data point proving that the work is real and compounding. "
        f"Don't let it disappear."
    )
    t[("nudge_to_log", "generic")] = lambda f, ctx: (
        f"Hi {f}, just a reminder to log your results when you can — "
        f"it helps me keep your programming on track."
    )

    # ── post_comp ────────────────────────────────────────────────────────────
    # A: acknowledge the courage it takes to compete
    # C: frame the experience as the thing that matters most, result second
    # A: invite debrief on their terms, timeline theirs to set

    t[("post_comp", "challenge")] = lambda f, ctx: (
        f"Hi {f}, competition done — take a couple of days, then let's debrief properly. "
        f"I want your honest read: what went to plan, what didn't, and what you want to build toward next."
    )
    t[("post_comp", "logic")] = lambda f, ctx: (
        f"Hi {f}, how did {ctx.get('comp', 'the competition')} go? "
        f"When you've had a chance to recover, I'd love to run through it with you — "
        f"what went to plan, what the data tells us, and what we can target next."
    )
    t[("post_comp", "warmth")] = lambda f, ctx: (
        f"Hi {f}, I hope {ctx.get('comp', 'the competition')} went well — I'm really proud of you for getting out there and competing. "
        f"Take a proper rest and let me know how it went when you're ready. I'm here."
    )
    t[("post_comp", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, how did {ctx.get('comp', 'the competition')} go? "
        f"Take all the time you need to recover — when you're ready, give me your take before I suggest anything. "
        f"I want to hear what you experienced first."
    )
    t[("post_comp", "social")] = lambda f, ctx: (
        f"Hi {f}, can't wait to hear how {ctx.get('comp', 'the competition')} went! "
        f"Take a few days to recover, then let's catch up properly — I want to hear everything."
    )
    t[("post_comp", "belief")] = lambda f, ctx: (
        f"Hi {f}, whatever happened at {ctx.get('comp', 'the competition')} — you showed up and competed. "
        f"That's the evidence that matters most. "
        f"When you've recovered, let's talk about what it means for what you're building."
    )
    t[("post_comp", "generic")] = lambda f, ctx: (
        f"Hi {f}, how did {ctx.get('comp', 'the competition')} go? "
        f"Take a few days to recover, then let's debrief."
    )

    return t


_TEMPLATES = _build_templates()


# ── Combined (multi-signal) templates ────────────────────────────────────────

def _build_combined_templates():
    """Templates for athletes with multiple concurrent signals.

    Each lambda takes (first_name, signals_list) where signals_list is
    [{"reason_type": str, "ctx": dict}, ...].

    Structure: A-C-A but the signals are woven into one coherent narrative
    rather than listed as separate issues.
    """
    t = {}

    t[("combined", "challenge")] = lambda f, signals: (
        f"Hi {f}, I've been looking at your data and a few things have come up together that are worth a direct conversation — "
        f"{_signal_narrative(signals)}. "
        f"That combination tells a story and I'd rather hear it from you than guess. "
        f"What's your honest read on how training's been feeling lately?"
    )
    t[("combined", "logic")] = lambda f, signals: (
        f"Hi {f}, I've been running through your data and there are a few things I want to flag together rather than in isolation — "
        f"{_signal_narrative(signals)}. "
        f"Individually each one is manageable, but together they're pointing at something I need to understand before I touch the programme. "
        f"What's your analysis — what do you think is driving it?"
    )
    t[("combined", "warmth")] = lambda f, signals: (
        f"Hi {f}, a few things have come up at once that I'd rather check in about directly than let sit — "
        f"{_signal_narrative(signals)}. "
        f"None of this is a big deal on its own, but taken together I want to make sure you're okay "
        f"and that everything's working for you. How are you feeling about things at the moment?"
    )
    t[("combined", "autonomy")] = lambda f, signals: (
        f"Hi {f}, a few signals have come in together this week — "
        f"{_signal_narrative(signals)}. "
        f"I'm not drawing conclusions yet — I'd rather hear your take first. "
        f"What do you think is going on, and what would be most useful from me right now?"
    )
    t[("combined", "social")] = lambda f, signals: (
        f"Hi {f}, a couple of things have come up that I think are worth a proper catch-up about — "
        f"{_signal_narrative(signals)}. "
        f"I'd rather have a real conversation than just adjust things from my end. "
        f"How are things actually going?"
    )
    t[("combined", "belief")] = lambda f, signals: (
        f"Hi {f}, I've been looking at your data and a few things have surfaced together — "
        f"{_signal_narrative(signals)}. "
        f"This kind of thing is part of the journey, not outside of it. "
        f"I need your honest take on where you're at so we can build the right response. What's going on?"
    )
    t[("combined", "generic")] = lambda f, signals: (
        f"Hi {f}, a few things have come up that I want to check in about — "
        f"{_signal_narrative(signals)}. "
        f"Before I adjust anything I'd love to hear your take. How are things going?"
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

    return f"Hi {first}, just checking in — hope training is going well."


def generate_combined_message(name, signals, archetype_id=None):
    """Generate a single message covering multiple concurrent signals for one athlete.

    Follows A-C-A: relational anchor → weave all signals into one narrative →
    single open question. Never produces a list of separate issues.

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
