# Graph technology roadmap

The platform remains Neo4j-first. These additions strengthen evaluation and
interoperability without introducing a second production graph database or a
dual-write path.

| Area | Current decision | Safe next action |
| --- | --- | --- |
| Graph database / Neo4j | Production graph store | Keep Neo4j as the system of record. |
| Knowledge Graph | Implemented: tenant-scoped entities, relations, provenance, ontology links | Extend only through governed ingestion. |
| Graph analytics | Neo4j GDS PageRank is implemented | Assess the in-memory, read-only workload before adopting further GDS algorithms. |
| Graph RAG | Hybrid vector, lexical, and relationship retrieval is implemented | Continue measuring retrieval quality with grounded evaluation sets. |
| Context Graph | Persisted decision, action, outcome, feedback, and retention trace | Monitor per-tenant coverage before expanding automation. |
| RDF / SKOS | RDF export and SKOS vocabularies are implemented | Catalogue each external source, then link reviewed equivalents only. |
| GQL | Not a production runtime dependency | Keep a bounded read-query contract for future conformance tests. |
| Ultipa | Not integrated | Evaluate only against a same-dataset, read-only benchmark gate. |

## Capability checks

```powershell
python scripts/evaluate_gds.py --tenant default --top-k 10
python scripts/evaluate_context_graph_operations.py --tenant default
python scripts/evaluate_context_graph_operations.py --tenant default --retention-before 2026-01-01T00:00:00Z
python scripts/assess_rdf_interoperability.py --external .\partner.ttl --format turtle
python scripts/link_external_ontology.py --help
python scripts/compare_graph_backends.py --baseline .\neo4j-result.json --candidate .\ultipa-result.json
```

`evaluate_gds.py` creates and drops only a tenant-scoped in-memory GDS
projection; it does not persist PageRank properties. The existing
`pagerank_compute.py` command is the explicit maintenance command that writes
scores.

The RDF assessment performs no import. After review, use the existing
`CrossOntologyLinker` workflow to create auditable `owl:sameAs` bridges; do
not silently materialise unknown partner classes or relationships.

The GQL query contracts in `graphrag.graph.gql_portability` are a deliberately
small, parameterised, bounded read surface. They are a test and evaluation
artifact, not a claim that Neo4j is executing ISO GQL at runtime.

## Second-backend promotion gate

The candidate and Neo4j benchmark files must use the same dataset fingerprint,
scenario, and query count. A candidate is eligible for a design review only
when it has at least 99.9% result equivalence, passes tenant isolation, reduces
p95 latency by at least 20%, improves throughput by at least 25%, and costs no
more than 20% above the Neo4j baseline. A positive report is not production
approval: it is evidence to consider a limited, read-only pilot.
