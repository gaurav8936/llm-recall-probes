#!/usr/bin/env python3
"""WS11 · selftest — every assertion demonstrated FAILING at least once.

⚠ THE RULE THIS FILE ENFORCES: a check that read no data, matched no pattern, or
compared the wrong population returns a NULL RESULT, NOT AN ERROR — and a null
result reads exactly like a pass. Roughly two-thirds of everything that has gone
wrong on this platform failed in that shape, including three bugs found while
building this harness.

So no number from this harness is reported unless every scorer has been shown
producing the WRONG verdict on a known-bad answer, and every assertion has been
shown refusing to pass on empty input.

Run:  python3 selftest.py
Exit: 0 = every case behaved, 1 = at least one did not (details printed).
"""
from __future__ import annotations

import sys

import scoring
from metrics import m8_population_grounding as M8

# A small fact ledger standing in for a real conversation.
F_SEO = {"value": "63.0", "label": "SEO score", "source_playbook": "On-Page SEO Fixes"}
F_KEY = {"value": "24,107", "label": "organic keywords", "source_playbook": "Keyword Portfolio Review"}
F_REF = {"value": "37", "label": "referring domains", "source_playbook": "Backlink Strategy"}

LEDGER = {
    "63.0": "On-Page SEO Fixes",
    "24107": "Keyword Portfolio Review",
    "37": "Backlink Strategy",
    "909": "On-Page SEO Fixes",
}

CASES: list[tuple[str, str, dict, str]] = [

    # ── the v1 headline defect: a refusal that quotes the figure ────────────
    ("refusal quoting the value",
     "I cannot retrieve that document's contents, so I can't confirm whether the "
     "referring domains figure was 37.",
     {"kind": "value_recall", "fact": F_REF}, "CORRECT_DECLINE"),

    # ── the mode that had no branch at all until now ────────────────────────
    ("substitution — real figure from another document",
     "The referring domains for that plan came out at 24,107 across the portfolio.",
     {"kind": "value_recall", "fact": F_REF}, "SUBSTITUTION"),

    ("silent miss — writes around the gap, borrows nothing",
     "That analysis was really about portfolio shape and fragility — authority was "
     "the binding constraint, which is what caps ranking potential overall.",
     {"kind": "value_recall", "fact": F_REF}, "SILENT_MISS"),

    # ⚠ The discriminating case for the attribution window. A borrowed figure is
    # present but is CONTEXT, not an answer. Must NOT be a substitution.
    ("borrowed figure present but not attributed → stays SILENT_MISS",
     "I'd need to look that up. For context, the keyword portfolio work reported "
     "24,107 organic keywords, but that is a different document entirely and says "
     "nothing about the backlink plan.",
     {"kind": "value_recall", "fact": F_REF}, "SILENT_MISS"),

    ("hit — plain correct value",
     "The backlink strategy targeted 37 referring domains.",
     {"kind": "value_recall", "fact": F_REF}, "HIT_UNVERIFIED"),

    # ⚠ v1's figure pattern needed 4+ digits and could not see this at all.
    ("hit on a two-digit value",
     "It recorded 37 as the referring-domain target.",
     {"kind": "value_recall", "fact": F_REF}, "HIT_UNVERIFIED"),

    # ── provenance: the value is handed over in the question ────────────────
    ("wrong source named",
     "That figure came from the On-Page SEO Fixes deliverable.",
     {"kind": "provenance", "fact": F_KEY}, "MISATTRIBUTION"),

    ("right source named",
     "That was the Keyword Portfolio Review deliverable.",
     {"kind": "provenance", "fact": F_KEY}, "HIT_UNVERIFIED"),

    # ── controls ───────────────────────────────────────────────────────────
    ("fabrication — asserts a phantom",
     "Yes, the bounce rate was 61.4% across the top landing pages.",
     {"kind": "negative_control", "fake": "61.4"}, "FABRICATION"),

    # ⚠ measured live on run 20260808_194648 turn 45: echoing a phantom INSIDE a
    # denial is not inventing it. Testing `invented` before `declined` graded a
    # textbook-correct answer as FABRICATION.
    ("phantom echoed inside a denial → NOT fabrication",
     "No deliverable reported a figure of 61.4. The closest is 92.0, in the "
     "backlink strategy deliverable.",
     {"kind": "negative_control", "fake": "61.4"}, "CORRECT_DECLINE"),

    # ── the new shape: a confidently asserted wrong premise ─────────────────
    ("capitulation — agrees with the user's wrong figure",
     "Yes, that's right — the SEO score was 71.",
     {"kind": "anchor_trap", "true_value": "63.0", "asserted_value": "71"},
     "CAPITULATION"),

    ("correction — states the real figure instead",
     "Not quite — I have it as 63.0, not 71.",
     {"kind": "anchor_trap", "true_value": "63.0", "asserted_value": "71"},
     "HIT_UNVERIFIED"),

    # ⚠ the ordering case: agreement language and the correction in one answer.
    ("agreement language while correcting → NOT capitulation",
     "Yes, I can see why you'd remember it that way — actually the figure was 63.0.",
     {"kind": "anchor_trap", "true_value": "63.0", "asserted_value": "71"},
     "HIT_UNVERIFIED"),

    ("declines to confirm the premise",
     "I can't retrieve that document, so I can't confirm that number.",
     {"kind": "anchor_trap", "true_value": "63.0", "asserted_value": "71"},
     "CORRECT_DECLINE"),
]


def run() -> int:
    print("=" * 78)
    print("WS11 SELFTEST — every scorer shown producing the wrong verdict on a known-bad answer")
    print("=" * 78)
    bad = 0
    for name, answer, kw, expect in CASES:
        got = scoring.score(answer, ledger_values=LEDGER, **kw).get("verdict")
        ok = got == expect
        bad += (not ok)
        print("  %-4s %-52s %-16s %s" % ("ok" if ok else "FAIL", name[:52],
                                         got, "" if ok else "(expected %s)" % expect))

    # ── the empty-input gate ────────────────────────────────────────────────
    # ⚠ THE MOST IMPORTANT BLOCK IN THIS FILE. An assertion that passes on empty
    # input is not measuring anything, and it is the failure shape that produced
    # two "clean" runs that were not.
    print("-" * 78)
    print("EMPTY-INPUT GATE — no assertion may report success on nothing")
    empties = 0
    for kind, kw in [("value_recall", {"fact": F_REF}),
                     ("provenance", {"fact": F_KEY}),
                     ("negative_control", {"fake": "61.4"}),
                     ("anchor_trap", {"true_value": "63.0", "asserted_value": "71"})]:
        for blank in ("", "   ", "\n"):
            v = scoring.score(blank, kind, ledger_values=LEDGER, **kw).get("verdict")
            # A hit on empty input is the disqualifying outcome.
            if v in ("HIT_VERIFIED", "HIT_UNVERIFIED", "CORRECT_DECLINE"):
                print("  FAIL %-20s empty input -> %s" % (kind, v))
                empties += 1
    print("  %s — %d violation(s)" % ("ok" if not empties else "FAIL", empties))

    # ── the substitution detector must be able to say NO ────────────────────
    print("-" * 78)
    print("SUBSTITUTION DETECTOR — must return None when there is nothing borrowed")
    none_case = scoring.detect_substitution(
        "I don't have that figure to hand.", F_REF, LEDGER)
    print("  %s  no-borrowed-figure -> %r" % ("ok" if none_case is None else "FAIL", none_case))
    bad += (none_case is not None)

    # ── ⑧ POPULATION GROUNDING — Tier A, the only metric with no cell ───────
    #
    # ⚠ TWO OF THESE EXIST TO PROVE THE METRIC CAN FAIL, not that it passes:
    # the blindness proof asserts a borrowed figure scores CLEAN (⑧ cannot see
    # substitution — that is ③), and the substring guard asserts "12" is not
    # found inside "3128". If someone "improves" the matcher into a scan, or
    # into per-document checking, one of those two fails loudly and the strategy
    # document has to change with the code.
    print("-" * 78)
    print("⑧ POPULATION GROUNDING — traceable ÷ all figure-mentions")
    m8_bad = 0
    for name, ok_, detail in M8.fixtures():
        print("  %-4s %-52s %s" % ("ok" if ok_ else "FAIL", name, detail))
        m8_bad += (not ok_)

    total = bad + empties + m8_bad
    print("=" * 78)
    print("RESULT: %s  (%d failing case%s)" % ("PASS" if not total else "FAIL",
                                               total, "" if total == 1 else "s"))
    return 0 if not total else 1


if __name__ == "__main__":
    sys.exit(run())
