"""Public query-message contract for bitemporal retrieval."""

from graphrag.core.models import QueryMessage
from graphrag.retrieval.query_cache import QueryCacheContext, build_cache_key


def test_query_message_serializes_temporal_boundaries() -> None:
    message = QueryMessage(
        question="What was valid then?",
        tenant="marketing",
        valid_at="2025-01-01T00:00:00+00:00",
        transaction_at="2025-02-01T00:00:00+00:00",
    )

    payload = message.model_dump(mode="json")
    assert payload["valid_at"] == "2025-01-01T00:00:00+00:00"
    assert payload["transaction_at"] == "2025-02-01T00:00:00+00:00"


def test_temporal_boundaries_are_part_of_answer_cache_identity() -> None:
    base = dict(
        corpus_revision=1,
        requested_mode="local",
        effective_mode="local",
        model_route={"primary": "test"},
        prompt_version="v1",
        retrieval_config={},
        ontology_version="v1",
    )
    current = QueryCacheContext(**base)
    historical = QueryCacheContext(**base, valid_at="2025-01-01T00:00:00+00:00")

    assert build_cache_key("What applied?", "marketing", current) != build_cache_key(
        "What applied?", "marketing", historical
    )
