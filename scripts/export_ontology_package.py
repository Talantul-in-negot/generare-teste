"""Package curated ontology source files and a manifest for reuse."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--domains", nargs="+", default=["aerospace_regulatory", "automotive_iatf", "marketing_adtech", "pharma_commercial", "telecom_oss"])
    args = parser.parse_args()
    sources = []
    for domain in args.domains:
        path = root / "config" / "ontologies" / f"{domain}.yml"
        if not path.is_file():
            raise SystemExit(f"ontology not found: {path}")
        sources.append(path)
    manifest = {
        "package_schema_version": "graphrag-ontology-package/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "license_note": "Review source licenses and domain governance before external publication.",
        "ontologies": [{"domain": path.stem, "path": f"ontologies/{path.name}", "sha256": _sha256(path)} for path in sources],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        for path in sources:
            archive.write(path, f"ontologies/{path.name}")
    print(json.dumps({"output": str(args.output), "ontologies": len(sources), "manifest": manifest}, indent=2))


if __name__ == "__main__":
    main()
