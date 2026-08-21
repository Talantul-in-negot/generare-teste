# ADR-0006 — Dual-LLM architecture: fast routing + quality synthesis

**Status:** Accepted  
**Date:** 2026-06-02  
**Author:** Sergiu Nicoara

---

## Context

The agentic IRCoT retriever (Interleaved Retrieval and Chain-of-Thought) runs a multi-step loop:

```
for step in range(max_steps):
    reasoning = await llm("Can you answer? If not, what do you still need to search for?")
    if "ANSWER:" in reasoning:
        return synthesise(reasoning)          # final answer
    sub_query = parse_search_query(reasoning)
    new_chunks = retrieve(sub_query)          # another retrieval pass
    context += new_chunks

final_answer = await llm(FINAL_PROMPT.format(context=context, question=question))
```

Initially, every LLM call in this loop used the same large model. This is useful
for final synthesis but wasteful for the intermediate reasoning steps.

The intermediate step asks the model to emit one of two structured tokens:
- `SEARCH: <sub-query>` (~15 output tokens)
- `ANSWER: <short text>` (~20 output tokens)

This is a classification / structured extraction task, not a generation task. It does not require a 70B model.

---

## Decision

Split the agentic loop into two model tiers:

| Step | Model | Rationale |
|---|---|---|
| Reasoning steps (SEARCH/ANSWER routing) | Configured `groq_fast_model` | Trivial structured output; speed dominates |
| Final synthesis | Configured Groq `groq_model` via `get_llm()` | User-facing answer; quality dominates; DeepSeek is the fallback |

Implementation in `graphrag/retrieval/agentic_retriever.py`:

```python
async def _reason(self, prompt: str) -> str:
    """Fast 8B model for intermediate SEARCH/ANSWER routing."""
    return await get_fast_llm().generate(prompt)

async def _synthesize(self, prompt: str) -> str:
    """Full 70B model for final user-facing synthesis."""
    return await get_llm().generate(prompt)
```

`get_fast_llm()` points at the configured Groq fast model (`groq_fast_model`)
with DeepSeek fallback. `get_llm()` defaults to the configured Groq large model
(`groq_model`) with DeepSeek fallback. `LLM_INGEST_PROVIDER=deepseek` selects
DeepSeek as primary; `LLM_INGEST_PROVIDER=cerebras` enables the Cerebras chain.

The direct `AgenticRetriever` default is two steps; the production retrieval
configuration permits a bounded maximum of four sub-searches.

---

## Latency impact

For a typical 2-step agentic query:

| Stage | Old (70B all) | New (split) |
|---|---|---|
| Initial retrieval | ~0.5s | ~0.5s |
| Reasoning step 1 (8B) | 1.5s | 0.2s |
| Sub-retrieval | ~0.5s | ~0.5s |
| Reasoning step 2 (8B) | 1.5s | 0.2s |
| Final synthesis (70B) | ~1.5s | ~1.5s |
| **Total** | **~5.5s** | **~2.9s** |

Measured p95 improvement: **6.8s → 3.4s** (−50%).  
The historical combined p95 measurement was **5.9s → 2.7s**. It is retained as
the original benchmark, not as the current platform baseline; the current
roadmap records later live measurements separately.

---

## Considered alternatives

### Option A — Use 8B for everything including synthesis

- Maximum speed, minimum cost
- Rejected: synthesis quality degrades measurably on multi-hop reasoning questions where the final answer requires integrating evidence from 3–4 chunks. Faithfulness dropped from 0.840 to ~0.71 in informal testing.

### Option B — Keep 70B for everything

- Maximum quality
- Rejected: p95 latency exceeds the 3s SLO and the quality improvement for routing decisions is negligible (they're trivially structured outputs)

### Option C — Model cascade (try 8B, escalate if uncertain)

- Use 8B for routing; if it produces malformed output, retry with 70B
- More complex; adds latency on escalation; routing output is so constrained (`SEARCH:` or `ANSWER:`) that malformation is rare
- Rejected: unnecessary complexity for the current traffic pattern

---

## Consequences

**Positive:**
- Agentic p95 within SLO for the first time
- Cost and latency reduction: the short routing calls use Groq's fast 8B tier instead of the large synthesis model
- Configurable: `groq_fast_model` in `settings.yml` allows swapping the routing model without changing code

**Negative / watch:**
- Model provenance: the routing model is not currently surfaced separately in the audit trail; `QueryResult.model_version` should be read as the synthesis-tier model.
- If the 8B model is replaced with one that produces different structured output format (`SEARCH:` / `ANSWER:`), the parser in `agentic_retriever.py` must be updated in parallel.

**Extension points:**
- The same pattern applies to any multi-step pipeline where intermediate steps are structured and the final step is generative: ingestion extraction (8B for entity detection, 70B for relation extraction), evaluation (8B for classification, 70B for explanation).

---

## Historical provider changes

Earlier provider-specific amendments are intentionally summarized rather than
kept as current configuration claims. The durable decision is the two-tier
routing split; active provider names come from `settings.yml` and
`graphrag/core/llm_client.py`.
