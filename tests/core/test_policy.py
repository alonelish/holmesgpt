"""Tests for the policy enforcement module."""

from holmes.core.policy import (
    PolicyConfig,
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

    def test_empty_policy_allows_everything(self):
        """No rules = all tools allowed."""
        config = PolicyConfig(enabled=True, rules=[])
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert result.allowed

    def test_unmatched_tool_allowed(self):
        """Tools not matching any rule are allowed."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="kubectl-rules",
                    match=["kubectl_*"],
                    when='params.get("namespace") != "blocked"',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # prometheus_* doesn't match kubectl_* - allowed
        result = enforcer.check("prometheus_query", {"namespace": "blocked"})
        assert result.allowed

    def test_matched_tool_must_pass_when(self):
        """Matched tools must pass 'when' condition."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="allow-team-namespaces",
                    match=["kubectl_*"],
                    when='params.get("namespace", "").startswith("team-a-")',
                    message="Only team-a namespaces allowed",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Passes condition
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod"})
        assert result.allowed

        # Fails condition
        result = enforcer.check("kubectl_get", {"namespace": "team-b-prod"})
        assert not result.allowed
        assert "team-a" in result.message

    def test_omitted_when_blocks_tool(self):
        """Rules without 'when' block matched tools entirely."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="block-bash",
                    match=["bash/*"],
                    # no 'when' = always block
                    message="Bash is disabled",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("bash/run_command", {})
        assert not result.allowed
        assert "disabled" in result.message.lower()

        # Other tools still allowed
        result = enforcer.check("kubectl_get", {})
        assert result.allowed

    def test_multiple_rules_all_must_pass(self):
        """When multiple rules match, ALL must pass (AND semantics)."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="team-namespace",
                    match=["kubectl_*"],
                    when='params.get("namespace", "").startswith("team-a-")',
                    message="Must be team-a namespace",
                ),
                PolicyRule(
                    name="no-secrets",
                    match=["kubectl_*"],
                    when='params.get("kind") != "secret"',
                    message="Secrets not allowed",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Both pass
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod", "kind": "pod"})
        assert result.allowed

        # First fails
        result = enforcer.check("kubectl_get", {"namespace": "team-b-prod", "kind": "pod"})
        assert not result.allowed
        assert result.rule_name == "team-namespace"

        # Second fails
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod", "kind": "secret"})
        assert not result.allowed
        assert result.rule_name == "no-secrets"

        # Both fail (first one triggers)
        result = enforcer.check("kubectl_get", {"namespace": "team-b-prod", "kind": "secret"})
        assert not result.allowed
        assert result.rule_name == "team-namespace"

    def test_match_patterns(self):
        """Rules use fnmatch patterns for tool matching."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="k8s-tools",
                    match=["kubectl_*", "kubernetes/*"],
                    when='params.get("allowed") == True',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Matches kubectl_*
        result = enforcer.check("kubectl_get", {"allowed": True})
        assert result.allowed

        result = enforcer.check("kubectl_get", {"allowed": False})
        assert not result.allowed

        # Matches kubernetes/*
        result = enforcer.check("kubernetes/list_pods", {"allowed": True})
        assert result.allowed

        # Doesn't match - allowed without checking condition
        result = enforcer.check("prometheus_query", {"allowed": False})
        assert result.allowed

    def test_helper_functions(self):
        """Helper functions available in expressions."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="helpers-test",
                    match=["*"],
                    when='startswith(params.get("ns", ""), "team-") and not contains(params.get("ns", ""), "secret")',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test", {"ns": "team-a"})
        assert result.allowed

        result = enforcer.check("test", {"ns": "team-secret"})
        assert not result.allowed

        result = enforcer.check("test", {"ns": "other"})
        assert not result.allowed

    def test_match_function_glob(self):
        """match() function for glob patterns in expressions."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="glob-test",
                    match=["*"],
                    when='match("team-*-prod", params.get("namespace", ""))',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test", {"namespace": "team-a-prod"})
        assert result.allowed

        result = enforcer.check("test", {"namespace": "team-a-staging"})
        assert not result.allowed

    def test_regex_function(self):
        """regex() function for regex patterns in expressions."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="regex-test",
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

    def test_context_in_expression(self):
        """Context dict available in expressions."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="admin-only-prod",
                    match=["kubectl_*"],
                    when='not params.get("namespace", "").startswith("prod-") or context.get("role") == "admin"',
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Non-prod - anyone allowed
        result = enforcer.check("kubectl_get", {"namespace": "staging"}, {"role": "developer"})
        assert result.allowed

        # Prod as admin - allowed
        result = enforcer.check("kubectl_get", {"namespace": "prod-us"}, {"role": "admin"})
        assert result.allowed

        # Prod as non-admin - denied
        result = enforcer.check("kubectl_get", {"namespace": "prod-us"}, {"role": "developer"})
        assert not result.allowed

    def test_vars_in_rule(self):
        """Custom vars available in expressions."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="approved-namespaces",
                    match=["kubectl_*"],
                    when='params.get("namespace") in approved_ns',
                    vars={"approved_ns": ["team-a-prod", "team-a-staging", "default"]},
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod"})
        assert result.allowed

        result = enforcer.check("kubectl_get", {"namespace": "team-b-prod"})
        assert not result.allowed

    def test_expression_error_denies(self):
        """Expression errors result in denial."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="bad-rule",
                    match=["*"],
                    when="undefined_variable == True",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test", {})
        assert not result.allowed
        assert "error" in result.message.lower()


class TestPolicyConfigFromDict:
    """Tests for loading PolicyConfig from YAML-like dict."""

    def test_full_config(self):
        config_dict = {
            "enabled": True,
            "rules": [
                {
                    "name": "team-namespaces",
                    "match": ["kubectl_*"],
                    "when": 'params.get("namespace", "").startswith("team-a-") or params.get("namespace") is None',
                    "message": "Only team-a namespaces allowed",
                },
                {
                    "name": "no-secrets",
                    "match": ["kubectl_*"],
                    "when": 'params.get("kind") != "secret"',
                    "message": "Secrets not allowed",
                },
                {
                    "name": "block-bash",
                    "match": ["bash/*"],
                    # no 'when' - blocks entirely
                },
            ],
        }

        config = PolicyConfig(**config_dict)
        enforcer = PolicyEnforcer(config)

        # Team-a namespace, not secret - allowed
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod", "kind": "pod"})
        assert result.allowed

        # No namespace (cluster-scoped) - allowed
        result = enforcer.check("kubectl_get", {"kind": "node"})
        assert result.allowed

        # Wrong namespace - denied
        result = enforcer.check("kubectl_get", {"namespace": "team-b-prod", "kind": "pod"})
        assert not result.allowed

        # Secret - denied
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod", "kind": "secret"})
        assert not result.allowed

        # Bash - blocked entirely
        result = enforcer.check("bash/run", {})
        assert not result.allowed

        # Unmatched tool - allowed
        result = enforcer.check("prometheus_query", {"query": "up"})
        assert result.allowed


class TestRealWorldScenarios:
    """Tests for realistic policy scenarios."""

    def test_namespace_restriction(self):
        """Restrict kubectl to specific namespaces."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="allowed-namespaces",
                    match=["kubectl_*", "kubernetes/*"],
                    when='params.get("namespace") is None or params.get("namespace", "").startswith("team-a-") or params.get("namespace") in ["default", "monitoring"]',
                    message="Namespace not allowed",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Team namespace - allowed
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod"})
        assert result.allowed

        # Shared namespace - allowed
        result = enforcer.check("kubectl_get", {"namespace": "monitoring"})
        assert result.allowed

        # No namespace (cluster-scoped) - allowed
        result = enforcer.check("kubectl_get", {"kind": "node"})
        assert result.allowed

        # System namespace - denied
        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert not result.allowed

        # Other team - denied
        result = enforcer.check("kubectl_get", {"namespace": "team-b-prod"})
        assert not result.allowed

    def test_sensitive_resources(self):
        """Block access to sensitive resource types."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="no-sensitive-resources",
                    match=["kubectl_*"],
                    when='params.get("kind", "").lower() not in blocked_kinds',
                    vars={"blocked_kinds": ["secret", "serviceaccount", "clusterrole", "clusterrolebinding"]},
                    message="Sensitive resource type blocked",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Normal resources - allowed
        result = enforcer.check("kubectl_get", {"kind": "pod"})
        assert result.allowed

        result = enforcer.check("kubectl_get", {"kind": "deployment"})
        assert result.allowed

        # Sensitive resources - denied
        result = enforcer.check("kubectl_get", {"kind": "secret"})
        assert not result.allowed

        result = enforcer.check("kubectl_get", {"kind": "Secret"})  # case insensitive
        assert not result.allowed

        result = enforcer.check("kubectl_get", {"kind": "clusterrole"})
        assert not result.allowed

    def test_block_dangerous_tools(self):
        """Block dangerous tools entirely."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="block-bash",
                    match=["bash/*"],
                    message="Bash commands disabled",
                ),
                PolicyRule(
                    name="block-exec",
                    match=["kubectl_exec"],
                    message="kubectl exec disabled",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Blocked tools
        result = enforcer.check("bash/run_command", {})
        assert not result.allowed

        result = enforcer.check("kubectl_exec", {"namespace": "default"})
        assert not result.allowed

        # Other kubectl - allowed (no matching rule)
        result = enforcer.check("kubectl_get", {"namespace": "default"})
        assert result.allowed

    def test_production_restrictions(self):
        """Extra restrictions for production namespaces."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="prod-admin-only",
                    match=["kubectl_exec", "kubectl_delete"],
                    when='not params.get("namespace", "").startswith("prod-") or context.get("role") == "admin"',
                    message="Only admins can exec/delete in production",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Non-prod - anyone allowed
        result = enforcer.check("kubectl_exec", {"namespace": "staging"}, {"role": "developer"})
        assert result.allowed

        # Prod as admin - allowed
        result = enforcer.check("kubectl_exec", {"namespace": "prod-us"}, {"role": "admin"})
        assert result.allowed

        # Prod as developer - denied
        result = enforcer.check("kubectl_exec", {"namespace": "prod-us"}, {"role": "developer"})
        assert not result.allowed

        # kubectl_get not affected (no matching rule)
        result = enforcer.check("kubectl_get", {"namespace": "prod-us"}, {"role": "developer"})
        assert result.allowed

    def test_multi_tenant_isolation(self):
        """Teams can only access their own namespaces."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="tenant-isolation",
                    match=["kubectl_*"],
                    when='params.get("namespace") is None or params.get("namespace", "").startswith(context.get("team", "") + "-") or params.get("namespace") in ["shared", "monitoring"]',
                    message="You can only access your team's namespaces",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Own namespace - allowed
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod"}, {"team": "team-a"})
        assert result.allowed

        # Shared namespace - allowed
        result = enforcer.check("kubectl_get", {"namespace": "shared"}, {"team": "team-a"})
        assert result.allowed

        # Other team's namespace - denied
        result = enforcer.check("kubectl_get", {"namespace": "team-b-prod"}, {"team": "team-a"})
        assert not result.allowed

    def test_layered_constraints(self):
        """Multiple rules act as layered constraints (AND)."""
        config = PolicyConfig(
            rules=[
                # Constraint 1: namespace
                PolicyRule(
                    name="namespace-constraint",
                    match=["kubectl_*"],
                    when='params.get("namespace", "").startswith("team-a-")',
                ),
                # Constraint 2: no secrets
                PolicyRule(
                    name="resource-constraint",
                    match=["kubectl_*"],
                    when='params.get("kind") != "secret"',
                ),
                # Constraint 3: no exec
                PolicyRule(
                    name="no-exec",
                    match=["kubectl_exec"],
                    # no when = always block
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Passes all
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod", "kind": "pod"})
        assert result.allowed

        # Fails namespace
        result = enforcer.check("kubectl_get", {"namespace": "team-b-prod", "kind": "pod"})
        assert not result.allowed

        # Fails resource type
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod", "kind": "secret"})
        assert not result.allowed

        # Exec always blocked
        result = enforcer.check("kubectl_exec", {"namespace": "team-a-prod"})
        assert not result.allowed
