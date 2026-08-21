from unittest.mock import AsyncMock

from graphrag.graph.gds_evaluator import GDSReadOnlyEvaluator


async def test_assess_reports_gds_version_and_streamed_scores() -> None:
    neo4j = AsyncMock()
    neo4j.run = AsyncMock(
        side_effect=[
            [{"entities": 2, "relations": 1}],
            [{"version": "2.6.0"}],
        ]
    )
    neo4j.run_pagerank = AsyncMock(
        return_value=[
            {"entity_id": "e1", "name": "SpaceX", "type": "ORG", "score": 0.73},
            {"entity_id": "e2", "name": "Falcon 9", "type": "ROCKET", "score": 0.42},
        ]
    )

    report = await GDSReadOnlyEvaluator(neo4j).assess("acme", top_k=1)

    assert report["available"] is True
    assert report["gds_version"] == "2.6.0"
    assert report["entities"] == 2
    assert report["pagerank"]["entities_scored"] == 2
    assert report["pagerank"]["top_entities"] == [
        {"id": "e1", "name": "SpaceX", "type": "ORG", "score": 0.73}
    ]
    neo4j.run_pagerank.assert_awaited_once_with(tenant="acme")


async def test_assess_returns_an_explicit_safe_result_when_gds_is_unavailable() -> None:
    neo4j = AsyncMock()
    neo4j.run = AsyncMock(
        side_effect=[
            [{"entities": 0, "relations": 0}],
            RuntimeError("Unknown procedure gds.version"),
        ]
    )

    report = await GDSReadOnlyEvaluator(neo4j).assess("acme")

    assert report["available"] is False
    assert report["pagerank"] is None
    assert "gds.version" in report["error"]
    neo4j.run_pagerank.assert_not_awaited()
