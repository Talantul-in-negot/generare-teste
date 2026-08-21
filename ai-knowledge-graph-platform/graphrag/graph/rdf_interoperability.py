"""Read-only RDF/SKOS import-readiness assessment.

External RDF is intentionally inspected before it is linked or imported.  This
keeps ontology interoperation tenant-safe: a partner vocabulary is first
catalogued, then ``CrossOntologyLinker`` can produce reviewed ``owl:sameAs``
bridges without silently creating domain entities from an unknown schema.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph
from rdflib.namespace import OWL, RDF, RDFS

SKOS_URI = "http://www.w3.org/2004/02/skos/core#"


def assess_rdf_interoperability(path: str | Path, rdf_format: str = "turtle") -> dict:
    """Return a deterministic, no-write catalogue of an RDF/SKOS source."""
    from rdflib import Namespace

    skos = Namespace(SKOS_URI)
    graph = Graph()
    graph.parse(str(path), format=rdf_format)

    labelled = set(graph.subjects(skos.prefLabel, None))
    labelled.update(graph.subjects(skos.altLabel, None))
    labelled.update(graph.subjects(RDFS.label, None))
    concepts = set(graph.subjects(RDF.type, skos.Concept))
    concepts.update(graph.subjects(RDF.type, OWL.Class))
    concepts.update(labelled)

    concept_uris = {str(subject) for subject in concepts}
    pref_labelled = {str(subject) for subject in graph.subjects(skos.prefLabel, None)}
    alt_labelled = {str(subject) for subject in graph.subjects(skos.altLabel, None)}
    hierarchy_edges = [
        (str(child), str(parent))
        for child, parent in graph.subject_objects(skos.broader)
        if str(child) in concept_uris and str(parent) in concept_uris
    ]
    relation_terms = set(graph.subjects(RDF.type, OWL.ObjectProperty))
    relation_terms.update(graph.subjects(RDF.type, RDF.Property))

    return {
        "source": str(path),
        "format": rdf_format,
        "triples": len(graph),
        "concept_schemes": len(set(graph.subjects(RDF.type, skos.ConceptScheme))),
        "concepts": len(concepts),
        "concepts_with_pref_label": len(pref_labelled),
        "concepts_with_alt_label": len(alt_labelled),
        "concepts_without_any_label": len(concepts - labelled),
        "broader_edges": len(hierarchy_edges),
        "relation_terms": len(relation_terms),
        "recommended_next_step": (
            "link with CrossOntologyLinker; require review for ambiguous matches"
            if concepts else "source has no labelled concepts to link"
        ),
    }
