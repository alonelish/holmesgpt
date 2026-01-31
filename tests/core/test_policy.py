"""Tests for the policy enforcement module."""

from holmes.core.policy import (
    PolicyConfig,
    PolicyEffect,
    PolicyEnforcer,
    PolicyResult,
    PolicyRule,
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

    def test_empty_policy_with_default_allow(self):
        config = PolicyConfig(enabled=True, default="allow")
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert result.allowed

    def test_empty_policy_with_default_deny(self):
        config = PolicyConfig(enabled=True, default="deny")
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("kubectl_get", {"namespace": "anything"})
        assert not result.allowed

    def test_deny_rule_blocks_when_condition_true(self):
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="deny-system-namespaces",
                    effect=PolicyEffect.DENY,
                    match=["kubectl_*"],
                    when='params.get("namespace") in ["kube-system", "kube-public"]',
                    message="System namespaces are denied",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Should be denied
        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert not result.allowed
        assert "denied" in result.message.lower()

        # Should be allowed (no matching rule, default=allow)
        result = enforcer.check("kubectl_get", {"namespace": "default"})
        assert result.allowed

    def test_allow_rule_permits_when_condition_true(self):
        config = PolicyConfig(
            enabled=True,
            default="deny",  # Deny by default
            rules=[
                PolicyRule(
                    name="allow-team-namespaces",
                    effect=PolicyEffect.ALLOW,
                    match=["kubectl_*"],
                    when='params.get("namespace", "").startswith("team-a-")',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Should be allowed by rule
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod"})
        assert result.allowed

        # Should be denied (no matching rule, default=deny)
        result = enforcer.check("kubectl_get", {"namespace": "other-ns"})
        assert not result.allowed

    def test_first_matching_rule_wins(self):
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="deny-secrets",
                    effect=PolicyEffect.DENY,
                    match=["kubectl_*"],
                    when='params.get("kind") == "secret"',
                    message="Secrets are denied",
                ),
                PolicyRule(
                    name="allow-all-kubectl",
                    effect=PolicyEffect.ALLOW,
                    match=["kubectl_*"],
                    when="True",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # First rule matches - denied
        result = enforcer.check("kubectl_get", {"kind": "secret"})
        assert not result.allowed
        assert result.rule_name == "deny-secrets"

        # Second rule matches - allowed
        result = enforcer.check("kubectl_get", {"kind": "pod"})
        assert result.allowed
        assert result.rule_name == "allow-all-kubectl"

    def test_rule_match_patterns(self):
        config = PolicyConfig(
            enabled=True,
            default="deny",
            rules=[
                PolicyRule(
                    name="allow-kubectl",
                    effect=PolicyEffect.ALLOW,
                    match=["kubectl_*", "kubernetes/*"],
                    when="True",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Matches kubectl_*
        result = enforcer.check("kubectl_get", {})
        assert result.allowed

        # Matches kubernetes/*
        result = enforcer.check("kubernetes/get_pods", {})
        assert result.allowed

        # Doesn't match - denied by default
        result = enforcer.check("prometheus_query", {})
        assert not result.allowed

    def test_helper_functions(self):
        config = PolicyConfig(
            enabled=True,
            default="deny",
            rules=[
                PolicyRule(
                    name="allow-with-helpers",
                    effect=PolicyEffect.ALLOW,
                    match=["*"],
                    when='startswith(params.get("namespace", ""), "team-") and not contains(params.get("namespace", ""), "secret") and endswith(params.get("name", ""), "-pod")',
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
            default="deny",
            rules=[
                PolicyRule(
                    name="allow-pattern",
                    effect=PolicyEffect.ALLOW,
                    match=["*"],
                    when='match("team-*-prod", params.get("namespace", ""))',
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
            default="deny",
            rules=[
                PolicyRule(
                    name="allow-regex",
                    effect=PolicyEffect.ALLOW,
                    match=["*"],
                    when=r'regex(r"^team-[a-z]+-\d+$", params.get("namespace", ""))',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test", {"namespace": "team-alpha-123"})
        assert result.allowed

        result = enforcer.check("test", {"namespace": "team-alpha-prod"})
        assert not result.allowed

    def test_context_available_in_expression(self):
        config = PolicyConfig(
            enabled=True,
            default="deny",
            rules=[
                PolicyRule(
                    name="allow-admin-prod",
                    effect=PolicyEffect.ALLOW,
                    match=["kubectl_*"],
                    when='params.get("namespace", "").startswith("prod-") and context.get("role") == "admin"',
                ),
                PolicyRule(
                    name="allow-non-prod",
                    effect=PolicyEffect.ALLOW,
                    match=["kubectl_*"],
                    when='not params.get("namespace", "").startswith("prod-")',
                ),
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

    def test_vars_in_rule(self):
        config = PolicyConfig(
            enabled=True,
            default="deny",
            rules=[
                PolicyRule(
                    name="allow-approved-namespaces",
                    effect=PolicyEffect.ALLOW,
                    match=["kubectl_*"],
                    when='params.get("namespace") in approved_namespaces',
                    vars={"approved_namespaces": ["team-a-prod", "team-a-staging"]},
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod"})
        assert result.allowed

        result = enforcer.check("kubectl_get", {"namespace": "team-b-prod"})
        assert not result.allowed

    def test_expression_error_denies_by_default(self):
        config = PolicyConfig(
            enabled=True,
            rules=[
                PolicyRule(
                    name="bad-rule",
                    effect=PolicyEffect.ALLOW,
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
            "default": "deny",
            "rules": [
                {
                    "name": "deny-system-namespaces",
                    "effect": "deny",
                    "match": ["kubectl_*"],
                    "when": 'params.get("namespace") in ["kube-system", "kube-public"]',
                    "message": "System namespaces are denied",
                },
                {
                    "name": "allow-team-namespaces",
                    "effect": "allow",
                    "match": ["kubectl_*"],
                    "when": 'params.get("namespace", "").startswith("team-a-") or params.get("namespace") == "default"',
                },
                {
                    "name": "deny-secrets",
                    "effect": "deny",
                    "match": ["kubectl_*"],
                    "when": 'params.get("kind") == "secret"',
                    "message": "Secrets access denied",
                },
            ],
        }

        config = PolicyConfig(**config_dict)
        enforcer = PolicyEnforcer(config)

        # Test system namespace denied
        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert not result.allowed

        # Test team namespace allowed
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod"})
        assert result.allowed

        # Test default namespace allowed
        result = enforcer.check("kubectl_get", {"namespace": "default"})
        assert result.allowed

        # Test other namespace denied (no match, default=deny)
        result = enforcer.check("kubectl_get", {"namespace": "other-ns"})
        assert not result.allowed

        # Test secrets denied
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod", "kind": "secret"})
        assert not result.allowed


class TestRealWorldScenarios:
    """Tests for realistic policy scenarios."""

    def test_multi_tenant_namespace_isolation(self):
        """Each team can only access their namespaces."""
        config = PolicyConfig(
            enabled=True,
            default="deny",
            rules=[
                PolicyRule(
                    name="allow-own-namespaces",
                    effect=PolicyEffect.ALLOW,
                    match=["kubectl_*", "kubernetes/*"],
                    when='params.get("namespace", "").startswith(context.get("team", "") + "-")',
                ),
                PolicyRule(
                    name="allow-shared-namespaces",
                    effect=PolicyEffect.ALLOW,
                    match=["kubectl_*", "kubernetes/*"],
                    when='params.get("namespace") in ["shared", "monitoring"]',
                ),
                PolicyRule(
                    name="allow-no-namespace",
                    effect=PolicyEffect.ALLOW,
                    match=["kubectl_*", "kubernetes/*"],
                    when='params.get("namespace") is None',
                ),
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
            default="allow",  # Allow by default for read operations
            rules=[
                PolicyRule(
                    name="deny-write-prod-non-admin",
                    effect=PolicyEffect.DENY,
                    match=["kubectl_exec", "kubectl_delete", "kubectl_apply"],
                    when='params.get("namespace", "").startswith("prod-") and context.get("role") != "admin"',
                    message="Write operations not allowed in production",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Read operations always allowed (no matching rule)
        result = enforcer.check(
            "kubectl_get",
            {"namespace": "prod-us"},
            context={"role": "developer"},
        )
        assert result.allowed

        # Write in staging allowed (condition doesn't match)
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

        # Write in prod by admin - allowed (condition doesn't match)
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
            default="allow",
            rules=[
                PolicyRule(
                    name="deny-sensitive-resources",
                    effect=PolicyEffect.DENY,
                    match=["kubectl_*"],
                    when='params.get("kind", "").lower() in sensitive_kinds',
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

    def test_combined_allow_deny_rules(self):
        """Test typical setup with both allow and deny rules."""
        config = PolicyConfig(
            enabled=True,
            default="deny",  # Deny everything not explicitly allowed
            rules=[
                # First: deny dangerous operations
                PolicyRule(
                    name="deny-secrets",
                    effect=PolicyEffect.DENY,
                    match=["kubectl_*"],
                    when='params.get("kind") == "secret"',
                    message="Secret access is denied",
                ),
                # Then: allow team namespaces
                PolicyRule(
                    name="allow-team-a",
                    effect=PolicyEffect.ALLOW,
                    match=["kubectl_*"],
                    when='params.get("namespace", "").startswith("team-a-")',
                ),
                # Allow monitoring tools for everyone
                PolicyRule(
                    name="allow-prometheus",
                    effect=PolicyEffect.ALLOW,
                    match=["prometheus_*"],
                    when="True",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Team namespace access - allowed
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod", "kind": "pod"})
        assert result.allowed

        # Secrets in team namespace - denied (first rule wins)
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod", "kind": "secret"})
        assert not result.allowed

        # Other namespace - denied (no matching allow rule)
        result = enforcer.check("kubectl_get", {"namespace": "team-b-prod", "kind": "pod"})
        assert not result.allowed

        # Prometheus - allowed for everyone
        result = enforcer.check("prometheus_query", {"query": "up"})
        assert result.allowed
