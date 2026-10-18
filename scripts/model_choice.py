"""The words a model choice is shown with (DRE-6786).

The CEO, 2026-10-10: any model with a declared price may be chosen for any
group (DRE-6785). So the console's Settings › Agents & models page no longer
grays a choice out for the policy: it shows the choice, and where there is
something to say, one plain-English sentence with both prices — the `caution`
of each choice in `config/model-choices.json`.

Two writers say it, and they say it from here so they say the same thing:
`scripts/sync_model_config.py` publishes it in that file, and the console's
change-writer puts it in the pull request a choice opens. Neither restates a
sentence.

    model_label(model_id)        `claude-opus-5-5` → `Opus 5.5`
    price_text(price)            `$10/$50`, `$0.10/$0.50` (input/output per MTok)
    caution_for(raw, prices, group_key, model_id, *, current=None)
                                 the sentence, or None
    set_aside_date(raw, model_id)
                                 the date a model was set aside, or None
    plain_words_problems(text)   every way a sentence fails the plain-words rule

The CEO reads these words as they are written, so they never name a ladder, a
rung, a kind, a numbered rule or a file.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_MODEL_ID = re.compile(r"claude-([a-z]+)-(\d+)(?:-(\d+))?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# The one group whose volume the dearest model's caution names (the hot path).
BUSIEST_GROUP = "workhorse"


def model_label(model_id: str) -> str:
    """`claude-<family>-<major>-<minor>` → `<Family> <major>.<minor>`
    (`claude-opus-5` → `Opus 5`). An id of any other shape is its own label."""
    match = _MODEL_ID.fullmatch(model_id)
    if not match:
        return model_id
    family, major, minor = match.groups()
    return f"{family.title()} {major}" + (f".{minor}" if minor else "")


def _dollars(amount) -> str:
    amount = float(amount)
    return f"${amount:.0f}" if amount.is_integer() else f"${amount:.2f}"


def price_text(price: Mapping) -> str:
    """`$<input>/$<output>` per million tokens: whole dollars bare (`$10/$50`),
    anything else to the cent (`$0.10/$0.50`)."""
    return f"{_dollars(price['input'])}/{_dollars(price['output'])}"


def _entry_id(entry) -> str | None:
    if isinstance(entry, Mapping):
        model = entry.get("model")
        return model if isinstance(model, str) else None
    return entry if isinstance(entry, str) else None


def _excluded_reason(raw_config: Mapping, model_id: str) -> tuple[bool, str]:
    """(whether `model_id` is excluded, its reason or "")."""
    for entry in raw_config.get("excluded") or []:
        if _entry_id(entry) == model_id:
            reason = entry.get("reason") if isinstance(entry, Mapping) else None
            return True, reason if isinstance(reason, str) else ""
    return False, ""


def _group(raw_config: Mapping, group_key: str) -> tuple[str, str]:
    """(the group's label, the first rung of its ladder). `KeyError` naming
    `group_key` when the file does not declare it under `kinds`."""
    kinds = raw_config.get("kinds") or {}
    if group_key not in kinds:
        raise KeyError(f"{group_key!r} is not a group declared under `kinds`")
    spec = kinds[group_key]
    ladder = spec.get("ladder") if isinstance(spec, Mapping) else spec
    label = spec.get("label") if isinstance(spec, Mapping) else None
    rungs = (raw_config.get("ladders") or {})[ladder]
    return str(label or group_key.title()), _entry_id(rungs[0])


def _dearer(chosen: Mapping, current: Mapping) -> bool:
    """Dearer on input or on output — a cheaper side never buys a dearer one."""
    return (float(chosen["input"]) > float(current["input"])
            or float(chosen["output"]) > float(current["output"]))


def _dearest(prices: Mapping, model_id: str) -> bool:
    """Input and output both at least every other priced model's."""
    mine = prices[model_id]
    return all(
        float(mine["input"]) >= float(other["input"])
        and float(mine["output"]) >= float(other["output"])
        for other in prices.values()
    )


def caution_for(raw_config: Mapping, prices: Mapping, group_key: str, model_id: str,
                *, current: str | None = None) -> str | None:
    """The one sentence a choice of `model_id` for `group_key` carries, or None.

    "Current" is `current` when given (a project's standing exception for that
    group), else the first rung of the group's ladder; "Group" is its label.
    The rules, in this order:

      1. the chosen model is Current — None;
      2. it is excluded — the set-aside sentence, which also says it costs
         more when it does (rule 3), still one sentence;
      3. it is dearer than Current on input or output — the costs-more
         sentence, ending on the busiest group when the group is the Builders
         and the model is the dearest priced one;
      4. otherwise None.
    """
    label, first = _group(raw_config, group_key)
    current = current or first
    if model_id == current:
        return None
    chosen, now = model_label(model_id), model_label(current)
    theirs, ours = price_text(prices[model_id]), price_text(prices[current])
    dearer = _dearer(prices[model_id], prices[current])
    excluded, _ = _excluded_reason(raw_config, model_id)
    if excluded and dearer:
        return (f"{chosen} is a model we set aside; choosing it brings it back for "
                f"{label}, and it costs more than {now}: {theirs} against {ours} per "
                "million tokens.")
    if excluded:
        return (f"{chosen} is a model we set aside; choosing it brings it back for "
                f"{label} at {theirs} per million tokens, against {now} at {ours}.")
    if dearer:
        busiest = (", and Builders is the busiest group"
                   if group_key == BUSIEST_GROUP and _dearest(prices, model_id) else "")
        return (f"{chosen} costs more than {now}: {theirs} against {ours} per million "
                f"tokens{busiest}.")
    return None


def set_aside_date(raw_config: Mapping, model_id: str) -> str | None:
    """The first `YYYY-MM-DD` in `model_id`'s `excluded` reason; None when it
    is not excluded or its reason carries no date."""
    excluded, reason = _excluded_reason(raw_config, model_id)
    match = _DATE.search(reason) if excluded else None
    return match.group(0) if match else None


# Each test, and the plain words for what it found.
_PLAIN_WORDS = (
    (re.compile(r"ladder", re.I), "says `ladder`"),
    (re.compile(r"rung", re.I), "says `rung`"),
    (re.compile(r"\bkinds?\b", re.I), "says `kind`"),
    (re.compile(r"\brule\s*\d", re.I), "names a numbered rule"),
    (re.compile(
        r"\S*/\S*\.[a-z][a-z0-9]*\b|config/|scripts/|\.yaml\b|\.py\b|\.json\b",
        re.I), "names a file"),
)


def plain_words_problems(text: str) -> list[str]:
    """Every way `text` fails the plain-words rule — `ladder`, `rung`, `kind`
    as a word, `rule` followed by a number, a file path — one entry per way,
    case-insensitive. Empty when the CEO can read it as written."""
    return [problem for pattern, problem in _PLAIN_WORDS if pattern.search(text)]
