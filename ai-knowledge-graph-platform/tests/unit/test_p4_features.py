from unittest.mock import AsyncMock, MagicMock

import pytest

from graphrag.business_matrix.timescale_kpi_store import TimescaleKPIStore
from graphrag.graph.multimodal import MediaTransformation, MultiModalEntityService


def test_timescale_backend_requires_explicit_url():
    with pytest.raises(ValueError, match="TIMESCALE_DB_URL"):
        TimescaleKPIStore("")


async def test_multimodal_transformation_preserves_provenance():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(return_value=[])
    transformation = MediaTransformation(
        tenant="acme", input_attachment_id="media-1", output_artifact_id="artifact-1",
        transform_type="ocr", model_version="ocr-v1", output_digest="sha256:abc",
    )
    result = await MultiModalEntityService(neo4j).record_transformation(transformation)
    assert result == transformation.id
    assert neo4j.run.await_args.kwargs["tenant"] == "acme"
    assert neo4j.run.await_args.kwargs["transform_type"] == "ocr"
