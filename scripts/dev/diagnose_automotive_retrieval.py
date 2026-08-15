"""Print ranked local-search chunks for one automotive golden question."""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


async def main() -> None:
    from scripts.run_golden_eval import _check
    from graphrag.retrieval.hybrid_retriever import HybridRetriever
    from graphrag.graph.neo4j_client import get_neo4j

    golden = json.loads(
        (ROOT / "data" / "eval_golden" / "queries_automotive.json").read_text(
            encoding="utf-8"
        )
    )
    question = next(q for q in golden["questions"] if q["id"] == "CON-02")
    neo4j = get_neo4j()
    chunk_rows = await neo4j.run(
        """
        MATCH (c:Chunk)-[:PART_OF]->(d:Document {tenant: $tenant})
        RETURN c.id AS chunk_id, d.filename AS filename
        """,
        tenant="automotive",
    )
    chunk_slugs = {
        row["chunk_id"]: Path(row["filename"]).stem.lower() for row in chunk_rows
    }

    result = await HybridRetriever().retrieve_and_answer(
        question["question"], tenant="automotive"
    )
    citations = [chunk_slugs.get(c, c) for c in result.citations]
    passed, failures = _check(
        question, {"answer": result.answer, "citations": citations}
    )
    print(f"passed={passed}")
    print(f"answer={result.answer}")
    print(f"citations={citations}")
    print(f"failures={failures}")

    await neo4j.close()


if __name__ == "__main__":
    asyncio.run(main())
