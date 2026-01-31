"""Tests for the policy enforcement module."""

from holmes.core.policy import (
    DenyRule,
    PolicyConfig,
    PolicyEnforcer,
    PolicyResult,
)


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
        config = PolicyConfig(enabled=True, deny=[])
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert result.allowed

    def test_deny_rule_with_when_condition(self):
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="block-system-namespaces",
                    match=["kubectl_*"],
                    when='params.get("namespace") in ["kube-system", "kube-public"]',
                    message="System namespaces are restricted",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Should be denied
        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert not result.allowed
        assert "restricted" in result.message.lower()

        # Should be allowed (condition not met)
        result = enforcer.check("kubectl_get", {"namespace": "default"})
        assert result.allowed

    def test_deny_rule_without_when_blocks_tool(self):
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="block-dangerous-tools",
                    match=["bash/*", "kubectl_exec"],
                    # no 'when' = always deny
                    message="Dangerous tool blocked",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Should be denied (matches pattern)
        result = enforcer.check("bash/run_command", {})
        assert not result.allowed

        result = enforcer.check("kubectl_exec", {})
        assert not result.allowed

        # Should be allowed (doesn't match)
        result = enforcer.check("kubectl_get", {})
        assert result.allowed

    def test_first_matching_deny_wins(self):
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="block-secrets",
                    match=["kubectl_*"],
                    when='params.get("kind") == "secret"',
                    message="Secrets blocked",
                ),
                DenyRule(
                    name="block-system-ns",
                    match=["kubectl_*"],
                    when='params.get("namespace") == "kube-system"',
                    message="System namespace blocked",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # First rule matches
        result = enforcer.check("kubectl_get", {"kind": "secret", "namespace": "default"})
        assert not result.allowed
        assert result.rule_name == "block-secrets"

        # Second rule matches
        result = enforcer.check("kubectl_get", {"kind": "pod", "namespace": "kube-system"})
        assert not result.allowed
        assert result.rule_name == "block-system-ns"

        # Neither matches
        result = enforcer.check("kubectl_get", {"kind": "pod", "namespace": "default"})
        assert result.allowed

    def test_match_patterns(self):
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="block-k8s-tools",
                    match=["kubectl_*", "kubernetes/*"],
                    when='params.get("namespace") == "blocked"',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Matches kubectl_*
        result = enforcer.check("kubectl_get", {"namespace": "blocked"})
        assert not result.allowed

        # Matches kubernetes/*
        result = enforcer.check("kubernetes/get_pods", {"namespace": "blocked"})
        assert not result.allowed

        # Doesn't match pattern - allowed even with blocked namespace
        result = enforcer.check("prometheus_query", {"namespace": "blocked"})
        assert result.allowed

    def test_helper_functions(self):
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="block-with-helpers",
                    match=["*"],
                    when='startswith(params.get("namespace", ""), "prod-") and contains(params.get("name", ""), "secret")',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Both conditions met - denied
        result = enforcer.check("test", {"namespace": "prod-us", "name": "my-secret-config"})
        assert not result.allowed

        # Only first condition met - allowed
        result = enforcer.check("test", {"namespace": "prod-us", "name": "my-config"})
        assert result.allowed

        # Only second condition met - allowed
        result = enforcer.check("test", {"namespace": "staging", "name": "my-secret-config"})
        assert result.allowed

    def test_match_function_glob(self):
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="block-pattern",
                    match=["*"],
                    when='match("*-prod", params.get("namespace", ""))',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test", {"namespace": "team-a-prod"})
        assert not result.allowed

        result = enforcer.check("test", {"namespace": "team-a-staging"})
        assert result.allowed

    def test_regex_function(self):
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="block-regex",
                    match=["*"],
                    when=r'regex(r"^prod-[0-9]+$", params.get("namespace", ""))',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test", {"namespace": "prod-123"})
        assert not result.allowed

        result = enforcer.check("test", {"namespace": "prod-abc"})
        assert result.allowed

    def test_context_available_in_expression(self):
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="block-non-admin-prod",
                    match=["kubectl_*"],
                    when='params.get("namespace", "").startswith("prod-") and context.get("role") != "admin"',
                    message="Non-admins cannot access production",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Non-admin accessing prod - denied
        result = enforcer.check(
            "kubectl_get",
            {"namespace": "prod-us"},
            context={"role": "developer"},
        )
        assert not result.allowed

        # Admin accessing prod - allowed
        result = enforcer.check(
            "kubectl_get",
            {"namespace": "prod-us"},
            context={"role": "admin"},
        )
        assert result.allowed

        # Non-admin accessing non-prod - allowed
        result = enforcer.check(
            "kubectl_get",
            {"namespace": "staging"},
            context={"role": "developer"},
        )
        assert result.allowed

    def test_vars_in_rule(self):
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="block-sensitive-kinds",
                    match=["kubectl_*"],
                    when='params.get("kind", "").lower() in sensitive_kinds',
                    vars={"sensitive_kinds": ["secret", "configmap", "serviceaccount"]},
                    message="Sensitive resource blocked",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("kubectl_get", {"kind": "secret"})
        assert not result.allowed

        result = enforcer.check("kubectl_get", {"kind": "Secret"})  # case insensitive
        assert not result.allowed

        result = enforcer.check("kubectl_get", {"kind": "pod"})
        assert result.allowed

    def test_expression_error_denies_by_default(self):
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="bad-rule",
                    match=["*"],
                    when="undefined_variable == True",
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
            "deny": [
                {
                    "name": "block-system-namespaces",
                    "match": ["kubectl_*"],
                    "when": 'params.get("namespace") in ["kube-system", "kube-public"]',
                    "message": "System namespaces are restricted",
                },
                {
                    "name": "block-secrets",
                    "match": ["kubectl_*"],
                    "when": 'params.get("kind") == "secret"',
                    "message": "Secrets are restricted",
                },
                {
                    "name": "block-bash",
                    "match": ["bash/*"],
                    # no 'when' - always block
                },
            ],
        }

        config = PolicyConfig(**config_dict)
        enforcer = PolicyEnforcer(config)

        # System namespace blocked
        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert not result.allowed

        # Secrets blocked
        result = enforcer.check("kubectl_get", {"namespace": "default", "kind": "secret"})
        assert not result.allowed

        # Bash blocked
        result = enforcer.check("bash/run", {})
        assert not result.allowed

        # Normal access allowed
        result = enforcer.check("kubectl_get", {"namespace": "default", "kind": "pod"})
        assert result.allowed


class TestRealWorldScenarios:
    """Tests for realistic policy scenarios."""

    def test_namespace_restrictions(self):
        """Restrict access to system and sensitive namespaces."""
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="system-namespaces",
                    match=["kubectl_*", "kubernetes/*"],
                    when='params.get("namespace") in ["kube-system", "kube-public", "kube-node-lease"]',
                    message="System namespaces are restricted",
                ),
                DenyRule(
                    name="istio-namespaces",
                    match=["kubectl_*", "kubernetes/*"],
                    when='params.get("namespace", "").startswith("istio-")',
                    message="Istio namespaces are restricted",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # System namespace - denied
        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert not result.allowed

        # Istio namespace - denied
        result = enforcer.check("kubectl_get", {"namespace": "istio-system"})
        assert not result.allowed

        # App namespace - allowed
        result = enforcer.check("kubectl_get", {"namespace": "my-app"})
        assert result.allowed

    def test_sensitive_resources(self):
        """Block access to sensitive Kubernetes resources."""
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="sensitive-resources",
                    match=["kubectl_*"],
                    when='params.get("kind", "").lower() in sensitive_kinds',
                    vars={
                        "sensitive_kinds": [
                            "secret",
                            "serviceaccount",
                            "clusterrole",
                            "clusterrolebinding",
                            "role",
                            "rolebinding",
                        ]
                    },
                    message="Access to sensitive resource types is restricted",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Sensitive resources - denied
        result = enforcer.check("kubectl_get", {"kind": "secret"})
        assert not result.allowed

        result = enforcer.check("kubectl_get", {"kind": "ClusterRole"})
        assert not result.allowed

        # Normal resources - allowed
        result = enforcer.check("kubectl_get", {"kind": "pod"})
        assert result.allowed

        result = enforcer.check("kubectl_get", {"kind": "deployment"})
        assert result.allowed

    def test_dangerous_tools(self):
        """Block dangerous tools entirely."""
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="block-bash",
                    match=["bash/*"],
                    message="Bash commands are disabled",
                ),
                DenyRule(
                    name="block-write-operations",
                    match=["kubectl_exec", "kubectl_delete", "kubectl_apply", "kubectl_patch"],
                    message="Write operations are disabled",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Bash - denied
        result = enforcer.check("bash/run_command", {"command": "ls"})
        assert not result.allowed

        # Write operations - denied
        result = enforcer.check("kubectl_exec", {"namespace": "default"})
        assert not result.allowed

        result = enforcer.check("kubectl_delete", {"namespace": "default"})
        assert not result.allowed

        # Read operations - allowed
        result = enforcer.check("kubectl_get", {"namespace": "default"})
        assert result.allowed

        result = enforcer.check("kubectl_describe", {"namespace": "default"})
        assert result.allowed

    def test_production_restrictions(self):
        """Extra restrictions for production namespaces."""
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="no-exec-in-prod",
                    match=["kubectl_exec"],
                    when='params.get("namespace", "").startswith("prod-")',
                    message="kubectl exec is disabled in production",
                ),
                DenyRule(
                    name="no-logs-in-prod-for-non-admin",
                    match=["kubectl_logs"],
                    when='params.get("namespace", "").startswith("prod-") and context.get("role") != "admin"',
                    message="Only admins can view logs in production",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Exec in prod - always denied
        result = enforcer.check(
            "kubectl_exec",
            {"namespace": "prod-us"},
            context={"role": "admin"},
        )
        assert not result.allowed

        # Logs in prod as non-admin - denied
        result = enforcer.check(
            "kubectl_logs",
            {"namespace": "prod-us"},
            context={"role": "developer"},
        )
        assert not result.allowed

        # Logs in prod as admin - allowed
        result = enforcer.check(
            "kubectl_logs",
            {"namespace": "prod-us"},
            context={"role": "admin"},
        )
        assert result.allowed

        # Exec in non-prod - allowed
        result = enforcer.check(
            "kubectl_exec",
            {"namespace": "staging"},
            context={"role": "developer"},
        )
        assert result.allowed

    def test_multi_tenant_isolation(self):
        """Teams can only access their own namespaces."""
        config = PolicyConfig(
            deny=[
                DenyRule(
                    name="tenant-isolation",
                    match=["kubectl_*", "kubernetes/*"],
                    when='params.get("namespace") is not None and not params.get("namespace", "").startswith(context.get("team", "") + "-") and params.get("namespace") not in ["shared", "monitoring"]',
                    message="You can only access your team's namespaces",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Team A accessing team-a namespace - allowed
        result = enforcer.check(
            "kubectl_get",
            {"namespace": "team-a-prod"},
            context={"team": "team-a"},
        )
        assert result.allowed

        # Team A accessing shared namespace - allowed
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
