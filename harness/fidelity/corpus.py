#!/usr/bin/env python3
"""WS11 · the grounding corpus — what the conversation was GIVEN, persisted.

⚠ THIS MODULE EXISTS BECAUSE THE EVIDENCE WAS BEING THROWN AWAY. The driver
already fetched every deliverable body (``run_fidelity.harvest`` line ~297) and
the instrument already read the wrapped payload every turn — and both discarded
the text after extracting counts and facts. Measured on the one scorable run on
disk (20260809_222624_smoke): 237 figure-mentions across ten turns, and a corpus
reconstructed from the stored fact ledger alone could account for 16.9% of them.
That number is not a finding about the system; it is the shape of a missing
corpus. Nothing downstream of a missing corpus can be believed.

Capture is APPEND-ONLY and READ-ONLY on the platform: it writes extra files into
the run directory and changes nothing the agent sees, nothing the driver decides
and nothing any other metric computes. If it is wrong, the failure mode is a
missing file, not a corrupted result.

────────────────────────────────────────────────────────────────────────────────
THE ONE RULE THAT MAKES THE METRIC MEAN ANYTHING

  THE AGENT'S OWN PROSE IS NOT A GROUNDING SOURCE.

If the carried payload were used wholesale, a figure the agent invented at turn 4
would sit in the window at turn 8 and be scored "traceable" — the metric would
launder inventions into groundedness and read ~1.0 forever. So the payload is
split: system-injected structure (the 📚 manifest block and 📎 reference lines,
which the daemon writes, not the model) is corpus; the model's own sentences are
not. Assistant prose is captured anyway, in its own channel, so the exclusion is
auditable rather than asserted.
"""
from __future__ import annotations

import json
from pathlib import Path

import instrument as INST

#: Markers the daemon emits. Same constants the address check uses — if the
#: daemon's templates change these must change with them.
REF_MARK = INST.REF_MARK            # 📎 one deliverable's pointer
MANIFEST_MARK = INST.MANIFEST_MARK  # 📚 the regenerated index

#: The channels a figure can be grounded by, in reporting order. Kept explicit
#: so a scorecard can say WHICH source accounted for a mention rather than only
#: that something did.
CHANNELS = ["deliverable", "tool_output", "user", "carried_system"]


# ── splitting one turn's span into channels ─────────────────────────────────

def _is_tool_output(it: dict) -> bool:
    """A tool RESULT, not a tool CALL.

    ⚠ The call carries the agent's own arguments and is deliberately excluded:
    the corpus is what the conversation RECEIVED. ``item_text`` already reads the
    ``output`` key (that is why it lists it), so no call_id pairing is needed.
    """
    t = str(it.get("type") or "")
    return t.endswith("function_call_output") or (
        "output" in it and t != "function_call")


def carried_system_text(items: list[dict]) -> str:
    """System-injected structure from the payload — never the model's prose.

    Two things qualify, both written by ``session_memory``, not by the model:
      * the 📚 manifest block — everything from the marker to the end of that
        item, whose rows carry titles, ids and the per-deliverable facts;
      * any line carrying a 📎 reference.

    ⚠ Read from the WRAPPED payload. Reading the raw store shows neither, because
    wrap_session substitutes references and appends the manifest AT LOAD TIME.
    → [[the_carried_payload_is_not_the_stored_session_history]]
    """
    out: list[str] = []
    for it in items:
        txt = INST.item_text(it)
        if not txt:
            continue
        if MANIFEST_MARK in txt:
            out.append(txt[txt.index(MANIFEST_MARK):])
        for line in txt.splitlines():
            if REF_MARK in line:
                out.append(line)
    return "\n".join(out)


def split_span(span: list[dict], window_items: list[dict]) -> dict:
    """The four channels of one turn, kept apart on purpose."""
    user, tool, assistant = [], [], []
    for it in span:
        if it.get("role") == "user":
            user.append(INST.item_text(it))
        elif _is_tool_output(it):
            tool.append(INST.item_text(it))
        elif it.get("role") == "assistant":
            assistant.append(INST.item_text(it))
    return {
        "user": "\n".join(x for x in user if x),
        "tool_output": "\n".join(x for x in tool if x),
        "carried_system": carried_system_text(window_items or []),
        # ⚠ CAPTURED, NEVER PART OF THE CORPUS. Stored so the exclusion above can
        # be checked by a human instead of taken on trust.
        "assistant_excluded": "\n".join(x for x in assistant if x),
    }


# ── writing ─────────────────────────────────────────────────────────────────

class CorpusWriter:
    """Append-only capture into ``<run>/corpus/``."""

    def __init__(self, run_dir: Path) -> None:
        self.root = Path(run_dir) / "corpus"
        (self.root / "deliverables").mkdir(parents=True, exist_ok=True)
        (self.root / "turns").mkdir(parents=True, exist_ok=True)

    def deliverable(self, mid: str, html: str, *, playbook: str = "",
                    turn: int = 0, title: str = "") -> None:
        """One deliverable body, written once, never rewritten.

        Monotone by construction — artifact rows are never deleted — so a fact
        created at turn 2 is still corpus at turn 200.
        """
        p = self.root / "deliverables" / ("%s.html" % mid)
        if p.exists():
            return
        p.write_text(html or "")
        meta = self.root / "deliverables" / ("%s.json" % mid)
        meta.write_text(json.dumps({"mid": mid, "playbook": playbook,
                                    "turn": turn, "title": title,
                                    "chars": len(html or "")}, indent=1))

    def turn(self, turn: int, span: list[dict], window_items: list[dict]) -> dict:
        ch = split_span(span, window_items)
        rec = {"turn": turn, **ch,
               "chars": {k: len(v) for k, v in ch.items()}}
        (self.root / "turns" / ("%03d.json" % turn)).write_text(json.dumps(rec, indent=1))
        return rec

    def note_missing(self, turn: int, why: str) -> None:
        """⚠ A turn with no snapshot must be VISIBLE, not absent. An absent file
        and an empty corpus are the same thing to a naive reader, and one of them
        is a measurement while the other is a gap."""
        (self.root / "turns" / ("%03d.json" % turn)).write_text(json.dumps(
            {"turn": turn, "missing": True, "why": why}, indent=1))


# ── reading ─────────────────────────────────────────────────────────────────

def load(run_dir: Path) -> dict | None:
    """Everything a scorer needs, or None when the run predates capture.

    ⚠ RETURNS None RATHER THAN AN EMPTY CORPUS. A run captured before this module
    existed has no corpus, and scoring it against nothing would report every
    mention ungrounded — a spectacular false finding. The caller must report
    UNCAPTURED, never a rate.
    """
    root = Path(run_dir) / "corpus"
    if not root.is_dir():
        return None
    dl = {}
    for p in sorted((root / "deliverables").glob("*.html")):
        meta_p = p.with_suffix(".json")
        meta = json.loads(meta_p.read_text()) if meta_p.is_file() else {}
        dl[p.stem] = {"html": p.read_text(), "turn": meta.get("turn", 0),
                      "playbook": meta.get("playbook", "")}
    turns = {}
    for p in sorted((root / "turns").glob("*.json")):
        r = json.loads(p.read_text())
        turns[int(r["turn"])] = r
    if not dl and not turns:
        return None
    return {"deliverables": dl, "turns": turns}
