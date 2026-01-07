"""
Configuration management for AI-Alertmanager.
"""

import os
from typing import Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class AIAlertmanagerConfig(BaseSettings):
    """
    Configuration for AI-Alertmanager.

    All settings can be configured via environment variables.
    """

    # Server settings
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=9093, description="Server port (Alertmanager default)")

    # HolmesGPT settings
    holmes_url: str = Field(
        default="http://localhost:8080",
        description="HolmesGPT server URL"
    )
    holmes_timeout: int = Field(
        default=300,
        description="HolmesGPT request timeout in seconds"
    )

    # AI Investigation settings
    enable_ai_investigation: bool = Field(
        default=True,
        description="Enable AI-powered investigation of alerts"
    )
    investigation_label_key: str = Field(
        default="ai_investigation",
        description="Label key to store AI investigation results"
    )
    investigation_label_status_key: str = Field(
        default="ai_investigation_status",
        description="Label key to store investigation status"
    )
    investigate_on_create: bool = Field(
        default=True,
        description="Automatically investigate alerts when they are created"
    )
    investigation_concurrency: int = Field(
        default=5,
        description="Maximum number of concurrent investigations"
    )

    # Alertmanager compatibility settings
    alertmanager_version: str = Field(
        default="0.27.0",
        description="Alertmanager version to emulate"
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Log level (DEBUG, INFO, WARNING, ERROR)"
    )

    class Config:
        env_prefix = "AI_ALERTMANAGER_"
        case_sensitive = False


def load_config() -> AIAlertmanagerConfig:
    """
    Load configuration from environment variables.

    Returns:
        Loaded configuration
    """
    return AIAlertmanagerConfig()
