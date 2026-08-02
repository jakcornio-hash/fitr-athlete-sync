"""Every athlete-facing template, held against the tone-of-voice rules.

A one-off read finds today's problems. This runs the whole set on every commit,
which is the only way copy rules survive contact with a codebase this size.

Rules enforced here come from the JST Tone of Voice doc:
  no em dashes (the number one AI tell), no exclamation marks, no emoji, no
  banned hype, contractions, and an ending that is either a genuine open
  question or one clear next action.

Two templates are Jak's own verbatim copy and are exempted by name below. His
words are his call, not a rule to enforce against him.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics
import message_templates as mt

# Realistic context, using the keys the dashboard actually supplies.
CTX = {
    "days": 35, "weeks": 6, "comp": "Regionals", "days_out": 21,
    "bench": "1RM bench press: down to 125 kg from 130 kg",
    "result": "a 130 kg bench press", "issue": "high stress (8/10)",
}

BANNED_HYPE = (
    "delve", "unpack", "holistic", "embark", "unlock", "empower", "moreover",
    "furthermore", "important to note", "elevate", "transform", "game-changer",
    "next-level", "dominate", "let's get to work", "crush it", "smash it",
)

# Jak's verbatim copy. Flagged in review, deliberately not enforced against.
VERBATIM = {"check_in_28d", "never_logged"}

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def _all_messages():
    """(label, message) for every reason type and archetype cluster."""
    out = []
    for rt in mt.REASON_TYPES:
        out.append((rt, mt.generate_message("Amy", rt, context=CTX)))
        for cluster in ("challenge", "logic", "warmth", "autonomy", "social",
                        "belief", "generic"):
            for aid, cl in getattr(mt, "_CLUSTER", {}).items():
                if cl != cluster:
                    continue
                out.append((f"{rt}/{aid}",
                            mt.generate_message("Amy", rt, context=CTX, archetype_id=aid)))
                break
    return [(label, m) for label, m in out if m]


def _enforced():
    return [(label, m) for label, m in _all_messages()
            if label.split("/")[0] not in VERBATIM]


def test_there_are_messages_to_scan():
    assert len(_all_messages()) > 20


def test_no_em_dashes_anywhere():
    bad = [l for l, m in _enforced() if "—" in m or "–" in m]
    assert bad == [], f"em dash in: {bad}"


def test_no_exclamation_marks():
    bad = [l for l, m in _enforced() if "!" in m]
    assert bad == [], f"exclamation mark in: {bad}"


def test_no_emoji():
    bad = [l for l, m in _enforced() if EMOJI.search(m)]
    assert bad == [], f"emoji in: {bad}"


def test_no_banned_hype():
    bad = [(l, b) for l, m in _enforced() for b in BANNED_HYPE if b in m.lower()]
    assert bad == [], f"banned phrase: {bad}"


def test_no_raw_database_benchmark_names():
    """"AMRAP 5 Minutes - Bar Muscle Ups" is a database row, not something a
    coach says. Anything reaching an athlete goes through
    analytics.humanise_benchmark first."""
    bad = [l for l, m in _enforced()
           if re.search(r"AMRAP \d+ Minutes", m) or re.search(r"\b\d+RM [A-Z]", m)]
    assert bad == [], f"raw benchmark name in: {bad}"


def test_no_coach_is_named_in_an_automated_message():
    """Automatic sending is off, so a coach sends these. A message that says
    "It's Jak" is wrong when Ed is the one pressing send."""
    bad = [l for l, m in _enforced() if re.search(r"\bIt's (Jak|Ed)\b", m)]
    assert bad == [], f"names a coach: {bad}"


def test_every_message_asks_something_or_asks_for_something():
    """The tone doc: one clear next action, or a genuine open question.

    A question anywhere counts, not only as the last character: "How did it
    feel? I'd love to hear your version of it." is a question with a warm tail,
    which is fine. So does an imperative close, which is an action.
    """
    actions = ("let's", "give us a shout", "message me", "book", "reply",
               "log ", "get it in", "let me know", "keep it going", "don't stop",
               "have a look", "send", "whenever you get a moment", "get in",
               "get your results logged", "don't let it")
    bad = []
    for label, m in _enforced():
        if "?" in m or "http" in m:
            continue
        if any(a in m.lower() for a in actions):
            continue
        bad.append(label)
    assert bad == [], f"neither asks a question nor asks for anything: {bad}"


def test_contractions_are_used():
    bad = [l for l, m in _enforced()
           if re.search(r"\b(You are|do not|does not|we are|it is|cannot)\b", m)]
    assert bad == [], f"missing contraction: {bad}"


# ── the humanising the messages depend on ─────────────────────────────────────

def test_humanise_handles_the_real_worst_cases():
    """Names taken from the live Benchmarks tab."""
    for raw in ("AMRAP 5 Minutes - Bar Muscle Ups",
                "Average RPM - Assault Bike 30 Minutes",
                "JST 50 Cal Fan Bike RPM Average of 3 Intervals",
                "Max Unbroken Strict Wall Facing HSPU"):
        out = analytics.humanise_benchmark(raw)
        assert " - " not in out, out
        assert not re.search(r"AMRAP \d+ Minutes", out), out
