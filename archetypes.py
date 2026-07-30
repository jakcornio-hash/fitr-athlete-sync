"""
Archetype scoring engine — Conscious Coaching (Brett Bartholomew, 2017).

Loads the JSON instruments from data/ and exposes:
  score_forced_choice(answers)  -> output object
  score_rating(ratings, pairs)  -> output object
  get_archetype(id)             -> archetype dict (coach voice)
  ARCHETYPES                    -> full archetype map
  FORCED_CHOICE                 -> instrument dict
  RATING                        -> instrument dict
"""
import json
import os
import re

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "data")


def _load(name):
    with open(os.path.join(_DATA, name), encoding="utf-8") as f:
        return json.load(f)


_archetypes_raw = _load("archetypes.json")
ARCHETYPES = _archetypes_raw["archetypes"]
FORCED_CHOICE = _load("instrument-forced-choice.json")
RATING = _load("instrument-rating.json")


def get_archetype(archetype_id):
    return ARCHETYPES.get(archetype_id, {})


def result_is_close(profile, margin=8):
    """A read is 'close' when the top two archetypes sit within `margin` pct
    points. Used to soften the delivery message rather than overclaim."""
    if not profile or len(profile) < 2:
        return False
    return (profile[0].get("pct", 0) - profile[1].get("pct", 0)) < margin


def athlete_result_message(name, primary, profile=None):
    """Athlete-facing 'here's your archetype' message, in the coaching voice.

    Confidence-aware: when the read is clear it states the archetype plainly;
    when the top two are close it presents both and invites the athlete to say
    which feels more them. Strips em dashes per the tone guidelines and ends on
    an open question. Returns None if the archetype is unknown.
    """
    arch = get_archetype(primary)
    if not arch:
        return None
    first = name.split()[0] if name else "there"
    athlete = arch.get("athlete", {}) or {}
    arch_name = arch.get("name", primary.replace("_", " ").title())
    tagline = str(athlete.get("tagline", "")).strip()
    works = athlete.get("works", []) or []

    def _clean(s):
        return re.sub(r"\s*[—–]\s*", ", ", str(s)).strip()

    # Three or more archetypes bunched at the top is a genuine spread, not a
    # winner. Saying "you're a Leader" there is picking one of four arbitrarily,
    # which is exactly what makes a result feel wrong. Name them and ask.
    if profile and len(profile) >= 3 and result_is_close(profile, margin=4) \
            and (profile[0].get("pct", 0) - profile[2].get("pct", 0)) < 4:
        tied = [p["archetype"] for p in profile
                if profile[0].get("pct", 0) - p.get("pct", 0) < 4][:4]
        names = [get_archetype(t).get("name", t.replace("_", " ").title()) for t in tied]
        listed = ", ".join(names[:-1]) + " and " + names[-1]
        return (f"{first}, had a proper look at your athlete profile and you're a "
                f"genuine mix. {listed} all came through about level, which usually "
                f"means you adapt depending on the situation rather than sitting in "
                f"one box. Which of those feels most like you on a hard day? That "
                f"tells me more than the test does.")

    if profile and result_is_close(profile):
        sec = get_archetype(profile[1]["archetype"]) or {}
        second = sec.get("name", str(profile[1]["archetype"]).replace("_", " ").title())
        msg = (f"{first}, had a proper look at your athlete profile. You lean "
               f"{arch_name}, with a fair bit of {second} in there too.")
        if tagline:
            msg += f" {_clean(tagline)}"
        msg += (" Does that feel right, or does one side feel more you? Either way "
                "it helps me coach you the way you actually respond to.")
        return msg

    _article = "an" if arch_name[:1].lower() in "aeiou" else "a"
    msg = f"{first}, your athlete profile's confirmed. You've come out as {_article} {arch_name}."
    if tagline:
        msg += f" {_clean(tagline)}"
    if works:
        msg += f" One thing that tends to work for you: {_clean(works[0]).rstrip('.').lower()}."
    msg += " Have a think, does that ring true? Knowing this helps me coach you the way you actually respond to."
    return msg


def _build_output(scores, top_n):
    """Convert raw score dict to the standard output object."""
    total = sum(scores.values())
    if total == 0:
        return {"primary": None, "profile": []}
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    profile = [
        {
            "archetype": aid,
            "score": round(score, 3),
            "pct": round(score / total * 100),
        }
        for aid, score in ranked
        if score > 0
    ][:top_n]
    return {"primary": profile[0]["archetype"] if profile else None, "profile": profile}


def score_forced_choice(answers):
    """
    answers: list of chosen option indices, one per question.
    Returns {primary, profile} per spec.
    """
    scores = {}
    questions = FORCED_CHOICE.get("questions", [])
    for q_idx, option_idx in enumerate(answers):
        if q_idx >= len(questions):
            break
        options = questions[q_idx].get("options", [])
        # None means the answer couldn't be mapped (e.g. a question the athlete
        # didn't answer, or form text that has drifted). Skip it rather than
        # crash, so a partial response still scores on what it does have.
        if option_idx is None or option_idx >= len(options):
            continue
        archs = options[option_idx].get("archetypes", [])
        weight = 1.0 / len(archs) if archs else 0
        for aid in archs:
            scores[aid] = scores.get(aid, 0) + weight
    return _build_output(scores, top_n=4)


def score_rating(ratings, pairs=None):
    """
    ratings: list of 0-3 values, one per statement (len == 25).
    pairs:   list of {most: idx, least: idx} dicts (may be empty or None).
    Returns {primary, profile} per spec.
    """
    statements = RATING.get("statements", [])

    # Step 1: mean per archetype from rated statements
    sums = {}
    counts = {}
    for i, rating in enumerate(ratings):
        if i >= len(statements):
            break
        aid = statements[i].get("archetype")
        if not aid:
            continue
        sums[aid] = sums.get(aid, 0) + rating
        counts[aid] = counts.get(aid, 0) + 1

    scores = {aid: sums[aid] / counts[aid] for aid in sums if counts[aid] > 0}

    # Step 2: forced-pair adjustments
    instrument_pairs = RATING.get("forced_pairs", [])
    for pair_idx, pair_answer in enumerate(pairs or []):
        if pair_idx >= len(instrument_pairs):
            break
        pair_def = instrument_pairs[pair_idx]
        options = pair_def.get("options", [])
        most_idx = pair_answer.get("most")
        least_idx = pair_answer.get("least")
        if most_idx is not None and most_idx < len(options):
            aid = options[most_idx].get("archetype")
            if aid:
                scores[aid] = scores.get(aid, 0) + 0.8
        if least_idx is not None and least_idx < len(options):
            aid = options[least_idx].get("archetype")
            if aid:
                scores[aid] = max(0, scores.get(aid, 0) - 0.6)

    return _build_output(scores, top_n=5)


# ── Typeform intake helpers ──────────────────────────────────────────────────
# The athlete self-assessment is collected via a Typeform whose questions and
# answer options mirror the forced-choice instrument. Responses arrive as answer
# TEXT, so map that text back to the option index and score with the canonical
# engine (never Typeform's own tallies) — that keeps self-reads directly
# comparable with coach reads.

def _norm_text(s):
    """Lowercase, strip everything but alphanumerics. Survives punctuation edits
    (e.g. an em dash swapped for a comma)."""
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def forced_choice_question_texts():
    """The athlete-voice question strings, in order (match the form headers)."""
    return [q.get("q_athlete", "") for q in FORCED_CHOICE.get("questions", [])]


def forced_choice_answer_index(q_idx, answer_text):
    """Map a chosen answer's TEXT back to its option index for question q_idx.

    Returns None if it can't be matched, so the caller can skip rather than
    silently score a wrong archetype.
    """
    questions = FORCED_CHOICE.get("questions", [])
    if q_idx >= len(questions):
        return None
    target = _norm_text(answer_text)
    if not target:
        return None
    options = questions[q_idx].get("options", [])

    for i, o in enumerate(options):
        if _norm_text(o.get("athlete", "")) == target:
            return i
    # Tolerant fallback: one is a prefix of the other (handles truncation/edits)
    for i, o in enumerate(options):
        n = _norm_text(o.get("athlete", ""))
        if n and (n.startswith(target[:40]) or target.startswith(n[:40])):
            return i
    return None
