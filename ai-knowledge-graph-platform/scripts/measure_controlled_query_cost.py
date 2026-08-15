"""Record the actual model-token cost of the local controlled-query workflow.

``query_graph_facts`` uses a fixed Cypher template and performs no model call;
this report therefore records zero observed model tokens and zero model cost.
It does not estimate infrastructure cost or savings.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--load-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load = json.loads(args.load_report.read_text(encoding="utf-8"))
    report = {
        "report_schema_version": "controlled-query-cost/v1",
        "operation": load.get("operation"),
        "request_count": load.get("total"),
        "observed_model_input_tokens": 0,
        "observed_model_output_tokens": 0,
        "observed_model_cost_usd": 0.0,
        "reason": "Fixed parameterized Cypher graph-fact retrieval does not call an embedding or language model.",
        "claim_policy": "This is model-cost attribution for one local workflow only; it is not infrastructure cost or savings.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
