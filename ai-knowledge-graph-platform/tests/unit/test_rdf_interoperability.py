from rdflib import Graph, Literal, Namespace
from rdflib.namespace import OWL, RDF

from graphrag.graph.rdf_interoperability import SKOS_URI, assess_rdf_interoperability


def test_assessment_catalogues_skos_labels_hierarchy_and_relation_terms(tmp_path) -> None:
    example = Namespace("https://example.test/")
    skos = Namespace(SKOS_URI)
    graph = Graph()
    graph.add((example.scheme, RDF.type, skos.ConceptScheme))
    graph.add((example.launcher, RDF.type, skos.Concept))
    graph.add((example.launcher, skos.prefLabel, Literal("Launcher")))
    graph.add((example.falcon, RDF.type, skos.Concept))
    graph.add((example.falcon, skos.altLabel, Literal("Falcon Nine")))
    graph.add((example.falcon, skos.broader, example.launcher))
    graph.add((example.launches, RDF.type, OWL.ObjectProperty))
    source = tmp_path / "partner.ttl"
    graph.serialize(source, format="turtle")

    report = assess_rdf_interoperability(source)

    assert report["triples"] == 7
    assert report["concept_schemes"] == 1
    assert report["concepts"] == 2
    assert report["concepts_with_pref_label"] == 1
    assert report["concepts_with_alt_label"] == 1
    assert report["concepts_without_any_label"] == 0
    assert report["broader_edges"] == 1
    assert report["relation_terms"] == 1
    assert "CrossOntologyLinker" in report["recommended_next_step"]
