"""
Archetype-aware coaching message templates.

Each outreach reason has 6 cluster variants (challenge / logic / warmth /
autonomy / social / belief) plus a generic fallback, derived from each
archetype's documented communication profile (Brett Bartholomew, 2017).
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


def archetype_cluster(archetype_id):
    key = str(archetype_id or "").lower().replace(" ", "_")
    return _CLUSTER.get(key, "generic")


def _build_templates():
    t = {}

    # ── re_engage ────────────────────────────────────────────────────────────
    t[("re_engage", "challenge")] = lambda f, ctx: (
        f"Hi {f}, I've noticed you've gone quiet — {ctx.get('days', 'several')} days without a result logged. "
        f"You're better than a gap. Let's get back after it this week."
    )
    t[("re_engage", "logic")] = lambda f, ctx: (
        f"Hi {f}, it's been {ctx.get('days', 'a while')} days since your last result. "
        f"The data gap matters — without it I'm programming blind. "
        f"Even a quick entry gets us back on track."
    )
    t[("re_engage", "warmth")] = lambda f, ctx: (
        f"Hi {f}, just checking in — it's been {ctx.get('days', 'a few weeks')} since your last log. "
        f"No pressure, genuinely just want to make sure you're okay and still getting your sessions in. "
        f"How are things going?"
    )
    t[("re_engage", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, you've gone quiet — {ctx.get('days', 'a few weeks')} without a result. "
        f"Totally fine if life got in the way. What would help you get back on track? "
        f"Happy to adjust the plan if something isn't working."
    )
    t[("re_engage", "social")] = lambda f, ctx: (
        f"Hi {f}, hope all's well — haven't seen a result from you in {ctx.get('days', 'a few weeks')} days. "
        f"What's been going on? Would love to catch up and see where you're at."
    )
    t[("re_engage", "belief")] = lambda f, ctx: (
        f"Hi {f}, it's been {ctx.get('days', 'a few weeks')} days since your last log. "
        f"The physical capacity doesn't disappear — but we need the record of it. "
        f"Let's get back to building the evidence base."
    )
    t[("re_engage", "generic")] = lambda f, ctx: (
        f"Hi {f}, just checking in — it's been {ctx.get('days', 'a while')} days since your last result. "
        f"How are you getting on? Let me know if there's anything I can adjust to support you better."
    )

    # ── never_logged ─────────────────────────────────────────────────────────
    t[("never_logged", "challenge")] = lambda f, ctx: (
        f"Hi {f}, you're in the system but haven't logged a result yet — let's fix that. "
        f"Start with whatever you did this week. I need the data to coach you properly."
    )
    t[("never_logged", "logic")] = lambda f, ctx: (
        f"Hi {f}, I don't have any results for you yet. "
        f"To programme effectively I need a baseline — even one session's data gets us started."
    )
    t[("never_logged", "warmth")] = lambda f, ctx: (
        f"Hi {f}, welcome to JST Compete — I haven't seen a result from you yet. "
        f"No rush, but whenever you're ready to log your first session I'll be watching. "
        f"Every athlete starts somewhere, and I'm here from day one."
    )
    t[("never_logged", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, I don't have any results from you yet. "
        f"Whenever you're ready — log whatever you hit this week and we'll build from there. You set the pace."
    )
    t[("never_logged", "social")] = lambda f, ctx: (
        f"Hi {f}, haven't seen a result from you yet. "
        f"Give me a shout when you're ready to start tracking — happy to help you get set up."
    )
    t[("never_logged", "belief")] = lambda f, ctx: (
        f"Hi {f}, no results logged yet — but that's fine, everyone starts here. "
        f"Your first log is just about establishing a baseline. Whatever you hit this week, get it in."
    )
    t[("never_logged", "generic")] = lambda f, ctx: (
        f"Hi {f}, I don't have any results from you yet. "
        f"Whenever you're ready, log your first session and we'll take it from there."
    )

    # ── celebrate ────────────────────────────────────────────────────────────
    t[("celebrate", "challenge")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('result', 'great result')} — that's what we're here for. "
        f"Now let's see what comes next."
    )
    t[("celebrate", "logic")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('result', 'great result')}. "
        f"That's exactly the data point I was looking for — the trend is moving in the right direction."
    )
    t[("celebrate", "warmth")] = lambda f, ctx: (
        f"Hi {f}, I just saw your result — {ctx.get('result', 'really well done')}. "
        f"You've been putting the work in consistently and it's showing. Really proud of that."
    )
    t[("celebrate", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, loved seeing {ctx.get('result', 'that result')} — you owned the process on that one. Great work."
    )
    t[("celebrate", "social")] = lambda f, ctx: (
        f"Hi {f}, brilliant — {ctx.get('result', 'great result')}! That's a big one. "
        f"How did it feel? Would love to hear about it."
    )
    t[("celebrate", "belief")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('result', 'great result')}. "
        f"I want you to let that land — that result is evidence of what you're actually capable of. Build on it."
    )
    t[("celebrate", "generic")] = lambda f, ctx: (
        f"Hi {f}, great result — {ctx.get('result', 'well done')}! Keep the consistency going."
    )

    # ── consistency ──────────────────────────────────────────────────────────
    t[("consistency", "challenge")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('weeks', 'several')} consecutive weeks of logging — "
        f"that discipline is what separates athletes. Keep going."
    )
    t[("consistency", "logic")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('weeks', 'several')} straight weeks of logging — "
        f"your dataset is now big enough to identify real trends. Good work."
    )
    t[("consistency", "warmth")] = lambda f, ctx: (
        f"Hi {f}, just wanted to say — {ctx.get('weeks', 'several')} weeks of consecutive logging. "
        f"That consistency is what builds athletes. You're doing brilliantly."
    )
    t[("consistency", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('weeks', 'several')} weeks straight — that's down to you. "
        f"No one made that happen. Keep going."
    )
    t[("consistency", "social")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('weeks', 'several')} weeks of logging in a row — love to see it! "
        f"You're setting the standard."
    )
    t[("consistency", "belief")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('weeks', 'several')} consecutive weeks logged. "
        f"This is how belief is built — win by win, week by week. Don't stop."
    )
    t[("consistency", "generic")] = lambda f, ctx: (
        f"Hi {f}, {ctx.get('weeks', 'several')} consecutive weeks of logging — great consistency. Keep it up."
    )

    # ── performance_concern ───────────────────────────────────────────────────
    t[("performance_concern", "challenge")] = lambda f, ctx: (
        f"Hi {f}, your {ctx.get('bench', 'numbers')} have dipped — let's find out why and fix it. "
        f"What's your honest read on how training has felt lately?"
    )
    t[("performance_concern", "logic")] = lambda f, ctx: (
        f"Hi {f}, I've been looking at your {ctx.get('bench', 'benchmark data')} and there's a downward trend I want to talk through. "
        f"How's sleep, recovery, accumulated fatigue been? Let's figure out what's driving it."
    )
    t[("performance_concern", "warmth")] = lambda f, ctx: (
        f"Hi {f}, just something I wanted to flag — your {ctx.get('bench', 'numbers')} have softened a little recently. "
        f"Nothing to stress about, but I want to make sure everything's okay. "
        f"How are you feeling about training at the moment?"
    )
    t[("performance_concern", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, I've noticed your {ctx.get('bench', 'performance data')} has dipped a bit. "
        f"What's your take on it? I have some ideas but I'd rather hear your read first."
    )
    t[("performance_concern", "social")] = lambda f, ctx: (
        f"Hi {f}, just wanted to have a quiet chat — your {ctx.get('bench', 'performance')} has dipped slightly. "
        f"Nothing major, but I'd love to catch up and make sure everything's going well."
    )
    t[("performance_concern", "belief")] = lambda f, ctx: (
        f"Hi {f}, your {ctx.get('bench', 'numbers')} have dipped — but your capacity hasn't gone anywhere. "
        f"Let's look at what's happening and build the confidence back. A few good sessions will fix this."
    )
    t[("performance_concern", "generic")] = lambda f, ctx: (
        f"Hi {f}, your {ctx.get('bench', 'numbers')} have been trending down recently. "
        f"Worth a check-in — how are you feeling about training?"
    )

    # ── recovery_flag ────────────────────────────────────────────────────────
    t[("recovery_flag", "challenge")] = lambda f, ctx: (
        f"Hi {f}, your recovery survey flagged {ctx.get('issue', 'something worth looking at')}. "
        f"Recovery is part of the process — what's the honest story?"
    )
    t[("recovery_flag", "logic")] = lambda f, ctx: (
        f"Hi {f}, your recovery survey flagged {ctx.get('issue', 'some concerns')}. "
        f"I take this data seriously — before I adjust the plan, tell me more about what's going on."
    )
    t[("recovery_flag", "warmth")] = lambda f, ctx: (
        f"Hi {f}, I saw your recovery survey — {ctx.get('issue', 'it flagged something')}. "
        f"I just want to check in and make sure you're okay. No pressure, genuinely just asking."
    )
    t[("recovery_flag", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, your recovery data flagged {ctx.get('issue', 'something')} this week. "
        f"Entirely up to you how we respond — but I want to give you the option. What would help most?"
    )
    t[("recovery_flag", "social")] = lambda f, ctx: (
        f"Hi {f}, your recovery survey flagged {ctx.get('issue', 'some concerns')}. "
        f"Let me know how you're really doing — happy to adjust anything to help."
    )
    t[("recovery_flag", "belief")] = lambda f, ctx: (
        f"Hi {f}, your recovery data flagged {ctx.get('issue', 'something to look at')}. "
        f"Managing this is a skill — the fact you're tracking it means you're already ahead of most. "
        f"What do you need from me?"
    )
    t[("recovery_flag", "generic")] = lambda f, ctx: (
        f"Hi {f}, your latest recovery survey flagged {ctx.get('issue', 'some concerns')}. "
        f"Just checking in — how are you doing?"
    )

    # ── nudge_to_log ──────────────────────────────────────────────────────────
    t[("nudge_to_log", "challenge")] = lambda f, ctx: (
        f"Hi {f}, quick one — I know you're doing the sessions but the results aren't logged. "
        f"Two minutes to enter them keeps the data clean and my programming sharp for you."
    )
    t[("nudge_to_log", "logic")] = lambda f, ctx: (
        f"Hi {f}, we've been in contact but I don't have results logged for you. "
        f"Without the data I'm coaching blind — can you take a few minutes to get them in?"
    )
    t[("nudge_to_log", "warmth")] = lambda f, ctx: (
        f"Hi {f}, I know you're putting the sessions in — just a gentle reminder to log the results too. "
        f"Even rough numbers help me understand how things are going for you."
    )
    t[("nudge_to_log", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, just a nudge on logging — you decide what to track, "
        f"but having something in the system helps me programme better for you."
    )
    t[("nudge_to_log", "social")] = lambda f, ctx: (
        f"Hi {f}, hope all's good! Just a friendly nudge to log your results when you get a chance — "
        f"love to see what you've been hitting."
    )
    t[("nudge_to_log", "belief")] = lambda f, ctx: (
        f"Hi {f}, please log your results — I want to build the picture of your progress. "
        f"Every session logged is another piece of evidence that the work is compounding."
    )
    t[("nudge_to_log", "generic")] = lambda f, ctx: (
        f"Hi {f}, just a reminder to log your results when you can — "
        f"it helps me keep your programming on track."
    )

    # ── post_comp ────────────────────────────────────────────────────────────
    t[("post_comp", "challenge")] = lambda f, ctx: (
        f"Hi {f}, how did {ctx.get('comp', 'the competition')} go? "
        f"Take a couple of days, then let's sit down and debrief — and plan what's next."
    )
    t[("post_comp", "logic")] = lambda f, ctx: (
        f"Hi {f}, how did {ctx.get('comp', 'the competition')} go? "
        f"I'd love to run through the data with you — what went to plan and what can we improve on."
    )
    t[("post_comp", "warmth")] = lambda f, ctx: (
        f"Hi {f}, I hope {ctx.get('comp', 'the competition')} went well — really proud of you for getting out there. "
        f"Take a proper rest and then let me know how it went when you're ready."
    )
    t[("post_comp", "autonomy")] = lambda f, ctx: (
        f"Hi {f}, how did {ctx.get('comp', 'the competition')} go? "
        f"Take all the time you need to recover, then let me know your thoughts — "
        f"I want to hear your take before I suggest anything."
    )
    t[("post_comp", "social")] = lambda f, ctx: (
        f"Hi {f}, can't wait to hear how {ctx.get('comp', 'the competition')} went! "
        f"Take a few days to recover, then let's catch up."
    )
    t[("post_comp", "belief")] = lambda f, ctx: (
        f"Hi {f}, how did {ctx.get('comp', 'the competition')} go? Whatever the result — "
        f"you showed up and competed. That's evidence. Let's talk about it when you're recovered."
    )
    t[("post_comp", "generic")] = lambda f, ctx: (
        f"Hi {f}, how did {ctx.get('comp', 'the competition')} go? "
        f"Take a few days to recover and then let's debrief."
    )

    return t


_TEMPLATES = _build_templates()


def generate_message(name, reason_type, context=None, archetype_id=None):
    """Return a ready-to-send coaching message personalised to the athlete's archetype.

    Args:
        name: Athlete full name (first name extracted automatically).
        reason_type: Key from REASON_TYPES.
        context: Optional dict — keys vary by reason_type:
            re_engage / nudge_to_log: {'days': int}
            celebrate: {'result': str}
            consistency: {'weeks': int}
            performance_concern: {'bench': str}
            recovery_flag: {'issue': str}
            post_comp: {'comp': str}
        archetype_id: Archetype slug (e.g. 'soldier', 'free_spirit') or None for generic.

    Returns:
        str — message text, ready to paste into Fitr or any messaging tool.
    """
    first = (name or "").split()[0] or name
    cluster = archetype_cluster(archetype_id) if archetype_id else "generic"
    ctx = context or {}

    fn = _TEMPLATES.get((reason_type, cluster)) or _TEMPLATES.get((reason_type, "generic"))
    if fn:
        return fn(first, ctx)

    return f"Hi {first}, just checking in — hope training is going well."


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
    "🚨 Programme Switch": "post_comp",   # comp action, use comp message via analytics
    "🏁 Comp Prep":        "post_comp",   # same
}
