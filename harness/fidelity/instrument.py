"""Stand-in for the harness's instrument module (the copy's own file; the real one stays private).

The real `instrument.py` reads a live conversation's carried payload out of the agent daemon. `corpus.py` needs only
three things from it: the two marker characters the daemon writes into that payload, and `item_text`, which pulls the
readable text out of one conversation item. Those three are reproduced here unchanged.
"""
from __future__ import annotations

REF_MARK = "\U0001F4CE"        # 📎 — a single deliverable's pointer
MANIFEST_MARK = "\U0001F4DA"   # 📚 — the pinned, regenerated-every-turn index


def item_text(it: dict) -> str:
    """The human-readable text of one item, whatever shape it arrived in.

    DO NOT SEARCH json.dumps(items) FOR MARKERS. Two separate null-result bugs
    came from that while building this module, both the same family:
      * ensure_ascii=True escapes the manifest emoji to a surrogate pair, so a
        literal-emoji test never matches -- the loader returned 101 rows (the
        manifest WAS appended) while the check reported manifest_present=False;
      * JSON escapes the inner quotes of artifact_message_id="<uuid>", so a
        pattern written against the daemon's own template never matches either.
    Both report "no address present" on a payload full of addresses, which reads
    exactly like the defect this workstream exists to find. Search the TEXT.
    """
    c = it.get("content")
    if isinstance(c, str):
        return c
    parts: list[str] = []
    if isinstance(c, list):
        for pp in c:
            if isinstance(pp, dict):
                parts.append(str(pp.get("text") or ""))
            elif isinstance(pp, str):
                parts.append(pp)
    for k in ("arguments", "output", "text"):
        v = it.get(k)
        if isinstance(v, str):
            parts.append(v)
    return "\n".join(x for x in parts if x)
