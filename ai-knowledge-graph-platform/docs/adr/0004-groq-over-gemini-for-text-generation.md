# ADR-0004 — LLM provider routing and OpenAI embeddings

**Status:** Accepted and amended (current routing: Groq → DeepSeek; Cerebras opt-in)
**Date:** 2026-05-30  
**Author:** Sergiu Nicoara

---

## Context

The platform requires two distinct LLM capabilities:

1. **Text generation** — extraction, synthesis, reasoning steps, RAGAS evaluation judge
2. **Embeddings** — 3072d vector representations for ANN search and entity resolution

Initially both were served by Google Gemini (`gemini-2.0-flash` for generation, `gemini-embedding-001` for embeddings). Under load, Gemini's free-tier rate limits (15 RPM / 1M TPM on Flash) became the ingestion bottleneck — a moderate corpus would exhaust daily quota before completing.

*Note: A subsequent amendment (2026-06-03) also migrated embeddings from Gemini to OpenAI `text-embedding-3-large` (3072d). See the Amendment section at the end of this document.*

---

## Decision

The current provider split is:

- **Embeddings:** OpenAI `text-embedding-3-large` (3072d).
- **Large text generation:** Groq `groq_model` primary through `get_llm()`,
  with DeepSeek `deepseek-v4-flash` fallback.
- **Fast routing:** Groq `groq_fast_model` primary through `get_fast_llm()`,
  with DeepSeek fallback.
- **Provider overrides:** `LLM_INGEST_PROVIDER=deepseek` selects DeepSeek
  primary with Groq fallback; `LLM_INGEST_PROVIDER=cerebras` opts into the
  Cerebras → DeepSeek → Groq chain.

Rationale for each choice:

### Why OpenAI for embeddings

- The Neo4j vector index is created at 3072 dimensions. Changing embedding providers requires re-embedding every chunk and rebuilding the index — an expensive, risky migration.
- The original `gemini-embedding-001` evaluation established that a 3072d embedding contract was suitable for domain-specific technical text; production embeddings are now OpenAI `text-embedding-3-large`.
- Embedding calls are low-frequency compared to generation calls (one per chunk at ingestion time, not per query).
- The embedding API has separate quota from the generation API, so they don't compete.

### Why Groq remains in the stack

- **Speed:** Groq's fast inference makes the 8B routing tier inexpensive and low-latency; each routing step is about 0.2s in the measured setup.
- **Fallback:** Groq provides an independent generation fallback when DeepSeek is unavailable or rate-limited.
- **Development override:** Groq-primary mode remains useful for quick, low-volume development runs.
- **Decoupled providers:** Generation and embeddings can change independently without touching retrieval or graph logic.

### Why not a single provider for both

- No single provider offers both a competitive 3072d embedding model AND a fast inference endpoint on the same free/low-cost tier.
- Provider decoupling is an architectural feature: `get_embedder()` and `get_llm()` / `get_fast_llm()` are independent. A client deployment can point each at different internal endpoints without touching retrieval or graph logic.

---

## Two-model design (extension of this ADR)

The agentic IRCoT path uses two provider tiers:

| Call site | Model | Rationale |
|---|---|---|
| Routing steps (SEARCH/ANSWER) | Configured `groq_fast_model` | ~15 output tokens; speed matters, quality doesn't |
| Final synthesis | Configured Groq `groq_model` via `get_llm()` | Quality matters; DeepSeek is the automatic fallback |

Configured via `groq_model` and `groq_fast_model` in `settings.yml`.

---

## Consequences

**Positive:**
- Ingestion throughput is no longer Gemini-quota-limited
- Agentic path p95 reduced by ~50%
- Provider swap is a single-function change

**Negative / watch:**
- The RAGAS judge currently tries DeepSeek first, then Groq, then Gemini if the earlier clients are unavailable.
- The fast routing model is separate from the user-facing synthesis model and should be recorded separately in future provenance improvements.

**Migration path for client deployments:**
Change `get_llm()`, `get_fast_llm()`, and `get_embedder()` in `graphrag/core/llm_client.py`. Nothing else requires modification.

---

## Amendment — 2026-06-03: Embeddings migrated from Gemini to OpenAI

### Context

After ADR-0004 was accepted, the Google Gemini API key was revoked, removing access to `gemini-embedding-001`. The embeddings provider was migrated to OpenAI `text-embedding-3-large`.

### Decision

- **Embeddings:** Switch from `gemini-embedding-001` to `openai/text-embedding-3-large` (3072d)
- **Generation fallback:** Switch from Gemini Flash to DeepSeek-V3 (`deepseek-chat`) via OpenAI-compatible API

### Why OpenAI for embeddings

- `text-embedding-3-large` also produces 3072d vectors — no schema migration required; the existing Neo4j vector index is fully compatible
- Switching was a single-line change in `get_embedder()` in `llm_client.py`
- Cost: ~$0.13/1M tokens (negligible for the 12-doc corpus)
- Quality on technical text is at least equivalent to `gemini-embedding-001`

### Why DeepSeek-V3 as generation fallback

- OpenAI-compatible REST API — the existing `openai` SDK is reused
- Generous rate limits; instant failover on Groq 429 with no sleep required
- Cost: ~$0.07/1M input tokens (cheaper than Gemini Flash)

### Impact

No behavioral change to the retrieval pipeline. Vector dimensions unchanged. The migration was a historical corpus re-embedding and re-indexing exercise; its entity and edge counts are not a current corpus claim.

---

## Historical amendments

The 2026-06-03 embedding migration and later provider changes are retained as
decision history only. They do not override the current route above; consult
`graphrag/core/llm_client.py` and the configuration for the active model names.
