# Context Graph - Live Retrieval Video Script

> **Runtime:** 4:01 in the current capture
> **Scenario:** WPP marketing retrieval with an unresolved policy conflict
> **Audience:** engineering, product, governance, and AI architecture stakeholders
> **Source:** live `HybridRetriever.retrieve_and_answer()` execution on tenant `marketing`

This movie is generated from `docs/presentation/context_graph_movie_trace.json`, which is
**not source-controlled** — generate it with `python scripts/capture_context_graph_demo.py`
against a live stack before rendering. The
trace is created by the live retrieval path with a stable query ID, a second
identical request proves the governed Redis answer cache, then the trace is
loaded back through `ContextGraphRepository` using the tenant-scoped API shape.

## Captured Run

- Question: Is the Nova Beverages EU Q3 campaign placement alongside sports-betting app promotional content allowed under the applicable privacy policy and campaign rules?
- Cold query ID: `movie-live-wpp-20260801-v4`
- Repeat query ID: `movie-live-wpp-20260801-v4-repeat`
- Retrieval mode selected by the planner: `local`
- Model returned by the live configuration: `llama-3.1-8b-instant`
- Evidence: 14 chunks from 4 documents
- Citations: Brand Guideline, Campaign Brief, Data Privacy Policy, and SOW
- Result: the privacy policy does not address sports-betting placements; SOW and campaign adjacency rules conflict
- Context Graph trace: `decision-query-aa6086b8b66cb06cd5a2`
- Manifest hash: `7cfa3b3e50bd46c7c33214073ae31c578aecfb72c1bc0cdbc2da76e826d0c9fc`
- Cache proof: the repeat query gets a new query ID, `cache_hit=true`, and points back to the cold trace
- Local timing in this capture: cold run `29,312 ms`; cache hit `<1 ms`
- Cache key tail shown on screen: `...2ee57394aaed6c7eec75`

## Scene 0 - Live Retrieval Starts with Indexed Evidence [0:00-0:20]

**Screen:** A real tenant snapshot: four documents, 24 chunks, 66 entities, 51 edges, and zero open conflicts from Neo4j. Show the query request identity and the retrieval mode returned by the live result.

**Action:** Hold the tenant snapshot still, then reveal the request identity and retrieval mode. Use the generated frame `scene_01.png`; do not animate the screenshot itself.

**Voiceover:**

> "The live request starts from an already indexed tenant. Marketing contains four documents and twenty-four chunks. The query enters the same retrieval path used by the application, with no hand-selected evidence in the movie."

## Scene 1 - The Question Enters the API [0:20-0:40]

**Screen:** Show the exact question, tenant, stable query ID, and completed response state.

**Action:** Reveal the question first, then the tenant and cold query ID `movie-live-wpp-20260801-v4`. Keep the completed response state visible through the transition.

**Voiceover:**

> "The question asks whether a Nova Beverages EU Q3 placement beside sports-betting promotional content is allowed. The application assigns a stable query identity so the answer and its decision trace can be found again."

## Scene 2 - Retrieval Captures Its Evidence [0:40-1:05]

**Screen:** Show all four document filenames flowing into the manifest: Campaign Brief, Statement of Work, Data Privacy Policy, and Brand Guideline. Show 14 chunks and three answer citations.

**Action:** Highlight the four document cards, then the 14-chunk count and the three citations returned by the live response. Do not imply that the movie manually selected these sources.

**Voiceover:**

> "The live retriever returns the exact chunks behind the answer. They include the Campaign Brief, the Statement of Work, the Data Privacy Policy, and the global Brand Guideline. Their document lineage is captured in the Context Graph manifest."

## Scene 3 - The Graph Expands and Reranks Context [1:05-1:30]

**Screen:** Show the actual stages reported by the live retrieval path: planner selects local mode, lexical and vector search, two-hop graph expansion, GNN scoring, and cross-encoder reranking.

**Action:** Reveal the stages from left to right and finish on the ranked evidence set. Keep the retrieval configuration readable long enough to establish that it came from the captured manifest.

**Voiceover:**

> "The planner selects local retrieval for this fact question. The path then applies vector and lexical search, two-hop graph expansion, GNN scoring, and reranking. The manifest records the retrieval mode and configuration used for this run."

## Scene 4 - The API Returns a Grounded Answer [1:30-2:00]

**Screen:** Show the exact returned answer, citations, model version, retrieval mode, and measured latency. Do not paraphrase the answer on screen.

**Action:** Show the answer response first, then mark the conflicting SOW and Campaign Brief passages. Keep `llama-3.1-8b-instant`, `local`, and the cold latency visible as response metadata.

**Voiceover:**

> "The answer does not hide the conflict. The Statement of Work excludes gambling and sports-betting placements, while the Campaign Brief lists a sports-betting companion-app adjacency. Because the privacy provisions are not present in the retrieved context, the system says that permissibility cannot be determined."

## Scene 5 - The Manifest Locks the Inference Moment [2:00-2:25]

**Screen:** Show manifest fields from the trace: tenant, model, prompt, retrieval mode, ontology, `corpus_revision`, `cache_schema_version`, evidence counts, canonical JSON byte count, the SHA-256 integrity hash, and `INTEGRITY VALID`.

**Action:** Zoom from the manifest summary into the hash field, then show the canonical JSON byte count and the integrity check. Do not show raw environment values or secrets.

**Voiceover:**

> "The manifest captures the question, chunks, documents, retrieval configuration, model, prompt version, ontology, and temporal boundaries. Canonical content is hashed with SHA-256. Reconstructing the same context produces the same hash."

## Scene 6 - A Repeat Question Hits Governed Cache [2:25-2:53]

**Screen:** Show cold run latency (`29,312 ms`), repeat-run latency (`<1 ms`), `cache_hit=true`, the tail of the Redis cache key (`...2ee57394aaed6c7eec75`), corpus revision `0`, speedup as greater than the cold-run millisecond count, and the original `source_trace_id`.

**Action:** Compare the cold and repeat request rows side by side. Highlight that the repeat has a new query ID but the same cache key and `source_trace_id`. Keep the speedup explicitly labelled as a local demo measurement.

**Voiceover:**

> "The second identical request does not rerun retrieval or the language model. Redis returns the completed answer only because the tenant, normalized question, model route, retrieval settings, ontology, prompt version, and corpus revision match the governed cache key. The new query keeps a fresh identity and points back to the original trace."

## Scene 7 - The Answer Becomes a Decision Trace [2:53-3:18]

**Screen:** Show `CGCase`, `CGAgentRun`, and `CGDecision`, with the actual `ADDRESSES` and `PRODUCED_DECISION` relationships. Show the selected option `answer` and reason `retrieved_evidence`.

**Action:** Traverse case -> run -> decision, then connect the decision to the manifest, evidence, and policy evaluation. Keep the selected option and reason code visible; do not add an unrecorded approval workflow.

**Voiceover:**

> "The live retrieval path records the answer as the selected option of a governed decision. The trace keeps the case, agent run, evidence, policy evaluation, and structured rationale together. It stores the answer, not hidden chain-of-thought."

## Scene 8 - Replay Through the Tenant-Scoped API [3:18-3:43]

**Screen:** Show the actual GET path with `tenant=marketing`. Draw the real topology: case to run, run to manifest and decision, and manifest to evidence. Show selected option, policy version, tenant, and hash validation.

**Action:** Load the trace response using the tenant-scoped API shape, expand the evidence and policy sections, and finish on the valid integrity hash. Keep the response readable rather than scrolling through raw JSON.

**Voiceover:**

> "A later request can load the same trace through the Context Graph API. The tenant is explicit, the evidence references are visible, and the reconstructed manifest still matches its integrity hash. The decision can be audited without rerunning the model."

## Scene 9 - From Answer to Accountable Decision [3:43-4:01]

**Screen:** Return to the connected trace with the case, run, manifest, evidence, and decision. Keep the final statement visible.

**Action:** Hold the complete trace, then settle on the relationship between Knowledge Graph evidence and the Context Graph decision. End on the accountable-decision message, not on a generic product title card.

**Voiceover:**

> "The Knowledge Graph finds the evidence. The Context Graph records what the system retrieved, what it answered, and why it refused to overstate the result. That is the difference between a fast answer and an accountable decision."

## Recording Notes

- The movie is a generated presentation layer over a real live retrieval capture. It is not a screen recording of the Neo4j Browser.
- The rendered scene files are still PNG frames with voiceover audio; there is no artificial camera motion over the screenshots.
- The capture script flushes the marketing tenant's answer cache, runs `HybridRetriever.retrieve_and_answer()` once to produce a governed trace, runs it again with the same question and a new query ID to prove cache reuse, then loads the persisted trace from Neo4j.
- All document IDs in the manifest are derived from the retrieved chunk parents.
- The API path includes the tenant query parameter because the Context Graph endpoint is tenant-scoped.
- Cache speedup is a demo measurement from the current local environment. Do not present it as a production benchmark.
- Never display API keys, hidden chain-of-thought, or unsupported claims about production scale.
