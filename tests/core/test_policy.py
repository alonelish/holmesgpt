"""Tests for the policy enforcement module."""

from holmes.core.policy import PolicyConfig, PolicyEnforcer, PolicyResult, PolicyRule


class TestPolicyResult:
    """Tests for PolicyResult."""

    def test_allowed_result_is_truthy(self):
        result = PolicyResult(allowed=True)
        assert result
        assert bool(result) is True

    def test_denied_result_is_falsy(self):
        result = PolicyResult(allowed=False, message="denied")
        assert not result
        assert bool(result) is False


class TestPolicyEnforcer:
    """Tests for PolicyEnforcer."""

    def test_disabled_policy_allows_everything(self):
        config = PolicyConfig(enabled=False)
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("any_tool", {"any": "param"})
        assert result.allowed

    def test_empty_policy_allows_everything(self):
        config = PolicyConfig(enabled=True)
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert result.allowed

    def test_namespace_deny_list(self):
        config = PolicyConfig(
            enabled=True,
            namespaces={"deny": ["kube-system", "kube-public"]},
        )
        enforcer = PolicyEnforcer(config)

        # Denied namespace
        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert not result.allowed
        assert "denied" in result.message.lower()

        # Allowed namespace
        result = enforcer.check("kubectl_get", {"namespace": "default"})
        assert result.allowed

    def test_namespace_allow_list(self):
        config = PolicyConfig(
            enabled=True,
            namespaces={"allow": ["team-a-*", "default"]},
        )
        enforcer = PolicyEnforcer(config)

        # Allowed by exact match
        result = enforcer.check("kubectl_get", {"namespace": "default"})
        assert result.allowed

        # Allowed by glob pattern
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod"})
        assert result.allowed

        # Denied - not in allow list
        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert not result.allowed

    def test_namespace_allow_and_deny(self):
        config = PolicyConfig(
            enabled=True,
            namespaces={
                "allow": ["team-a-*"],
                "deny": ["team-a-secret"],  # Specific deny within allow
            },
        )
        enforcer = PolicyEnforcer(config)

        # Allowed by pattern
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod"})
        assert result.allowed

        # Denied by specific deny
        result = enforcer.check("kubectl_get", {"namespace": "team-a-secret"})
        assert not result.allowed

    def test_tool_deny_list(self):
        config = PolicyConfig(
            enabled=True,
            tools={"deny": ["bash/*", "kubectl_exec"]},
        )
        enforcer = PolicyEnforcer(config)

        # Denied by pattern
        result = enforcer.check("bash/run_command", {})
        assert not result.allowed

        # Denied by exact match
        result = enforcer.check("kubectl_exec", {})
        assert not result.allowed

        # Allowed
        result = enforcer.check("kubectl_get", {})
        assert result.allowed

    def test_tool_allow_list(self):
        config = PolicyConfig(
            enabled=True,
            tools={"allow": ["kubectl_get_*", "kubectl_describe"]},
        )
        enforcer = PolicyEnforcer(config)

        # Allowed by pattern
        result = enforcer.check("kubectl_get_pods", {})
        assert result.allowed

        # Allowed by exact match
        result = enforcer.check("kubectl_describe", {})
        assert result.allowed

        # Denied - not in allow list
        result = enforcer.check("kubectl_exec", {})
        assert not result.allowed

    def test_custom_rule_simple(self):
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="no-secrets",
                    match=["kubectl_*"],
                    expression='params.get("kind") != "secret"',
                    message="Cannot access secrets",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Allowed - not a secret
        result = enforcer.check("kubectl_get", {"kind": "pod", "namespace": "default"})
        assert result.allowed

        # Denied - is a secret
        result = enforcer.check("kubectl_get", {"kind": "secret", "namespace": "default"})
        assert not result.allowed
        assert "secret" in result.message.lower()

    def test_custom_rule_with_vars(self):
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="team-namespaces",
                    match=["kubectl_*"],
                    expression='params.get("namespace", "default") in allowed_ns',
                    vars={"allowed_ns": ["team-a-prod", "team-a-staging", "default"]},
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Allowed - in list
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod"})
        assert result.allowed

        # Allowed - default
        result = enforcer.check("kubectl_get", {})
        assert result.allowed

        # Denied - not in list
        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert not result.allowed

    def test_custom_rule_complex_expression(self):
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="prod-readonly",
                    match=["kubectl_*"],
                    expression='not params.get("namespace", "").startswith("prod-") or params.get("kind") in ["pod", "deployment", "service"]',
                    message="Only read-only resources allowed in production",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Allowed - not production
        result = enforcer.check("kubectl_get", {"namespace": "staging", "kind": "secret"})
        assert result.allowed

        # Allowed - production but readonly resource
        result = enforcer.check("kubectl_get", {"namespace": "prod-us", "kind": "pod"})
        assert result.allowed

        # Denied - production and sensitive resource
        result = enforcer.check("kubectl_get", {"namespace": "prod-us", "kind": "secret"})
        assert not result.allowed

    def test_multiple_rules_all_must_pass(self):
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="rule-1",
                    match=["*"],
                    expression='params.get("namespace") != "forbidden"',
                ),
                PolicyRule(
                    name="rule-2",
                    match=["*"],
                    expression='params.get("kind") != "secret"',
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Both rules pass
        result = enforcer.check("kubectl_get", {"namespace": "default", "kind": "pod"})
        assert result.allowed

        # First rule fails
        result = enforcer.check("kubectl_get", {"namespace": "forbidden", "kind": "pod"})
        assert not result.allowed
        assert result.rule_name == "rule-1"

        # Second rule fails
        result = enforcer.check("kubectl_get", {"namespace": "default", "kind": "secret"})
        assert not result.allowed
        assert result.rule_name == "rule-2"

    def test_rule_match_patterns(self):
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="k8s-only",
                    match=["kubectl_*", "kubernetes/*"],
                    expression='params.get("namespace") != "kube-system"',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Rule applies to kubectl_*
        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert not result.allowed

        # Rule applies to kubernetes/*
        result = enforcer.check("kubernetes/get_pods", {"namespace": "kube-system"})
        assert not result.allowed

        # Rule doesn't apply to other tools
        result = enforcer.check("prometheus_query", {"namespace": "kube-system"})
        assert result.allowed

    def test_helper_functions(self):
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="test-helpers",
                    match=["*"],
                    expression='startswith(params.get("namespace", ""), "team-") and not contains(params.get("namespace", ""), "secret") and endswith(params.get("name", ""), "-pod")',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # All conditions pass
        result = enforcer.check("test", {"namespace": "team-a", "name": "my-pod"})
        assert result.allowed

        # startswith fails
        result = enforcer.check("test", {"namespace": "other", "name": "my-pod"})
        assert not result.allowed

        # contains "secret" fails
        result = enforcer.check("test", {"namespace": "team-secret", "name": "my-pod"})
        assert not result.allowed

        # endswith fails
        result = enforcer.check("test", {"namespace": "team-a", "name": "my-svc"})
        assert not result.allowed

    def test_match_function_glob(self):
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="test-match",
                    match=["*"],
                    expression='match("team-*-prod", params.get("namespace", ""))',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test", {"namespace": "team-a-prod"})
        assert result.allowed

        result = enforcer.check("test", {"namespace": "team-b-staging"})
        assert not result.allowed

    def test_regex_function(self):
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="test-regex",
                    match=["*"],
                    expression=r'regex(r"^team-[a-z]+-\d+$", params.get("namespace", ""))',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test", {"namespace": "team-alpha-123"})
        assert result.allowed

        result = enforcer.check("test", {"namespace": "team-alpha-prod"})
        assert not result.allowed

    def test_no_namespace_param_allowed(self):
        """Tools without namespace param should be allowed by namespace rules."""
        config = PolicyConfig(
            enabled=True,
            namespaces={"allow": ["team-a-*"]},
        )
        enforcer = PolicyEnforcer(config)

        # No namespace param - should be allowed
        result = enforcer.check("prometheus_query", {"query": "up"})
        assert result.allowed

    def test_context_available_in_expression(self):
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="admin-only-prod",
                    match=["kubectl_*"],
                    expression='not params.get("namespace", "").startswith("prod-") or context.get("role") == "admin"',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Non-admin accessing non-prod - allowed
        result = enforcer.check(
            "kubectl_get",
            {"namespace": "staging"},
            context={"role": "developer"},
        )
        assert result.allowed

        # Admin accessing prod - allowed
        result = enforcer.check(
            "kubectl_get",
            {"namespace": "prod-us"},
            context={"role": "admin"},
        )
        assert result.allowed

        # Non-admin accessing prod - denied
        result = enforcer.check(
            "kubectl_get",
            {"namespace": "prod-us"},
            context={"role": "developer"},
        )
        assert not result.allowed

    def test_expression_error_denies_by_default(self):
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="bad-rule",
                    match=["*"],
                    expression="undefined_variable == True",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Expression error should deny
        result = enforcer.check("test", {})
        assert not result.allowed
        assert "error" in result.message.lower()


class TestPolicyConfigFromDict:
    """Tests for loading PolicyConfig from dict (as would come from YAML)."""

    def test_full_config(self):
        config_dict = {
            "enabled": True,
            "namespaces": {
                "allow": ["team-a-*", "default"],
                "deny": ["kube-system"],
            },
            "tools": {
                "deny": ["bash/*"],
            },
            "rules": [
                {
                    "name": "no-secrets-in-team-a-prod",
                    "match": ["kubectl_*"],
                    "expression": 'not (params.get("namespace", "") == "team-a-prod" and params.get("kind") == "secret")',
                    "message": "Cannot access secrets in team-a-prod",
                }
            ],
        }

        config = PolicyConfig(**config_dict)
        enforcer = PolicyEnforcer(config)

        # Test namespace allow
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod"})
        assert result.allowed

        # Test namespace allow by pattern
        result = enforcer.check("kubectl_get", {"namespace": "team-a-staging"})
        assert result.allowed

        # Test namespace deny
        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert not result.allowed

        # Test namespace not in allow list
        result = enforcer.check("kubectl_get", {"namespace": "other-ns"})
        assert not result.allowed

        # Test tool deny
        result = enforcer.check("bash/run", {})
        assert not result.allowed

        # Test custom rule - allowed (pod in team-a-prod)
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod", "kind": "pod"})
        assert result.allowed

        # Test custom rule - denied (secret in team-a-prod)
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod", "kind": "secret"})
        assert not result.allowed


class TestRealWorldScenarios:
    """Tests for realistic policy scenarios."""

    def test_multi_tenant_namespace_isolation(self):
        """Each team can only access their namespaces."""
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="tenant-isolation",
                    match=["kubectl_*", "kubernetes/*"],
                    expression='params.get("namespace") is None or params.get("namespace", "").startswith(context.get("team", "") + "-") or params.get("namespace") in ["shared", "monitoring"]',
                    message="Access denied: namespace does not belong to your team",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Team A accessing team-a namespace
        result = enforcer.check(
            "kubectl_get",
            {"namespace": "team-a-prod"},
            context={"team": "team-a"},
        )
        assert result.allowed

        # Team A accessing shared namespace
        result = enforcer.check(
            "kubectl_get",
            {"namespace": "shared"},
            context={"team": "team-a"},
        )
        assert result.allowed

        # Team A accessing team-b namespace - denied
        result = enforcer.check(
            "kubectl_get",
            {"namespace": "team-b-prod"},
            context={"team": "team-a"},
        )
        assert not result.allowed

    def test_readonly_production(self):
        """Production namespaces are read-only for non-admins."""
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="prod-readonly",
                    match=["kubectl_exec", "kubectl_delete", "kubectl_apply"],
                    expression='not params.get("namespace", "").startswith("prod-") or context.get("role") == "admin"',
                    message="Write operations not allowed in production",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Read operations always allowed (rule doesn't match)
        result = enforcer.check(
            "kubectl_get",
            {"namespace": "prod-us"},
            context={"role": "developer"},
        )
        assert result.allowed

        # Write in staging allowed
        result = enforcer.check(
            "kubectl_exec",
            {"namespace": "staging"},
            context={"role": "developer"},
        )
        assert result.allowed

        # Write in prod by developer - denied
        result = enforcer.check(
            "kubectl_exec",
            {"namespace": "prod-us"},
            context={"role": "developer"},
        )
        assert not result.allowed

        # Write in prod by admin - allowed
        result = enforcer.check(
            "kubectl_exec",
            {"namespace": "prod-us"},
            context={"role": "admin"},
        )
        assert result.allowed

    def test_deny_sensitive_resources(self):
        """Block access to sensitive Kubernetes resources."""
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="no-sensitive-resources",
                    match=["kubectl_*"],
                    expression='params.get("kind", "").lower() not in sensitive_kinds',
                    vars={
                        "sensitive_kinds": [
                            "secret",
                            "serviceaccount",
                            "clusterrole",
                            "clusterrolebinding",
                        ]
                    },
                    message="Access to sensitive resource types is denied",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Regular resources allowed
        result = enforcer.check("kubectl_get", {"kind": "pod"})
        assert result.allowed

        result = enforcer.check("kubectl_get", {"kind": "deployment"})
        assert result.allowed

        # Sensitive resources denied
        result = enforcer.check("kubectl_get", {"kind": "secret"})
        assert not result.allowed

        result = enforcer.check("kubectl_get", {"kind": "Secret"})  # Case insensitive
        assert not result.allowed

        result = enforcer.check("kubectl_get", {"kind": "clusterrole"})
        assert not result.allowed
