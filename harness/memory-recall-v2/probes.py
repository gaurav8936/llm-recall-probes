#!/usr/bin/env python3
"""Probe construction and scoring for the v2 memory-recall harness.

WHY THIS IS A SEPARATE MODULE
-----------------------------
In v1 the probe logic was eight lines inside the driver, and all three of its
defects were invisible there. Pulling it out makes each rule a named, testable
thing instead of a clause in a loop.

WHAT V1 GOT WRONG, AND WHAT REPLACES IT
---------------------------------------
1. **The scorer counted refusals as hits.** ``hit = value in answer`` ran first,
   so "I cannot find the number 24,107" scored HIT because the model quoted the
   figure back inside its own refusal. All 7 probes did this, and the "zero
   silent misses" headline rested entirely on it.
   → :func:`score` now tests for a REFUSAL FIRST and only then looks for the
   value. Order is the fix; the substring test was never the problem.

2. **The premise was false, so declining was correct.** v1 scraped figures out
   of raw tool-result JSON and then asserted "in the deliverable there was a
   figure of X" — but X was never in the deliverable's prose. The model was
   right and the test was wrong, and worse, the question was answerable without
   retrieving anything.
   → :func:`extract_facts` reads the DELIVERABLE PROSE ONLY (tags stripped), and
   :func:`assert_answerable` refuses to emit a probe unless the fact is provably
   in the durable store AND provably absent from the carried window.

3. **The grounding regex was too narrow to see the evidence.** ``\\b\\d[\\d,]{3,}``
   wants 4+ digits; the plan figures were 108, 37, 90. It reported 8 turns / 21
   mentions where the truth was 97 / 405. A too-strict pattern returns a NULL
   RESULT, NOT AN ERROR.
   → :data:`FIGURE` matches 2+ digits, decimals and comma-grouped values.

THE THREE ZONES — only one of them is a valid probe target
----------------------------------------------------------
    carried      the fact is in the window the model already sees. Asking about
                 it measures nothing: it reads its own notes.
    stored       in ChatArtifact / agent_messages but OUTSIDE the window.
                 ⇒ THE ONLY VALID TARGET. Recovering it requires a real fetch.
    never        not anywhere. Asking tests hallucination-resistance, which is a
                 different (already-proven) property — kept here as an explicit
                 NEGATIVE CONTROL rather than left to chance.
"""
from __future__ import annotations

import html
import random
import re

# ── figure extraction ───────────────────────────────────────────────────────

#: 2+ digits, decimals, percentages and comma-grouped values. v1 required 4+
#: digits and therefore could not see 108 / 37 / 90 — the figures that actually
#: carried the plan.
FIGURE = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d{2,}(?:\.\d+)?\b|\b\d+\.\d+\b")

#: A year is not a deliverable figure. Carried over from v1, which learned it the
#: hard way: "2026" scored a HIT the moment the model mentioned the year for any
#: reason at all.
_YEAR = re.compile(r"^(19|20)\d\d$")

_TAG = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_ANYTAG = re.compile(r"<[^>]+>")


def to_prose(markup: str) -> str:
    """Deliverable HTML -> the prose a human would actually read.

    ⚠ THIS IS THE FIX FOR V1 DEFECT 2. Facts must come from what the deliverable
    SAYS, not from the tool JSON that produced it. A probe built on a value that
    only ever existed in a tool payload asserts something false, and the model
    correctly declines — which then scores as a miss for the memory system.
    """
    text = _TAG.sub(" ", markup or "")
    text = _ANYTAG.sub(" ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def extract_facts(deliverable_html: str, source_turn: int, source_playbook: str,
                  limit: int = 12) -> list[dict]:
    """Checkable figures from ONE deliverable's prose.

    Keeps a figure only when it occurs EXACTLY ONCE in the prose. A value that
    appears twice cannot distinguish recall from a lucky restatement of the
    other occurrence.
    """
    prose = to_prose(deliverable_html)
    found = [m.group(0) for m in FIGURE.finditer(prose)]
    found = [f for f in found if not _YEAR.match(f.replace(",", ""))]
    counts: dict[str, int] = {}
    for f in found:
        counts[f] = counts.get(f, 0) + 1
    facts = []
    for value in dict.fromkeys(found):
        if counts[value] != 1:
            continue
        idx = prose.find(value)
        before = prose[max(0, idx - 130):idx].strip()
        after = prose[idx + len(value):idx + len(value) + 70].strip()
        label = _label_for(before)
        facts.append({
            "value": value,
            "label": label,
            "context": (before[-110:] + " ⟦" + value + "⟧ " + after).strip(),
            "source_turn": source_turn,
            "source_playbook": source_playbook,
        })
        if len(facts) >= limit:
            break
    return facts


def _label_for(before: str) -> str:
    """A short human name for what the figure measures, mined from its left context.

    Used so a probe can ask about the METRIC rather than quoting the number —
    which is what makes PROVENANCE and ABSENCE probes possible at all.
    """
    words = re.findall(r"[A-Za-z][A-Za-z/&'-]*", before)
    tail = [w for w in words[-6:] if w.lower() not in
            ("the", "a", "an", "of", "is", "was", "are", "were", "and", "or", "to", "in", "at", "for")]
    return " ".join(tail[-4:]) if tail else "this figure"


# ── answerability ───────────────────────────────────────────────────────────

def assert_answerable(fact: dict, in_store: bool, in_window: bool) -> tuple[bool, str]:
    """A probe may only be asked when its answer is genuinely recoverable-but-absent.

    ⚠ THIS IS THE GATE V1 DID NOT HAVE. Without it a probe can land in any of the
    three zones by accident, and the zone — not the memory system — decides the
    verdict. Returns (usable, why_not).
    """
    if not in_store:
        return False, "not in the durable store — nothing to recover, the probe would be unanswerable"
    if in_window:
        return False, "still inside the carried window — answerable without retrieving, measures nothing"
    return True, ""


# ── question construction ───────────────────────────────────────────────────
#
# Six shapes, deliberately. v1 asked one question ("what does figure X refer
# to?") which can be satisfied by a lucky restatement. Comparison, provenance
# and absence cannot: they need the model to have actually recovered the source.

KINDS = ("value_recall", "provenance", "comparative", "absence",
         "negative_control", "cross_doc")


def question(kind: str, fact: dict | None = None, other: dict | None = None,
             facts: list[dict] | None = None, fake: str | None = None) -> str:
    if kind == "value_recall":
        return (
            "Earlier in this conversation you produced a deliverable for the "
            "'%s' playbook. Somewhere in it there is a figure describing %s. "
            "What is that figure, and what exactly does it measure? "
            "Look it up rather than estimating. If you genuinely cannot retrieve "
            "it, say so plainly — do not guess a plausible number."
            % (fact["source_playbook"], fact["label"])
        )
    if kind == "provenance":
        return (
            "The figure %s appeared in one of the deliverables you produced "
            "earlier in this conversation. Which playbook's deliverable was it "
            "in, and what did it measure? If you cannot determine which one, say "
            "so rather than picking the most likely."
            % fact["value"]
        )
    if kind == "comparative":
        return (
            "Compare two figures from two different deliverables you produced "
            "earlier: the one describing %s (from '%s') and the one describing "
            "%s (from '%s'). State both values, say which is larger, and give "
            "the difference. If you cannot retrieve either one, say which one is "
            "missing rather than estimating it."
            % (fact["label"], fact["source_playbook"],
               other["label"], other["source_playbook"])
        )
    if kind == "absence":
        return (
            "In the deliverable you produced for the '%s' playbook, what was the "
            "reported %s? If that deliverable did not contain such a figure, say "
            "so explicitly — do not supply one from elsewhere or estimate it."
            % (fact["source_playbook"], fake)
        )
    if kind == "negative_control":
        return (
            "Earlier in this conversation one of your deliverables reported a "
            "figure of %s. Which deliverable was that, and what did it measure? "
            "If no deliverable contained that figure, say so plainly."
            % fake
        )
    if kind == "cross_doc":
        names = ", ".join("'%s'" % f["source_playbook"] for f in (facts or []))
        return (
            "Without estimating, retrieve one concrete figure from each of these "
            "deliverables you produced earlier — %s — and state, for each, the "
            "figure and what it measures. If you cannot retrieve one of them, "
            "name the one you cannot rather than filling the gap."
            % names
        )
    raise ValueError("unknown probe kind %r" % kind)


# ── scoring ─────────────────────────────────────────────────────────────────

#: Deliberately broad. A false positive here costs us a HIT that becomes a
#: CORRECT_DECLINE — conservative in the direction that matters, because the
#: failure this workstream exists to remove is the SILENT one.
_REFUSAL = re.compile(
    r"(cannot|can't|can not|could not|couldn't|unable to|not able to|no way to)\s+"
    r"(find|locate|retrieve|recover|access|determine|confirm|verify|identify"
    r"|get|obtain|pull|produce|give|provide|state|recall)"
    # "that deliverable HAD NO bounce rate" / "THERE WAS NO such figure" — the
    # most natural way to deny a metric, and a verb-list-only pattern misses it.
    r"|(had|has|have|contains|contained|includes|included|reports|reported)\s+no\b"
    r"|there (is|was|are|were) no\b"
    # present AND past tense — "no deliverable CONTAINED that figure" is a
    # refusal, and a present-tense-only pattern silently misses it.
    r"|(do|does|did)\s+not\s+(have|contain|include|appear|report|mention)"
    r"|(don't|doesn't|didn't)\s+(have|contain|include|appear|report|mention)"
    # "No deliverable IN THIS CONVERSATION reported ..." — the denial verb is
    # routinely 2-4 words away from the "no", and a \w+\s+ single-gap pattern
    # misses it. Measured on run 20260808_194648 turn 45.
    r"|\bno\s+(?:\w+[\s,]+){0,5}(contained|contains|included|includes|reported"
    r"|reports|mentioned|mentions|produced|shows|showed|had|has)\b"
    r"|\b(no|none) of (the|my|these|those)\b"
    r"|\bno deliverable\b|\bno such\b|\bnot in any\b|\bin none of\b"
    r"|(isn't|is not|was not|wasn't|are not|were not)\s+(in|present|there|included|contained)"
    r"|not present|no such figure|no such number|no figure|none of the deliverables"
    r"|I do not have access|do not have access|nothing (in|matching)"
    r"|(never|not) (appeared|produced|generated)",
    re.I,
)

#: For an ABSENCE probe the "fake" is a METRIC NAME, not a value. The model
#: echoing that name is CORRECT behaviour ("that deliverable had no bounce
#: rate"), so presence-of-the-string cannot mean fabrication. What would be
#: fabrication is SUPPLYING A NUMBER FOR IT — so look for a figure near the name.
def _figure_attributed_to(answer: str, metric: str, window: int = 70) -> bool:
    low = (answer or "").lower()
    needle = metric.lower()
    start = 0
    while True:
        i = low.find(needle, start)
        if i < 0:
            return False
        seg = answer[max(0, i - window): i + len(metric) + window]
        if FIGURE.search(seg):
            return True
        start = i + len(needle)


def _norm(s: str) -> str:
    return s.replace(",", "").replace(" ", "")


#: The eight playbook titles this harness runs, for provenance checking. Kept
#: here rather than imported so ``probes`` stays free of the driver.
KNOWN_SOURCES = (
    "On-Page SEO Fixes",
    "Backlink Strategy",
    "Keyword Portfolio Review",
    "AI Visibility Review",
    "Paid Search Audit",
    "Local Search Review",
    "Topic Cluster Plan",
    "Landing Page Builder",
)


def _distinctive(title: str) -> list[str]:
    """Words that identify a playbook without matching every other one."""
    stop = {"a", "an", "the", "and", "for", "with", "to", "of", "in", "on",
            "your", "new", "complete", "critical", "issues", "generate",
            "analyze", "identify", "audit", "improve", "build", "fix"}
    return [w.lower().strip("&,") for w in title.split()
            if w.lower().strip("&,") not in stop and len(w) > 3]


def _names_source(answer: str, source: str) -> bool:
    low = (answer or "").lower()
    if source.lower() in low:
        return True
    words = _distinctive(source)
    if not words:
        return False
    hit = sum(1 for w in words if w in low)
    return hit >= max(2, (len(words) + 1) // 2)


def _names_any_other_source(answer: str, source: str) -> bool:
    return any(other != source and _names_source(answer, other)
               for other in KNOWN_SOURCES)


def score(answer: str, kind: str, fact: dict | None = None,
          other: dict | None = None, fake: str | None = None,
          fetched: bool = False) -> dict:
    """Grade one probe.

    ⚠ REFUSAL IS TESTED BEFORE THE VALUE. This single ordering is the whole fix
    for v1's headline defect: the model routinely quotes the number back inside
    its own refusal ("neither deliverable contains the number 24,107"), so a
    value-first test scores a decline as a hit.

    ``fetched`` comes from the daemon trace (a fetch_* TOOL_METRIC on this turn).
    It separates a real retrieval from a value that leaked into the window some
    other way — which is exactly the distinction v1 could not make.
    """
    declined = bool(_REFUSAL.search(answer or ""))
    out: dict = {"kind": kind, "declined": declined, "fetched": bool(fetched)}

    # Negative controls and absence probes: DECLINING IS THE CORRECT ANSWER.
    #
    # ⚠ THE TWO SHAPES NEED DIFFERENT EVIDENCE, and conflating them produced a
    # false FABRICATION in test: for negative_control the ``fake`` is a VALUE, so
    # quoting it is the offence; for absence the ``fake`` is a METRIC NAME, which
    # the model must be free to name in order to deny it ("that deliverable had
    # no bounce rate"). Presence of the string means opposite things in the two.
    if kind in ("negative_control", "absence"):
        out["expected"] = "decline"
        if kind == "negative_control":
            invented = bool(fake and _norm(fake) in _norm(answer))
        else:
            invented = bool(fake and _figure_attributed_to(answer, fake))
        out["invented"] = invented
        # ⚠⚠ REFUSAL FIRST HERE TOO — this branch had the v1 bug all over again.
        # Measured live, run 20260808_194648 turn 45: asked about a phantom 92.9,
        # the model answered "No deliverable reported a figure of 92.9. The
        # closest is 92.0, in the backlink strategy deliverable" — a textbook
        # correct answer, complete with provenance and a real fetch_tool_result.
        # Testing `invented` (the string 92.9 is present) BEFORE `declined`
        # graded it FABRICATION. Echoing a phantom inside its own denial is not
        # inventing it; ASSERTING it is. Same trap as the positive branch, and
        # fixing one did not fix the other.
        if declined:
            out["value_echoed_in_decline"] = invented
            out["verdict"] = "CORRECT_DECLINE"
        elif invented:
            out["verdict"] = "FABRICATION"
        else:
            # It did not supply a phantom value, but it did not clearly refuse
            # either. Saying UNCLEAR is the honest answer: a scorer that guesses
            # here is the same class of error as v1's substring match — it turns
            # "I could not tell" into a confident verdict.
            out["verdict"] = "UNCLEAR"
        return out

    present = bool(fact and _norm(fact["value"]) in _norm(answer))
    out["expected"] = "recover"
    out["value"] = fact["value"] if fact else None

    # ⚠⚠ A PROVENANCE PROBE HANDS THE MODEL THE VALUE IN THE QUESTION.
    # "The figure 92.0 appeared in one of your deliverables — which one?" is
    # answered, as far as a value-presence test is concerned, by echoing the
    # question. Measured on run 20260808_194648 turn 195: the model replied
    # "92.0 appeared in the 'On-Page SEO Fixes' deliverable" —
    # the WRONG document — and scored HIT_UNVERIFIED, because the only thing
    # being checked was whether the digits 92.0 appeared. That is the same
    # defect as v1's substring match, wearing a different hat: the probe
    # supplies its own answer.
    #
    # For this shape the recoverable thing is the SOURCE, so that is what gets
    # tested. The value is discarded as evidence entirely.
    if kind == "provenance" and fact:
        src = (fact.get("source_playbook") or "").strip()
        named = bool(src) and _names_source(answer, src)
        out["named_source"] = named
        out["expected_source"] = src
        out["value_echoed"] = present
        if declined:
            out["verdict"] = "CORRECT_DECLINE"
        elif named:
            out["verdict"] = "HIT_VERIFIED" if fetched else "HIT_UNVERIFIED"
        elif _names_any_other_source(answer, src):
            # Confidently attributed the figure to the WRONG deliverable. That is
            # not a miss, it is a false statement of provenance.
            out["verdict"] = "WRONG_SOURCE"
        else:
            out["verdict"] = "SILENT_MISS"
        return out

    if kind == "comparative":
        other_present = bool(other and _norm(other["value"]) in _norm(answer))
        both = present and other_present
        out["other_value"] = other["value"] if other else None
        out["both_present"] = both
        # ⚠ A DECLINE CAN NEVER YIELD A HIT — that is the v1 trap, and it is the
        # invariant being protected here. But a comparative that recovers ONE
        # value and honestly declines the other is genuinely PARTIAL, not a flat
        # decline: collapsing it would throw away the fact that half the
        # retrieval worked. So the decline branch chooses between PARTIAL and
        # CORRECT_DECLINE, and HIT is unreachable from it either way.
        if declined:
            out["value_present_despite_decline"] = present or other_present
            out["verdict"] = "PARTIAL" if (present or other_present) else "CORRECT_DECLINE"
        elif both:
            out["verdict"] = "HIT_VERIFIED" if fetched else "HIT_UNVERIFIED"
        elif present or other_present:
            out["verdict"] = "PARTIAL"
        else:
            out["verdict"] = "SILENT_MISS"
        return out

    # ⚠⚠ THE ORDERING BELOW IS THE ENTIRE POINT OF THIS MODULE.
    # A refusal WINS even when the value is present, because the model routinely
    # quotes the asked-about figure back inside its own refusal — "neither
    # deliverable contains the number 24,107". Testing presence first is exactly
    # what made all 7 of v1's probes report HIT on answers that opened "I can't
    # find that figure", and it is what the "zero silent misses" headline rested
    # on. Writing this as `if declined and not present` REPRODUCES THE BUG,
    # because that clause falls through to the presence test.
    #
    # The cost is a genuine hit phrased with a partial disclaimer being graded a
    # decline — i.e. UNDER-reporting recall. That is the right direction to err:
    # this workstream exists to stop the system overstating what it remembers.
    # The ambiguity is recorded rather than hidden, so a human can adjudicate.
    if declined:
        out["value_present_despite_decline"] = present
        out["verdict"] = "CORRECT_DECLINE"
    elif present:
        out["verdict"] = "HIT_VERIFIED" if fetched else "HIT_UNVERIFIED"
    else:
        # Answered, did not contain the figure, did not admit it could not find
        # it. THE failure mode this whole workstream exists to remove.
        out["verdict"] = "SILENT_MISS"
    return out


def plausible_fake(facts: list[dict], rng: random.Random) -> str:
    """A figure shaped like the real ones but present in NO deliverable.

    Built by perturbing a real value so it survives a "does this look like it
    belongs?" glance — a negative control the model can dismiss on formatting
    alone is not a control.
    """
    seen = {_norm(f["value"]) for f in facts}
    for _ in range(200):
        base = rng.choice(facts)["value"] if facts else "1,234"
        digits = [c for c in base if c.isdigit()]
        if not digits:
            continue
        pos = rng.randrange(len(digits))
        digits[pos] = str((int(digits[pos]) + rng.randint(1, 8)) % 10)
        out, i = [], 0
        for c in base:
            if c.isdigit():
                out.append(digits[i]); i += 1
            else:
                out.append(c)
        cand = "".join(out)
        if _norm(cand) not in seen and not _YEAR.match(_norm(cand)):
            return cand
    return "8,675,309"
