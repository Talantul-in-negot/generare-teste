"""Operational metrics and the alert rules that consume them.

The value of these metrics is that they make *silent* failures visible — a
dead consumer, a discarded message, a store that quietly stopped being shared.
So the tests here check two things that are easy to get wrong and impossible to
notice in production:

1. the metric is actually recorded on the path that matters;
2. every metric an alert rule references actually exists under that name. A
   rule referencing a typo'd metric never fires, and a rule that never fires
   looks exactly like a system that is healthy.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from graphrag.observability import genai_telemetry as gt
from graphrag.observability import agent_telemetry as at
from graphrag.observability import operational_metrics as om

ROOT = Path(__file__).resolve().parents[2]
ALERTS = ROOT / "monitoring" / "prometheus" / "alerts.yml"

prometheus_client = pytest.importorskip("prometheus_client")


def _sample(name: str, **labels) -> float:
    """Read one sample from the default registry, or 0.0 if absent."""
    from prometheus_client import REGISTRY

    value = REGISTRY.get_sample_value(name, labels or None)
    return float(value) if value is not None else 0.0


class TestBrokerMetrics:
    def test_publish_success_and_failure_are_both_counted(self):
        before_ok = _sample("graphrag_broker_publish_total", exchange="t", outcome="success")
        with om.record_publish("t"):
            pass
        assert _sample("graphrag_broker_publish_total", exchange="t", outcome="success") == before_ok + 1

        before_fail = _sample("graphrag_broker_publish_total", exchange="t", outcome="failure")
        with pytest.raises(RuntimeError):
            with om.record_publish("t"):
                raise RuntimeError("broker down")
        # The failure path is the whole point: a publish that fails silently
        # means the API returned 200 for work that never enqueued.
        assert _sample("graphrag_broker_publish_total", exchange="t", outcome="failure") == before_fail + 1

    def test_publish_failure_does_not_pollute_the_latency_histogram(self):
        # A failed publish often fails fast; counting it as a fast publish
        # would make the latency distribution look better during an outage.
        before = _sample("graphrag_broker_publish_duration_seconds_count", exchange="lat")
        with pytest.raises(RuntimeError):
            with om.record_publish("lat"):
                raise RuntimeError("nope")
        assert _sample("graphrag_broker_publish_duration_seconds_count", exchange="lat") == before

    def test_message_age_is_recorded_from_the_enqueue_stamp(self):
        import time

        before = _sample("graphrag_broker_message_age_seconds_count", queue="q")
        om.record_message_age("q", time.time() - 42)
        assert _sample("graphrag_broker_message_age_seconds_count", queue="q") == before + 1

    def test_missing_enqueue_stamp_records_nothing_rather_than_zero(self):
        # A message with no stamp is unknown-age, not zero-age. Recording zero
        # would drag the p95 down exactly when old messages appear.
        before = _sample("graphrag_broker_message_age_seconds_count", queue="unstamped")
        om.record_message_age("unstamped", None)
        assert _sample("graphrag_broker_message_age_seconds_count", queue="unstamped") == before

    def test_retry_and_dlq_are_counted_separately(self):
        om.record_retry("q", "TimeoutError")
        om.record_dlq("q", "TimeoutError")
        assert _sample("graphrag_broker_message_retries_total", queue="q", exception_type="TimeoutError") >= 1
        assert _sample("graphrag_broker_dlq_messages_total", queue="q", exception_type="TimeoutError") >= 1

    def test_unknown_exception_type_gets_a_bounded_label(self):
        om.record_dlq("q", "")
        assert _sample("graphrag_broker_dlq_messages_total", queue="q", exception_type="unknown") >= 1


class TestGraphMetrics:
    def test_query_outcomes_are_counted(self):
        before = _sample("graphrag_graph_queries_total", outcome="failure")
        with pytest.raises(ValueError):
            with om.record_graph_query():
                raise ValueError("bad cypher")
        assert _sample("graphrag_graph_queries_total", outcome="failure") == before + 1

    def test_pool_gauge_reports_occupancy_and_ceiling(self):
        om.set_graph_pool(7, 50)
        assert _sample("graphrag_graph_pool_connections_in_use") == 7
        assert _sample("graphrag_graph_pool_max_size") == 50

    def test_pool_gauge_clamps_nonsense_values(self):
        om.set_graph_pool(-5, 50)
        assert _sample("graphrag_graph_pool_connections_in_use") == 0


class TestDegradationGauge:
    def test_degraded_and_recovered_are_both_representable(self):
        om.set_store_degraded("query_cache", True)
        assert _sample("graphrag_store_degraded", store="query_cache") == 1
        om.set_store_degraded("query_cache", False)
        assert _sample("graphrag_store_degraded", store="query_cache") == 0


class TestInstrumentationNeverBreaksTheCaller:
    def test_metric_errors_are_swallowed(self, monkeypatch):
        # Telemetry raising into a request path would turn an observability
        # problem into an availability one.
        class _Exploding:
            def labels(self, **_):
                raise RuntimeError("registry exploded")

        monkeypatch.setattr(om, "_dlq_messages", _Exploding())
        om.record_dlq("q", "Boom")  # must not raise

    def test_publish_context_reraises_the_original_error(self, monkeypatch):
        class _Exploding:
            def labels(self, **_):
                raise RuntimeError("registry exploded")

        monkeypatch.setattr(om, "_publish_attempts", _Exploding())
        with pytest.raises(ValueError, match="real failure"):
            with om.record_publish("t"):
                raise ValueError("real failure")


class TestGenAiTelemetry:
    def test_provider_names_map_onto_the_convention_vocabulary(self):
        assert gt.system_for("groq") == "groq"
        assert gt.system_for("gemini") == "gcp.gemini"
        # Unknown providers pass through rather than being dropped.
        assert gt.system_for("some-new-vendor") == "some-new-vendor"

    def test_span_records_call_count_and_duration(self):
        before = _sample(
            "graphrag_gen_ai_calls_total", system="groq", operation="chat", outcome="success",
        )
        with gt.llm_call_span(provider="groq", model="m", temperature=0.0):
            pass
        assert _sample(
            "graphrag_gen_ai_calls_total", system="groq", operation="chat", outcome="success",
        ) == before + 1

    def test_failure_outcome_is_the_exception_type(self):
        with pytest.raises(TimeoutError):
            with gt.llm_call_span(provider="groq", model="m"):
                raise TimeoutError()
        assert _sample(
            "graphrag_gen_ai_calls_total", system="groq", operation="chat", outcome="TimeoutError",
        ) >= 1

    def test_reported_tokens_are_counted(self):
        before = _sample("graphrag_gen_ai_client_token_usage_total", system="groq", direction="input")
        with gt.llm_call_span(provider="groq", model="m") as response:
            response["input_tokens"] = 120
            response["output_tokens"] = 30
        assert _sample(
            "graphrag_gen_ai_client_token_usage_total", system="groq", direction="input",
        ) == before + 120

    def test_unreported_tokens_are_not_counted_as_zero(self):
        # Several providers here return a bare string. Representing that as
        # zero tokens would put an invented number into a cost-adjacent metric.
        before = _sample("graphrag_gen_ai_client_token_usage_total", system="deepseek", direction="input")
        with gt.llm_call_span(provider="deepseek", model="m"):
            pass
        assert _sample(
            "graphrag_gen_ai_client_token_usage_total", system="deepseek", direction="input",
        ) == before

    def test_evaluation_faithfulness_is_exported_and_bounded(self):
        at.record_evaluation_quality(faithfulness=1.4, source="ragas")
        assert _sample("graphrag_evaluation_faithfulness", source="ragas") == 1.0
        at.record_evaluation_quality(faithfulness=-0.2, source="reference_judge")
        assert _sample("graphrag_evaluation_faithfulness", source="reference_judge") == 0.0

    def test_prompt_content_is_never_an_attribute(self):
        # Prompts carry customer document text; a trace backend has none of the
        # retention/tenancy/erasure guarantees that data requires.
        source = (ROOT / "graphrag/observability/genai_telemetry.py").read_text(encoding="utf-8")
        assert "gen_ai.prompt" not in source
        assert "gen_ai.completion" not in source


class TestAlertRulesMatchTheMetrics:
    """Every metric an alert references must exist, or the rule never fires."""

    @staticmethod
    def _rule_document() -> dict:
        yaml = pytest.importorskip("yaml")
        return yaml.safe_load(ALERTS.read_text(encoding="utf-8"))

    def test_alert_file_is_valid_yaml_with_groups(self):
        document = self._rule_document()
        assert document["groups"]
        for group in document["groups"]:
            assert group["name"] and group["rules"]

    def test_every_referenced_graphrag_metric_is_defined_in_code(self):
        document = self._rule_document()
        referenced: set[str] = set()
        for group in document["groups"]:
            for rule in group["rules"]:
                for name in re.findall(r"\bgraphrag_[a-z0-9_]+", rule["expr"]):
                    # Histograms are queried through generated suffixes.
                    for suffix in ("_bucket", "_count", "_sum"):
                        if name.endswith(suffix):
                            name = name[: -len(suffix)]
                            break
                    referenced.add(name)

        sources = "\n".join(
            (ROOT / "graphrag" / "observability" / module).read_text(encoding="utf-8")
            for module in ("operational_metrics.py", "genai_telemetry.py",
                           "agent_telemetry.py", "cost_attribution.py", "budgets.py")
        )
        missing = sorted(name for name in referenced if f'"{name}"' not in sources)
        assert not missing, f"alert rules reference undefined metrics: {missing}"

    def test_every_alert_has_a_duration_and_an_action(self):
        document = self._rule_document()
        for group in document["groups"]:
            for rule in group["rules"]:
                name = rule["alert"]
                # A rule that fires on a single scrape trains people to ignore it.
                assert rule.get("for"), f"{name} has no `for:` duration"
                annotations = rule.get("annotations", {})
                assert annotations.get("summary"), f"{name} has no summary"
                assert annotations.get("action"), (
                    f"{name} has no action — an alert nobody can act on is a dashboard panel"
                )

    def test_severities_are_actionable_only(self):
        document = self._rule_document()
        allowed = {"page", "ticket"}
        for group in document["groups"]:
            for rule in group["rules"]:
                severity = rule["labels"]["severity"]
                assert severity in allowed, (
                    f"{rule['alert']} uses severity {severity!r}; "
                    "'warning' is where alerts go to be filtered out"
                )
