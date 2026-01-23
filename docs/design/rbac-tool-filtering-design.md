# RBAC Tool Filtering Design Options for HolmesGPT

## Executive Summary

This document explores architecture and design options for implementing policy-based filtering of tool calls and MCP calls in HolmesGPT. The goal is to restrict what tools can access (e.g., limiting namespace access) based on configurable policies.

## Current Architecture

HolmesGPT's tool execution flow:

```
User Query → LLM → Tool Call Request → Tool Execution → Result
                        ↓
               ToolExecutor.invoke_tool()
                        ↓
               tool.invoke(params)
```

**Existing filtering mechanisms:**

- `restricted: bool` - Tool-level flag requiring runbook authorization
- `restricted_tools: List[str]` - Toolset-level patterns (e.g., `bash/*`)
- `approval_required_tools: List[str]` - Patterns needing user approval
- Pattern matching with `fnmatch`

**Key insertion points for policy enforcement:**

1. **Tool listing** - Filter available tools before LLM sees them
2. **Pre-execution** - Validate parameters before tool runs
3. **Post-execution** - Filter/redact results after tool runs

---

## Design Option 1: Simple YAML Policy Rules

**Inspired by:** Kubernetes NetworkPolicy, simple allowlist/denylist patterns

### Architecture

```yaml
# ~/.holmes/policy.yaml
policies:
  - name: restrict-namespaces
    match:
      tools: ["kubectl_*", "kubernetes/*"]
    rules:
      - parameter: namespace
        allow: ["production", "staging"]
        deny: ["kube-system", "monitoring"]
      - parameter: namespace
        pattern: "team-a-*"  # Regex/glob pattern

  - name: restrict-prometheus-queries
    match:
      tools: ["prometheus_query", "prometheus_instant_query"]
    rules:
      - parameter: query
        deny_patterns:
          - ".*kube_secret.*"  # Block secret-related metrics
          - ".*credentials.*"
```

### Implementation

```python
# holmes/core/policy.py
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import fnmatch
import re

@dataclass
class PolicyRule:
    parameter: str
    allow: Optional[List[str]] = None
    deny: Optional[List[str]] = None
    pattern: Optional[str] = None
    deny_patterns: Optional[List[str]] = None

@dataclass
class Policy:
    name: str
    match: Dict[str, List[str]]  # tools: ["kubectl_*"]
    rules: List[PolicyRule]

class PolicyEnforcer:
    def __init__(self, policies: List[Policy]):
        self.policies = policies

    def check(self, tool_name: str, params: Dict[str, Any]) -> PolicyResult:
        for policy in self.policies:
            if not self._matches_tool(tool_name, policy.match.get("tools", [])):
                continue

            for rule in policy.rules:
                param_value = params.get(rule.parameter)
                if param_value is None:
                    continue

                # Check deny list
                if rule.deny and param_value in rule.deny:
                    return PolicyResult(
                        allowed=False,
                        reason=f"Parameter '{rule.parameter}={param_value}' denied by policy '{policy.name}'"
                    )

                # Check allow list (if specified, value must be in list)
                if rule.allow and param_value not in rule.allow:
                    return PolicyResult(
                        allowed=False,
                        reason=f"Parameter '{rule.parameter}={param_value}' not in allowed values"
                    )

                # Check deny patterns
                if rule.deny_patterns:
                    for pattern in rule.deny_patterns:
                        if re.match(pattern, str(param_value)):
                            return PolicyResult(allowed=False, reason=f"Matches denied pattern")

        return PolicyResult(allowed=True)
```

### Integration Point

```python
# In tool_calling_llm.py, modify _directly_invoke_tool_call()
def _directly_invoke_tool_call(self, tool: Tool, params: Dict, ...) -> StructuredToolResult:
    # NEW: Policy enforcement
    policy_result = self.policy_enforcer.check(tool.name, params)
    if not policy_result.allowed:
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=f"Policy violation: {policy_result.reason}"
        )

    # Existing execution logic
    return tool.invoke(params)
```

### Concrete Example: Namespace Restriction

```yaml
# Policy: Only allow access to team-a namespaces
policies:
  - name: team-a-namespace-restriction
    match:
      tools: ["kubectl_*", "kubectl_get_*", "kubectl_describe", "kubectl_logs"]
    rules:
      - parameter: namespace
        allow: ["team-a-prod", "team-a-staging", "team-a-dev"]
```

**Result when LLM tries to access `kube-system`:**
```
Tool: kubectl_describe
Parameters: {kind: "pod", name: "coredns-xxx", namespace: "kube-system"}
Result: ERROR - Policy violation: Parameter 'namespace=kube-system' not in allowed values
```

### Trade-offs

| Pros | Cons |
|------|------|
| Simple to understand and configure | Limited expressiveness |
| Familiar YAML syntax | No cross-parameter validation |
| Fast evaluation (O(n) rules) | No context-aware decisions |
| Easy to audit | No inheritance/composition |
| No external dependencies | Static rules only |

---

## Design Option 2: Kyverno-Style Declarative Policies

**Inspired by:** Kyverno, Kubernetes admission controllers

### Architecture

Kyverno uses a rich declarative policy language with validation, mutation, and generation capabilities.

```yaml
# ~/.holmes/policies/namespace-policy.yaml
apiVersion: holmes.dev/v1
kind: ToolPolicy
metadata:
  name: restrict-production-namespaces
spec:
  match:
    tools:
      - pattern: "kubectl_*"
      - pattern: "kubernetes/*"
    context:
      # Only apply during investigation (not ask mode)
      mode: ["investigate"]
  validate:
    message: "Access to {{request.parameters.namespace}} namespace is restricted"
    rules:
      - name: check-namespace-allowlist
        match:
          parameters:
            namespace: "?*"  # Only when namespace is provided
        deny:
          conditions:
            any:
              - key: "{{request.parameters.namespace}}"
                operator: NotIn
                value: ["default", "app-*"]
              - key: "{{request.parameters.namespace}}"
                operator: AnyIn
                value: ["kube-system", "kube-public", "istio-system"]

      - name: check-resource-type
        deny:
          conditions:
            all:
              - key: "{{request.parameters.kind}}"
                operator: In
                value: ["secret", "configmap"]
              - key: "{{request.parameters.namespace}}"
                operator: Equals
                value: "production"
```

### Advanced Features

**1. Context-Aware Policies:**
```yaml
spec:
  context:
    - name: userRole
      variable:
        jmesPath: "request.context.user_role"
  validate:
    deny:
      conditions:
        - key: "{{userRole}}"
          operator: NotEquals
          value: "admin"
        - key: "{{request.parameters.namespace}}"
          operator: In
          value: ["production"]
```

**2. Mutation (Parameter Injection/Modification):**
```yaml
spec:
  mutate:
    # Force all kubectl commands to include resource limits
    patchStrategicMerge:
      parameters:
        # Add label selector to limit scope
        label_selector: "managed-by=holmes"
```

**3. Generation (Auto-generate parameters):**
```yaml
spec:
  generate:
    # Auto-add namespace if not provided, based on alert context
    rules:
      - name: default-namespace-from-alert
        match:
          parameters:
            namespace: null
        generate:
          parameters:
            namespace: "{{request.context.alert_namespace}}"
```

### Implementation

```python
# holmes/core/policy/kyverno_policy.py
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
import jmespath

class Condition(BaseModel):
    key: str  # Supports {{variable}} interpolation
    operator: str  # Equals, NotEquals, In, NotIn, AnyIn, AllIn, Gt, Lt, etc.
    value: Any

class DenyRule(BaseModel):
    conditions: Optional[Dict[str, List[Condition]]] = None  # any/all

class ValidationRule(BaseModel):
    name: str
    match: Optional[Dict] = None
    deny: Optional[DenyRule] = None

class PolicySpec(BaseModel):
    match: Dict
    validate: Optional[Dict] = None
    mutate: Optional[Dict] = None
    context: Optional[List[Dict]] = None

class KyvernoStylePolicy(BaseModel):
    apiVersion: str
    kind: str
    metadata: Dict
    spec: PolicySpec

class KyvernoPolicyEngine:
    def __init__(self):
        self.policies: List[KyvernoStylePolicy] = []

    def evaluate(self, tool_name: str, params: Dict, context: Dict) -> PolicyResult:
        request = {
            "tool_name": tool_name,
            "parameters": params,
            "context": context
        }

        for policy in self.policies:
            if not self._matches(policy.spec.match, request):
                continue

            # Evaluate validation rules
            if policy.spec.validate:
                result = self._evaluate_validation(policy, request)
                if not result.allowed:
                    return result

            # Apply mutations
            if policy.spec.mutate:
                params = self._apply_mutation(policy.spec.mutate, params)

        return PolicyResult(allowed=True, mutated_params=params)

    def _interpolate(self, template: str, request: Dict) -> str:
        """Replace {{path}} with actual values using JMESPath"""
        import re
        pattern = r'\{\{([^}]+)\}\}'

        def replacer(match):
            path = match.group(1).strip()
            if path.startswith("request."):
                path = path[8:]  # Remove "request." prefix
            return str(jmespath.search(path, request) or "")

        return re.sub(pattern, replacer, template)
```

### Concrete Example: Namespace Restriction

```yaml
apiVersion: holmes.dev/v1
kind: ToolPolicy
metadata:
  name: team-a-namespace-restriction
  annotations:
    description: "Restrict Holmes to team-a namespaces only"
spec:
  match:
    tools:
      - pattern: "kubectl_*"
      - pattern: "kubernetes/*"

  validate:
    message: "Access denied: namespace '{{request.parameters.namespace}}' is not accessible"
    rules:
      - name: enforce-team-namespace
        deny:
          conditions:
            all:
              - key: "{{request.parameters.namespace}}"
                operator: NotIn
                value:
                  - "team-a-prod"
                  - "team-a-staging"
                  - "team-a-dev"
              - key: "{{request.parameters.namespace}}"
                operator: NotMatch
                value: "team-a-*"

  # Also mutate: inject team label selector for list operations
  mutate:
    patchStrategicMerge:
      parameters:
        label_selector: "team=team-a"
```

### Trade-offs

| Pros | Cons |
|------|------|
| Rich, expressive policy language | Steeper learning curve |
| Familiar to K8s users | More complex implementation |
| Supports mutation & generation | Requires policy validation |
| Context-aware decisions | Debugging can be difficult |
| Composable policies | Performance overhead for complex rules |
| Audit trail capability | |

---

## Design Option 3: OPA/Rego Policy Engine

**Inspired by:** Open Policy Agent, used in K8s admission, Envoy, etc.

### Architecture

OPA provides a general-purpose policy engine with Rego, a declarative query language.

```rego
# policies/holmes/namespace_access.rego
package holmes.tools

import future.keywords.in
import future.keywords.if

# Default deny
default allow := false

# Allow if tool doesn't require namespace
allow if {
    not requires_namespace_check
}

# Allow if namespace is in permitted list
allow if {
    requires_namespace_check
    input.parameters.namespace in permitted_namespaces
}

# Allow if namespace matches permitted pattern
allow if {
    requires_namespace_check
    some pattern in permitted_namespace_patterns
    glob.match(pattern, [], input.parameters.namespace)
}

requires_namespace_check if {
    glob.match("kubectl_*", [], input.tool_name)
    input.parameters.namespace
}

requires_namespace_check if {
    startswith(input.tool_name, "kubernetes/")
    input.parameters.namespace
}

# Data: loaded from external source or embedded
permitted_namespaces := ["team-a-prod", "team-a-staging", "team-a-dev"]
permitted_namespace_patterns := ["team-a-*", "shared-*"]

# Detailed violation messages
violations[msg] if {
    not allow
    requires_namespace_check
    msg := sprintf("Namespace '%s' is not permitted for tool '%s'", [input.parameters.namespace, input.tool_name])
}
```

### Advanced Rego Policies

**1. Role-Based Access:**
```rego
package holmes.rbac

import future.keywords.in

# Role definitions (could be loaded from external data)
roles := {
    "admin": {
        "namespaces": ["*"],
        "tools": ["*"]
    },
    "developer": {
        "namespaces": ["dev-*", "staging-*"],
        "tools": ["kubectl_get_*", "kubectl_describe", "kubectl_logs"]
    },
    "viewer": {
        "namespaces": ["*"],
        "tools": ["kubectl_get_*"]
    }
}

default allow := false

allow if {
    role := roles[input.context.user_role]
    tool_allowed(role.tools, input.tool_name)
    namespace_allowed(role.namespaces, input.parameters.namespace)
}

tool_allowed(patterns, tool) if {
    some pattern in patterns
    pattern == "*"
}

tool_allowed(patterns, tool) if {
    some pattern in patterns
    glob.match(pattern, [], tool)
}

namespace_allowed(patterns, ns) if {
    some pattern in patterns
    pattern == "*"
}

namespace_allowed(patterns, ns) if {
    some pattern in patterns
    glob.match(pattern, [], ns)
}
```

**2. Time-Based Policies:**
```rego
package holmes.time_based

import future.keywords.if

default allow := false

# Allow production access only during business hours
allow if {
    not is_production_namespace
}

allow if {
    is_production_namespace
    is_business_hours
}

is_production_namespace if {
    startswith(input.parameters.namespace, "prod")
}

is_business_hours if {
    now := time.now_ns()
    [hour, _, _] := time.clock([now, "America/Los_Angeles"])
    hour >= 9
    hour < 17
}
```

### Implementation

```python
# holmes/core/policy/opa_policy.py
from typing import Dict, Any, Optional
import httpx
from pydantic import BaseModel

class OPAClient:
    """Client for OPA REST API or embedded OPA"""

    def __init__(self, opa_url: str = "http://localhost:8181"):
        self.opa_url = opa_url
        self.client = httpx.Client(timeout=5.0)

    def evaluate(self, policy_path: str, input_data: Dict[str, Any]) -> Dict:
        """Evaluate policy at given path with input data"""
        response = self.client.post(
            f"{self.opa_url}/v1/data/{policy_path}",
            json={"input": input_data}
        )
        response.raise_for_status()
        return response.json().get("result", {})

class OPAPolicyEnforcer:
    def __init__(self, opa_client: OPAClient):
        self.opa = opa_client

    def check(self, tool_name: str, params: Dict, context: Dict) -> PolicyResult:
        input_data = {
            "tool_name": tool_name,
            "parameters": params,
            "context": context,
            "timestamp": datetime.utcnow().isoformat()
        }

        result = self.opa.evaluate("holmes/tools", input_data)

        if result.get("allow", False):
            return PolicyResult(allowed=True)

        violations = result.get("violations", ["Policy check failed"])
        return PolicyResult(
            allowed=False,
            reason="; ".join(violations)
        )
```

### Embedded OPA (No External Service)

```python
# Using py-opa or rego-python for embedded evaluation
from regopy import Rego

class EmbeddedOPAEnforcer:
    def __init__(self, policy_dir: str):
        self.rego = Rego()
        self.rego.load_directory(policy_dir)

    def check(self, tool_name: str, params: Dict, context: Dict) -> PolicyResult:
        input_data = {"tool_name": tool_name, "parameters": params, "context": context}
        result = self.rego.query("data.holmes.tools.allow", input=input_data)
        # ... process result
```

### Concrete Example: Namespace Restriction

```rego
# policies/namespace_restriction.rego
package holmes.namespace

import future.keywords.in
import future.keywords.if

default allow := false

# Team A can only access team-a namespaces
allow if {
    input.context.team == "team-a"
    namespace_permitted_for_team("team-a", input.parameters.namespace)
}

# Define namespace permissions per team
namespace_permitted_for_team("team-a", ns) if {
    ns in ["team-a-prod", "team-a-staging", "team-a-dev"]
}

namespace_permitted_for_team("team-a", ns) if {
    startswith(ns, "team-a-")
}

# Generate helpful error message
message := msg if {
    not allow
    msg := sprintf(
        "Team '%s' is not authorized to access namespace '%s'. Permitted: team-a-*",
        [input.context.team, input.parameters.namespace]
    )
}
```

### Trade-offs

| Pros | Cons |
|------|------|
| Industry standard (CNCF graduated) | External dependency (OPA server) |
| Extremely powerful and flexible | Rego learning curve |
| Decoupled policy management | Additional operational overhead |
| Policy testing with `opa test` | Latency for external OPA |
| Rich ecosystem and tooling | Overkill for simple use cases |
| Data-driven policies | |
| Built-in policy bundling | |

---

## Design Option 4: Kubernetes-Native RBAC Integration

**Inspired by:** Kubernetes RBAC, ServiceAccounts, ClusterRoles

### Architecture

Leverage existing Kubernetes RBAC by running Holmes with specific ServiceAccount permissions.

```yaml
# k8s/holmes-rbac.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: holmes-team-a
  namespace: holmes
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: holmes-team-a-role
  namespace: team-a-prod
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log", "services", "events"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: holmes-team-a-binding
  namespace: team-a-prod
subjects:
  - kind: ServiceAccount
    name: holmes-team-a
    namespace: holmes
roleRef:
  kind: Role
  name: holmes-team-a-role
  apiGroup: rbac.authorization.k8s.io
```

### Multi-Tenant Configuration

```yaml
# Holmes deployment per team
apiVersion: apps/v1
kind: Deployment
metadata:
  name: holmes-team-a
  namespace: holmes
spec:
  template:
    spec:
      serviceAccountName: holmes-team-a
      containers:
        - name: holmes
          env:
            - name: HOLMES_TEAM
              value: "team-a"
            - name: ALLOWED_NAMESPACES
              value: "team-a-prod,team-a-staging,team-a-dev"
```

### Holmes-Side Enforcement

```python
# holmes/core/policy/k8s_rbac.py
from kubernetes import client, config

class K8sRBACEnforcer:
    """Enforces policies based on K8s RBAC of current service account"""

    def __init__(self):
        config.load_incluster_config()  # or load_kube_config()
        self.authz_api = client.AuthorizationV1Api()

    def check(self, tool_name: str, params: Dict) -> PolicyResult:
        """Check if current SA can perform the operation"""

        if not self._is_k8s_tool(tool_name):
            return PolicyResult(allowed=True)

        namespace = params.get("namespace", "default")
        resource = self._tool_to_resource(tool_name)
        verb = self._tool_to_verb(tool_name)

        # Use SelfSubjectAccessReview to check permissions
        review = client.V1SelfSubjectAccessReview(
            spec=client.V1SelfSubjectAccessReviewSpec(
                resource_attributes=client.V1ResourceAttributes(
                    namespace=namespace,
                    verb=verb,
                    resource=resource,
                    group=""
                )
            )
        )

        result = self.authz_api.create_self_subject_access_review(review)

        if result.status.allowed:
            return PolicyResult(allowed=True)

        return PolicyResult(
            allowed=False,
            reason=f"K8s RBAC denied: cannot {verb} {resource} in {namespace}"
        )

    def _tool_to_resource(self, tool_name: str) -> str:
        mappings = {
            "kubectl_get_pods": "pods",
            "kubectl_describe": "pods",  # or infer from params
            "kubectl_logs": "pods/log",
        }
        return mappings.get(tool_name, "pods")

    def _tool_to_verb(self, tool_name: str) -> str:
        if "get" in tool_name or "describe" in tool_name or "list" in tool_name:
            return "get"
        if "logs" in tool_name:
            return "get"
        return "get"
```

### Concrete Example: Namespace Restriction

The restriction happens at two levels:

**1. Kubernetes Level (Hard Enforcement):**
```yaml
# SA only has RoleBindings in team-a namespaces
# Kubernetes API will reject requests to other namespaces
```

**2. Holmes Level (Fast-Fail + Better UX):**
```python
# Pre-check before tool execution
ALLOWED_NAMESPACES = os.environ.get("ALLOWED_NAMESPACES", "").split(",")

class NamespacePreCheck:
    def check(self, tool_name: str, params: Dict) -> PolicyResult:
        ns = params.get("namespace")
        if ns and ALLOWED_NAMESPACES and ns not in ALLOWED_NAMESPACES:
            return PolicyResult(
                allowed=False,
                reason=f"Namespace '{ns}' not in allowed list. Permitted: {ALLOWED_NAMESPACES}"
            )
        return PolicyResult(allowed=True)
```

### Trade-offs

| Pros | Cons |
|------|------|
| Uses existing K8s security model | Only works for K8s tools |
| No new policy language to learn | Requires per-team deployments |
| Audited via K8s audit logs | Complex multi-namespace setup |
| Familiar to K8s admins | Doesn't cover non-K8s tools |
| Hard enforcement at API level | |
| Leverages existing SA/RBAC infra | |

---

## Design Option 5: Attribute-Based Access Control (ABAC)

**Inspired by:** AWS IAM policies, cloud provider ABAC systems

### Architecture

Policies evaluate attributes of the request, user, resource, and environment.

```yaml
# ~/.holmes/abac-policy.yaml
policies:
  - id: "namespace-access-policy"
    effect: "deny"
    description: "Deny access to system namespaces"
    condition:
      all:
        - attribute: "tool.category"
          operator: "equals"
          value: "kubernetes"
        - attribute: "params.namespace"
          operator: "in"
          value: ["kube-system", "kube-public", "istio-system"]

  - id: "team-namespace-policy"
    effect: "allow"
    description: "Allow team access to their namespaces"
    condition:
      all:
        - attribute: "context.team"
          operator: "equals"
          value: "team-a"
        - attribute: "params.namespace"
          operator: "matches"
          value: "^team-a-.*$"

  - id: "time-restricted-prod-access"
    effect: "deny"
    description: "Deny production access outside business hours"
    condition:
      all:
        - attribute: "params.namespace"
          operator: "starts_with"
          value: "prod-"
        - any:
            - attribute: "env.hour"
              operator: "less_than"
              value: 9
            - attribute: "env.hour"
              operator: "greater_than"
              value: 17
```

### Implementation

```python
# holmes/core/policy/abac.py
from typing import Any, Dict, List
from dataclasses import dataclass
from enum import Enum
import re

class Operator(Enum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"
    NOT_IN = "not_in"
    MATCHES = "matches"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    CONTAINS = "contains"
    LESS_THAN = "less_than"
    GREATER_THAN = "greater_than"

@dataclass
class Condition:
    attribute: str  # e.g., "params.namespace", "context.team", "env.hour"
    operator: Operator
    value: Any

@dataclass
class ABACPolicy:
    id: str
    effect: str  # "allow" or "deny"
    condition: Dict  # "all" or "any" with list of conditions
    description: str = ""

class ABACEngine:
    def __init__(self, policies: List[ABACPolicy]):
        self.policies = policies

    def evaluate(self, request: Dict) -> PolicyResult:
        """
        request = {
            "tool": {"name": "kubectl_describe", "category": "kubernetes"},
            "params": {"namespace": "team-a-prod", "kind": "pod"},
            "context": {"team": "team-a", "user_role": "developer"},
            "env": {"hour": 14, "day_of_week": "monday"}
        }
        """
        # Collect all matching policies
        deny_matched = []
        allow_matched = []

        for policy in self.policies:
            if self._evaluate_condition(policy.condition, request):
                if policy.effect == "deny":
                    deny_matched.append(policy)
                else:
                    allow_matched.append(policy)

        # Deny takes precedence
        if deny_matched:
            return PolicyResult(
                allowed=False,
                reason=f"Denied by policy: {deny_matched[0].id} - {deny_matched[0].description}"
            )

        # If no explicit allow and using allowlist mode
        if not allow_matched and self.require_explicit_allow:
            return PolicyResult(allowed=False, reason="No matching allow policy")

        return PolicyResult(allowed=True)

    def _evaluate_condition(self, condition: Dict, request: Dict) -> bool:
        if "all" in condition:
            return all(self._evaluate_single(c, request) for c in condition["all"])
        if "any" in condition:
            return any(self._evaluate_single(c, request) for c in condition["any"])
        return self._evaluate_single(condition, request)

    def _evaluate_single(self, cond: Dict, request: Dict) -> bool:
        # Handle nested any/all
        if "all" in cond or "any" in cond:
            return self._evaluate_condition(cond, request)

        attr_path = cond["attribute"]
        operator = Operator(cond["operator"])
        expected = cond["value"]

        actual = self._get_attribute(request, attr_path)

        return self._compare(actual, operator, expected)

    def _get_attribute(self, request: Dict, path: str) -> Any:
        """Navigate dot-notation path: 'params.namespace' -> request['params']['namespace']"""
        parts = path.split(".")
        value = request
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
        return value

    def _compare(self, actual: Any, operator: Operator, expected: Any) -> bool:
        if operator == Operator.EQUALS:
            return actual == expected
        elif operator == Operator.NOT_EQUALS:
            return actual != expected
        elif operator == Operator.IN:
            return actual in expected
        elif operator == Operator.NOT_IN:
            return actual not in expected
        elif operator == Operator.MATCHES:
            return bool(re.match(expected, str(actual or "")))
        elif operator == Operator.STARTS_WITH:
            return str(actual or "").startswith(expected)
        elif operator == Operator.ENDS_WITH:
            return str(actual or "").endswith(expected)
        elif operator == Operator.CONTAINS:
            return expected in str(actual or "")
        elif operator == Operator.LESS_THAN:
            return actual < expected
        elif operator == Operator.GREATER_THAN:
            return actual > expected
        return False
```

### Concrete Example: Namespace Restriction

```yaml
policies:
  # Deny system namespaces for everyone
  - id: "deny-system-namespaces"
    effect: "deny"
    condition:
      all:
        - attribute: "params.namespace"
          operator: "in"
          value: ["kube-system", "kube-public", "kube-node-lease"]

  # Team A: only their namespaces
  - id: "team-a-namespaces"
    effect: "allow"
    condition:
      all:
        - attribute: "context.team"
          operator: "equals"
          value: "team-a"
        - any:
            - attribute: "params.namespace"
              operator: "in"
              value: ["team-a-prod", "team-a-staging", "team-a-dev"]
            - attribute: "params.namespace"
              operator: "matches"
              value: "^team-a-.*$"
```

### Trade-offs

| Pros | Cons |
|------|------|
| Flexible attribute-based decisions | Custom implementation needed |
| Familiar to AWS/cloud users | No standard tooling |
| Good balance of power/simplicity | Less ecosystem support |
| Environment-aware (time, etc.) | Testing requires custom framework |
| Composable conditions | |

---

## Design Option 6: CEL (Common Expression Language)

**Inspired by:** Kubernetes admission policies, Google Cloud IAM conditions

### Architecture

CEL is a non-Turing-complete expression language designed for security policies.

```yaml
# ~/.holmes/cel-policies.yaml
policies:
  - name: restrict-namespaces
    match:
      tools: ["kubectl_*"]
    cel:
      expression: |
        !(params.namespace in ["kube-system", "kube-public"]) &&
        (params.namespace.startsWith("team-a-") || params.namespace in ["shared"])
      message: "Namespace access denied"

  - name: restrict-secrets-in-prod
    match:
      tools: ["kubectl_get_*", "kubectl_describe"]
    cel:
      expression: |
        !(params.kind == "secret" && params.namespace.startsWith("prod-"))
      message: "Cannot access secrets in production namespaces"

  - name: time-based-prod-access
    match:
      tools: ["kubectl_*"]
    cel:
      expression: |
        !params.namespace.startsWith("prod-") ||
        (timestamp.getHours() >= 9 && timestamp.getHours() < 17)
      message: "Production access only during business hours (9-17)"
```

### Implementation

```python
# holmes/core/policy/cel_policy.py
from typing import Dict, List, Any
import celpy
from celpy import celtypes

class CELPolicyEngine:
    def __init__(self, policies: List[Dict]):
        self.policies = policies
        self.env = celpy.Environment()
        self._compile_policies()

    def _compile_policies(self):
        """Pre-compile CEL expressions for performance"""
        for policy in self.policies:
            ast = self.env.compile(policy["cel"]["expression"])
            policy["_compiled"] = self.env.program(ast)

    def evaluate(self, tool_name: str, params: Dict, context: Dict) -> PolicyResult:
        for policy in self.policies:
            if not self._matches_tool(tool_name, policy["match"]["tools"]):
                continue

            # Build CEL activation (variables available to expression)
            activation = {
                "tool": celtypes.StringType(tool_name),
                "params": self._to_cel_map(params),
                "context": self._to_cel_map(context),
                "timestamp": celtypes.TimestampType(datetime.utcnow()),
            }

            result = policy["_compiled"].evaluate(activation)

            if not result:
                return PolicyResult(
                    allowed=False,
                    reason=policy["cel"].get("message", f"Policy '{policy['name']}' denied")
                )

        return PolicyResult(allowed=True)

    def _to_cel_map(self, d: Dict) -> celtypes.MapType:
        """Convert Python dict to CEL map type"""
        return celtypes.MapType({
            celtypes.StringType(k): self._to_cel_value(v)
            for k, v in (d or {}).items()
        })

    def _to_cel_value(self, v: Any) -> Any:
        if isinstance(v, str):
            return celtypes.StringType(v)
        elif isinstance(v, bool):
            return celtypes.BoolType(v)
        elif isinstance(v, int):
            return celtypes.IntType(v)
        elif isinstance(v, float):
            return celtypes.DoubleType(v)
        elif isinstance(v, list):
            return celtypes.ListType([self._to_cel_value(i) for i in v])
        elif isinstance(v, dict):
            return self._to_cel_map(v)
        return celtypes.StringType(str(v))
```

### Concrete Example: Namespace Restriction

```yaml
policies:
  - name: team-a-namespace-policy
    match:
      tools: ["kubectl_*", "kubernetes/*"]
    cel:
      expression: |
        // Allow if no namespace specified (cluster-scoped)
        !has(params.namespace) ||
        // Allow team-a namespaces
        params.namespace.startsWith("team-a-") ||
        params.namespace in ["team-a-prod", "team-a-staging", "team-a-dev"] ||
        // Allow shared namespaces
        params.namespace in ["shared-monitoring", "shared-logging"]
      message: |
        Namespace '${params.namespace}' is not accessible.
        Allowed: team-a-*, shared-monitoring, shared-logging
```

### Trade-offs

| Pros | Cons |
|------|------|
| Standard language (used by K8s) | Requires CEL library dependency |
| Non-Turing-complete (safe) | Learning curve for CEL syntax |
| Fast evaluation | Less readable than pure YAML |
| Type-safe expressions | Limited built-in functions |
| Familiar to K8s 1.26+ users | |
| Pre-compilation for performance | |

---

## Design Option 7: Hybrid Approach (Recommended)

### Architecture

Combine multiple approaches for flexibility and ease of use:

```
┌─────────────────────────────────────────────────────────────┐
│                    Policy Configuration                      │
├─────────────────────────────────────────────────────────────┤
│  Level 1: Simple YAML Rules (Fast Path)                     │
│  - Namespace allowlists/denylists                           │
│  - Tool enable/disable                                       │
│  - Parameter restrictions                                    │
├─────────────────────────────────────────────────────────────┤
│  Level 2: CEL Expressions (Medium Complexity)               │
│  - Conditional logic                                         │
│  - Cross-parameter validation                                │
│  - Time-based rules                                          │
├─────────────────────────────────────────────────────────────┤
│  Level 3: OPA/Rego (Advanced)                               │
│  - Complex business logic                                    │
│  - External data integration                                 │
│  - Enterprise policy management                              │
├─────────────────────────────────────────────────────────────┤
│  Level 4: K8s RBAC (Hard Enforcement)                       │
│  - ServiceAccount permissions                                │
│  - API-level enforcement                                     │
└─────────────────────────────────────────────────────────────┘
```

### Configuration

```yaml
# ~/.holmes/config.yaml
policy:
  # Quick configuration for common cases
  namespaces:
    allow: ["team-a-*", "shared-*"]
    deny: ["kube-system", "kube-public"]

  tools:
    disabled: ["bash/*"]  # Disable dangerous tools
    restricted: ["kubectl_exec", "kubectl_delete"]  # Require approval

  # CEL expressions for medium complexity
  cel_rules:
    - name: no-secrets-in-prod
      match: ["kubectl_get_*", "kubectl_describe"]
      expression: |
        !(params.kind == "secret" && params.namespace.startsWith("prod-"))

  # OPA for advanced cases (optional)
  opa:
    enabled: false
    url: "http://opa:8181"
    policy_path: "holmes/authz"
```

### Implementation

```python
# holmes/core/policy/hybrid.py
from typing import Dict, Optional, List
from dataclasses import dataclass, field

@dataclass
class PolicyConfig:
    namespaces: Dict = field(default_factory=dict)
    tools: Dict = field(default_factory=dict)
    cel_rules: List[Dict] = field(default_factory=list)
    opa: Dict = field(default_factory=dict)

class HybridPolicyEnforcer:
    def __init__(self, config: PolicyConfig):
        self.config = config
        self.cel_engine = CELPolicyEngine(config.cel_rules) if config.cel_rules else None
        self.opa_client = OPAClient(config.opa["url"]) if config.opa.get("enabled") else None

    def check(self, tool_name: str, params: Dict, context: Dict) -> PolicyResult:
        # Level 1: Quick YAML checks (fast path)
        result = self._check_simple_rules(tool_name, params)
        if not result.allowed:
            return result

        # Level 2: CEL expressions
        if self.cel_engine:
            result = self.cel_engine.evaluate(tool_name, params, context)
            if not result.allowed:
                return result

        # Level 3: OPA (if enabled)
        if self.opa_client:
            result = self._check_opa(tool_name, params, context)
            if not result.allowed:
                return result

        return PolicyResult(allowed=True)

    def _check_simple_rules(self, tool_name: str, params: Dict) -> PolicyResult:
        # Check disabled tools
        for pattern in self.config.tools.get("disabled", []):
            if fnmatch.fnmatch(tool_name, pattern):
                return PolicyResult(allowed=False, reason=f"Tool '{tool_name}' is disabled")

        # Check namespace restrictions
        namespace = params.get("namespace")
        if namespace:
            # Check deny list first
            for pattern in self.config.namespaces.get("deny", []):
                if fnmatch.fnmatch(namespace, pattern):
                    return PolicyResult(
                        allowed=False,
                        reason=f"Namespace '{namespace}' is denied"
                    )

            # Check allow list (if specified)
            allow_list = self.config.namespaces.get("allow", [])
            if allow_list:
                if not any(fnmatch.fnmatch(namespace, p) for p in allow_list):
                    return PolicyResult(
                        allowed=False,
                        reason=f"Namespace '{namespace}' not in allowed list"
                    )

        return PolicyResult(allowed=True)
```

### Concrete Example: Namespace Restriction (All Levels)

```yaml
# ~/.holmes/config.yaml
policy:
  # Level 1: Simple rules (covers 90% of cases)
  namespaces:
    allow: ["team-a-prod", "team-a-staging", "team-a-dev", "team-a-*"]
    deny: ["kube-system", "kube-public", "istio-system"]

  # Level 2: CEL for edge cases
  cel_rules:
    - name: prod-business-hours-only
      match: ["kubectl_*"]
      expression: |
        !params.namespace.startsWith("team-a-prod") ||
        (timestamp.getHours() >= 9 && timestamp.getHours() < 18)
      message: "Production access restricted to business hours"

    - name: no-exec-in-prod
      match: ["kubectl_exec"]
      expression: |
        !params.namespace.startsWith("team-a-prod")
      message: "kubectl exec disabled in production"

  # Level 3: OPA for complex scenarios (optional)
  opa:
    enabled: false
    url: "http://opa:8181"
```

---

## Comparison Matrix

| Feature | Simple YAML | Kyverno-Style | OPA/Rego | K8s RBAC | ABAC | CEL | Hybrid |
|---------|------------|---------------|----------|----------|------|-----|--------|
| **Learning Curve** | Low | Medium | High | Medium | Medium | Medium | Low-High |
| **Expressiveness** | Low | High | Very High | Medium | High | High | Very High |
| **Performance** | Fast | Medium | Medium | API call | Fast | Fast | Fast |
| **K8s Integration** | None | Good | Good | Native | None | Good | Good |
| **External Deps** | None | None | OPA Server | K8s API | None | CEL lib | Optional |
| **Audit Trail** | Manual | Built-in | Built-in | K8s Audit | Manual | Manual | Layered |
| **Hot Reload** | Yes | Yes | Yes | No | Yes | Yes | Yes |
| **Testing Tools** | Manual | Custom | opa test | kubectl | Custom | cel-go | Mixed |
| **MCP Support** | Easy | Easy | Easy | N/A | Easy | Easy | Easy |
| **Multi-Tenant** | Limited | Good | Excellent | Native | Good | Good | Excellent |

---

## Recommendations

### For Simple Deployments (Single Team)
**Use: Simple YAML Rules**
```yaml
policy:
  namespaces:
    allow: ["my-app-*"]
    deny: ["kube-system"]
```

### For Multi-Tenant SaaS
**Use: Hybrid (YAML + K8s RBAC)**
- Per-tenant Holmes deployments with dedicated ServiceAccounts
- Simple YAML for fast-path filtering
- K8s RBAC for hard enforcement

### For Enterprise with Existing OPA
**Use: Hybrid (YAML + OPA)**
- Simple YAML for common cases
- OPA for centralized policy management
- Integration with existing policy infrastructure

### For Kubernetes-Native Teams
**Use: CEL + K8s RBAC**
- CEL expressions (familiar from ValidatingAdmissionPolicy)
- K8s RBAC for actual enforcement
- Aligned with Kubernetes 1.26+ patterns

---

## Implementation Roadmap

### Phase 1: Foundation (MVP)
1. Add `PolicyEnforcer` interface
2. Implement Simple YAML rules
3. Add policy config to `~/.holmes/config.yaml`
4. Hook into `_directly_invoke_tool_call()`

### Phase 2: Enhanced Policies
1. Add CEL expression support
2. Add MCP tool policy support
3. Implement policy hot-reload
4. Add policy violation logging

### Phase 3: Enterprise Features
1. OPA integration (optional)
2. Policy testing framework
3. Audit trail and reporting
4. Multi-tenant isolation patterns

---

## Appendix: MCP Tool Policy Considerations

MCP tools present unique challenges:

1. **Dynamic Discovery**: Tools are discovered at runtime from MCP servers
2. **Unknown Parameters**: Parameter schemas come from external servers
3. **Trust Boundary**: MCP servers may be untrusted

### MCP-Specific Policies

```yaml
policy:
  mcp:
    # Allowlist of permitted MCP servers
    allowed_servers:
      - url: "https://trusted-mcp.example.com"
        tools: ["*"]  # All tools from this server
      - url: "https://semi-trusted.example.com"
        tools: ["read_*"]  # Only read tools

    # Default policy for MCP tools
    default: "deny"  # or "allow"

    # Parameter restrictions apply to MCP tools too
    parameter_rules:
      - match:
          server: "*"
          tool: "*"
        rules:
          - parameter: "path"
            deny_patterns: ["/etc/*", "/root/*", "~/*"]
```

### Implementation Notes

```python
class MCPPolicyEnforcer(PolicyEnforcer):
    def check_mcp_tool(self, server_url: str, tool_name: str, params: Dict) -> PolicyResult:
        # Check server allowlist
        if not self._is_server_allowed(server_url):
            return PolicyResult(allowed=False, reason=f"MCP server not in allowlist: {server_url}")

        # Check tool allowlist for this server
        if not self._is_tool_allowed(server_url, tool_name):
            return PolicyResult(allowed=False, reason=f"Tool not allowed from this server: {tool_name}")

        # Apply parameter rules
        return self._check_parameters(tool_name, params)
```
