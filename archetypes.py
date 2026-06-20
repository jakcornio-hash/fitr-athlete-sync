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
    answers: list of chosen option indices, one per question (len == 10).
    Returns {primary, profile} per spec.
    """
    scores = {}
    questions = FORCED_CHOICE.get("questions", [])
    for q_idx, option_idx in enumerate(answers):
        if q_idx >= len(questions):
            break
        options = questions[q_idx].get("options", [])
        if option_idx >= len(options):
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
    instrument_pairs = RATING.get("pairs", [])
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
