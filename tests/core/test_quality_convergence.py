"""Unit tests for quality convergence module."""

import pytest

from holmes.core.quality_convergence import (
    EvidenceMetrics,
    QualityAssessment,
    QualityConvergenceConfig,
    QualityConvergenceTracker,
    QualityLevel,
    assess_evidence_quality,
    build_refinement_prompt,
    extract_data_source_from_tool,
)


class TestEvidenceMetrics:
    """Tests for EvidenceMetrics class."""

    def test_record_successful_tool_call(self):
        metrics = EvidenceMetrics()
        metrics.record_tool_call("kubectl_get_pods", "success", "kubernetes")

        assert metrics.total_tool_calls == 1
        assert metrics.successful_tool_calls == 1
        assert metrics.error_tool_calls == 0
        assert "kubectl_get_pods" in metrics.unique_tools_used
        assert "kubernetes" in metrics.data_sources_accessed

    def test_record_error_tool_call(self):
        metrics = EvidenceMetrics()
        metrics.record_tool_call("prometheus_query", "error")

        assert metrics.total_tool_calls == 1
        assert metrics.successful_tool_calls == 0
        assert metrics.error_tool_calls == 1

    def test_record_no_data_tool_call(self):
        metrics = EvidenceMetrics()
        metrics.record_tool_call("loki_query_logs", "no_data")

        assert metrics.total_tool_calls == 1
        assert metrics.no_data_tool_calls == 1

    def test_success_rate_calculation(self):
        metrics = EvidenceMetrics()
        metrics.record_tool_call("tool1", "success")
        metrics.record_tool_call("tool2", "success")
        metrics.record_tool_call("tool3", "error")
        metrics.record_tool_call("tool4", "no_data")

        assert metrics.success_rate == 0.5  # 2/4

    def test_success_rate_empty(self):
        metrics = EvidenceMetrics()
        assert metrics.success_rate == 0.0

    def test_evidence_breadth(self):
        metrics = EvidenceMetrics()
        metrics.record_tool_call("kubectl_get_pods", "success", "kubernetes")
        metrics.record_tool_call("prometheus_query", "success", "prometheus")
        metrics.record_tool_call("kubectl_get_events", "success", "kubernetes")

        assert metrics.evidence_breadth == 2  # kubernetes and prometheus

    def test_to_dict(self):
        metrics = EvidenceMetrics()
        metrics.record_tool_call("kubectl_get_pods", "success", "kubernetes")

        result = metrics.to_dict()
        assert result["total_tool_calls"] == 1
        assert result["successful_tool_calls"] == 1
        assert "kubectl_get_pods" in result["unique_tools_used"]


class TestExtractDataSource:
    """Tests for extract_data_source_from_tool function."""

    def test_kubernetes_tools(self):
        assert extract_data_source_from_tool("kubectl_get_pods") == "kubernetes"
        assert extract_data_source_from_tool("kubectl_describe_pod") == "kubernetes"
        assert extract_data_source_from_tool("k8s_events") == "kubernetes"

    def test_prometheus_tools(self):
        assert extract_data_source_from_tool("prometheus_query") == "prometheus"
        assert extract_data_source_from_tool("prometheus_range_query") == "prometheus"

    def test_grafana_tools(self):
        assert extract_data_source_from_tool("grafana_dashboards") == "grafana"

    def test_loki_tools(self):
        assert extract_data_source_from_tool("loki_query_logs") == "loki"

    def test_generic_log_tools(self):
        assert extract_data_source_from_tool("fetch_logs") == "logs"
        assert extract_data_source_from_tool("get_application_logs") == "logs"

    def test_unknown_tool(self):
        assert extract_data_source_from_tool("unknown_tool") is None


class TestQualityAssessment:
    """Tests for assess_evidence_quality function."""

    def test_insufficient_quality_no_tools(self):
        metrics = EvidenceMetrics()
        config = QualityConvergenceConfig(enabled=True)

        assessment = assess_evidence_quality(metrics, config)

        assert assessment.level == QualityLevel.INSUFFICIENT
        assert assessment.should_continue is True
        assert assessment.confidence < 0.5

    def test_insufficient_quality_all_errors(self):
        metrics = EvidenceMetrics()
        metrics.record_tool_call("tool1", "error")
        metrics.record_tool_call("tool2", "error")
        config = QualityConvergenceConfig(enabled=True, require_successful_tool_call=True)

        assessment = assess_evidence_quality(metrics, config)

        assert assessment.level == QualityLevel.INSUFFICIENT
        assert assessment.should_continue is True
        assert len(assessment.gaps) > 0

    def test_partial_quality_low_success(self):
        metrics = EvidenceMetrics()
        metrics.record_tool_call("tool1", "success", "kubernetes")
        metrics.record_tool_call("tool2", "error")
        metrics.record_tool_call("tool3", "error")
        metrics.record_tool_call("tool4", "error")
        config = QualityConvergenceConfig(enabled=True, min_success_rate=0.5)

        assessment = assess_evidence_quality(metrics, config)

        assert assessment.level == QualityLevel.PARTIAL
        assert assessment.should_continue is True

    def test_sufficient_quality(self):
        metrics = EvidenceMetrics()
        metrics.record_tool_call("tool1", "success", "kubernetes")
        metrics.record_tool_call("tool2", "success", "kubernetes")
        config = QualityConvergenceConfig(enabled=True)

        assessment = assess_evidence_quality(metrics, config)

        assert assessment.level == QualityLevel.SUFFICIENT
        assert assessment.should_continue is False

    def test_comprehensive_quality(self):
        metrics = EvidenceMetrics()
        metrics.record_tool_call("tool1", "success", "kubernetes")
        metrics.record_tool_call("tool2", "success", "prometheus")
        metrics.record_tool_call("tool3", "success", "loki")
        config = QualityConvergenceConfig(enabled=True)

        assessment = assess_evidence_quality(metrics, config)

        assert assessment.level == QualityLevel.COMPREHENSIVE
        assert assessment.should_continue is False
        assert assessment.confidence > 0.8


class TestBuildRefinementPrompt:
    """Tests for build_refinement_prompt function."""

    def test_empty_assessment(self):
        assessment = QualityAssessment(
            level=QualityLevel.SUFFICIENT,
            confidence=0.9,
            gaps=[],
            suggestions=[],
            should_continue=False,
        )

        prompt = build_refinement_prompt(assessment)
        assert prompt == ""

    def test_assessment_with_gaps(self):
        assessment = QualityAssessment(
            level=QualityLevel.PARTIAL,
            confidence=0.5,
            gaps=["No successful tool calls"],
            suggestions=["Try alternative data sources"],
            should_continue=True,
        )

        prompt = build_refinement_prompt(assessment)
        assert "No successful tool calls" in prompt
        assert "Try alternative data sources" in prompt


class TestQualityConvergenceTracker:
    """Tests for QualityConvergenceTracker class."""

    def test_should_assess_quality_disabled(self):
        config = QualityConvergenceConfig(enabled=False)
        tracker = QualityConvergenceTracker(config)

        assert tracker.should_assess_quality(5) is False

    def test_should_assess_quality_enabled(self):
        config = QualityConvergenceConfig(
            enabled=True, min_iterations_before_assessment=2
        )
        tracker = QualityConvergenceTracker(config)

        assert tracker.should_assess_quality(1) is False
        assert tracker.should_assess_quality(2) is True

    def test_assess_and_decide_increments_refinement(self):
        config = QualityConvergenceConfig(enabled=True, max_refinement_iterations=3)
        tracker = QualityConvergenceTracker(config)
        tracker.record_tool_call("tool1", "error")

        assessment = tracker.assess_and_decide()
        assert assessment.should_continue is True
        assert tracker.refinement_count == 1

    def test_assess_and_decide_max_refinements(self):
        config = QualityConvergenceConfig(enabled=True, max_refinement_iterations=2)
        tracker = QualityConvergenceTracker(config)
        tracker.record_tool_call("tool1", "error")

        # First refinement
        tracker.assess_and_decide()
        assert tracker.refinement_count == 1

        # Second refinement
        tracker.assess_and_decide()
        assert tracker.refinement_count == 2

        # Third attempt should not continue (max reached)
        assessment = tracker.assess_and_decide()
        assert assessment.should_continue is False
        assert "max refinement iterations" in assessment.reasoning.lower()

    def test_get_metadata(self):
        config = QualityConvergenceConfig(enabled=True)
        tracker = QualityConvergenceTracker(config)
        tracker.record_tool_call("tool1", "success")

        metadata = tracker.get_metadata()
        assert "quality_convergence" in metadata
        assert metadata["quality_convergence"]["enabled"] is True
        assert metadata["quality_convergence"]["metrics"]["total_tool_calls"] == 1
