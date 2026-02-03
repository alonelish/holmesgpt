"""Tests for ToolsetConfig base class and deprecated field mappings."""

import logging
from typing import ClassVar, Dict, Optional

import pytest
from pydantic import Field

from holmes.utils.pydantic_utils import ToolsetConfig


class SampleConfig(ToolsetConfig):
    """Sample config class for testing deprecated mappings."""

    _deprecated_mappings: ClassVar[Dict[str, Optional[str]]] = {
        "old_field": "new_field",
        "another_old": "another_new",
        "removed_field": None,
    }

    new_field: str = Field(default="default_value")
    another_new: int = Field(default=10)
    unchanged_field: str = Field(default="unchanged")


class TestToolsetConfig:
    """Tests for ToolsetConfig base class."""

    def test_new_field_names_work(self):
        """Test that new field names work without warnings."""
        config = SampleConfig(new_field="test", another_new=20)
        assert config.new_field == "test"
        assert config.another_new == 20

    def test_deprecated_field_name_migrated(self, caplog):
        """Test that deprecated field names are migrated to new names."""
        with caplog.at_level(logging.WARNING):
            config = SampleConfig(old_field="migrated_value")

        assert config.new_field == "migrated_value"
        assert "old_field -> new_field" in caplog.text

    def test_multiple_deprecated_fields(self, caplog):
        """Test that multiple deprecated fields are migrated."""
        with caplog.at_level(logging.WARNING):
            config = SampleConfig(old_field="value1", another_old=42)

        assert config.new_field == "value1"
        assert config.another_new == 42
        assert "old_field -> new_field" in caplog.text
        assert "another_old -> another_new" in caplog.text

    def test_new_field_takes_precedence(self, caplog):
        """Test that new field takes precedence over deprecated field."""
        with caplog.at_level(logging.WARNING):
            config = SampleConfig(old_field="old_value", new_field="new_value")

        # New field should take precedence
        assert config.new_field == "new_value"

    def test_removed_field_logged(self, caplog):
        """Test that removed fields are logged but not cause errors."""
        with caplog.at_level(logging.WARNING):
            config = SampleConfig(removed_field="some_value")

        # Config should still be valid
        assert config.new_field == "default_value"
        assert "removed_field (removed)" in caplog.text

    def test_no_warning_for_new_fields(self, caplog):
        """Test that using new field names doesn't trigger warnings."""
        with caplog.at_level(logging.WARNING):
            config = SampleConfig(new_field="test", another_new=5)

        assert "deprecated" not in caplog.text.lower()

    def test_extra_fields_allowed(self):
        """Test that extra fields are allowed (for forward compatibility)."""
        config = SampleConfig(new_field="test", unknown_future_field="value")
        assert config.new_field == "test"

    def test_unchanged_field_works(self):
        """Test that fields without deprecation mappings work normally."""
        config = SampleConfig(unchanged_field="custom")
        assert config.unchanged_field == "custom"


class TestPrometheusConfigBackwardCompatibility:
    """Test backward compatibility for PrometheusConfig deprecated fields."""

    def test_deprecated_prometheus_fields(self, caplog):
        """Test that deprecated Prometheus config fields are migrated."""
        from holmes.plugins.toolsets.prometheus.prometheus import PrometheusConfig

        with caplog.at_level(logging.WARNING):
            config = PrometheusConfig(
                prometheus_url="http://prometheus:9090",
                default_query_timeout_seconds=45,
                prometheus_ssl_enabled=False,
                headers={"X-Custom": "value"},
            )

        assert str(config.api_url).rstrip("/") == "http://prometheus:9090"
        assert config.query_timeout_seconds_default == 45
        assert config.verify_ssl is False
        assert config.additional_headers == {"X-Custom": "value"}
        assert "prometheus_url -> api_url" in caplog.text
        assert "default_query_timeout_seconds -> query_timeout_seconds_default" in caplog.text
        assert "prometheus_ssl_enabled -> verify_ssl" in caplog.text
        assert "headers -> additional_headers" in caplog.text

    def test_new_prometheus_fields_no_warning(self, caplog):
        """Test that new Prometheus field names don't trigger warnings."""
        from holmes.plugins.toolsets.prometheus.prometheus import PrometheusConfig

        with caplog.at_level(logging.WARNING):
            config = PrometheusConfig(
                api_url="http://prometheus:9090",
                query_timeout_seconds_default=30,
                verify_ssl=True,
                additional_headers={"X-Custom": "value"},
            )

        assert str(config.api_url).rstrip("/") == "http://prometheus:9090"
        assert config.query_timeout_seconds_default == 30
        assert config.additional_headers == {"X-Custom": "value"}
        assert "deprecated" not in caplog.text.lower()

    def test_old_and_new_prometheus_configs_match(self):
        """Test that configs created with old and new field names produce equivalent results."""
        from holmes.plugins.toolsets.prometheus.prometheus import PrometheusConfig

        old_config = PrometheusConfig(
            prometheus_url="http://prometheus:9090",
            headers={"X-Custom": "value"},
        )
        new_config = PrometheusConfig(
            api_url="http://prometheus:9090",
            additional_headers={"X-Custom": "value"},
        )

        assert old_config.api_url == new_config.api_url
        assert old_config.additional_headers == new_config.additional_headers


class TestDatadogConfigBackwardCompatibility:
    """Test backward compatibility for DatadogBaseConfig deprecated fields."""

    def test_deprecated_datadog_fields(self, caplog):
        """Test that deprecated Datadog config fields are migrated."""
        from holmes.plugins.toolsets.datadog.datadog_api import DatadogBaseConfig

        with caplog.at_level(logging.WARNING):
            config = DatadogBaseConfig(
                dd_api_key="test_api_key",
                dd_app_key="test_app_key",
                site_api_url="https://api.datadoghq.com",
                request_timeout=120,
            )

        assert config.api_key == "test_api_key"
        assert config.app_key == "test_app_key"
        assert str(config.api_url) == "https://api.datadoghq.com/"
        assert config.timeout_seconds == 120
        assert "dd_api_key -> api_key" in caplog.text
        assert "dd_app_key -> app_key" in caplog.text
        assert "site_api_url -> api_url" in caplog.text
        assert "request_timeout -> timeout_seconds" in caplog.text

    def test_old_and_new_datadog_configs_match(self):
        """Test that configs created with old and new field names produce equivalent results."""
        from holmes.plugins.toolsets.datadog.datadog_api import DatadogBaseConfig

        old_config = DatadogBaseConfig(
            dd_api_key="test_key",
            dd_app_key="test_app",
            site_api_url="https://api.datadoghq.com",
            request_timeout=60,
        )
        new_config = DatadogBaseConfig(
            api_key="test_key",
            app_key="test_app",
            api_url="https://api.datadoghq.com",
            timeout_seconds=60,
        )

        assert old_config.api_key == new_config.api_key
        assert old_config.app_key == new_config.app_key
        assert old_config.api_url == new_config.api_url
        assert old_config.timeout_seconds == new_config.timeout_seconds


class TestNewRelicConfigBackwardCompatibility:
    """Test backward compatibility for NewrelicConfig deprecated fields."""

    def test_deprecated_newrelic_fields(self, caplog):
        """Test that deprecated NewRelic config fields are migrated."""
        from holmes.plugins.toolsets.newrelic.newrelic import NewrelicConfig

        with caplog.at_level(logging.WARNING):
            config = NewrelicConfig(
                nr_api_key="NRAK-TEST123",
                nr_account_id="1234567",
            )

        assert config.api_key == "NRAK-TEST123"
        assert config.account_id == "1234567"
        assert "nr_api_key -> api_key" in caplog.text
        assert "nr_account_id -> account_id" in caplog.text

    def test_old_and_new_newrelic_configs_match(self):
        """Test that configs created with old and new field names produce equivalent results."""
        from holmes.plugins.toolsets.newrelic.newrelic import NewrelicConfig

        old_config = NewrelicConfig(
            nr_api_key="NRAK-TEST123",
            nr_account_id="1234567",
        )
        new_config = NewrelicConfig(
            api_key="NRAK-TEST123",
            account_id="1234567",
        )

        assert old_config.api_key == new_config.api_key
        assert old_config.account_id == new_config.account_id


class TestElasticsearchConfigBackwardCompatibility:
    """Test backward compatibility for ElasticsearchConfig deprecated fields."""

    def test_deprecated_elasticsearch_fields(self, caplog):
        """Test that deprecated Elasticsearch config fields are migrated."""
        from holmes.plugins.toolsets.elasticsearch.elasticsearch import ElasticsearchConfig

        with caplog.at_level(logging.WARNING):
            config = ElasticsearchConfig(
                url="https://elasticsearch:9200",
                timeout=30,
            )

        assert config.api_url == "https://elasticsearch:9200"
        assert config.timeout_seconds == 30
        assert "url -> api_url" in caplog.text
        assert "timeout -> timeout_seconds" in caplog.text

    def test_old_and_new_elasticsearch_configs_match(self):
        """Test that configs created with old and new field names produce equivalent results."""
        from holmes.plugins.toolsets.elasticsearch.elasticsearch import ElasticsearchConfig

        old_config = ElasticsearchConfig(
            url="https://elasticsearch:9200",
            timeout=30,
        )
        new_config = ElasticsearchConfig(
            api_url="https://elasticsearch:9200",
            timeout_seconds=30,
        )

        assert old_config.api_url == new_config.api_url
        assert old_config.timeout_seconds == new_config.timeout_seconds


class TestRabbitMQConfigBackwardCompatibility:
    """Test backward compatibility for RabbitMQClusterConfig deprecated fields."""

    def test_deprecated_rabbitmq_fields(self, caplog):
        """Test that deprecated RabbitMQ config fields are migrated."""
        from holmes.plugins.toolsets.rabbitmq.api import RabbitMQClusterConfig

        with caplog.at_level(logging.WARNING):
            config = RabbitMQClusterConfig(
                management_url="http://rabbitmq:15672",
                request_timeout_seconds=60,
                verify_certs=False,
            )

        assert config.api_url == "http://rabbitmq:15672"
        assert config.timeout_seconds == 60
        assert config.verify_ssl is False
        assert "management_url -> api_url" in caplog.text
        assert "request_timeout_seconds -> timeout_seconds" in caplog.text
        assert "verify_certs -> verify_ssl" in caplog.text

    def test_old_and_new_rabbitmq_configs_match(self):
        """Test that configs created with old and new field names produce equivalent results."""
        from holmes.plugins.toolsets.rabbitmq.api import RabbitMQClusterConfig

        old_config = RabbitMQClusterConfig(
            management_url="http://rabbitmq:15672",
            request_timeout_seconds=30,
        )
        new_config = RabbitMQClusterConfig(
            api_url="http://rabbitmq:15672",
            timeout_seconds=30,
        )

        assert old_config.api_url == new_config.api_url
        assert old_config.timeout_seconds == new_config.timeout_seconds


class TestKafkaConfigBackwardCompatibility:
    """Test backward compatibility for KafkaClusterConfig deprecated fields."""

    def test_deprecated_kafka_cluster_fields(self, caplog):
        """Test that deprecated Kafka cluster config fields are migrated."""
        from holmes.plugins.toolsets.kafka import KafkaClusterConfig

        with caplog.at_level(logging.WARNING):
            config = KafkaClusterConfig(
                name="test-cluster",
                kafka_broker="broker:9092",
                kafka_security_protocol="SASL_SSL",
                kafka_sasl_mechanism="PLAIN",
                kafka_username="user",
                kafka_password="pass",
                kafka_client_id="test-client",
            )

        assert config.broker == "broker:9092"
        assert config.security_protocol == "SASL_SSL"
        assert config.sasl_mechanism == "PLAIN"
        assert config.username == "user"
        assert config.password == "pass"
        assert config.client_id == "test-client"
        assert "kafka_broker -> broker" in caplog.text
        assert "kafka_security_protocol -> security_protocol" in caplog.text
        assert "kafka_sasl_mechanism -> sasl_mechanism" in caplog.text
        assert "kafka_username -> username" in caplog.text
        assert "kafka_password -> password" in caplog.text
        assert "kafka_client_id -> client_id" in caplog.text

    def test_deprecated_kafka_config_fields(self, caplog):
        """Test that deprecated KafkaConfig fields are migrated."""
        from holmes.plugins.toolsets.kafka import KafkaClusterConfig, KafkaConfig

        with caplog.at_level(logging.WARNING):
            config = KafkaConfig(
                kafka_clusters=[
                    KafkaClusterConfig(name="test", broker="broker:9092")
                ]
            )

        assert len(config.clusters) == 1
        assert config.clusters[0].name == "test"
        assert "kafka_clusters -> clusters" in caplog.text

    def test_old_and_new_kafka_configs_match(self):
        """Test that configs created with old and new field names produce equivalent results."""
        from holmes.plugins.toolsets.kafka import KafkaClusterConfig

        old_config = KafkaClusterConfig(
            name="test",
            kafka_broker="broker:9092",
            kafka_client_id="client",
        )
        new_config = KafkaClusterConfig(
            name="test",
            broker="broker:9092",
            client_id="client",
        )

        assert old_config.broker == new_config.broker
        assert old_config.client_id == new_config.client_id


class TestGrafanaConfigBackwardCompatibility:
    """Test backward compatibility for GrafanaConfig deprecated fields."""

    def test_deprecated_grafana_fields(self, caplog):
        """Test that deprecated Grafana config fields are migrated."""
        from holmes.plugins.toolsets.grafana.common import GrafanaConfig

        with caplog.at_level(logging.WARNING):
            config = GrafanaConfig(
                url="http://grafana:3000",
                headers={"Authorization": "Bearer token"},
            )

        assert config.api_url == "http://grafana:3000"
        assert config.additional_headers == {"Authorization": "Bearer token"}
        assert "url -> api_url" in caplog.text
        assert "headers -> additional_headers" in caplog.text

    def test_old_and_new_grafana_configs_match(self):
        """Test that configs created with old and new field names produce equivalent results."""
        from holmes.plugins.toolsets.grafana.common import GrafanaConfig

        old_config = GrafanaConfig(
            url="http://grafana:3000",
            headers={"X-Custom": "value"},
        )
        new_config = GrafanaConfig(
            api_url="http://grafana:3000",
            additional_headers={"X-Custom": "value"},
        )

        assert old_config.api_url == new_config.api_url
        assert old_config.additional_headers == new_config.additional_headers


class TestServiceNowConfigBackwardCompatibility:
    """Test backward compatibility for ServiceNowTablesConfig deprecated fields."""

    def test_deprecated_servicenow_fields(self, caplog):
        """Test that deprecated ServiceNow config fields are migrated."""
        from holmes.plugins.toolsets.servicenow_tables.servicenow_tables import ServiceNowTablesConfig

        with caplog.at_level(logging.WARNING):
            config = ServiceNowTablesConfig(
                api_key="test_key",
                instance_url="https://instance.service-now.com",
            )

        assert config.api_url == "https://instance.service-now.com"
        assert "instance_url -> api_url" in caplog.text

    def test_old_and_new_servicenow_configs_match(self):
        """Test that configs created with old and new field names produce equivalent results."""
        from holmes.plugins.toolsets.servicenow_tables.servicenow_tables import ServiceNowTablesConfig

        old_config = ServiceNowTablesConfig(
            api_key="test_key",
            instance_url="https://instance.service-now.com",
        )
        new_config = ServiceNowTablesConfig(
            api_key="test_key",
            api_url="https://instance.service-now.com",
        )

        assert old_config.api_url == new_config.api_url


class TestMCPConfigBackwardCompatibility:
    """Test backward compatibility for MCPConfig deprecated fields."""

    def test_deprecated_mcp_fields(self, caplog):
        """Test that deprecated MCP config fields are migrated."""
        from holmes.plugins.toolsets.mcp.toolset_mcp import MCPConfig

        with caplog.at_level(logging.WARNING):
            config = MCPConfig(
                url="http://mcp-server:8000/mcp",
                headers={"Authorization": "Bearer token"},
            )

        assert str(config.api_url) == "http://mcp-server:8000/mcp"
        assert config.additional_headers == {"Authorization": "Bearer token"}
        assert "url -> api_url" in caplog.text
        assert "headers -> additional_headers" in caplog.text

    def test_old_and_new_mcp_configs_match(self):
        """Test that configs created with old and new field names produce equivalent results."""
        from holmes.plugins.toolsets.mcp.toolset_mcp import MCPConfig

        old_config = MCPConfig(
            url="http://mcp-server:8000/mcp",
            headers={"X-Custom": "value"},
        )
        new_config = MCPConfig(
            api_url="http://mcp-server:8000/mcp",
            additional_headers={"X-Custom": "value"},
        )

        assert old_config.api_url == new_config.api_url
        assert old_config.additional_headers == new_config.additional_headers
