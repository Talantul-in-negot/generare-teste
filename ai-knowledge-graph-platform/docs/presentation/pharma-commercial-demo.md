# Synthetic Commercial-Pharma Demo

This is a **Knowledge Graph and ontology demonstration**, designed for a
commercial-pharma conversation. Its core workflow does not require Context
Graph. The accompanying presentation movie adds an optional, final Context
Graph trace over a live retrieval run to demonstrate auditability. All content
is synthetic: there are no real products, medical claims, patients, HCPs,
prescribing instructions, or commercial campaigns.

## Scenario

Question: *Which approved synthetic content can be used for a cardiology
specialist in Germany for CardioDemo and Demo Cardiac Condition?*

The graph must select `SYNTHETIC-CONTENT-CardioDemo-DE-approved-v2` and reject
the expired `...-expired-v1` version. A record without a controlled evidence
reference is escalated for review.

## What It Demonstrates

1. A tenant-scoped `pharma` corpus in `data/pharma_commercial/`.
2. A config-driven ontology in `config/ontologies/pharma_commercial.yml`:
   `PHARMA_PRODUCT -> TREATS -> INDICATION`, `PERSON -> SPECIALIZES_IN ->
   MEDICAL_SPECIALTY`, and `COMMERCIAL_CONTENT -> APPROVED_FOR -> MARKET`.
3. Domain/range validation. For example, a `PERSON -> TREATS -> INDICATION`
   triple is rejected as invalid.
4. RDF/OWL export and SHACL validation using the platform's normal exporter.
5. A deterministic, auditable content-approval policy that distinguishes
   approved, expired, out-of-scope, and evidence-free content.
6. The existing hybrid retrieval path, with its citations, when a live Neo4j
   corpus and configured generation provider are available.

The policy evaluator is intentionally limited to commercial-content metadata;
it is **not** a clinical rules engine or medical decision-support component.

## Run It

The deterministic ontology, SHACL fixture, and policy demo needs no services:

```powershell
python scripts/demo_pharma_commercial.py
```

To ingest the synthetic corpus into Neo4j:

```powershell
python scripts/ingest_corpus.py --tenant pharma --commit
python scripts/export_rdf.py --tenant pharma --validate
python scripts/demo_pharma_commercial.py --live-retrieval
```

The last command uses `HybridRetriever.retrieve_and_answer()` without a
`query_id`. It uses the live KG retrieval path but intentionally does not create
a Context Graph decision trace.

## English Presentation Movie

The movie keeps the KG as its main story and uses a final Context Graph scene
only to show the audit record of a normal live retrieval. Capture fresh values,
then render the static-frame English narration:

```powershell
python scripts/capture_pharma_commercial_movie.py
python docs/presentation/render_pharma_commercial_movie.py
```

The scene-by-scene production script is in
[`pharma-commercial-video-script.md`](pharma-commercial-video-script.md).
The generated MP4 is deliberately not source-controlled.

## Verified Live Capture

Captured locally on 2026-08-03 after `--wipe --commit` ingestion of the
synthetic corpus:

- 7 documents, 9 chunks, 26 entities, 30 asserted/inferred edges, and one
  inferred edge in the isolated `pharma` tenant.
- RDF export: 391 triples; SHACL validation conformed.
- The factoid router selected `local` within `HybridRetriever`; the run used
  vector ANN, BM25/RRF, cross-encoder reranking, depth-2 multi-hop traversal,
  and GAT re-scoring before synthesis.
- The movie capture's configured synthesis run completed in 37,547 ms and
  cited the approved content, campaign, label, HCP profile, and claim
  documents. It selected `SYNTHETIC-CONTENT-CardioDemo-DE-approved-v2` and
  stated that it is approved for Germany, the synthetic indication, and the
  Cardiology specialty.

These are a single local demo capture, not a general production latency or
quality benchmark. Re-capture the values before recording a new presentation.

## Presentation Flow

| Time | Screen | Voiceover |
|---|---|---|
| 0:00-0:15 | Title plus the synthetic-data disclaimer. | "This is a commercial-pharma knowledge-engineering demo. Every product, indication, HCP profile, and document is fictional." |
| 0:15-0:35 | `data/pharma_commercial/` with policy, label, claim, HCP profile, campaign, and two content revisions. | "The corpus mirrors the governance artifacts behind a commercial interaction, without using patient or real product data." |
| 0:35-0:55 | `pharma_commercial.yml`: types and `TREATS`, `SPECIALIZES_IN`, `APPROVED_FOR`. | "The ontology separates product, indication, specialty, content, market, and policy concepts, then constrains the valid relationships between them." |
| 0:55-1:15 | Terminal output from `demo_pharma_commercial.py`, showing valid and invalid triples. | "A product may be linked to an indication. A person cannot treat an indication in this model, so the schema rejects that extraction." |
| 1:15-1:30 | RDF export and actual `--validate` output after it has run. | "The graph is exportable as RDF/OWL, and SHACL independently checks structural integrity." |
| 1:30-1:55 | Enter the question in the normal query UI or run `--live-retrieval`. Show the returned five citations and capture-specific latency. | "The normal retrieval pipeline combines lexical, vector, and graph evidence to locate the product, market, specialty, policy, and controlled content." |
| 1:55-2:20 | Side-by-side policy result: expired v1 is denied; approved v2 is allowed; evidence-free content is escalated. | "The decision is deterministic. The old version is expired. The current version is scoped to Germany, the synthetic indication, and Cardiology. Missing evidence is not silently accepted." |
| 2:20-2:35 | Final cited answer and ontology diagram. | "The outcome is a concise, cited content recommendation backed by a formal semantic model, not an unsupported model assertion." |

Only show counts, IDs, answers, and timings captured from commands actually run
against the local environment. Do not manufacture live-retrieval output for a
recording.
