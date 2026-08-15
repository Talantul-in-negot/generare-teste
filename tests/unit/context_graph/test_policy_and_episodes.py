import hashlib
from unittest.mock import AsyncMock, MagicMock

from graphrag.context_graph.models import (
    CGEpisode, ConditionOperator, EpisodeRole, PolicyCondition, PolicyResult,
    PolicyRule, PolicyVersion,
)
from graphrag.context_graph.policy_engine import evaluate_policy
from graphrag.context_graph.repository import ContextGraphRepository


def _policy() -> PolicyVersion:
    return PolicyVersion(
        id="policy-v1", tenant="marketing", policy_id="retrieval", version="1",
        rules=[
            PolicyRule(
                id="escalate-conflict", priority=10,
                conditions=[PolicyCondition(
                    field="conflicts", operator=ConditionOperator.GT, value=0,
                )], result=PolicyResult.ESCALATE, reason_code="conflict",
            ),
            PolicyRule(
                id="allow-evidence", priority=20,
                conditions=[PolicyCondition(
                    field="evidence", operator=ConditionOperator.GT, value=0,
                )], result=PolicyResult.ALLOW, reason_code="grounded",
            ),
        ],
        default_result=PolicyResult.DENY,
    )


def test_policy_engine_uses_priority_and_structured_reason():
    result = evaluate_policy(
        _policy(), {"conflicts": 1, "evidence": 3},
        decision_id="decision-1", evaluation_id="evaluation-1",
    )
    assert result.result == PolicyResult.ESCALATE
    assert result.matched_rule == "escalate-conflict"
    assert result.reason_code == "conflict"


def test_policy_engine_uses_declared_default():
    result = evaluate_policy(
        _policy(), {"conflicts": 0, "evidence": 0},
        decision_id="decision-1", evaluation_id="evaluation-1",
    )
    assert result.result == PolicyResult.DENY
    assert result.matched_rule == "default"


async def test_episode_persistence_and_session_load_are_tenant_scoped():
    neo4j = MagicMock()
    neo4j.run = AsyncMock(side_effect=[
        [{"id": "episode-1"}],
        [{"episode": {"id": "episode-2"}}, {"episode": {"id": "episode-1"}}],
    ])
    repo = ContextGraphRepository(neo4j)
    content = "What changed?"
    episode = CGEpisode(
        id="episode-1", tenant="marketing", run_id="run-1", session_id="session-1",
        sequence=0, role=EpisodeRole.USER, episode_type="query", content=content,
        content_digest=hashlib.sha256(content.encode()).hexdigest(),
    )

    assert await repo.record_episode(episode) == "episode-1"
    loaded = await repo.load_session_episodes("session-1", "marketing", 5)

    assert [item["id"] for item in loaded] == ["episode-1", "episode-2"]
    assert neo4j.run.await_args.kwargs["tenant"] == "marketing"
    assert "CGEpisode" in neo4j.run.await_args.args[0]
