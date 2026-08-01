# Technical Glossary — Knowledge Graph Developer JD ↔ This Project

Reference for interview prep: JD terms defined, mapped to whether/how this
project demonstrates them. Use alongside
[talking-points-kg-infra-frontend-role.md](talking-points-kg-infra-frontend-role.md).

## Metaphactory & platform terms

**Metaphactory** — Commercial knowledge-graph application platform
(metaphacts GmbH) built on RDF4J. Provides low-code UI components (search
views, dashboards, forms) that read/write directly against a SPARQL
endpoint, so business users get a web app without hand-written frontend
code per feature. *Not used in this project — Neo4j + hand-built React/JS
UI plays the equivalent role, but as custom code, not a configured product.*

**Application-building capability** — JD phrase for a KG platform's ability
to let developers assemble UI screens (search, forms, graph views) declar-
atively rather than writing bespoke frontend code. *This project takes the
opposite path: bespoke HTML5/CSS/vanilla-JS UI (chat panel + Knowledge
Graph tab with an embedded interactive graph view) — same end-user outcome,
different build model.*

## Graph database & query terms

**Neo4j** — Property-graph database (nodes + typed relationships +
key/value properties) with the Cypher query language. *Primary datastore
in this project — Entity nodes, RELATES_TO/NEGATIVE_RELATES_TO edges,
EntityType hierarchy, GDS PageRank, Leiden community detection.*

**Amazon Neptune** — AWS-managed graph database supporting both property
graphs (via openCypher/Gremlin) and RDF triple stores (via SPARQL).
*Not used directly, but Neptune's openCypher dialect is compatible with
the Cypher used here — queries are portable in principle, though AWS
deployment/ops (VPC, IAM, instance sizing) has not been exercised.*

**openCypher** — The open, vendor-neutral specification of the Cypher
query language, implemented by both Neo4j and Neptune. *All graph queries
in this project (`neo4j_client.py`) are written in standard Cypher and
would run against Neptune's openCypher endpoint largely unchanged.*

**SPARQL** — W3C standard query language for RDF triple stores (the RDF
equivalent of SQL). *Implemented in `graphrag/graph/sparql_bridge.py`,
queried against the exported RDF graph.*

## Semantic web / ontology terms

**RDF (Resource Description Framework)** — W3C data model representing
facts as subject–predicate–object triples (e.g. `:Doc1 :hasAuthor :Alice`).
The universal interchange format for linked data. *Exported by
`scripts/export_rdf.py`, producing 49,000+ triples from the Neo4j graph
(entities, types, reified relations with confidence).*

**OWL (Web Ontology Language)** — RDF-based language for defining classes,
subclass hierarchies, and logical axioms, enabling automated inference
(entailment). *Entity types are exported as `owl:Class` with
`rdfs:subClassOf` hierarchies; `graphrag/graph/owl_reasoner.py` applies
OWL-RL closure (inferred triples + consistency checking) via `owlrl`.*

**SHACL (Shapes Constraint Language)** — W3C standard for validating RDF
graphs against structural "shapes" (required properties, cardinality,
datatypes, value ranges) — the RDF equivalent of a JSON Schema or DB
constraint layer. *Implemented in `graphrag/graph/shacl_validator.py`
using `pyshacl`: validates every entity has a label + domain type, every
reified relation (owl:Axiom) has source/property/target, and confidence
values fall in `[0.0, 1.0]` as `xsd:float`.*

**Ontology** — A formal, machine-readable model of a domain's concepts and
relationships (classes, properties, constraints). *The EntityType hierarchy
+ OWL export + SHACL shapes together form this project's ontology layer.*

**Triple / Triple store** — A single RDF fact (subject-predicate-object);
a database purpose-built to store and query large collections of them.
*rdflib's in-memory `Graph` object serves this role here; Neptune/Fuseki/
Oxigraph would be production triple-store options.*

**Reification** — Representing a statement *about* a triple (e.g. its
confidence or provenance) by modeling the triple itself as a resource
(`owl:Axiom` with `annotatedSource/Property/Target`). *Used throughout
`export_rdf.py` to attach confidence scores and source-document provenance
to each relation without polluting the base RDF model.*

## Graph algorithm terms

**PageRank** — Centrality algorithm scoring nodes by the structure of
incoming links (a node is important if important nodes point to it).
*Run natively via Neo4j GDS (`gds.pageRank.stream`) in
`scripts/pagerank_compute.py`, persisted onto Entity nodes.*

**Community detection (Leiden)** — Graph-clustering algorithm that
partitions a graph into densely-connected subgroups ("communities"),
improving on the older Louvain method by guaranteeing well-connected
output clusters. *Implemented in `graphrag/graph/community_builder.py`,
visualized as color-coded clusters in the Knowledge Graph tab.*

**Multi-hop traversal** — Querying across more than one relationship edge
to answer a question (e.g. A→B→C), as opposed to single-edge lookups.
*Stage 5 of the project's 6-stage retrieval pipeline.*

## Frontend / infrastructure terms

**HTML5 / CSS / vanilla JavaScript** — Browser-native markup, styling, and
scripting without a UI framework (React, Vue, etc.). *The demo UI's
Knowledge Graph tab and chat panel are hand-built this way — no framework
dependency, direct DOM manipulation and `fetch()` calls.*

**CI/CD (Continuous Integration/Deployment)** — Automated pipelines that
build, test, and deploy code on every change. *Docker-based build +
deploy flow used across this project's services.*

**Docker** — Container runtime packaging an app with its dependencies for
consistent deployment across environments. *Used for Neo4j, the API
service, and worker processes (`docker-compose.yml`).*

## Workflow — ingestion to query, short form

```
Document -> Entity/Relation extraction -> Neo4j (property graph)
                                                |
                    +---------------------------+---------------------------+
                    v                                                       v
        PageRank + Leiden (GDS)                                  export_rdf.py -> RDF/OWL triples
                    |                                                       |
                    v                                                       v
        Entity.pagerank / community_id                          SHACL validate -> SPARQL query
                    |                                                       |
                    +---------------------------+---------------------------+
                                                v
                    Retrieval: ANN -> BM25 -> RRF -> Cross-Encoder -> Multi-Hop -> GAT GNN
                                                |
                                                v
                                    LLM answer + Knowledge Graph UI tab
```

**Example (automotive tenant):**

```
IATF 16949 audit report PDF
  -> Entity extraction: "AutoCorp GmbH" (SUPPLIER), "CSR-2023-04" (DOCUMENT)
  -> Neo4j: (:Entity {name:"AutoCorp GmbH"})-[:RELATES_TO {relation:"AUDITED_BY", confidence:0.91}]->(:Entity {name:"CSR-2023-04"})
       |                                                                    |
       v                                                                    v
  PageRank -> AutoCorp GmbH scores high (many inbound audit refs)   export_rdf.py -> owl:NamedIndividual + owl:Axiom (reified confidence 0.91)
       |                                                                    |
       v                                                                    v
  Leiden -> grouped into "Supplier Compliance" community            SHACL validate -> conforms (label + type + axiom complete)
                                                                             |
                                                                             v
                                                             SPARQL: SELECT ?doc WHERE { :AutoCorp_GmbH :AUDITED_BY ?doc }

Query: "Which suppliers were flagged in 2023 audits?"
  -> ANN + BM25 retrieve CSR-2023-04 chunk -> RRF fusion -> Cross-Encoder rerank
  -> Multi-Hop: AutoCorp GmbH -> AUDITED_BY -> CSR-2023-04 -> FLAGGED_ISSUE -> "late corrective action"
  -> GAT GNN re-scores using PageRank/community signal -> answer cites AutoCorp GmbH, confidence 0.91
  -> Same entity + edge visible as a node in the Knowledge Graph UI tab, colored by its Leiden community
```

## RDF/OWL/SHACL — real output

Live output of `python -m scripts.export_rdf --tenant automotive --limit 5
--validate` (automotive tenant, 5 entities/5 edges → 84 triples):

**1. RDF entity (`owl:NamedIndividual`) with OWL class + label:**

```turtle
<https://graphrag.example.com/entity/automotive/CONCEPT/SPEC-PROD-01>
    a owl:NamedIndividual, base:CONCEPT ;
    rdfs:label "SPEC-PROD-01" ;
    rdfs:comment "Codul procedurii de sistem pentru gestionarea furnizorilor." ;
    annot:tenant "automotive" ;
    base:COMPLIES_WITH <https://graphrag.example.com/entity/automotive/CONCEPT/ISO%2FIATF> ;
    base:IS_CODE_OF <https://graphrag.example.com/entity/automotive/CONCEPT/Procedura%20de%20Sistem%20de%20Calitate...> .
```

**2. OWL reification (`owl:Axiom`) — the same `COMPLIES_WITH` edge with
confidence and provenance attached, since a plain triple can't carry
metadata about itself:**

```turtle
<https://graphrag.example.com/entity/axiom/69f372eb99e6>
    a owl:Axiom ;
    owl:annotatedSource   <.../CONCEPT/SPEC-PROD-01> ;
    owl:annotatedProperty base:COMPLIES_WITH ;
    owl:annotatedTarget   <.../CONCEPT/ISO%2FIATF> ;
    annot:confidence "0.9"^^xsd:float ;
    annot:sourceDoc "3ebaa620-1bff-4634-9554-f00be60b33e6" .
```

**3. SHACL validation of the above graph:**

```
2026-07-15 11:44:47  shacl_validator.validated  conforms=True triples=84
✅  SHACL validation: conforms
```

Every entity had a label + domain type (`base:CONCEPT`), every axiom had
source/property/target, and `0.9`/`0.95`/`1.0` all fell inside the
required `[0.0, 1.0]` `xsd:float` range — so the shapes in
`shacl_validator.py` passed without a single violation on real data.

**4. Equivalent SPARQL query against this graph:**

```sparql
PREFIX base: <https://graphrag.example.com/ontology#>
SELECT ?target ?confidence WHERE {
  ?axiom a owl:Axiom ;
         owl:annotatedSource <.../CONCEPT/SPEC-PROD-01> ;
         owl:annotatedProperty base:COMPLIES_WITH ;
         owl:annotatedTarget ?target ;
         annot:confidence ?confidence .
}
# -> ?target = ISO/IATF, ?confidence = 0.9
```
