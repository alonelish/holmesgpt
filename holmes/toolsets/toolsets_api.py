import logging
import threading

import yaml
from fastapi import FastAPI, HTTPException

from holmes.config import Config
from holmes.core.models import (
    HolmesToolsetConfig,
    ValidateToolsetRequest,
    ValidateToolsetResponse,
    ValidateToolsetResult,
)
from holmes.core.tools import ToolsetStatusEnum, ToolsetType
from holmes.core.toolset_manager import ToolsetManager
from holmes.plugins.toolsets import load_builtin_toolsets, load_toolsets_from_config

toolsets_app = FastAPI()

_CONFIG: Config
_REFRESH_EVENT: threading.Event


def init_toolsets_app(main_app: FastAPI, config: Config, refresh_event: threading.Event) -> None:
    global _CONFIG, _REFRESH_EVENT
    _CONFIG = config
    _REFRESH_EVENT = refresh_event

    main_app.mount("/api/toolsets", toolsets_app)


@toolsets_app.post("/validate")
def validate_toolset(request: ValidateToolsetRequest) -> ValidateToolsetResponse:
    """Validate a toolset configuration by running check_prerequisites without deploying."""
    try:
        # 1. Parse YAML and validate structure via Pydantic model
        try:
            parsed = yaml.safe_load(request.yaml_config)
        except yaml.YAMLError as e:
            logging.error(f"Failed to parse YAML config: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}") from e

        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="YAML must parse to a dictionary")

        # Support both "holmes: {toolsets: ...}" and direct "{toolsets: ...}" formats
        holmes_raw = parsed.get("holmes", parsed)
        if not isinstance(holmes_raw, dict):
            raise HTTPException(status_code=400, detail="'holmes' value must be a mapping")

        try:
            config = HolmesToolsetConfig(**holmes_raw)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid config structure: {e}") from e

        toolsets_config = config.toolsets
        mcp_servers_config = config.mcp_servers

        logging.info(f"Validating toolsets: {list(toolsets_config.keys())}, mcp_servers: {list(mcp_servers_config.keys())}")

        # 2. Merge MCP servers into the combined dict with type: "mcp"
        combined = dict(toolsets_config)
        for name, mcp_config in mcp_servers_config.items():
            mcp_config["type"] = ToolsetType.MCP.value
            combined[name] = mcp_config

        if not combined:
            raise HTTPException(status_code=400, detail="No toolsets or mcp_servers found in the YAML config")

        # 4. Load fresh builtin toolset definitions (safe to mutate, no shared state with server)
        builtin_toolsets = load_builtin_toolsets(dal=None)
        builtins_by_name = {t.name: t for t in builtin_toolsets}

        # 5. Split into builtin overrides vs custom/MCP toolsets
        builtin_overrides = {}
        custom_dict = {}
        for name, cfg in combined.items():
            if name in builtins_by_name:
                builtin_overrides[name] = cfg
                logging.info(f"Toolset '{name}' is a builtin override")
            else:
                if cfg.get("type") is None:
                    cfg["type"] = ToolsetType.CUSTOMIZED.value
                cfg["enabled"] = cfg.get("enabled", True)
                custom_dict[name] = cfg
                logging.info(f"Toolset '{name}' is a custom/MCP toolset")

        toolsets_to_check = []
        results = []

        # 6. Handle builtin overrides — merge user config onto the fresh builtin definition
        if builtin_overrides:
            try:
                override_toolsets = load_toolsets_from_config(builtin_overrides, strict_check=False)
                for override in override_toolsets:
                    if override.name in builtins_by_name:
                        full_toolset = builtins_by_name[override.name]
                        full_toolset.override_with(override)
                        full_toolset.enabled = True
                        toolsets_to_check.append(full_toolset)
                        logging.info(f"Merged config for builtin toolset '{override.name}'")
            except Exception as e:
                logging.error(f"Failed to load builtin override toolsets: {e}", exc_info=True)
                for name in builtin_overrides:
                    results.append(ValidateToolsetResult(
                        toolset_name=name,
                        status="invalid",
                        error=f"Failed to load toolset config: {e}",
                    ))

        # 7. Handle custom/MCP toolsets
        if custom_dict:
            try:
                custom_toolsets = load_toolsets_from_config(custom_dict, strict_check=True)
                for ts in custom_toolsets:
                    ts.enabled = True
                    toolsets_to_check.append(ts)
                    logging.info(f"Loaded custom/MCP toolset '{ts.name}'")
            except Exception as e:
                logging.error(f"Failed to load custom/MCP toolsets: {e}", exc_info=True)
                for name in custom_dict:
                    results.append(ValidateToolsetResult(
                        toolset_name=name,
                        status="invalid",
                        error=f"Failed to load toolset config: {e}",
                    ))

        # 8. Run prerequisite checks concurrently
        if toolsets_to_check:
            logging.info(f"Running prerequisite checks for: {[t.name for t in toolsets_to_check]}")
            ToolsetManager.check_toolset_prerequisites(toolsets_to_check, silent=True)

        # 9. Build results from checked toolsets
        #    Map internal statuses: ENABLED -> "valid", FAILED/DISABLED -> "invalid"
        for ts in toolsets_to_check:
            status = "valid" if ts.status == ToolsetStatusEnum.ENABLED else "invalid"
            logging.info(f"Toolset '{ts.name}': status={status}, error={ts.error}")
            results.append(ValidateToolsetResult(
                toolset_name=ts.name,
                status=status,
                error=ts.error,
                description=ts.description,
            ))

        return ValidateToolsetResponse(results=results)

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Unexpected error in /api/toolsets/validate: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@toolsets_app.post("/refresh")
def trigger_toolset_refresh() -> dict[str, str]:
    """Signal the refresh thread to re-check all toolsets now and sync statuses to DB."""
    logging.info("Received request to trigger toolset refresh")
    _REFRESH_EVENT.set()
    return {"status": "refresh_triggered"}
