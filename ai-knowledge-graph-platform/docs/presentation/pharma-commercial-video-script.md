# Pharma Commercial Knowledge Graph: Presentation Movie

**Format:** English narrated presentation movie, approximately three minutes.

**Scope note:** The commercial-pharma data is synthetic. It contains no real
patients, products, medical claims, treatment advice, or customer data. The
Knowledge Graph is the main story. The final Context Graph scene is an optional
audit extension over the real retrieval run.

| Time | Scene | On screen | Voiceover |
|---|---|---|---|
| 0:00-0:18 | The business question | Synthetic-data disclosure, tenant, and the live question. | "Commercial teams need approved content for a precise interaction, not a plausible answer. This synthetic demonstration asks which CardioDemo content may be used for a cardiology specialist in Germany, for a defined synthetic indication." |
| 0:18-0:36 | Governed source corpus | The seven locally ingested source documents and the live document, chunk, entity, and edge counts. | "The tenant starts with a small but realistic governed corpus: label, claims, HCP profile, campaign, policy, and two content versions. Each source is synthetic and versioned before it becomes retrievable evidence." |
| 0:36-0:58 | Commercial ontology | Product, indication, market, specialty, content, and policy classes with their permitted relations. | "The ontology makes the commercial vocabulary explicit. A product treats an indication. A professional has a specialty. Content is approved for a market. These are graph constraints, not instructions hidden in a prompt." |
| 0:58-1:16 | Validation and semantic export | Valid and invalid triples, then the live RDF and SHACL result. | "The schema rejects a relationship that violates its domain and range. The same graph exports to RDF and its structure is checked by SHACL, providing an independent semantic control." |
| 1:16-1:44 | Live hybrid retrieval | The actual question moving through ANN, BM25/RRF, reranking, two-hop traversal, and GAT scoring. | "The normal application path retrieves evidence through vector and lexical retrieval, cross-encoder reranking, graph expansion, and graph-aware scoring. It connects the question to product, market, specialty, policy, and content evidence." |
| 1:44-2:08 | Grounded answer | The live answer, measured latency, and the five captured citations. | "The system recommends the current Germany Cardiology Detail Aid. The response is grounded in the approved content and its supporting campaign, label, HCP profile, and claims. The expired version is not silently treated as equivalent." |
| 2:08-2:32 | Deterministic policy result | Actual allow and deny outputs from the synthetic commercial policy evaluator. | "Policy evaluation is deterministic. The current version is allowed because product, indication, market, specialty, validity, and evidence match. The prior revision is denied because it is expired. This is commercial content governance, not medical advice." |
| 2:32-2:55 | Optional Context Graph trace | The real case, agent run, manifest, decision, selected option, and manifest SHA-256 validation. | "The Knowledge Graph makes the selection defensible. The optional Context Graph makes the agent run auditable: which case was handled, which evidence was available, what it selected, and the exact immutable manifest used for that retrieval." |
| 2:55-3:10 | Close | KG and CG flow diagram with the live integrity result. | "Together, the Knowledge Graph governs what is known and allowed. The Context Graph records what an AI system actually used and concluded. That makes commercial AI more precise, inspectable, and easier to govern." |

The renderer reads `pharma_commercial_movie_trace.json`, produced by the live
capture script. Re-capture before publishing if the tenant corpus, model route,
or local result changes.
