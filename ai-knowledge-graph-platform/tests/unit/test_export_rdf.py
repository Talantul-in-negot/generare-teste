"""Unit tests for scripts/export_rdf.py — valid Turtle output via rdflib."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Allow importing from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from rdflib import Graph, Namespace, OWL, RDF, RDFS
from rdflib.namespace import XSD

BASE  = Namespace("https://graphrag.example.com/ontology#")
INST  = Namespace("https://graphrag.example.com/entity/")
ANNOT = Namespace("https://graphrag.example.com/annotation#")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_graph_with(
    entities: list[dict] | None = None,
    edges: list[dict] | None = None,
    type_rows: list[dict] | None = None,
) -> Graph:
    """Build an rdflib Graph using export_rdf helpers, with mocked Neo4j data."""
    from export_rdf import _init_graph, _entity_uri, _type_uri, _rel_uri, _axiom_uri
    from rdflib import Literal

    g = _init_graph()

    for row in (type_rows or []):
        child, parent = row["child"], row["parent"]
        g.add((_type_uri(child), RDF.type, OWL.Class))
        g.add((_type_uri(parent), RDF.type, OWL.Class))
        g.add((_type_uri(child), RDFS.subClassOf, _type_uri(parent)))

    for row in (entities or []):
        uri = _entity_uri(row["name"], row["type"], row.get("tenant", "default"))
        g.add((uri, RDF.type, OWL.NamedIndividual))
        g.add((uri, RDF.type, _type_uri(row["type"])))
        g.add((uri, RDFS.label, Literal(row["name"])))

    for row in (edges or []):
        s_uri = _entity_uri(row["sname"], row["stype"], row.get("tenant", "default"))
        o_uri = _entity_uri(row["tname"], row["ttype"], row.get("tenant", "default"))
        p_uri = _rel_uri(row["rel"])
        g.add((s_uri, p_uri, o_uri))
        if conf := row.get("conf"):
            ax = _axiom_uri(row["sname"], row["rel"], row["tname"])
            g.add((ax, RDF.type, OWL.Axiom))
            g.add((ax, OWL.annotatedSource, s_uri))
            g.add((ax, OWL.annotatedProperty, p_uri))
            g.add((ax, OWL.annotatedTarget, o_uri))
            g.add((ax, ANNOT.confidence, Literal(round(float(conf), 4), datatype=XSD.float)))
    return g


# ── Graph structure ───────────────────────────────────────────────────────────

class TestGraphStructure:
    def test_ontology_declaration_present(self):
        g = _build_graph_with()
        ont = list(g.subjects(RDF.type, OWL.Ontology))
        assert len(ont) >= 1

    def test_annotation_properties_declared(self):
        g = _build_graph_with()
        annot_props = list(g.subjects(RDF.type, OWL.AnnotationProperty))
        assert len(annot_props) >= 4   # confidence, validFrom, validTo, sourceDoc, tenant

    def test_entity_is_named_individual(self):
        g = _build_graph_with(entities=[
            {"name": "SpaceX", "type": "ORG", "tenant": "default"}
        ])
        from export_rdf import _entity_uri
        uri = _entity_uri("SpaceX", "ORG", "default")
        types = set(g.objects(uri, RDF.type))
        assert OWL.NamedIndividual in types

    def test_entity_has_rdfs_label(self):
        from export_rdf import _entity_uri
        from rdflib import Literal
        g = _build_graph_with(entities=[
            {"name": "NASA", "type": "ORG", "tenant": "default"}
        ])
        uri = _entity_uri("NASA", "ORG", "default")
        labels = list(g.objects(uri, RDFS.label))
        assert Literal("NASA") in labels

    def test_type_hierarchy_subclass_of(self):
        from export_rdf import _type_uri
        g = _build_graph_with(type_rows=[
            {"child": "AIRWORTHINESS_DIRECTIVE", "parent": "REGULATION"}
        ])
        ad_uri  = _type_uri("AIRWORTHINESS_DIRECTIVE")
        reg_uri = _type_uri("REGULATION")
        assert (ad_uri, RDFS.subClassOf, reg_uri) in g

    def test_relation_triple_present(self):
        from export_rdf import _entity_uri, _rel_uri
        g = _build_graph_with(edges=[{
            "sname": "AD-2024", "stype": "CONCEPT",
            "tname": "AD-2022", "ttype": "CONCEPT",
            "rel": "SUPERSEDES", "tenant": "default",
        }])
        s_uri = _entity_uri("AD-2024", "CONCEPT", "default")
        o_uri = _entity_uri("AD-2022", "CONCEPT", "default")
        p_uri = _rel_uri("SUPERSEDES")
        assert (s_uri, p_uri, o_uri) in g


# ── Reified confidence (owl:Axiom) ────────────────────────────────────────────

class TestReifiedConfidence:
    def test_axiom_node_created_for_confident_edge(self):
        g = _build_graph_with(edges=[{
            "sname": "FAA", "stype": "ORG",
            "tname": "AD-2024", "ttype": "CONCEPT",
            "rel": "MANDATES", "conf": 0.95, "tenant": "default",
        }])
        axioms = list(g.subjects(RDF.type, OWL.Axiom))
        assert len(axioms) == 1

    def test_axiom_has_confidence_annotation(self):
        from rdflib import Literal
        g = _build_graph_with(edges=[{
            "sname": "FAA", "stype": "ORG",
            "tname": "AD-2024", "ttype": "CONCEPT",
            "rel": "MANDATES", "conf": 0.95, "tenant": "default",
        }])
        axiom = list(g.subjects(RDF.type, OWL.Axiom))[0]
        conf_values = list(g.objects(axiom, ANNOT.confidence))
        assert len(conf_values) == 1
        assert abs(float(conf_values[0]) - 0.95) < 0.001

    def test_no_axiom_for_edge_without_confidence(self):
        g = _build_graph_with(edges=[{
            "sname": "A", "stype": "ORG",
            "tname": "B", "ttype": "ORG",
            "rel": "RELATED_TO", "tenant": "default",
            # no "conf" key
        }])
        axioms = list(g.subjects(RDF.type, OWL.Axiom))
        assert len(axioms) == 0


# ── Valid Turtle serialisation ────────────────────────────────────────────────

class TestTurtleSerialisation:
    def test_round_trips_through_turtle(self, tmp_path):
        """Write to Turtle, re-parse: triple count must be preserved."""
        from export_rdf import _entity_uri, _rel_uri
        g_out = _build_graph_with(
            entities=[
                {"name": "SpaceX", "type": "ORG", "tenant": "t1"},
                {"name": "Falcon 9", "type": "PRODUCT", "tenant": "t1"},
            ],
            edges=[{
                "sname": "SpaceX", "stype": "ORG",
                "tname": "Falcon 9", "ttype": "PRODUCT",
                "rel": "MANUFACTURES", "conf": 0.99, "tenant": "t1",
            }],
        )
        ttl_path = tmp_path / "test.ttl"
        g_out.serialize(destination=str(ttl_path), format="turtle")

        # Re-parse and verify
        g_in = Graph()
        g_in.parse(str(ttl_path), format="turtle")
        assert len(g_in) == len(g_out)

    def test_unicode_entity_names_safe(self, tmp_path):
        """Entity names with non-ASCII chars must not break Turtle output."""
        g = _build_graph_with(entities=[
            {"name": "Société Générale", "type": "ORG", "tenant": "t1"}
        ])
        ttl_path = tmp_path / "unicode.ttl"
        # Should not raise
        g.serialize(destination=str(ttl_path), format="turtle")
        assert ttl_path.exists()

    def test_special_chars_in_names_safe(self, tmp_path):
        """Entity names with quotes/newlines must not break Turtle output."""
        g = _build_graph_with(entities=[
            {"name": 'He said "hello"', "type": "PERSON", "tenant": "t1"}
        ])
        ttl_path = tmp_path / "special.ttl"
        g.serialize(destination=str(ttl_path), format="turtle")
        assert ttl_path.exists()


# ── Aware datetimes ────────────────────────────────────────────────────────────

class TestAwareDatetime:
    def test_init_graph_uses_no_utcnow(self):
        """Verify the module doesn't use the deprecated utcnow."""
        import inspect
        import export_rdf
        src = inspect.getsource(export_rdf)
        assert "utcnow" not in src, "export_rdf.py must not use datetime.utcnow()"


# ── SHACL conformance, end to end ───────────────────────────────────────────────
#
# The classes above test export_rdf.py's graph-building *helpers* directly
# (structure, serialisation, datetime hygiene) — they don't exercise the real
# async export() pipeline, and none of them check SHACL conformance.
# test_shacl_validator.py separately proves the validator's *logic* is correct
# against hand-built graphs. Neither closes the actual gap: nothing asserts
# that export()'s real Neo4j-to-RDF output conforms to the platform's own
# SHACL shapes. That's the guarantee "operationalize SHACL into CI/CD to
# prevent non-compliant mutations" requires — these tests provide it, running
# in tests/unit/, which CI already executes on every push.

def _make_neo4j(
    type_rows=None, rel_rows=None, ent_rows=None, edge_rows=None, neg_rows=None,
) -> AsyncMock:
    neo4j = AsyncMock()
    # export() issues 5 sequential neo4j.run() calls in this fixed order:
    # type hierarchy, distinct relation names, entities, RELATES_TO edges,
    # NEGATIVE_RELATES_TO edges.
    neo4j.run.side_effect = [
        type_rows or [],
        rel_rows or [],
        ent_rows or [],
        edge_rows or [],
        neg_rows or [],
    ]
    return neo4j


class TestExportProducesConformantGraph:
    async def test_typical_entities_and_edges_conform(self, tmp_path: Path) -> None:
        """Representative export (2 entities, 1 relation, 1 type edge) must
        pass the platform's own SHACL shapes."""
        from export_rdf import export
        from graphrag.graph.shacl_validator import SHACLValidator

        neo4j = _make_neo4j(
            type_rows=[{"child": "AIRCRAFT_MODEL", "parent": "ASSET"}],
            rel_rows=[{"rel": "OPERATES"}],
            ent_rows=[
                {"name": "Boeing 737 MAX", "type": "AIRCRAFT_MODEL", "desc": None,
                 "vf": None, "vt": None, "tenant": "aerospace"},
                {"name": "Southwest Airlines", "type": "ORG", "desc": None,
                 "vf": None, "vt": None, "tenant": "aerospace"},
            ],
            edge_rows=[
                {"sname": "Southwest Airlines", "stype": "ORG",
                 "tname": "Boeing 737 MAX", "ttype": "AIRCRAFT_MODEL",
                 "rel": "OPERATES", "conf": 0.9,
                 "src_doc": "fleet-2024.pdf", "tenant": "aerospace"},
            ],
        )

        output = tmp_path / "export.ttl"
        with patch("graphrag.graph.neo4j_client.get_neo4j", return_value=neo4j):
            await export(tenant="aerospace", output=output, limit=1000)

        conforms, report = SHACLValidator.from_turtle(output).validate()
        assert conforms, report

    async def test_negative_relation_axiom_conforms(self, tmp_path: Path) -> None:
        """NEGATIVE_RELATES_TO edges are reified the same way as positive
        ones — must carry the same complete annotatedSource/Property/Target
        triple, or the axiom shape fails."""
        from export_rdf import export
        from graphrag.graph.shacl_validator import SHACLValidator

        neo4j = _make_neo4j(
            ent_rows=[
                {"name": "Part A", "type": "COMPONENT", "desc": None,
                 "vf": None, "vt": None, "tenant": "automotive"},
                {"name": "Part B", "type": "COMPONENT", "desc": None,
                 "vf": None, "vt": None, "tenant": "automotive"},
            ],
            neg_rows=[
                {"sname": "Part A", "stype": "COMPONENT",
                 "tname": "Part B", "ttype": "COMPONENT",
                 "rel": "COMPATIBLE_WITH", "conf": 0.8, "tenant": "automotive"},
            ],
        )

        output = tmp_path / "export_neg.ttl"
        with patch("graphrag.graph.neo4j_client.get_neo4j", return_value=neo4j):
            await export(tenant="automotive", output=output, limit=1000)

        conforms, report = SHACLValidator.from_turtle(output).validate()
        assert conforms, report

    async def test_confidence_out_of_pipeline_range_is_caught(self, tmp_path: Path) -> None:
        """Regression guard: if a future change to the ingestion pipeline
        ever let a confidence value outside [0,1] reach the graph, this
        fails here instead of silently exporting a non-conformant graph."""
        from export_rdf import export
        from graphrag.graph.shacl_validator import SHACLValidator

        neo4j = _make_neo4j(
            ent_rows=[
                {"name": "A", "type": "CONCEPT", "desc": None,
                 "vf": None, "vt": None, "tenant": "default"},
                {"name": "B", "type": "CONCEPT", "desc": None,
                 "vf": None, "vt": None, "tenant": "default"},
            ],
            edge_rows=[
                {"sname": "A", "stype": "CONCEPT", "tname": "B", "ttype": "CONCEPT",
                 "rel": "RELATED_TO", "conf": 1.4,  # invalid — out of [0,1]
                 "src_doc": "x.pdf", "tenant": "default"},
            ],
        )

        output = tmp_path / "export_bad.ttl"
        with patch("graphrag.graph.neo4j_client.get_neo4j", return_value=neo4j):
            await export(tenant="default", output=output, limit=1000)

        conforms, report = SHACLValidator.from_turtle(output).validate()
        assert not conforms
        assert "confidence" in report.lower()
