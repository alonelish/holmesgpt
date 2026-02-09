"""Tests for the policy enforcement module."""

from holmes.core.policy import (
    AllowCondition,
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


class TestAllowCondition:
    """Tests for AllowCondition validation."""

    def test_python_only(self):
        cond = AllowCondition(python="True")
        assert cond.python == "True"
        assert cond.bash is None

    def test_bash_only(self):
        cond = AllowCondition(bash="echo ok")
        assert cond.bash == "echo ok"
        assert cond.python is None

    def test_neither_raises(self):
        import pytest

        with pytest.raises(ValueError, match="exactly one"):
            AllowCondition()

    def test_both_raises(self):
        import pytest

        with pytest.raises(ValueError, match="exactly one"):
            AllowCondition(python="True", bash="echo ok")


class TestPolicyEnforcer:
    """Tests for PolicyEnforcer."""

    def test_disabled_policy_allows_everything(self):
        config = PolicyConfig(enabled=False)
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("any_tool", {"any": "param"})
        assert result.allowed

    def test_empty_policy_allows_everything(self):
        """No rules = all tools allowed (default: allow)."""
        config = PolicyConfig(enabled=True, rules=[])
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("kubectl_get", {"namespace": "kube-system"})
        assert result.allowed

    def test_unmatched_tool_allowed(self):
        """Tools not matching any rule are allowed (default: allow)."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="kubectl-rules",
                    match=["kubectl_*"],
                    allow_if=AllowCondition(
                        python='params.get("namespace") != "blocked"'
                    ),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # prometheus_* doesn't match kubectl_* - allowed
        result = enforcer.check("prometheus_query", {"namespace": "blocked"})
        assert result.allowed

    def test_matched_tool_must_pass_allow_if(self):
        """Matched tools must pass 'allow_if' condition."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="allow-team-namespaces",
                    match=["kubectl_*"],
                    allow_if=AllowCondition(
                        python='params.get("namespace", "").startswith("team-a-")'
                    ),
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

    def test_python_false_blocks_tool(self):
        """Rules with allow_if python='False' block matched tools entirely."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="block-bash",
                    match=["bash/*"],
                    allow_if=AllowCondition(python="False"),
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
                    allow_if=AllowCondition(
                        python='params.get("namespace", "").startswith("team-a-")'
                    ),
                    message="Must be team-a namespace",
                ),
                PolicyRule(
                    name="no-secrets",
                    match=["kubectl_*"],
                    allow_if=AllowCondition(python='params.get("kind") != "secret"'),
                    message="Secrets not allowed",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Both pass
        result = enforcer.check(
            "kubectl_get", {"namespace": "team-a-prod", "kind": "pod"}
        )
        assert result.allowed

        # First fails
        result = enforcer.check(
            "kubectl_get", {"namespace": "team-b-prod", "kind": "pod"}
        )
        assert not result.allowed
        assert result.rule_name == "team-namespace"

        # Second fails
        result = enforcer.check(
            "kubectl_get", {"namespace": "team-a-prod", "kind": "secret"}
        )
        assert not result.allowed
        assert result.rule_name == "no-secrets"

        # Both fail (first one triggers)
        result = enforcer.check(
            "kubectl_get", {"namespace": "team-b-prod", "kind": "secret"}
        )
        assert not result.allowed
        assert result.rule_name == "team-namespace"

    def test_match_patterns(self):
        """Rules use fnmatch patterns for tool matching."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="k8s-tools",
                    match=["kubectl_*", "kubernetes/*"],
                    allow_if=AllowCondition(python='params.get("allowed") == True'),
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
        """Helper functions available in Python expressions."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="helpers-test",
                    match=["*"],
                    allow_if=AllowCondition(
                        python='startswith(params.get("ns", ""), "team-") and not contains(params.get("ns", ""), "secret")'
                    ),
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
                    allow_if=AllowCondition(
                        python='match("team-*-prod", params.get("namespace", ""))'
                    ),
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
                    allow_if=AllowCondition(
                        python=r'regex(r"^team-[a-z]+-\d+$", params.get("namespace", ""))'
                    ),
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
                    allow_if=AllowCondition(
                        python='not params.get("namespace", "").startswith("prod-") or context.get("role") == "admin"'
                    ),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Non-prod - anyone allowed
        result = enforcer.check(
            "kubectl_get", {"namespace": "staging"}, {"role": "developer"}
        )
        assert result.allowed

        # Prod as admin - allowed
        result = enforcer.check(
            "kubectl_get", {"namespace": "prod-us"}, {"role": "admin"}
        )
        assert result.allowed

        # Prod as non-admin - denied
        result = enforcer.check(
            "kubectl_get", {"namespace": "prod-us"}, {"role": "developer"}
        )
        assert not result.allowed

    def test_vars_in_rule(self):
        """Custom vars available in expressions."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="approved-namespaces",
                    match=["kubectl_*"],
                    allow_if=AllowCondition(
                        python='params.get("namespace") in approved_ns'
                    ),
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
                    allow_if=AllowCondition(python="undefined_variable == True"),
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
                    "allow_if": {
                        "python": 'params.get("namespace", "").startswith("team-a-") or params.get("namespace") is None'
                    },
                    "message": "Only team-a namespaces allowed",
                },
                {
                    "name": "no-secrets",
                    "match": ["kubectl_*"],
                    "allow_if": {"python": 'params.get("kind") != "secret"'},
                    "message": "Secrets not allowed",
                },
                {
                    "name": "block-bash",
                    "match": ["bash/*"],
                    "allow_if": {"python": "False"},
                },
            ],
        }

        config = PolicyConfig(**config_dict)
        enforcer = PolicyEnforcer(config)

        # Team-a namespace, not secret - allowed
        result = enforcer.check(
            "kubectl_get", {"namespace": "team-a-prod", "kind": "pod"}
        )
        assert result.allowed

        # No namespace (cluster-scoped) - allowed
        result = enforcer.check("kubectl_get", {"kind": "node"})
        assert result.allowed

        # Wrong namespace - denied
        result = enforcer.check(
            "kubectl_get", {"namespace": "team-b-prod", "kind": "pod"}
        )
        assert not result.allowed

        # Secret - denied
        result = enforcer.check(
            "kubectl_get", {"namespace": "team-a-prod", "kind": "secret"}
        )
        assert not result.allowed

        # Bash - blocked entirely
        result = enforcer.check("bash/run", {})
        assert not result.allowed

        # Unmatched tool - allowed
        result = enforcer.check("prometheus_query", {"query": "up"})
        assert result.allowed


class TestDefaultDeny:
    """Tests for default: deny (whitelist mode)."""

    def test_default_allow_is_default(self):
        """Default behavior is allow when no rules match."""
        config = PolicyConfig(rules=[])
        assert config.default == "allow"

        enforcer = PolicyEnforcer(config)
        result = enforcer.check("any_tool", {})
        assert result.allowed

    def test_default_deny_blocks_unmatched_tools(self):
        """With default: deny, unmatched tools are blocked."""
        config = PolicyConfig(
            default="deny",
            rules=[
                PolicyRule(
                    name="allow-prometheus",
                    match=["prometheus_*"],
                    allow_if=AllowCondition(python="True"),
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Matched tool with passing condition - allowed
        result = enforcer.check("prometheus_query", {"query": "up"})
        assert result.allowed

        # Unmatched tool - denied (default deny)
        result = enforcer.check("kubectl_get", {"namespace": "default"})
        assert not result.allowed
        assert "no matching rules" in result.message.lower()

    def test_default_deny_with_explicit_python_true(self):
        """Whitelist mode requires explicit allow_if python: 'True' to allow."""
        config = PolicyConfig(
            default="deny",
            rules=[
                PolicyRule(
                    name="allow-safe-tools",
                    match=["prometheus_*", "grafana_*"],
                    allow_if=AllowCondition(python="True"),
                ),
                PolicyRule(
                    name="allow-kubectl-read",
                    match=["kubectl_get_*", "kubectl_describe"],
                    allow_if=AllowCondition(python="True"),
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Allowed tools
        assert enforcer.check("prometheus_query", {}).allowed
        assert enforcer.check("grafana_list_dashboards", {}).allowed
        assert enforcer.check("kubectl_get_pods", {}).allowed
        assert enforcer.check("kubectl_describe", {}).allowed

        # Not in allowlist - denied
        assert not enforcer.check("kubectl_delete", {}).allowed
        assert not enforcer.check("bash/run_command", {}).allowed

    def test_default_deny_with_conditions(self):
        """Whitelist mode with additional conditions."""
        config = PolicyConfig(
            default="deny",
            rules=[
                PolicyRule(
                    name="allow-team-kubectl",
                    match=["kubectl_*"],
                    allow_if=AllowCondition(
                        python='params.get("namespace", "").startswith("team-a-")'
                    ),
                    message="Only team-a namespaces allowed",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Matched tool, condition passes - allowed
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod"})
        assert result.allowed

        # Matched tool, condition fails - denied
        result = enforcer.check("kubectl_get", {"namespace": "team-b-prod"})
        assert not result.allowed
        assert "team-a" in result.message

        # Unmatched tool - denied (default deny)
        result = enforcer.check("prometheus_query", {})
        assert not result.allowed
        assert "no matching rules" in result.message.lower()

    def test_default_deny_python_false_blocks(self):
        """With default: deny, allow_if python='False' blocks matched tools."""
        config = PolicyConfig(
            default="deny",
            rules=[
                PolicyRule(
                    name="allow-prometheus",
                    match=["prometheus_*"],
                    allow_if=AllowCondition(python="True"),
                ),
                PolicyRule(
                    name="block-dangerous",
                    match=["prometheus_delete_*"],
                    allow_if=AllowCondition(python="False"),
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Safe prometheus - allowed
        assert enforcer.check("prometheus_query", {}).allowed

        # Dangerous prometheus - matches both rules, second blocks
        result = enforcer.check("prometheus_delete_series", {})
        assert not result.allowed
        assert "block-dangerous" in result.rule_name

    def test_default_deny_multiple_rules_and_semantics(self):
        """With default: deny, multiple matching rules still use AND semantics."""
        config = PolicyConfig(
            default="deny",
            rules=[
                PolicyRule(
                    name="allow-kubectl",
                    match=["kubectl_*"],
                    allow_if=AllowCondition(python="True"),
                ),
                PolicyRule(
                    name="namespace-constraint",
                    match=["kubectl_*"],
                    allow_if=AllowCondition(
                        python='params.get("namespace", "").startswith("team-a-")'
                    ),
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Both rules match kubectl, both must pass
        result = enforcer.check("kubectl_get", {"namespace": "team-a-prod"})
        assert result.allowed

        # First passes (python: True), second fails (wrong namespace)
        result = enforcer.check("kubectl_get", {"namespace": "team-b-prod"})
        assert not result.allowed
        assert result.rule_name == "namespace-constraint"

        # Unmatched tool - denied (default deny)
        assert not enforcer.check("bash/run", {}).allowed


class TestBashConditions:
    """Tests for bash command conditions."""

    def test_bash_exit_zero_allows(self):
        """Bash command exiting 0 allows the tool call."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="always-allow",
                    match=["*"],
                    allow_if=AllowCondition(bash="exit 0"),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("any_tool", {})
        assert result.allowed

    def test_bash_exit_nonzero_denies(self):
        """Bash command exiting non-zero denies the tool call."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="always-deny",
                    match=["*"],
                    allow_if=AllowCondition(bash="exit 1"),
                    message="Always denied",
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("any_tool", {})
        assert not result.allowed
        assert "denied" in result.message.lower()

    def test_bash_template_params(self):
        """Bash templates can access params."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="check-namespace",
                    match=["*"],
                    allow_if=AllowCondition(
                        bash='[ "{{ params.namespace }}" = "allowed" ]'
                    ),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test", {"namespace": "allowed"})
        assert result.allowed

        result = enforcer.check("test", {"namespace": "denied"})
        assert not result.allowed

    def test_bash_template_context(self):
        """Bash templates can access context."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="check-user",
                    match=["*"],
                    allow_if=AllowCondition(bash='[ "{{ context.role }}" = "admin" ]'),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test", {}, {"role": "admin"})
        assert result.allowed

        result = enforcer.check("test", {}, {"role": "user"})
        assert not result.allowed

    def test_bash_template_default_filter(self):
        """Bash templates support default filter."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="with-default",
                    match=["*"],
                    allow_if=AllowCondition(
                        bash='[ "{{ params.ns | default:"fallback" }}" = "fallback" ]'
                    ),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Missing param uses default
        result = enforcer.check("test", {})
        assert result.allowed

        # Present param overrides default
        result = enforcer.check("test", {"ns": "other"})
        assert not result.allowed

    def test_bash_template_quote_filter(self):
        """Bash templates support quote filter for shell escaping."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="with-quote",
                    match=["*"],
                    allow_if=AllowCondition(bash="echo {{ params.value | quote }}"),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Value with special chars is safely quoted
        result = enforcer.check("test", {"value": "hello; rm -rf /"})
        assert result.allowed  # Command runs safely due to quoting

    def test_bash_stderr_as_message(self):
        """Bash stderr is captured as denial message."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="with-stderr",
                    match=["*"],
                    allow_if=AllowCondition(bash='echo "Custom error" >&2; exit 1'),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test", {})
        assert not result.allowed
        assert "Custom error" in result.message


class TestRealWorldScenarios:
    """Tests for realistic policy scenarios."""

    def test_namespace_restriction(self):
        """Restrict kubectl to specific namespaces."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="allowed-namespaces",
                    match=["kubectl_*", "kubernetes/*"],
                    allow_if=AllowCondition(
                        python='params.get("namespace") is None or params.get("namespace", "").startswith("team-a-") or params.get("namespace") in ["default", "monitoring"]'
                    ),
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
                    allow_if=AllowCondition(
                        python='params.get("kind", "").lower() not in blocked_kinds'
                    ),
                    vars={
                        "blocked_kinds": [
                            "secret",
                            "serviceaccount",
                            "clusterrole",
                            "clusterrolebinding",
                        ]
                    },
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
                    allow_if=AllowCondition(python="False"),
                    message="Bash commands disabled",
                ),
                PolicyRule(
                    name="block-exec",
                    match=["kubectl_exec"],
                    allow_if=AllowCondition(python="False"),
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
                    allow_if=AllowCondition(
                        python='not params.get("namespace", "").startswith("prod-") or context.get("role") == "admin"'
                    ),
                    message="Only admins can exec/delete in production",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Non-prod - anyone allowed
        result = enforcer.check(
            "kubectl_exec", {"namespace": "staging"}, {"role": "developer"}
        )
        assert result.allowed

        # Prod as admin - allowed
        result = enforcer.check(
            "kubectl_exec", {"namespace": "prod-us"}, {"role": "admin"}
        )
        assert result.allowed

        # Prod as developer - denied
        result = enforcer.check(
            "kubectl_exec", {"namespace": "prod-us"}, {"role": "developer"}
        )
        assert not result.allowed

        # kubectl_get not affected (no matching rule)
        result = enforcer.check(
            "kubectl_get", {"namespace": "prod-us"}, {"role": "developer"}
        )
        assert result.allowed

    def test_multi_tenant_isolation(self):
        """Teams can only access their own namespaces."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="tenant-isolation",
                    match=["kubectl_*"],
                    allow_if=AllowCondition(
                        python='params.get("namespace") is None or params.get("namespace", "").startswith(context.get("team", "") + "-") or params.get("namespace") in ["shared", "monitoring"]'
                    ),
                    message="You can only access your team's namespaces",
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Own namespace - allowed
        result = enforcer.check(
            "kubectl_get", {"namespace": "team-a-prod"}, {"team": "team-a"}
        )
        assert result.allowed

        # Shared namespace - allowed
        result = enforcer.check(
            "kubectl_get", {"namespace": "shared"}, {"team": "team-a"}
        )
        assert result.allowed

        # Other team's namespace - denied
        result = enforcer.check(
            "kubectl_get", {"namespace": "team-b-prod"}, {"team": "team-a"}
        )
        assert not result.allowed

    def test_layered_constraints(self):
        """Multiple rules act as layered constraints (AND)."""
        config = PolicyConfig(
            rules=[
                # Constraint 1: namespace
                PolicyRule(
                    name="namespace-constraint",
                    match=["kubectl_*"],
                    allow_if=AllowCondition(
                        python='params.get("namespace", "").startswith("team-a-")'
                    ),
                ),
                # Constraint 2: no secrets
                PolicyRule(
                    name="resource-constraint",
                    match=["kubectl_*"],
                    allow_if=AllowCondition(python='params.get("kind") != "secret"'),
                ),
                # Constraint 3: no exec
                PolicyRule(
                    name="no-exec",
                    match=["kubectl_exec"],
                    allow_if=AllowCondition(python="False"),
                ),
            ],
        )
        enforcer = PolicyEnforcer(config)

        # Passes all
        result = enforcer.check(
            "kubectl_get", {"namespace": "team-a-prod", "kind": "pod"}
        )
        assert result.allowed

        # Fails namespace
        result = enforcer.check(
            "kubectl_get", {"namespace": "team-b-prod", "kind": "pod"}
        )
        assert not result.allowed

        # Fails resource type
        result = enforcer.check(
            "kubectl_get", {"namespace": "team-a-prod", "kind": "secret"}
        )
        assert not result.allowed

        # Exec always blocked
        result = enforcer.check("kubectl_exec", {"namespace": "team-a-prod"})
        assert not result.allowed


class TestHttpHelpers:
    """Tests for HTTP helper functions."""

    def test_env_function(self, monkeypatch):
        """env() function retrieves environment variables."""
        monkeypatch.setenv("TEST_POLICY_VAR", "test_value")

        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="env-test",
                    match=["*"],
                    allow_if=AllowCondition(python='env("TEST_POLICY_VAR") == "test_value"'),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test_tool", {})
        assert result.allowed

    def test_env_function_default(self):
        """env() function returns default for missing variables."""
        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="env-default-test",
                    match=["*"],
                    allow_if=AllowCondition(
                        python='env("NONEXISTENT_VAR_12345", "fallback") == "fallback"'
                    ),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test_tool", {})
        assert result.allowed

    def test_http_get_success(self, responses):
        """http_get() makes GET request and returns JSON."""
        responses.add(
            responses.GET,
            "https://api.example.com/user",
            json={"id": "123", "name": "test"},
            status=200,
        )

        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="http-get-test",
                    match=["*"],
                    allow_if=AllowCondition(
                        python='http_get("https://api.example.com/user").get("id") == "123"'
                    ),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test_tool", {})
        assert result.allowed

    def test_http_get_with_params(self, responses):
        """http_get() passes query parameters."""
        responses.add(
            responses.GET,
            "https://api.example.com/search",
            json={"results": [{"email": "user@example.com"}]},
            status=200,
        )

        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="http-get-params-test",
                    match=["*"],
                    allow_if=AllowCondition(
                        python='len(http_get("https://api.example.com/search", params={"q": "test"}).get("results", [])) > 0'
                    ),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test_tool", {})
        assert result.allowed

    def test_http_get_failure_returns_empty(self, responses):
        """http_get() returns empty dict on failure."""
        responses.add(
            responses.GET,
            "https://api.example.com/error",
            status=500,
        )

        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="http-get-error-test",
                    match=["*"],
                    allow_if=AllowCondition(
                        python='http_get("https://api.example.com/error") == {}'
                    ),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test_tool", {})
        assert result.allowed

    def test_http_post_success(self, responses):
        """http_post() makes POST request and returns JSON."""
        responses.add(
            responses.POST,
            "https://api.example.com/check",
            json={"hasPermission": True},
            status=200,
        )

        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="http-post-test",
                    match=["*"],
                    allow_if=AllowCondition(
                        python='http_post("https://api.example.com/check", json_data={"user": "test"}).get("hasPermission", False)'
                    ),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test_tool", {})
        assert result.allowed

    def test_http_post_with_auth(self, responses):
        """http_post() supports basic auth."""
        responses.add(
            responses.POST,
            "https://api.example.com/secure",
            json={"allowed": True},
            status=200,
        )

        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="http-post-auth-test",
                    match=["*"],
                    allow_if=AllowCondition(
                        python='http_post("https://api.example.com/secure", json_data={}, auth=("user", "pass")).get("allowed", False)'
                    ),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check("test_tool", {})
        assert result.allowed

    def test_http_functions_with_context(self, responses):
        """HTTP functions can use context and params."""
        responses.add(
            responses.GET,
            "https://api.example.com/user",
            json={"accountId": "acc-123"},
            status=200,
        )
        responses.add(
            responses.POST,
            "https://api.example.com/permission",
            json={"hasPermission": True},
            status=200,
        )

        config = PolicyConfig(
            rules=[
                PolicyRule(
                    name="api-permission-check",
                    match=["*"],
                    allow_if=AllowCondition(
                        python='http_get("https://api.example.com/user", params={"email": context.get("user_email")}).get("accountId") and http_post("https://api.example.com/permission", json_data={"page_id": params.get("page_id")}).get("hasPermission", False)'
                    ),
                )
            ],
        )
        enforcer = PolicyEnforcer(config)

        result = enforcer.check(
            "confluence_get_page",
            {"page_id": "page-456"},
            {"user_email": "user@example.com"},
        )
        assert result.allowed
