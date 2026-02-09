"""
Policy-based filtering for tool calls.

This module provides a policy engine that evaluates conditions to control
tool call access based on tool name, parameters, and context.

The `default` setting controls behavior when NO rules match a tool:
- `default: allow` (default) - tools not matching any rule are allowed
- `default: deny` - tools not matching any rule are denied (whitelist mode)

When rules DO match, ALL matching rules' `allow_if` conditions must pass.

Conditions can be:
- `python:` - Python expression evaluated with simpleeval (fast, in-process)
- `bash:` - Bash command with Jinja2 templating (exit 0 = allow)

Example policy configuration (blacklist mode - default allow):

    policy:
      default: allow
      rules:
        # Only allow team-a namespaces for kubectl
        - name: team-namespaces
          match: ["kubectl_*"]
          allow_if:
            python: 'params.get("namespace", "").startswith("team-a-")'

        # Block bash entirely
        - name: no-bash
          match: ["bash/*"]
          allow_if:
            python: "False"

Example policy configuration (whitelist mode - default deny):

    policy:
      default: deny
      rules:
        # Allow prometheus tools
        - name: allow-prometheus
          match: ["prometheus_*"]
          allow_if:
            python: "True"

        # Allow kubectl if user has K8s RBAC permission
        - name: user-rbac-check
          match: ["kubectl_*"]
          allow_if:
            bash: 'kubectl auth can-i get {{ params.kind }} -n {{ params.namespace }} --as={{ context.user_email }}'

Semantics:
- Tools matching NO rules → use `default` (allow or deny)
- Tools matching rules → ALL matching rules' `allow_if` must pass
- `allow_if` requires exactly one of `python:` or `bash:`
"""

import fnmatch
import json
import logging
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator
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


class AllowCondition(BaseModel):
    """Condition that must be true to allow a tool call."""

    model_config = ConfigDict(extra="forbid")

    python: Optional[str] = None  # Python expression (simpleeval)
    bash: Optional[str] = None  # Bash command with Jinja2 templating

    @model_validator(mode="after")
    def validate_exactly_one(self):
        """Ensure exactly one of python or bash is set."""
        has_python = self.python is not None
        has_bash = self.bash is not None

        if has_python and has_bash:
            raise ValueError(
                "allow_if must have exactly one of 'python' or 'bash', not both"
            )
        if not has_python and not has_bash:
            raise ValueError("allow_if must have exactly one of 'python' or 'bash'")

        return self


class PolicyRule(BaseModel):
    """A policy rule that defines conditions for tool access."""

    model_config = ConfigDict(extra="forbid")

    name: str
    match: List[str] = ["*"]  # Tool name patterns (fnmatch), e.g., ["kubectl_*"]
    allow_if: AllowCondition  # Condition that must be true to allow
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
    # - Tools matching rules → ALL matching 'allow_if' conditions must pass
    rules: List[PolicyRule] = []


class PolicyEnforcer:
    """
    Evaluates policy rules against tool calls.

    Semantics:
    - Tools matching NO rules → use `default` setting (allow or deny)
    - Tools matching one or more rules → ALL matching rules' `allow_if` must pass

    For Python conditions:
    - Evaluated with simpleeval (sandboxed)
    - Available variables: `tool`, `params`, `context`, plus rule `vars`
    - Built-in functions: len, str, int, bool, any, all, etc.
    - Helper functions: match(), regex(), startswith(), endswith(), contains()

    For Bash conditions:
    - Jinja2 templating: {{ params.X }}, {{ context.X }}, {{ tool }}
    - Exit code 0 = allow, non-zero = deny
    - Stderr captured for denial message
    """

    # Safe built-in functions for Python expressions
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
        self._evaluator.functions["startswith"] = lambda s, prefix: (
            s or ""
        ).startswith(prefix)
        self._evaluator.functions["endswith"] = lambda s, suffix: (s or "").endswith(
            suffix
        )
        self._evaluator.functions["contains"] = lambda s, sub: sub in (s or "")

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

        # Find all rules that match this tool
        matching_rules = [
            rule
            for rule in self.config.rules
            if self._matches_tool(tool_name, rule.match)
        ]

        # No matching rules = use default setting
        if not matching_rules:
            if self.config.default == "deny":
                logger.info(
                    f"Policy denied tool '{tool_name}': no matching rules (default: deny)"
                )
                return PolicyResult(
                    allowed=False,
                    message=f"Tool '{tool_name}' not allowed by policy (no matching rules)",
                )
            return PolicyResult(allowed=True)

        # All matching rules must pass
        for rule in matching_rules:
            result = self._evaluate_condition(rule, tool_name, params, context)
            if not result.allowed:
                return result

        # All matching rules passed
        return PolicyResult(allowed=True)

    def _evaluate_condition(
        self,
        rule: PolicyRule,
        tool_name: str,
        params: Dict[str, Any],
        context: Dict[str, Any],
    ) -> PolicyResult:
        """Evaluate a single rule's allow_if condition."""
        condition = rule.allow_if

        if condition.python is not None:
            return self._evaluate_python(rule, tool_name, params, context)
        else:
            return self._evaluate_bash(rule, tool_name, params, context)

    def _evaluate_python(
        self,
        rule: PolicyRule,
        tool_name: str,
        params: Dict[str, Any],
        context: Dict[str, Any],
    ) -> PolicyResult:
        """Evaluate a Python expression condition."""
        names = {
            "tool": tool_name,
            "params": params,
            "context": context,
            **rule.vars,
        }

        try:
            self._evaluator.names = names
            condition_passed = self._evaluator.eval(rule.allow_if.python)

            if not condition_passed:
                message = rule.message or f"Denied by policy rule '{rule.name}'"
                logger.info(
                    f"Policy denied tool '{tool_name}' with params {params}: {message}"
                )
                return PolicyResult(allowed=False, rule_name=rule.name, message=message)

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

    def _evaluate_bash(
        self,
        rule: PolicyRule,
        tool_name: str,
        params: Dict[str, Any],
        context: Dict[str, Any],
    ) -> PolicyResult:
        """Evaluate a bash command condition with Jinja2 templating."""
        template = rule.allow_if.bash
        assert template is not None  # Guaranteed by AllowCondition validation

        # Simple Jinja2-style template substitution
        # Supports: {{ params.key }}, {{ context.key }}, {{ tool }}
        # Also supports: {{ params.key | default:"value" }}
        try:
            command = self._render_template(
                template, tool_name, params, context, rule.vars
            )
        except Exception as e:
            logger.error(f"Policy rule '{rule.name}' template rendering failed: {e}")
            return PolicyResult(
                allowed=False,
                rule_name=rule.name,
                message=f"Template error: {e}",
            )

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,  # 10 second timeout
            )

            if result.returncode == 0:
                return PolicyResult(allowed=True)
            else:
                stderr = result.stderr.strip() if result.stderr else ""
                message = (
                    rule.message or stderr or f"Denied by policy rule '{rule.name}'"
                )
                logger.info(
                    f"Policy denied tool '{tool_name}': bash check failed (exit {result.returncode}): {message}"
                )
                return PolicyResult(allowed=False, rule_name=rule.name, message=message)

        except subprocess.TimeoutExpired:
            logger.error(f"Policy rule '{rule.name}' bash command timed out")
            return PolicyResult(
                allowed=False,
                rule_name=rule.name,
                message="Policy check timed out",
            )
        except Exception as e:
            logger.error(f"Policy rule '{rule.name}' bash execution failed: {e}")
            return PolicyResult(
                allowed=False,
                rule_name=rule.name,
                message=f"Policy check error: {e}",
            )

    def _render_template(
        self,
        template: str,
        tool_name: str,
        params: Dict[str, Any],
        context: Dict[str, Any],
        vars: Dict[str, Any],
    ) -> str:
        """
        Render Jinja2-style template.

        Supports:
        - {{ tool }} - tool name
        - {{ params.key }} - parameter value
        - {{ context.key }} - context value
        - {{ vars.key }} - rule variable
        - {{ params.key | default:"value" }} - with default
        - {{ params.key | quote }} - shell-escaped
        """
        import re as re_module

        def get_value(path: str, default: Optional[str] = None) -> Any:
            """Get value from nested path like 'params.namespace'."""
            parts = path.split(".")
            if not parts:
                return default

            root = parts[0]
            obj: Any = None
            if root == "tool":
                return tool_name
            elif root == "params":
                obj = params
            elif root == "context":
                obj = context
            elif root == "vars":
                obj = vars
            else:
                return default

            # Navigate nested path
            for part in parts[1:]:
                if isinstance(obj, dict):
                    obj = obj.get(part)
                else:
                    return default
                if obj is None:
                    return default

            return obj if obj is not None else default

        def replace_match(m: re_module.Match) -> str:
            expr = m.group(1).strip()

            # Check for filters
            if "|" in expr:
                parts = expr.split("|")
                path = parts[0].strip()
                filters = [f.strip() for f in parts[1:]]
            else:
                path = expr
                filters = []

            # Get default from filters
            default_value = ""
            for f in filters:
                if f.startswith("default:"):
                    default_value = f[8:].strip().strip('"').strip("'")

            value = get_value(path, default_value)

            # Convert to string
            if value is None:
                value = ""
            elif isinstance(value, bool):
                value = "true" if value else "false"
            elif isinstance(value, (dict, list)):
                value = json.dumps(value)
            else:
                value = str(value)

            # Apply quote filter
            if "quote" in filters:
                value = shlex.quote(value)

            return value

        # Match {{ ... }} patterns
        pattern = r"\{\{\s*(.+?)\s*\}\}"
        return re_module.sub(pattern, replace_match, template)

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
