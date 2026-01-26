"""
Policy-based filtering for tool calls using Python expressions.

This module provides a policy engine that evaluates Python expressions
to allow or deny tool calls based on tool name, parameters, and context.

Example policy configuration:

    policy:
      rules:
        - name: restrict-namespaces
          match: ["kubectl_*", "kubernetes/*"]
          expression: |
            params.get("namespace", "default") in allowed_namespaces or
            params.get("namespace", "").startswith("team-a-")
          vars:
            allowed_namespaces: ["team-a-prod", "team-a-staging", "default"]

        - name: deny-system-namespaces
          match: ["kubectl_*"]
          expression: |
            params.get("namespace") not in ["kube-system", "kube-public"]
          message: "Access to system namespaces is denied"
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


class PolicyRule(BaseModel):
    """A single policy rule with match patterns and expression."""

    model_config = ConfigDict(extra="forbid")

    name: str
    match: List[str]  # Tool name patterns (fnmatch), e.g., ["kubectl_*", "kubernetes/*"]
    expression: str  # Python expression that must evaluate to True to allow
    message: Optional[str] = None  # Custom denial message
    vars: Dict[str, Any] = {}  # Additional variables available in expression


class PolicyConfig(BaseModel):
    """Policy configuration with namespace shortcuts and custom rules."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True

    # Shortcut: namespace allow/deny lists (applies to params.namespace)
    namespaces: Optional[Dict[str, List[str]]] = None  # {"allow": [...], "deny": [...]}

    # Shortcut: tool allow/deny lists
    tools: Optional[Dict[str, List[str]]] = None  # {"allow": [...], "deny": [...]}

    # Custom rules with expressions
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

        # Build internal rules from shortcuts + custom rules
        self._rules = self._build_rules()

    def _build_rules(self) -> List[PolicyRule]:
        """Build rule list from config shortcuts and custom rules."""
        rules: List[PolicyRule] = []

        if not self.config.enabled:
            return rules

        # Add namespace rules from shortcut
        if self.config.namespaces:
            deny_list = self.config.namespaces.get("deny", [])
            allow_list = self.config.namespaces.get("allow", [])

            if deny_list:
                # Deny rule: namespace must not be in deny list
                rules.append(
                    PolicyRule(
                        name="_namespace_deny",
                        match=["*"],  # Apply to all tools
                        expression='params.get("namespace") is None or params.get("namespace") not in denied_namespaces',
                        vars={"denied_namespaces": deny_list},
                        message=f"Namespace is in denied list: {deny_list}",
                    )
                )

            if allow_list:
                # Allow rule: namespace must match at least one allow pattern
                # Build expression that checks glob patterns
                rules.append(
                    PolicyRule(
                        name="_namespace_allow",
                        match=["*"],
                        expression='params.get("namespace") is None or any(match(p, params.get("namespace", "")) for p in allowed_namespaces)',
                        vars={"allowed_namespaces": allow_list},
                        message=f"Namespace not in allowed list: {allow_list}",
                    )
                )

        # Add tool rules from shortcut
        if self.config.tools:
            deny_list = self.config.tools.get("deny", [])
            allow_list = self.config.tools.get("allow", [])

            if deny_list:
                rules.append(
                    PolicyRule(
                        name="_tool_deny",
                        match=["*"],
                        expression="not any(match(p, tool) for p in denied_tools)",
                        vars={"denied_tools": deny_list},
                        message="Tool is in denied list",
                    )
                )

            if allow_list:
                rules.append(
                    PolicyRule(
                        name="_tool_allow",
                        match=["*"],
                        expression="any(match(p, tool) for p in allowed_tools)",
                        vars={"allowed_tools": allow_list},
                        message="Tool not in allowed list",
                    )
                )

        # Add custom rules
        rules.extend(self.config.rules)

        return rules

    def check(
        self,
        tool_name: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> PolicyResult:
        """
        Check if a tool call is allowed by policy.

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

        for rule in self._rules:
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
                result = self._evaluator.eval(rule.expression)

                if not result:
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
