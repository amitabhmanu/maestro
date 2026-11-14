"""Gemness: how much a unit is a rare gem's kind of good, as a pluggable score for the explorer's map.

The explorer's map puts every unit on two axes: popularity (reach, from `popularity.py`) and gemness.
Gemness has no settled model yet, so it is a registry: each model reads one unit's popularity
summary (`popularity.unit_summary`) and returns a score from 0 to 100 with the basis it rests on,
or None when it has nothing to say. The snapshot carries every registered model's description
(`gemness.models`) and each unit's scores (`unit["gemness"][model_id]`); the page lets the reader
pick a model. A future model (intrinsic, audio, the user's calibrated picks) is one more entry in
`MODELS`; nothing in the page changes.

The only model today, `provisional`, invents nothing: it shows the expert word the gem scorer
already uses (`quality`) and, where there is none, the "worth a listen" lead score, marked as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# basis values the page knows how to draw: expert (an expert's word or the user's pick),
# lead (a hint without an expert's word). Anything else is drawn like a lead.
Score = dict[str, Any]  # {"score": 0-100, "basis": str}


@dataclass(frozen=True)
class Model:
    id: str
    label: str
    about: str
    provisional: bool
    score: Callable[[dict[str, Any]], Score | None]  # unit popularity summary -> score


def _provisional(pop: dict[str, Any]) -> Score | None:
    if pop.get("picked"):
        return {"score": 100, "basis": "expert"}
    if pop.get("quality"):
        return {"score": int(pop["quality"]), "basis": "expert"}
    if pop.get("lead"):
        return {"score": int(pop["lead"]["score"]), "basis": "lead"}
    return None


MODELS: list[Model] = [
    Model(
        id="provisional",
        label="Provisional: expert word, else lead",
        about="A placeholder until gemness has its own model. Expert word: how strongly expert gem lists "
              "(or your own picks) single the unit out, whatever its popularity. Lead: a 'worth a listen' "
              "hint (a strong team, a hit beside it) with no expert word yet; leads are not gems. "
              "Units with neither are unrated, not zero.",
        provisional=True,
        score=_provisional,
    ),
]


def describe() -> dict[str, Any]:
    """The snapshot's `gemness` block: the models a reader can choose, the default first."""
    return {"default": MODELS[0].id,
            "models": [{"id": m.id, "label": m.label, "about": m.about, "provisional": m.provisional}
                       for m in MODELS]}


def scores(pop: dict[str, Any] | None) -> dict[str, Score]:
    """Every model's score for one unit (models with nothing to say are left out)."""
    if not pop:
        return {}
    out = {}
    for m in MODELS:
        s = m.score(pop)
        if s is not None:
            out[m.id] = s
    return out
