"""
Policy-based filtering for tool calls using Python expressions.

This module provides a policy engine that evaluates Python expressions
to deny tool calls based on tool name, parameters, and context.

Default behavior is ALLOW everything. Users opt-in to restrictions
by adding deny rules.

Example policy configuration:

    policy:
      deny:
        - name: system-namespaces
          match: ["kubectl_*"]
          when: 'params.get("namespace") in ["kube-system", "kube-public"]'
          message: "System namespaces are restricted"

        - name: secrets
          match: ["kubectl_*"]
          when: 'params.get("kind") == "secret"'
          message: "Secret access is restricted"

        - name: dangerous-tools
          match: ["bash/*", "kubectl_exec"]
          # no 'when' = always deny when tool matches
"""

import fnmatch
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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


class DenyRule(BaseModel):
    """A deny rule that blocks tool calls when conditions match."""

    model_config = ConfigDict(extra="forbid")

    name: str
    match: List[str] = ["*"]  # Tool name patterns (fnmatch), e.g., ["kubectl_*"]
    when: Optional[str] = None  # Python expression - if True (or omitted), deny
    message: Optional[str] = None  # Custom denial message
    vars: Dict[str, Any] = {}  # Additional variables available in expression


class PolicyConfig(BaseModel):
    """Policy configuration with deny rules."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True

    # Deny rules - if any matches, tool call is denied
    # Default (no rules) = allow everything
    deny: List[DenyRule] = []


class PolicyEnforcer:
    """
    Evaluates policy rules against tool calls using simpleeval.

    Default behavior is ALLOW. Deny rules opt-in to restrictions.

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

        Default is ALLOW. If any deny rule matches, the call is denied.

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

        for rule in self.config.deny:
            # Check if rule applies to this tool
            if not self._matches_tool(tool_name, rule.match):
                continue

            # If no 'when' expression, deny immediately when tool matches
            if rule.when is None:
                message = rule.message or f"Denied by policy rule '{rule.name}'"
                logger.info(
                    f"Policy denied tool '{tool_name}': {message}"
                )
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
                condition_met = self._evaluator.eval(rule.when)

                if condition_met:
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

        # No deny rule matched - allow
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
