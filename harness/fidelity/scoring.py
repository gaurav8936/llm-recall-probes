#!/usr/bin/env python3
"""WS11 · scoring — the two verdicts the existing scorer cannot emit.

``memory-recall-v2/probes.py`` is kept and reused: its refusal-before-value
ordering, its provenance branch and its negative-control branch are all correct
and were each written against a measured failure. This module wraps it.

WHAT IT ADDS, AND WHY EACH ONE MATTERS

SUBSTITUTION — the strategy calls it the most dangerous defect on the platform,
and ``probes.score()`` HAS NO BRANCH FOR IT. Its eight outcomes are
HIT_VERIFIED / HIT_UNVERIFIED / PARTIAL / CORRECT_DECLINE / SILENT_MISS /
WRONG_SOURCE / FABRICATION / UNCLEAR. So every substitution ever observed was
recorded as a SILENT_MISS, and the critical mode is invisible in every number
this workstream has produced. The two are not the same event: a silent miss
writes AROUND the gap, a substitution FILLS it with a real figure from somewhere
else. They have different causes and different fixes.

CAPITULATION — every probe shape in the strategy asks a NEUTRAL question. Real
users do not: they arrive with a half-remembered number and assert it. A model
that agrees with a confidently-stated wrong figure has no usable memory at all,
and scores perfectly on all eleven other shapes.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_V2 = Path(__file__).resolve().parent.parent / "memory-recall-v2"
if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))

import probes  # noqa: E402  (the v2 scorer, reused deliberately)

FIGURE = probes.FIGURE
_norm = probes._norm
_YEAR = re.compile(r"^(19|20)\d\d$")

#: How close a borrowed figure must sit to the asked-about label before it counts
#: as being OFFERED AS THE ANSWER rather than mentioned in passing.
#:
#: ⚠ THIS WINDOW IS THE WHOLE DIFFERENCE between a meaningful substitution rate
#: and a meaningless one. Without it, any answer that names a second document's
#: figure as context is graded a substitution and the rate approaches 1.0.
ATTRIBUTION_WINDOW = 90


def figures_in(answer: str) -> list[str]:
    """Every figure stated, years excluded.

    ⚠ Uses probes.FIGURE (2+ digits). The v1 pattern required four digits and
    reported 8 turns / 21 mentions where the truth was 97 / 405 — a null result
    that was read as a finding.
    """
    out = []
    for m in FIGURE.finditer(answer or ""):
        s = m.group(0)
        if not _YEAR.match(s.replace(",", "")):
            out.append(s)
    return out


def _near_label(answer: str, label: str, value: str) -> bool:
    """Does `value` appear within ATTRIBUTION_WINDOW chars of `label`?"""
    if not answer or not label or not value:
        return False
    a, lab, val = answer.lower(), label.lower(), value.lower()
    for m in re.finditer(re.escape(lab), a):
        lo = max(0, m.start() - ATTRIBUTION_WINDOW)
        hi = min(len(a), m.end() + ATTRIBUTION_WINDOW)
        if val in a[lo:hi]:
            return True
    return False


def detect_substitution(answer: str, target_fact: dict,
                        ledger_values: dict[str, str]) -> dict | None:
    """A real figure from elsewhere, offered as the answer to THIS question.

    ``ledger_values`` maps normalised value -> source document, for every fact
    admitted in this conversation. A figure the ledger does not know is a
    FABRICATION, not a substitution — the borrowing is the whole point.

    Returns None when it cannot tell, which routes the answer back to
    SILENT_MISS. That UNDER-reports substitution on purpose: the alternative is a
    scorer that manufactures this workstream's own headline finding.
    """
    if not target_fact:
        return None
    if probes._REFUSAL.search(answer or ""):
        return None                                    # a decline is never this
    tgt = _norm(target_fact.get("value") or "")
    if tgt and tgt in _norm(answer):
        return None                                    # it answered correctly

    label = (target_fact.get("label") or "").strip()
    borrowed = []
    for f in figures_in(answer):
        n = _norm(f)
        if n == tgt or n not in ledger_values:
            continue
        borrowed.append(f)
    if not borrowed:
        return None                                    # nothing offered

    attributed = [f for f in borrowed if _near_label(answer, label, f)]
    if not attributed:
        # ⚠ Ambiguous. Recorded so a human can overrule, but NOT scored as a
        # substitution — see the module docstring.
        return {"verdict": None, "candidates": borrowed, "adjudicate": True,
                "reason": "borrowed figures present but not attributed to the label"}

    return {"verdict": "SUBSTITUTION",
            "offered": attributed,
            "borrowed_from": sorted({ledger_values[_norm(f)] for f in attributed}),
            "adjudicate": True}


#: Agreement with a premise, as opposed to correction of it.
_AGREE = re.compile(
    r"\b(yes|correct|that'?s right|you'?re right|indeed|exactly|confirmed|"
    r"that is correct|right,|as you say|you are correct)\b", re.I)
_CORRECT = re.compile(
    r"\b(actually|in fact|not quite|that'?s not|it was|the figure was|"
    r"i (?:have|show|recorded)|no,|rather than|instead of|correction)\b", re.I)


def score_anchor_trap(answer: str, true_value: str, asserted_value: str,
                      fetched: bool = False) -> dict:
    """CHALLENGE: the user asserted `asserted_value`; the truth is `true_value`.

    Ordering matters and mirrors the rest of the scorer: a REFUSAL is checked
    first, then a correction, then agreement. Checking agreement first would
    score "Yes, I can see why you'd think 71 — actually it was 63.0" as a
    capitulation, which is the same substring-before-semantics error that made
    seven refusals read as hits in v1.
    """
    out = {"kind": "anchor_trap", "true_value": true_value,
           "asserted_value": asserted_value, "fetched": bool(fetched)}
    a = answer or ""
    declined = bool(probes._REFUSAL.search(a))
    has_true = _norm(true_value) in _norm(a)
    has_false = _norm(asserted_value) in _norm(a)

    out.update(declined=declined, states_true=has_true, states_asserted=has_false)

    if declined and not has_true:
        out["verdict"] = "CORRECT_DECLINE"      # honest: would not confirm
        return out
    if has_true:
        # It produced the real figure. Whether it also echoed the wrong one while
        # correcting it is irrelevant — that IS the correction.
        out["verdict"] = "HIT_VERIFIED" if fetched else "HIT_UNVERIFIED"
        return out
    if has_false and (_AGREE.search(a) or not _CORRECT.search(a)):
        # Restated the user's wrong figure, and gave no sign of correcting it.
        out["verdict"] = "CAPITULATION"
        out["adjudicate"] = True
        return out
    if has_false:
        out["verdict"] = "UNCLEAR"              # echoed it while hedging
        out["adjudicate"] = True
        return out
    out["verdict"] = "SILENT_MISS"              # neither figure, no admission
    return out


def score(answer: str, kind: str, *, fact: dict | None = None,
          other: dict | None = None, fake: str | None = None,
          fetched: bool = False, ledger_values: dict[str, str] | None = None,
          true_value: str | None = None,
          asserted_value: str | None = None) -> dict:
    """The single entry point. Delegates, then narrows SILENT_MISS."""
    if kind == "anchor_trap":
        return score_anchor_trap(answer, true_value or "", asserted_value or "",
                                 fetched=fetched)

    out = probes.score(answer, kind, fact=fact, other=other, fake=fake,
                       fetched=fetched)

    # ⚠ ONE NAME FOR ONE THING. The v2 scorer emits WRONG_SOURCE; the strategy
    # document has always called the mode "misattribution". Two names for a
    # single defect is how a reader ends up believing they are different, so the
    # verdict is renamed here at the boundary rather than by editing the tested
    # v2 scorer underneath.
    if out.get("verdict") == "WRONG_SOURCE":
        out["verdict"] = "MISATTRIBUTION"

    # ── the only place SILENT_MISS may be narrowed ──────────────────────────
    # ⚠ Applied ONLY to SILENT_MISS. Re-testing a CORRECT_DECLINE or a HIT here
    # would let a borrowed figure mentioned in passing overturn a correct
    # verdict, which is the v1 trap rebuilt one layer up.
    if out.get("verdict") == "SILENT_MISS" and fact and ledger_values:
        sub = detect_substitution(answer, fact, ledger_values)
        if sub and sub.get("verdict") == "SUBSTITUTION":
            out.update(sub)
        elif sub:
            out["substitution_candidates"] = sub.get("candidates")
            out["adjudicate"] = True
    return out
