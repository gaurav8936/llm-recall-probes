#!/usr/bin/env python3
"""WS11 · ⑧ POPULATION GROUNDING — traceable figure-mentions ÷ all figure-mentions.

    over every turn in the run, not over the probes

The only one of the ten numbers with NO CELL BEHIND IT, which is exactly why it
is built first: it needs no level assigner, no grid, and no long run. Every other
number in Tier C is computed over a few dozen planted probes; this one is
computed over everything the model said — 237 figure-mentions in a ten-turn run,
so it cannot be starved of n.

────────────────────────────────────────────────────────────────────────────────
⚠ WHAT IT CANNOT SEE, STATED BEFORE ANY RESULT IS REPORTED

  IT SCORES A SUBSTITUTION AS CLEAN.

A real figure lifted from the WRONG document is in the corpus, so it is
traceable. The poster says so on its face. ⑧ is a FLOOR on fidelity, not a
measure of it — it catches figures that exist NOWHERE. Substitution is ③ and
misattribution is ⑥, and neither exists until the level assigner lands.
``blindness_fixture()`` below asserts this limitation is real, so that it stays
deliberate instead of quietly changing when someone edits the matcher.

────────────────────────────────────────────────────────────────────────────────
⚠ WHY THIS IS SET MEMBERSHIP AND NOT A SUBSTRING SCAN

``"12" in "3128"`` is True. The zone check in ledger.py is exactly that shape —
``_norm(value) in _norm(text)`` — and it is correct there, because it is asking
"is this specific known value present". Used HERE it would score almost every
mention traceable and the metric would read ≈1.0 for ever, which is the
family that has already produced two wrong results in this workstream.
→ [[a_substring_match_scores_a_refusal_as_a_hit]]

So: extract figures from BOTH sides with the SAME extractor, normalise both, and
compare as sets. Never scan.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
for _p in (str(_HERE), str(_HERE.parent / "memory-recall-v2")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import corpus as CORP   # noqa: E402
import scoring          # noqa: E402

#: Below this many total mentions the run reports UNTESTED, never a rate.
#: Matches ledger.BUCKET_FLOOR's rule everywhere else: what was not measured is
#: never reported as a pass.
MENTION_FLOOR = 25


# ── normalisation ───────────────────────────────────────────────────────────
#
# ⚠ DECLARED HERE, PRE-REGISTERED, AND TESTED IN BOTH DIRECTIONS. `63.0` `63`
# `63%` `6,300` are one figure to a reader and four strings to a matcher.
# The bias is DELIBERATELY GENEROUS: because ⑧ is a floor, over-crediting
# traceability makes the number conservative in the direction that matters — it
# will under-report a problem rather than manufacture one. A harness must never
# manufacture its own workstream's headline finding.
_TRAIL_ZEROS = re.compile(r"\.0+$")


def norm(fig: str) -> str:
    """One figure → its comparison key."""
    s = str(fig).strip().replace(",", "").replace("%", "").lstrip("$")
    s = _TRAIL_ZEROS.sub("", s)
    return s


def figures(text: str) -> list[str]:
    """Every figure in a blob, years excluded.

    ⚠ REUSES probes.FIGURE VIA scoring.figures_in — DOES NOT WRITE A NEW PATTERN.
    The v1 pattern required four digits and reported 8 turns / 21 mentions where
    the truth was 97 / 405: a null result that was read as a finding.
    → [[a_too_narrow_regex_returns_a_null_result_not_an_error]]
    """
    return scoring.figures_in(text or "")


def figure_set(text: str) -> set[str]:
    return {norm(f) for f in figures(text)}


# ── the corpus, as of a turn ────────────────────────────────────────────────

def creation_turns(state: dict, corp: dict) -> dict[str, int]:
    """mid → the turn the deliverable was CREATED, not the turn it was harvested.

    ⚠ THIS CORRECTION IS NOT COSMETIC. ``harvest()`` stamps a deliverable with the
    turn it was FOUND, and a playbook's artifact can land after its own turn has
    already been harvested — measured live on run 20260811_134746, where the
    turn-1 SEO deliverable only became visible at turn 4 and was stamped turn 4.
    Filtering the corpus on that stamp would score every figure the model
    correctly quoted from it at turns 2 and 3 as UNGROUNDED — the harness
    inventing a grounding failure out of its own harvest latency.

    The playbook schedule is the authority: ``playbooks_run`` records which
    playbook ran on which turn, and each deliverable carries its playbook name.
    Falls back to the recorded turn when a name cannot be matched.
    """
    by_name = {}
    for r in state.get("playbooks_run") or []:
        n = (r.get("name") or "").strip().lower()
        if n:
            by_name.setdefault(n, int(r.get("turn") or 0))
    out = {}
    for mid, d in (corp.get("deliverables") or {}).items():
        nm = (d.get("playbook") or "").strip().lower()
        out[mid] = by_name.get(nm, int(d.get("turn") or 0))
    return out


def corpus_sets(corp: dict, turn: int, created: dict | None = None) -> dict[str, set[str]]:
    """The four grounding channels, as of ``turn``, each as a set of figures.

    Deliverables are CUMULATIVE (monotone — artifact rows are never deleted).
    The per-turn channels are that turn's only.
    """
    dl: set[str] = set()
    for mid, d in (corp.get("deliverables") or {}).items():
        born = (created or {}).get(mid, int(d.get("turn") or 0))
        if born <= turn:
            dl |= figure_set(d.get("html") or "")
    t = (corp.get("turns") or {}).get(turn) or {}
    return {
        "deliverable":    dl,
        "tool_output":    figure_set(t.get("tool_output") or ""),
        "user":           figure_set(t.get("user") or ""),
        "carried_system": figure_set(t.get("carried_system") or ""),
    }


# ── the metric ──────────────────────────────────────────────────────────────

def compute(state: dict, corp: dict | None) -> dict:
    """⑧ over a whole run.

    Denominator: every figure the ASSISTANT emitted, every turn.
    ⚠ NEVER THE QUESTION TEXT. The harness authors the questions, and an
    ``anchor_trap`` question deliberately contains a WRONG figure — counting it
    would let the harness pollute its own denominator with figures it planted.
    """
    if corp is None:
        return {"status": "UNCAPTURED", "rate": None,
                "note": "this run predates corpus capture — no corpus on disk. "
                        "A rate computed against nothing would report every "
                        "mention ungrounded, which is a false finding, not a result."}

    created = creation_turns(state, corp)
    rows, ungrounded = [], []
    by_channel: Counter = Counter()
    n_total = n_traced = 0

    for rec in state.get("turns") or []:
        turn = int(rec.get("turn") or 0)
        answer = rec.get("answer") or ""
        mentions = figures(answer)
        if not mentions:
            rows.append({"turn": turn, "mentions": 0, "traceable": 0, "rate": None})
            continue
        sets = corpus_sets(corp, turn, created)
        t_here = 0
        for f in mentions:
            k = norm(f)
            hit = next((c for c in CORP.CHANNELS if k in sets[c]), None)
            n_total += 1
            if hit:
                n_traced += 1
                t_here += 1
                by_channel[hit] += 1
            else:
                ungrounded.append({"turn": turn, "figure": f,
                                   "kind": rec.get("kind"), "motif": rec.get("motif"),
                                   "context": _context(answer, f)})
        rows.append({"turn": turn, "mentions": len(mentions), "traceable": t_here,
                     "rate": round(t_here / len(mentions), 3)})

    status = "measured" if n_total >= MENTION_FLOOR else "UNTESTED"
    return {
        "status": status,
        "rate": (round(n_traced / n_total, 4) if n_total else None),
        "traceable": n_traced,
        "total_mentions": n_total,
        "ungrounded": n_total - n_traced,
        "floor": MENTION_FLOOR,
        "by_channel": dict(by_channel),
        "deliverable_creation_turns": created,
        "per_turn": rows,
        "ungrounded_gallery": ungrounded,
        "blindness": "⚠ A real figure taken from the WRONG document is in the "
                     "corpus and therefore scores CLEAN. This is a floor on "
                     "fidelity, not a measure of it — substitution is ③, "
                     "misattribution is ⑥.",
    }


def _context(answer: str, fig: str, width: int = 60) -> str:
    i = answer.find(fig)
    if i < 0:
        return ""
    return " ".join(answer[max(0, i - width): i + len(fig) + width].split())


# ── fixtures — two of them exist to prove the metric CAN fail ───────────────
#
# ⚠ THE DISCIPLINE: ask of any green result, WAS THIS TEST CAPABLE OF REPORTING
# FAILURE? Following selftest.py's convention, every detector is shown producing
# the wrong answer on a known-bad input.

def _fake_state(answers: list[str]) -> dict:
    return {"turns": [{"turn": i + 1, "answer": a, "kind": "filler"}
                      for i, a in enumerate(answers)]}


def _fake_corpus(bodies: dict[str, str], per_turn: dict | None = None) -> dict:
    return {"deliverables": {m: {"html": h, "turn": 1, "playbook": m}
                             for m, h in bodies.items()},
            "turns": per_turn or {}}


def fixtures() -> list[tuple[str, bool, str]]:
    """(name, passed, detail) — run by selftest.py."""
    out = []

    # 1 · a figure present in NO channel is counted ungrounded.
    #     If this passes trivially the metric is not wired up at all.
    r = compute(_fake_state(["Traffic rose to 4821 sessions."]),
                _fake_corpus({"doc": "<p>Traffic rose to 63 sessions.</p>"}))
    out.append(("planted ungrounded figure is caught",
                r["ungrounded"] == 1 and r["traceable"] == 0,
                "ungrounded=%s traceable=%s" % (r["ungrounded"], r["traceable"])))

    # 2 · BLINDNESS PROOF. A real figure borrowed from the WRONG document scores
    #     CLEAN. Asserts the stated limitation is deliberate — if someone
    #     "improves" the matcher into per-document checking, this fails loudly
    #     and the doc has to change with it.
    r = compute(_fake_state(["The client's backlink count is 412."]),
                _fake_corpus({"client": "<p>Client has 118 backlinks.</p>",
                              "competitor": "<p>Competitor has 412 backlinks.</p>"}))
    out.append(("blindness proof — borrowed figure scores clean",
                r["ungrounded"] == 0 and r["traceable"] == 1,
                "ungrounded=%s (must be 0 — ⑧ cannot see substitution)" % r["ungrounded"]))

    # 3 · SUBSTRING GUARD. "12" inside "3128" must NOT count as traceable.
    #     This is the single most important line in the metric.
    r = compute(_fake_state(["We saw 12 errors."]),
                _fake_corpus({"doc": "<p>Session 3128 completed.</p>"}))
    out.append(("substring guard — 12 is not inside 3128",
                r["ungrounded"] == 1,
                "ungrounded=%s (substring matching would make this 0)" % r["ungrounded"]))

    # 4 · the agent cannot ground itself. A figure invented at turn 1 and echoed
    #     at turn 2 stays ungrounded BOTH times — the corpus excludes assistant
    #     prose, so invention cannot launder into groundedness.
    r = compute(_fake_state(["I estimate 777 keywords.", "As I said, 777 keywords."]),
                _fake_corpus({"doc": "<p>Nothing numeric here.</p>"}))
    out.append(("agent cannot ground itself by repetition",
                r["ungrounded"] == 2,
                "ungrounded=%s of 2" % r["ungrounded"]))

    # 5 · normalisation, both directions.
    r = compute(_fake_state(["It was 63.0% and 6,300 total."]),
                _fake_corpus({"doc": "<p>63 percent; 6300 total.</p>"}))
    out.append(("normalisation credits 63.0/63 and 6,300/6300",
                r["traceable"] == 2, "traceable=%s of 2" % r["traceable"]))

    # 6 · EMPTY-INPUT GATE. Zero mentions is UNTESTED, never 1.0. A run with no
    #     figures is not a perfectly grounded run.
    r = compute(_fake_state(["No numbers at all here."]), _fake_corpus({"d": "<p>x</p>"}))
    out.append(("empty input returns UNTESTED, not 1.0",
                r["status"] == "UNTESTED" and r["rate"] is None,
                "status=%s rate=%s" % (r["status"], r["rate"])))

    # 7 · an UNCAPTURED run must refuse to produce a rate.
    r = compute(_fake_state(["Value 999."]), None)
    out.append(("uncaptured run refuses to emit a rate",
                r["status"] == "UNCAPTURED" and r["rate"] is None,
                "status=%s" % r["status"]))

    # 8 · HARVEST-LATENCY CORRECTION. A deliverable created at turn 1 but stamped
    #     turn 4 by harvest must still ground a mention at turn 2. Without the
    #     correction this reports an ungrounded figure that is plainly grounded.
    st = {"turns": [{"turn": 2, "answer": "The audit found 8814 issues.", "kind": "filler"}],
          "playbooks_run": [{"turn": 1, "name": "SEO Audit"}]}
    cp = {"deliverables": {"m1": {"html": "<p>8814 issues.</p>", "turn": 4,
                                  "playbook": "SEO Audit"}}, "turns": {}}
    r = compute(st, cp)
    out.append(("harvest latency does not fake an ungrounded figure",
                r["traceable"] == 1 and r["ungrounded"] == 0,
                "traceable=%s (stamped turn 4, created turn 1, asked at turn 2)" % r["traceable"]))

    # 9 · the tool-output and carried-system channels actually ground.
    r = compute(_fake_state(["The raw value is 8814."]),
                _fake_corpus({}, {1: {"turn": 1, "tool_output": "raw=8814", "user": "",
                                      "carried_system": ""}}))
    out.append(("tool_output channel grounds a mention",
                r["traceable"] == 1 and r["by_channel"].get("tool_output") == 1,
                "by_channel=%s" % r["by_channel"]))

    return out


if __name__ == "__main__":
    if len(sys.argv) > 1:
        d = Path(sys.argv[1])
        st = json.loads((d / "state.json").read_text())
        print(json.dumps(compute(st, CORP.load(d)), indent=1)[:4000])
    else:
        bad = 0
        for name, ok, detail in fixtures():
            print("%-4s %-52s %s" % ("ok" if ok else "FAIL", name, detail))
            bad += (not ok)
        print("%d/%d" % (len(fixtures()) - bad, len(fixtures())))
        sys.exit(1 if bad else 0)
