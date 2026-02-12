"""
Quality Convergence Module for HolmesGPT

This module implements iterative refinement and quality assessment for investigations.
Instead of running once and hoping for success, the system iterates until quality
targets are met, similar to the babysitter orchestration framework approach.

Key concepts:
- Evidence Sufficiency: Track how much useful data was gathered
- Quality Assessment: Let the LLM evaluate its own investigation completeness
- Iterative Refinement: If quality is insufficient, continue with targeted prompts
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class QualityLevel(str, Enum):
    """Quality assessment levels for investigation completeness."""

    INSUFFICIENT = "insufficient"  # Not enough data gathered
    PARTIAL = "partial"  # Some data but gaps remain
    SUFFICIENT = "sufficient"  # Enough data to form conclusions
    COMPREHENSIVE = "comprehensive"  # Thorough investigation with strong evidence


@dataclass
class EvidenceMetrics:
    """Tracks evidence gathering during an investigation."""

    total_tool_calls: int = 0
    successful_tool_calls: int = 0
    error_tool_calls: int = 0
    no_data_tool_calls: int = 0
    unique_tools_used: set = field(default_factory=set)
    data_sources_accessed: set = field(default_factory=set)

    def record_tool_call(
        self, tool_name: str, status: str, data_source: Optional[str] = None
    ) -> None:
        """Record a tool call and its result."""
        self.total_tool_calls += 1
        self.unique_tools_used.add(tool_name)

        if status == "success":
            self.successful_tool_calls += 1
            if data_source:
                self.data_sources_accessed.add(data_source)
        elif status == "error":
            self.error_tool_calls += 1
        elif status == "no_data":
            self.no_data_tool_calls += 1

    @property
    def success_rate(self) -> float:
        """Calculate the success rate of tool calls."""
        if self.total_tool_calls == 0:
            return 0.0
        return self.successful_tool_calls / self.total_tool_calls

    @property
    def evidence_breadth(self) -> int:
        """Number of unique data sources accessed."""
        return len(self.data_sources_accessed)

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary for logging/metadata."""
        return {
            "total_tool_calls": self.total_tool_calls,
            "successful_tool_calls": self.successful_tool_calls,
            "error_tool_calls": self.error_tool_calls,
            "no_data_tool_calls": self.no_data_tool_calls,
            "unique_tools_used": list(self.unique_tools_used),
            "data_sources_accessed": list(self.data_sources_accessed),
            "success_rate": self.success_rate,
            "evidence_breadth": self.evidence_breadth,
        }


class QualityAssessment(BaseModel):
    """Result of a quality assessment."""

    level: QualityLevel
    confidence: float  # 0.0 to 1.0
    gaps: List[str] = []  # Identified gaps in the investigation
    suggestions: List[str] = []  # Suggestions for improvement
    should_continue: bool = False  # Whether to continue investigating
    reasoning: str = ""  # Explanation of the assessment


class QualityConvergenceConfig(BaseModel):
    """Configuration for quality convergence behavior."""

    enabled: bool = False  # Whether to enable quality convergence
    min_iterations_before_assessment: int = (
        1  # Minimum iterations before checking quality
    )
    max_refinement_iterations: int = 2  # Maximum additional iterations for refinement
    min_evidence_breadth: int = 1  # Minimum unique data sources required
    min_success_rate: float = 0.3  # Minimum tool call success rate
    require_successful_tool_call: bool = (
        True  # At least one successful tool call required
    )


QUALITY_ASSESSMENT_PROMPT = """
Based on the investigation so far, assess the quality and completeness of the gathered evidence.

Evidence Metrics:
- Total tool calls: {total_tool_calls}
- Successful: {successful_tool_calls}
- Errors: {error_tool_calls}
- No data: {no_data_tool_calls}
- Unique tools used: {unique_tools_count}
- Data sources accessed: {data_sources_count}

Original question/issue:
{original_prompt}

Current findings summary:
{current_findings}

Evaluate:
1. Have you gathered enough evidence to answer the question confidently?
2. Are there obvious gaps in the investigation?
3. What additional information would strengthen your conclusions?

If you believe you have sufficient evidence, respond with "QUALITY_SUFFICIENT".
If you need more information, respond with "NEED_MORE_INFO:" followed by what you need to investigate.
"""

REFINEMENT_PROMPT = """
The initial investigation may not have gathered sufficient evidence. Please continue investigating to address these potential gaps:

{gaps}

Focus on gathering concrete data through tool calls. Do not repeat tool calls that already returned no data or errors.
"""


def assess_evidence_quality(
    metrics: EvidenceMetrics, config: QualityConvergenceConfig
) -> QualityAssessment:
    """
    Assess the quality of gathered evidence based on metrics.

    This is a heuristic-based assessment that can trigger refinement
    without requiring an additional LLM call.
    """
    gaps = []
    suggestions = []

    # Check minimum requirements
    if config.require_successful_tool_call and metrics.successful_tool_calls == 0:
        gaps.append("No successful tool calls - no data was gathered")
        suggestions.append("Try alternative data sources or adjust query parameters")

    if metrics.evidence_breadth < config.min_evidence_breadth:
        gaps.append(
            f"Limited data sources ({metrics.evidence_breadth}/{config.min_evidence_breadth} required)"
        )
        suggestions.append("Query additional data sources for corroborating evidence")

    if metrics.success_rate < config.min_success_rate and metrics.total_tool_calls > 0:
        gaps.append(
            f"Low tool success rate ({metrics.success_rate:.0%} vs {config.min_success_rate:.0%} required)"
        )
        suggestions.append("Address tool errors or try alternative approaches")

    # Determine quality level
    if metrics.successful_tool_calls == 0:
        level = QualityLevel.INSUFFICIENT
        confidence = 0.1
        should_continue = True
    elif gaps:
        level = QualityLevel.PARTIAL
        confidence = 0.4 + (0.2 * metrics.success_rate)
        should_continue = True
    elif metrics.evidence_breadth >= 2 and metrics.success_rate >= 0.7:
        level = QualityLevel.COMPREHENSIVE
        confidence = 0.9
        should_continue = False
    else:
        level = QualityLevel.SUFFICIENT
        confidence = 0.7
        should_continue = False

    return QualityAssessment(
        level=level,
        confidence=confidence,
        gaps=gaps,
        suggestions=suggestions,
        should_continue=should_continue and len(gaps) > 0,
        reasoning=f"Assessment based on {metrics.total_tool_calls} tool calls with {metrics.success_rate:.0%} success rate",
    )


def build_refinement_prompt(assessment: QualityAssessment) -> str:
    """Build a prompt to guide refinement based on assessment gaps."""
    if not assessment.gaps and not assessment.suggestions:
        return ""

    gaps_text = "\n".join(f"- {gap}" for gap in assessment.gaps)
    suggestions_text = "\n".join(f"- {s}" for s in assessment.suggestions)

    prompt_parts = []
    if gaps_text:
        prompt_parts.append(f"Identified gaps:\n{gaps_text}")
    if suggestions_text:
        prompt_parts.append(f"Suggestions:\n{suggestions_text}")

    return REFINEMENT_PROMPT.format(gaps="\n\n".join(prompt_parts))


def extract_data_source_from_tool(tool_name: str) -> Optional[str]:
    """
    Extract the data source category from a tool name.
    This helps track evidence breadth across different systems.
    """
    # Map tool prefixes to data source categories
    source_mappings = {
        "kubectl": "kubernetes",
        "k8s": "kubernetes",
        "prometheus": "prometheus",
        "grafana": "grafana",
        "loki": "loki",
        "elasticsearch": "elasticsearch",
        "opensearch": "opensearch",
        "aws": "aws",
        "gcp": "gcp",
        "azure": "azure",
        "docker": "docker",
        "helm": "helm",
        "argocd": "argocd",
        "datadog": "datadog",
        "newrelic": "newrelic",
        "splunk": "splunk",
        "servicenow": "servicenow",
        "jira": "jira",
        "github": "github",
        "gitlab": "gitlab",
        "confluence": "confluence",
        "opsgenie": "opsgenie",
        "pagerduty": "pagerduty",
        "slack": "slack",
        "bash": "shell",
    }

    tool_lower = tool_name.lower()
    for prefix, source in source_mappings.items():
        if tool_lower.startswith(prefix):
            return source

    # Check for common patterns
    if "log" in tool_lower:
        return "logs"
    if "metric" in tool_lower:
        return "metrics"
    if "event" in tool_lower:
        return "events"

    return None


class QualityConvergenceTracker:
    """
    Tracks quality convergence state across an investigation.

    This class maintains metrics and assessment state, enabling
    the tool calling loop to make informed decisions about
    whether to continue investigating.
    """

    def __init__(self, config: Optional[QualityConvergenceConfig] = None):
        self.config = config or QualityConvergenceConfig()
        self.metrics = EvidenceMetrics()
        self.assessments: List[QualityAssessment] = []
        self.refinement_count = 0
        self._initial_iteration_count = 0

    def record_tool_call(self, tool_name: str, status: str) -> None:
        """Record a tool call result."""
        data_source = extract_data_source_from_tool(tool_name)
        self.metrics.record_tool_call(tool_name, status, data_source)

    def should_assess_quality(self, current_iteration: int) -> bool:
        """Determine if quality assessment should be performed."""
        if not self.config.enabled:
            return False

        # Store initial iteration count on first call
        if self._initial_iteration_count == 0:
            self._initial_iteration_count = current_iteration

        iterations_completed = current_iteration - self._initial_iteration_count + 1
        return iterations_completed >= self.config.min_iterations_before_assessment

    def assess_and_decide(self) -> QualityAssessment:
        """
        Assess current quality and decide whether to continue.

        Returns an assessment with the should_continue flag set appropriately.
        """
        if self.refinement_count >= self.config.max_refinement_iterations:
            # Max refinements reached - stop regardless of quality
            assessment = assess_evidence_quality(self.metrics, self.config)
            assessment.should_continue = False
            assessment.reasoning += (
                f" (max refinement iterations {self.config.max_refinement_iterations} reached)"
            )
            self.assessments.append(assessment)
            return assessment

        assessment = assess_evidence_quality(self.metrics, self.config)
        self.assessments.append(assessment)

        if assessment.should_continue:
            self.refinement_count += 1
            logging.info(
                f"Quality convergence: {assessment.level.value} - initiating refinement {self.refinement_count}/{self.config.max_refinement_iterations}"
            )
        else:
            logging.info(
                f"Quality convergence: {assessment.level.value} - investigation complete"
            )

        return assessment

    def get_metadata(self) -> Dict[str, Any]:
        """Get quality convergence metadata for inclusion in results."""
        return {
            "quality_convergence": {
                "enabled": self.config.enabled,
                "metrics": self.metrics.to_dict(),
                "refinement_count": self.refinement_count,
                "final_quality": (
                    self.assessments[-1].level.value if self.assessments else None
                ),
                "final_confidence": (
                    self.assessments[-1].confidence if self.assessments else None
                ),
            }
        }
