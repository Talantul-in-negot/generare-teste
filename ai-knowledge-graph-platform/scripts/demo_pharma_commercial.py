"""Run the synthetic commercial-pharma Knowledge Graph demonstration.

The default mode is deterministic and needs no infrastructure. It demonstrates
the domain ontology, domain/range rejection, SHACL conformance, and content
approval policy. ``--live-retrieval`` additionally queries the ingested
synthetic corpus through the normal HybridRetriever without assigning a query
ID, so it creates no Context Graph trace.

This is a knowledge-engineering demo only. All products, indications, content,
and HCP profiles are fictional. It is not clinical decision support.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ONTOLOGY_PATH = REPO_ROOT / "config" / "ontologies" / "pharma_commercial.yml"
TENANT = "pharma"
QUESTION = (
    "Which approved synthetic content can be used for a cardiology specialist "
    "in Germany for CardioDemo and Demo Cardiac Condition?"
)


def _content(status: str, *, evidence: bool = True):
    from graphrag.graph.pharma_commercial import CommercialContent, ContentStatus

    document_id = (
        f"SYNTHETIC-CONTENT-CardioDemo-DE-{status}-v"
        f"{2 if status == 'approved' else 1}"
    )
    return CommercialContent(
        id=document_id.lower(),
        document_id=document_id,
        title="CardioDemo Germany Cardiology Detail Aid",
        tenant=TENANT,
        product="CardioDemo",
        indication="Demo Cardiac Condition",
        market="Germany",
        hcp_specialties=["Cardiology"],
        status=ContentStatus(status),
        valid_from=date(2026, 1, 1) if status == "approved" else date(2025, 1, 1),
        valid_to=None if status == "approved" else date(2025, 12, 31),
        evidence_document_ids=[document_id] if evidence else [],
    )


def _heading(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


async def run_deterministic_demo() -> None:
    from rdflib import Graph, Literal, Namespace
    from rdflib.namespace import OWL, RDF, RDFS

    from graphrag.graph.domain_ontology import (
        get_relation_rules,
        get_type_hierarchy_pairs,
        load_domain_ontology,
        validate_ontology_yaml,
    )
    from graphrag.graph.ontology_registry import OntologyRegistry
    from graphrag.graph.pharma_commercial import (
        ContentApprovalRequest,
        evaluate_content_approval,
    )
    from graphrag.graph.shacl_validator import SHACLValidator

    print("Synthetic commercial-pharma Knowledge Graph demo")
    print("No real products, medical claims, HCPs, or patient data are used.")

    _heading("1. Domain ontology")
    report = validate_ontology_yaml(ONTOLOGY_PATH)
    ontology = load_domain_ontology(ONTOLOGY_PATH)
    pairs = get_type_hierarchy_pairs(ontology)
    relation_rules = get_relation_rules(ontology)
    print(f"Ontology: {ontology['ontology']['id']} v{ontology['ontology']['version']}")
    print(f"Lifecycle validation: {report['valid']}")
    print(f"Type hierarchy pairs: {len(pairs)}")
    print(f"Relation constraints: {len(relation_rules)}")
    print("Examples: PHARMA_PRODUCT -[TREATS]-> INDICATION")
    print("          PERSON -[SPECIALIZES_IN]-> MEDICAL_SPECIALTY")
    print("          COMMERCIAL_CONTENT -[APPROVED_FOR]-> MARKET")

    _heading("2. Schema validation")
    registry = OntologyRegistry(AsyncMock())
    registry.add_domain_range_rules(relation_rules)
    valid, relation = registry.validate_relation_triplet(
        "PHARMA_PRODUCT", "TREATS", "INDICATION"
    )
    invalid, _ = registry.validate_relation_triplet("PERSON", "TREATS", "INDICATION")
    print(f"Product treats synthetic indication: valid={valid}, relation={relation}")
    print(f"HCP treats synthetic indication: valid={invalid} (rejected by domain/range)")

    _heading("3. RDF/OWL and SHACL")
    ex = Namespace("https://graphrag.example.com/demo/pharma#")
    rdf_graph = Graph()
    rdf_graph.add((ex.approved_content, RDF.type, OWL.NamedIndividual))
    rdf_graph.add((ex.approved_content, RDF.type, ex.COMMERCIAL_CONTENT))
    rdf_graph.add((ex.approved_content, RDFS.label, Literal("Synthetic approved content v2")))
    conforms, _ = SHACLValidator(rdf_graph).validate()
    print(f"Representative RDF content node conforms to SHACL: {conforms}")
    print("For an ingested live corpus: python scripts/export_rdf.py --tenant pharma --validate")

    _heading("4. Content policy decision")
    request = ContentApprovalRequest(
        tenant=TENANT,
        product="CardioDemo",
        indication="Demo Cardiac Condition",
        market="Germany",
        hcp_specialty="Cardiology",
        as_of=date(2026, 8, 3),
    )
    allowed = evaluate_content_approval(request, _content("approved"))
    expired = evaluate_content_approval(request, _content("expired"))
    missing_evidence = evaluate_content_approval(request, _content("approved", evidence=False))
    print(f"Selected: {allowed.decision.value} ({allowed.reason_code})")
    print(f"Rejected: {expired.decision.value} ({expired.reason_code})")
    print(f"Missing evidence: {missing_evidence.decision.value} ({missing_evidence.reason_code})")
    print(f"Citations: {', '.join(allowed.cited_document_ids)}")
    print("Answer: Recommend SYNTHETIC-CONTENT-CardioDemo-DE-approved-v2; it is current")
    print("and approved for the synthetic product, indication, Germany, and Cardiology.")


async def run_live_retrieval() -> None:
    """Run the normal retrieval stack; no query_id means no persisted CG trace."""
    from graphrag.graph.neo4j_client import get_neo4j
    from graphrag.retrieval.hybrid_retriever import HybridRetriever

    _heading("5. Live hybrid retrieval")
    neo4j = get_neo4j()
    try:
        rows = await neo4j.run(
            "MATCH (d:Document {tenant: $tenant}) RETURN count(d) AS count",
            tenant=TENANT,
        )
        count = int(rows[0]["count"]) if rows else 0
        if not count:
            print("No pharma corpus is loaded. Run:")
            print("  python scripts/ingest_corpus.py --tenant pharma --commit")
            return
        print(f"Live corpus documents: {count}")
        result = await HybridRetriever().retrieve_and_answer(
            QUESTION, tenant=TENANT, mode="hybrid"
        )
        print(f"Question: {QUESTION}")
        print(f"Answer: {result.answer}")
        print(f"Citations: {', '.join(result.citations) if result.citations else '(none)'}")
        print(f"Retrieval mode: {result.retrieval_mode}; latency_ms={result.latency_ms:.1f}")
        print("Context Graph trace: none (direct retrieval was invoked without query_id).")
    finally:
        await neo4j.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-retrieval",
        action="store_true",
        help="Query the ingested synthetic corpus through HybridRetriever after the deterministic demo.",
    )
    args = parser.parse_args()
    await run_deterministic_demo()
    if args.live_retrieval:
        await run_live_retrieval()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
