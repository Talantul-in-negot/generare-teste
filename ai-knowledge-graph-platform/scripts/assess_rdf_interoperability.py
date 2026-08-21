"""Catalogue an external RDF/SKOS source before any reviewed linking work."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from graphrag.graph.rdf_interoperability import assess_rdf_interoperability


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Read-only RDF/SKOS interoperability assessment."
    )
    parser.add_argument("--external", required=True, help="RDF file to inspect")
    parser.add_argument("--format", default="turtle", help="rdflib parser format")
    args = parser.parse_args()
    print(json.dumps(assess_rdf_interoperability(args.external, args.format), indent=2))
