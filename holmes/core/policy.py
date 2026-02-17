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

Example using HTTP functions for API-based permission checks:

    policy:
      rules:
        # Check Confluence page access via API
        - name: confluence-access
          match: ["confluence_*"]
          allow_if:
            python: |
              account = http_get(
                f"{env('CONFLUENCE_URL')}/wiki/rest/api/search/user",
                params={"cql": f"type=user and email={context.get('user_email')}"},
                auth=(env('CONFLUENCE_USER'), env('CONFLUENCE_TOKEN'))
              ).get('results', [{}])[0]
              and http_post(
                f"{env('CONFLUENCE_URL')}/wiki/rest/api/content/{params.get('page_id')}/permission/check",
                json_data={"subject": {"type": "user", "identifier": account.get('accountId')}, "operation": "read"},
                auth=(env('CONFLUENCE_USER'), env('CONFLUENCE_TOKEN'))
              ).get('hasPermission', False)

Semantics:
- Tools matching NO rules → use `default` (allow or deny)
- Tools matching rules → ALL matching rules' `allow_if` must pass
- `allow_if` requires exactly one of `python:` or `bash:`
"""

import fnmatch
import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import requests  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
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


class RateLimitPerGroup(BaseModel):
    """Rate limit per group (e.g., per namespace, per cluster)."""

    model_config = ConfigDict(extra="forbid")

    key: str  # Dotted path like "params.namespace" or "context.cluster"
    limit: int  # Max calls per group within the window


class RateLimitConfig(BaseModel):
    """Rate limiting configuration for a policy rule."""

    model_config = ConfigDict(extra="forbid")

    window: str  # Time window, e.g., "30m", "1h", "24h", "30s"
    max_total: Optional[int] = None  # Max total calls in window (across all groups)
    max_per: Optional[RateLimitPerGroup] = None  # Max calls per group in window

    @field_validator("window")
    @classmethod
    def validate_window(cls, v: str) -> str:
        """Validate window is a parseable duration string."""
        _parse_duration(v)  # Will raise ValueError if invalid
        return v

    @model_validator(mode="after")
    def validate_at_least_one_limit(self):
        """Ensure at least one of max_total or max_per is set."""
        if self.max_total is None and self.max_per is None:
            raise ValueError(
                "rate_limit must have at least one of 'max_total' or 'max_per'"
            )
        return self


class PolicyRule(BaseModel):
    """A policy rule that defines conditions for tool access."""

    model_config = ConfigDict(extra="forbid")

    name: str
    match: List[str] = ["*"]  # Tool name patterns (fnmatch), e.g., ["kubectl_*"]
    allow_if: AllowCondition  # Condition that must be true to allow
    message: Optional[str] = None  # Custom denial message
    vars: Dict[str, Any] = {}  # Additional variables available in expression
    rate_limit: Optional[RateLimitConfig] = None  # Optional rate limiting


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


def _parse_duration(duration: str) -> float:
    """
    Parse a duration string into seconds.

    Supported formats: "30s", "5m", "1h", "1d", "1h30m"

    Returns:
        Duration in seconds
    """
    total = 0.0
    remaining = duration.strip()

    if not remaining:
        raise ValueError("Empty duration string")

    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}

    while remaining:
        # Find the next number
        i = 0
        while i < len(remaining) and (remaining[i].isdigit() or remaining[i] == "."):
            i += 1

        if i == 0:
            raise ValueError(
                f"Invalid duration format: '{duration}'. Expected number before unit"
            )

        value = float(remaining[:i])

        if i >= len(remaining):
            raise ValueError(
                f"Invalid duration format: '{duration}'. Expected unit suffix (s/m/h/d)"
            )

        unit = remaining[i]
        if unit not in units:
            raise ValueError(
                f"Invalid duration unit '{unit}' in '{duration}'. Use s, m, h, or d"
            )

        total += value * units[unit]
        remaining = remaining[i + 1 :]

    return total


class RateLimitTracker:
    """
    Thread-safe in-memory tracker for rate limiting.

    Uses a sliding window approach: stores timestamps of allowed calls
    and checks against configured limits.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # rule_name -> list of (timestamp, group_key) tuples
        self._calls: Dict[str, List[Tuple[float, Optional[str]]]] = defaultdict(list)

    def check_and_record(
        self,
        rule_name: str,
        rate_limit: RateLimitConfig,
        group_value: Optional[str] = None,
    ) -> PolicyResult:
        """
        Check rate limits and record the call if allowed.

        Args:
            rule_name: Name of the policy rule
            rate_limit: Rate limit configuration
            group_value: Value of the group_by key (e.g., namespace name)

        Returns:
            PolicyResult - allowed if within limits, denied otherwise
        """
        window_seconds = _parse_duration(rate_limit.window)
        now = time.monotonic()
        cutoff = now - window_seconds

        with self._lock:
            # Clean up old entries
            self._calls[rule_name] = [
                (ts, gk) for ts, gk in self._calls[rule_name] if ts > cutoff
            ]

            calls = self._calls[rule_name]

            # Check max_total
            if rate_limit.max_total is not None:
                if len(calls) >= rate_limit.max_total:
                    return PolicyResult(
                        allowed=False,
                        rule_name=rule_name,
                        message=f"Rate limit exceeded: {len(calls)}/{rate_limit.max_total} calls in {rate_limit.window}",
                    )

            # Check max_per group
            if rate_limit.max_per is not None and group_value is not None:
                group_count = sum(1 for _, gk in calls if gk == group_value)
                if group_count >= rate_limit.max_per.limit:
                    return PolicyResult(
                        allowed=False,
                        rule_name=rule_name,
                        message=f"Rate limit exceeded for '{group_value}': {group_count}/{rate_limit.max_per.limit} calls in {rate_limit.window}",
                    )

            # Within limits - record this call
            self._calls[rule_name].append((now, group_value))
            return PolicyResult(allowed=True)

    def reset(self, rule_name: Optional[str] = None) -> None:
        """Reset tracked calls, optionally for a specific rule only."""
        with self._lock:
            if rule_name:
                self._calls.pop(rule_name, None)
            else:
                self._calls.clear()


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
    - HTTP functions: http_get(), http_post() for API-based permission checks

    For Bash conditions:
    - Jinja2 templating: {{ params.X }}, {{ context.X }}, {{ tool }}
    - Exit code 0 = allow, non-zero = deny
    - Stderr captured for denial message
    """

    # Default timeout for HTTP requests (seconds)
    HTTP_TIMEOUT = 10

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
        self._rate_limiter = RateLimitTracker()

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

        # Add HTTP helper functions for API-based permission checks
        self._evaluator.functions["http_get"] = self._http_get
        self._evaluator.functions["http_post"] = self._http_post
        self._evaluator.functions["env"] = self._get_env

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

        # All matching rules must pass (allow_if + rate_limit)
        for rule in matching_rules:
            result = self._evaluate_condition(rule, tool_name, params, context)
            if not result.allowed:
                return result

            # Check rate limits if configured
            if rule.rate_limit is not None:
                rate_result = self._check_rate_limit(rule, tool_name, params, context)
                if not rate_result.allowed:
                    logger.info(
                        f"Policy rate-limited tool '{tool_name}': {rate_result.message}"
                    )
                    return rate_result

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

    def _check_rate_limit(
        self,
        rule: PolicyRule,
        tool_name: str,
        params: Dict[str, Any],
        context: Dict[str, Any],
    ) -> PolicyResult:
        """Check rate limits for a rule."""
        assert rule.rate_limit is not None

        # Resolve group value if max_per is configured
        group_value: Optional[str] = None
        if rule.rate_limit.max_per is not None:
            group_value = self._resolve_group_value(
                rule.rate_limit.max_per.key, tool_name, params, context
            )

        return self._rate_limiter.check_and_record(
            rule.name, rule.rate_limit, group_value
        )

    @staticmethod
    def _resolve_group_value(
        key: str,
        tool_name: str,
        params: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[str]:
        """Resolve a dotted path to a value for rate limit grouping."""
        parts = key.split(".")
        if not parts:
            return None

        root = parts[0]
        if root == "tool":
            return tool_name
        elif root == "params":
            obj: Any = params
        elif root == "context":
            obj = context
        else:
            return None

        for part in parts[1:]:
            if isinstance(obj, dict):
                obj = obj.get(part)
            else:
                return None
            if obj is None:
                return None

        return str(obj) if obj is not None else None

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

    @staticmethod
    def _get_env(name: str, default: str = "") -> str:
        """
        Get environment variable value.

        Args:
            name: Environment variable name
            default: Default value if not set

        Returns:
            Environment variable value or default
        """
        return os.environ.get(name, default)

    @staticmethod
    def _http_get(
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        auth: Optional[Union[Tuple[str, str], str]] = None,
        timeout: int = 10,
    ) -> Dict[str, Any]:
        """
        Make an HTTP GET request and return JSON response.

        Args:
            url: URL to request
            params: Query parameters
            headers: Request headers
            auth: Authentication tuple (username, password) or bearer token string
            timeout: Request timeout in seconds

        Returns:
            Parsed JSON response as dict, or empty dict on error

        Example:
            http_get("https://api.example.com/user", params={"email": "user@example.com"})
        """
        try:
            request_headers = headers or {}
            request_auth = None

            # Handle auth - tuple for basic auth, string for bearer token
            if isinstance(auth, tuple):
                request_auth = auth
            elif isinstance(auth, str):
                request_headers["Authorization"] = f"Bearer {auth}"

            response = requests.get(
                url,
                params=params,
                headers=request_headers,
                auth=request_auth,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"HTTP GET failed for {url}: {e}")
            return {}
        except json.JSONDecodeError as e:
            logger.warning(f"HTTP GET response not JSON for {url}: {e}")
            return {}

    @staticmethod
    def _http_post(
        url: str,
        json_data: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        auth: Optional[Union[Tuple[str, str], str]] = None,
        timeout: int = 10,
    ) -> Dict[str, Any]:
        """
        Make an HTTP POST request and return JSON response.

        Args:
            url: URL to request
            json_data: JSON body (will set Content-Type: application/json)
            data: Form data body
            headers: Request headers
            auth: Authentication tuple (username, password) or bearer token string
            timeout: Request timeout in seconds

        Returns:
            Parsed JSON response as dict, or empty dict on error

        Example:
            http_post("https://api.example.com/check",
                      json_data={"subject": {"type": "user", "id": "123"}})
        """
        try:
            request_headers = headers or {}
            request_auth = None

            # Handle auth - tuple for basic auth, string for bearer token
            if isinstance(auth, tuple):
                request_auth = auth
            elif isinstance(auth, str):
                request_headers["Authorization"] = f"Bearer {auth}"

            response = requests.post(
                url,
                json=json_data,
                data=data,
                headers=request_headers,
                auth=request_auth,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"HTTP POST failed for {url}: {e}")
            return {}
        except json.JSONDecodeError as e:
            logger.warning(f"HTTP POST response not JSON for {url}: {e}")
            return {}


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
