"""
Policy-based filtering for tool calls using Python expressions.

This module provides a policy engine that evaluates Python expressions
to control tool call access based on tool name, parameters, and context.

The `default` setting controls behavior when NO rules match a tool:
- `default: allow` (default) - tools not matching any rule are allowed
- `default: deny` - tools not matching any rule are denied (whitelist mode)

When rules DO match, ALL matching rules' `when` conditions must be True.

Example policy configuration (blacklist mode - default allow):

    policy:
      default: allow  # Optional, this is the default
      rules:
        # Only allow team-a namespaces for kubectl
        - name: team-namespaces
          match: ["kubectl_*"]
          when: 'params.get("namespace", "").startswith("team-a-")'

        # Block bash entirely (when omitted = always block)
        - name: no-bash
          match: ["bash/*"]

Example policy configuration (whitelist mode - default deny):

    policy:
      default: deny
      rules:
        # Allow prometheus tools
        - name: allow-prometheus
          match: ["prometheus_*"]
          when: "True"

        # Allow kubectl with namespace constraint
        - name: allow-kubectl
          match: ["kubectl_*"]
          when: 'params.get("namespace", "").startswith("team-a-")'

Semantics:
- Tools matching NO rules → use `default` (allow or deny)
- Tools matching rules → ALL matching rules' 'when' must be True
- If 'when' is omitted → always blocks matched tools (regardless of default)
"""

import fnmatch
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict
from simpleeval import EvalWithCompoundTypes, NameNotDefined

logger = logging.getLogger(__name__)


@dataclass
class PolicyResult:
    """Result of a policy evaluation."""

    allowed: bool
    rule_name: Optional[str] = None
    message: Optional[str] = None

    def __bool__(self) -> bool:
        return self.allowed


class PolicyRule(BaseModel):
    """A policy rule that defines conditions for tool access."""

    model_config = ConfigDict(extra="forbid")

    name: str
    match: List[str] = ["*"]  # Tool name patterns (fnmatch), e.g., ["kubectl_*"]
    when: Optional[str] = None  # Python expression - must be True to allow. If omitted, always fails.
    message: Optional[str] = None  # Custom denial message
    vars: Dict[str, Any] = {}  # Additional variables available in expression


class PolicyConfig(BaseModel):
    """Policy configuration with rules."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True

    # Default behavior when NO rules match a tool:
    # - "allow": tool is allowed (blacklist mode - block specific tools)
    # - "deny": tool is denied (whitelist mode - allow specific tools)
    default: Literal["allow", "deny"] = "allow"

    # Rules define constraints for tools
    # - Tools matching NO rules → use 'default' setting
    # - Tools matching rules → ALL matching 'when' conditions must pass
    rules: List[PolicyRule] = []


class PolicyEnforcer:
    """
    Evaluates policy rules against tool calls using simpleeval.

    Semantics:
    - Tools matching NO rules → use `default` setting (allow or deny)
    - Tools matching one or more rules → ALL matching rules' 'when' must be True
    - If 'when' is omitted → always blocks matched tools (regardless of default)

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

        Semantics:
        - Tools matching NO rules → use `default` setting
        - Tools matching rules → ALL matching 'when' must be True

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

        # Find all rules that match this tool
        matching_rules = [
            rule for rule in self.config.rules
            if self._matches_tool(tool_name, rule.match)
        ]

        # No matching rules = use default setting
        if not matching_rules:
            if self.config.default == "deny":
                logger.info(f"Policy denied tool '{tool_name}': no matching rules (default: deny)")
                return PolicyResult(
                    allowed=False,
                    message=f"Tool '{tool_name}' not allowed by policy (no matching rules)"
                )
            return PolicyResult(allowed=True)

        # All matching rules must pass
        for rule in matching_rules:
            # If 'when' is omitted, treat as False (block matched tools)
            if rule.when is None:
                message = rule.message or f"Blocked by policy rule '{rule.name}'"
                logger.info(f"Policy denied tool '{tool_name}': {message}")
                return PolicyResult(
                    allowed=False, rule_name=rule.name, message=message
                )

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
                condition_passed = self._evaluator.eval(rule.when)

                if not condition_passed:
                    message = rule.message or f"Denied by policy rule '{rule.name}'"
                    logger.info(
                        f"Policy denied tool '{tool_name}' with params {params}: {message}"
                    )
                    return PolicyResult(
                        allowed=False, rule_name=rule.name, message=message
                    )

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

        # All matching rules passed
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
