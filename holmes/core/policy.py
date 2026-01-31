"""
Policy-based filtering for tool calls using Python expressions.

This module provides a policy engine that evaluates Python expressions
to allow or deny tool calls based on tool name, parameters, and context.

Example policy configuration:

    policy:
      default: deny  # or "allow" - what happens when no rule matches

      rules:
        - name: deny-system-namespaces
          effect: deny
          match: ["kubectl_*"]
          when: 'params.get("namespace") in ["kube-system", "kube-public"]'
          message: "System namespaces are denied"

        - name: allow-team-namespaces
          effect: allow
          match: ["kubectl_*", "kubernetes/*"]
          when: 'params.get("namespace", "").startswith("team-a-") or params.get("namespace") == "default"'

        - name: allow-prometheus-readonly
          effect: allow
          match: ["prometheus_*"]
          when: "True"  # Always allow prometheus tools
"""

import fnmatch
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict
from simpleeval import EvalWithCompoundTypes, NameNotDefined

logger = logging.getLogger(__name__)


class PolicyEffect(str, Enum):
    """The effect of a policy rule when its condition matches."""
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class PolicyResult:
    """Result of a policy evaluation."""

    allowed: bool
    rule_name: Optional[str] = None
    message: Optional[str] = None

    def __bool__(self) -> bool:
        return self.allowed


class PolicyRule(BaseModel):
    """A single policy rule with match patterns and expression."""

    model_config = ConfigDict(extra="forbid")

    name: str
    effect: PolicyEffect  # "allow" or "deny" - what happens when 'when' is True
    match: List[str] = ["*"]  # Tool name patterns (fnmatch), e.g., ["kubectl_*"]
    when: str  # Python expression - if True, apply the effect
    message: Optional[str] = None  # Custom message (used for deny)
    vars: Dict[str, Any] = {}  # Additional variables available in expression


class PolicyConfig(BaseModel):
    """Policy configuration with rules."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True

    # Default behavior when no rule matches: "allow" or "deny"
    default: Literal["allow", "deny"] = "allow"

    # Rules evaluated in order - first matching rule wins
    rules: List[PolicyRule] = []


class PolicyEnforcer:
    """
    Evaluates policy rules against tool calls using simpleeval.

    The evaluator provides a sandboxed Python expression environment with:
    - `tool`: The tool name being called
    - `params`: The parameters dict passed to the tool
    - `context`: Additional context (user, team, etc.)
    - Any custom `vars` defined in the rule

    Built-in functions available:
    - All standard Python builtins (len, str, int, etc.)
    - `match(pattern, string)`: fnmatch-style glob matching
    - `regex(pattern, string)`: regex matching
    """

    # Safe built-in functions for expressions
    SAFE_FUNCTIONS = {
        "len": len,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "list": list,
        "dict": dict,
        "set": set,
        "tuple": tuple,
        "abs": abs,
        "min": min,
        "max": max,
        "sum": sum,
        "sorted": sorted,
        "any": any,
        "all": all,
        "isinstance": isinstance,
        "hasattr": hasattr,
        "getattr": getattr,
    }

    def __init__(self, config: Optional[PolicyConfig] = None):
        self.config = config or PolicyConfig()
        self._evaluator = EvalWithCompoundTypes()

        # Add safe functions
        self._evaluator.functions.update(self.SAFE_FUNCTIONS)

        # Add helper functions
        self._evaluator.functions["match"] = lambda pattern, string: fnmatch.fnmatch(
            string or "", pattern
        )
        self._evaluator.functions["regex"] = lambda pattern, string: bool(
            re.search(pattern, string or "")
        )
        self._evaluator.functions["startswith"] = lambda s, prefix: (s or "").startswith(prefix)
        self._evaluator.functions["endswith"] = lambda s, suffix: (s or "").endswith(suffix)
        self._evaluator.functions["contains"] = lambda s, sub: sub in (s or "")

    def check(
        self,
        tool_name: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> PolicyResult:
        """
        Check if a tool call is allowed by policy.

        Rules are evaluated in order. First rule where 'when' evaluates to True
        determines the outcome based on its 'effect' (allow/deny).

        Args:
            tool_name: Name of the tool being called
            params: Parameters passed to the tool
            context: Additional context (user info, team, etc.)

        Returns:
            PolicyResult with allowed=True if permitted, False otherwise
        """
        if not self.config.enabled:
            return PolicyResult(allowed=True)

        context = context or {}

        for rule in self.config.rules:
            # Check if rule applies to this tool
            if not self._matches_tool(tool_name, rule.match):
                continue

            # Build evaluation context
            names = {
                "tool": tool_name,
                "params": params,
                "context": context,
                **rule.vars,
            }

            try:
                # Set names on evaluator before evaluation
                self._evaluator.names = names
                condition_met = self._evaluator.eval(rule.when)

                if condition_met:
                    # Condition matched - apply the effect
                    if rule.effect == PolicyEffect.DENY:
                        message = rule.message or f"Denied by policy rule '{rule.name}'"
                        logger.info(
                            f"Policy denied tool '{tool_name}' with params {params}: {message}"
                        )
                        return PolicyResult(
                            allowed=False, rule_name=rule.name, message=message
                        )
                    else:  # PolicyEffect.ALLOW
                        logger.debug(
                            f"Policy allowed tool '{tool_name}' by rule '{rule.name}'"
                        )
                        return PolicyResult(allowed=True, rule_name=rule.name)

            except NameNotDefined as e:
                logger.warning(
                    f"Policy rule '{rule.name}' references undefined name: {e}. Denying by default."
                )
                return PolicyResult(
                    allowed=False,
                    rule_name=rule.name,
                    message=f"Policy evaluation error: {e}",
                )
            except Exception as e:
                logger.error(
                    f"Policy rule '{rule.name}' evaluation failed: {e}. Denying by default."
                )
                return PolicyResult(
                    allowed=False,
                    rule_name=rule.name,
                    message=f"Policy evaluation error: {e}",
                )

        # No rule matched - use default behavior
        if self.config.default == "deny":
            logger.info(
                f"Policy denied tool '{tool_name}' (no matching rule, default=deny)"
            )
            return PolicyResult(
                allowed=False, message="No matching policy rule (default: deny)"
            )

        return PolicyResult(allowed=True)

    def _matches_tool(self, tool_name: str, patterns: List[str]) -> bool:
        """Check if tool name matches any of the patterns."""
        for pattern in patterns:
            if fnmatch.fnmatch(tool_name, pattern):
                return True
        return False


# Global default enforcer (can be replaced via config)
_default_enforcer: Optional[PolicyEnforcer] = None


def get_policy_enforcer() -> Optional[PolicyEnforcer]:
    """Get the global policy enforcer instance."""
    return _default_enforcer


def set_policy_enforcer(enforcer: Optional[PolicyEnforcer]) -> None:
    """Set the global policy enforcer instance."""
    global _default_enforcer
    _default_enforcer = enforcer


def init_policy_from_config(config: Optional[PolicyConfig]) -> Optional[PolicyEnforcer]:
    """Initialize policy enforcer from config and set as global default."""
    if config is None:
        set_policy_enforcer(None)
        return None

    enforcer = PolicyEnforcer(config)
    set_policy_enforcer(enforcer)
    return enforcer
