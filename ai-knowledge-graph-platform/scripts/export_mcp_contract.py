"""Export the versioned MCP capability contract for reuse by other clients.

The committed contract fixture remains the compatibility gate. This exporter
creates a consumable copy for another repository, SDK, documentation site, or
package release without scraping implementation details.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server.capabilities import build_registry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "contract_schema_version": "mcp-capability-contract/v1",
        "capabilities": build_registry().contract_snapshot(),
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
