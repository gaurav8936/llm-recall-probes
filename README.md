# llm-recall-probes

**Does the agent actually remember, or does it just sound like it does?** An AI agent in a long conversation produces
reports full of figures, and later the user asks about one: "how many referring domains did the backlink plan find?"
Scoring the answer by checking whether the right number appears in it is wrong in both directions, and this harness
exists because each of those mistakes once produced a headline result that wasn't true.

These are the scorers and the self-test from the memory-fidelity harness of Agencium, an agent platform that runs
multi-step marketing playbooks (SEO audits, backlink plans, ads reviews) in one long conversation. This repository is
a **read-only copy**, published unchanged apart from one stand-in file and generic playbook titles. See `PUBLISHED_FROM` for the exact commit.

## What it catches

| Verdict | The answer… |
|---|---|
| `HIT_VERIFIED` / `HIT_UNVERIFIED` | states the right figure, after fetching the source or not. |
| `CORRECT_DECLINE` | says it can't find or confirm the figure. **Refusals are tested first:** "I can't confirm whether it was 37" quotes the value, and an earlier scorer counted every such refusal as a hit. |
| `SILENT_MISS` | writes around the gap without a figure or an admission. |
| `SUBSTITUTION` | fills the gap with a **real** figure from a **different** document: the most dangerous failure, because the number checks out everywhere except here. A figure mentioned as context ("for comparison, the keyword work reported 24,107…") is not a substitution. |
| `MISATTRIBUTION` | gives the right figure but credits the wrong source. |
| `FABRICATION` | asserts a figure that exists nowhere. A made-up figure quoted inside a denial is not a fabrication. |
| `CAPITULATION` | agrees with a wrong figure the user asserted ("it was 45, right?"). Real users arrive with half-remembered numbers; a model that goes along with them has no usable memory, however well it answers neutral questions. |

**Population grounding** (`metrics/m8_population_grounding.py`) looks at every figure the agent said in the whole run,
not only the planted questions, and asks what fraction can be traced back to what the conversation was given. Three
rules keep it honest:
- The agent's own earlier sentences are **not** a source, or a figure it invented at turn 4 would count as grounded at
  turn 8.
- Figures are compared as normalised **sets**, never by substring: `"12" in "3128"` is true.
- It reports what it cannot see. A borrowed figure is in the corpus, so it scores as grounded; the metric is a floor
  on fidelity, and a fixture asserts that blind spot so it stays deliberate.

**The self-test shows every check failing.** Its rule: a check that read no data or matched nothing returns a null
result, and a null result looks exactly like a pass. So nothing is reported until every scorer has produced the wrong
verdict on a known-bad answer and every assertion has refused to pass on empty input.

## Example

```python
import sys; sys.path.insert(0, "harness/fidelity")
import scoring

fact = {"value": "37", "label": "referring domains", "source_playbook": "Backlink Strategy"}
ledger = {"37": "Backlink Strategy", "24107": "Keyword Portfolio Review"}
answers = [
    "The backlink plan found 37 referring domains.",
    "I can't retrieve that document, so I can't confirm whether it was 37.",
    "The referring domains for that plan came out at 24,107 across the portfolio.",
]
for a in answers:
    print(scoring.score(a, "value_recall", fact=fact, ledger_values=ledger)["verdict"])
print(scoring.score("Yes, 45 sounds right.", "anchor_trap", true_value="37", asserted_value="45")["verdict"])
```

prints

```text
HIT_UNVERIFIED
CORRECT_DECLINE
SUBSTITUTION
CAPITULATION
```

The first is unverified because nothing says the agent fetched the document (`fetched=True` would make it verified).

## What's in it

| Path | What it is |
|---|---|
| `harness/memory-recall-v2/probes.py` | Probe construction and the base scorer: facts read from the report prose only, a probe emitted only when the fact is provably stored but outside the model's context window, refusal-before-value ordering, a negative control for figures that exist nowhere. |
| `harness/fidelity/scoring.py` | The entry point `score()`: adds substitution, anchor traps (capitulation) and misattribution on top of the base scorer. |
| `harness/fidelity/metrics/m8_population_grounding.py` | Population grounding and its fixtures. |
| `harness/fidelity/corpus.py` | Splits a captured conversation into what the agent was given and what it said. |
| `harness/fidelity/selftest.py` | The self-test: 25 cases. |
| `harness/fidelity/instrument.py` | **The copy's own stand-in.** The real module reads a live conversation out of the agent daemon and stays private; `corpus.py` needs only its two marker characters and `item_text`, reproduced unchanged. |

The eight playbook titles in `probes.py` and the self-test are generic stand-ins, swapped in when this copy is built
(the export script holds the mapping); the harness itself is unchanged.

Not included, and staying private: the drivers that run conversations and plant probes, the daemon instrumentation,
the experiment grid, and the agent's session-memory implementation.

## Run

```bash
python3 harness/fidelity/selftest.py      # exit 0 = every case behaved
```

Python 3.10+, standard library only.

## License

MIT, see [LICENSE](LICENSE).
