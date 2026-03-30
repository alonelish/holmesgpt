import copy
import logging
import threading
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException

from holmes.config import Config
from holmes.core.models import (
    ValidateToolsetRequest,
    ValidateToolsetResponse,
    ValidateToolsetResult,
)
from holmes.core.tools import ToolsetStatusEnum, ToolsetType
from holmes.core.toolset_manager import ToolsetManager
from holmes.plugins.toolsets import load_toolsets_from_config

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
        # 1. Parse the YAML string
        try:
            parsed = yaml.safe_load(request.yaml_config)
        except yaml.YAMLError as e:
            logging.error(f"Failed to parse YAML config: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}") from e

        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="YAML must parse to a dictionary")

        # 2. Extract toolsets and mcp_servers from under the 'holmes' key
        holmes_config = parsed.get("holmes", parsed)
        if not isinstance(holmes_config, dict):
            raise HTTPException(status_code=400, detail="'holmes' value must be a mapping")

        toolsets_config = holmes_config.get("toolsets") or {}
        mcp_servers_config = holmes_config.get("mcp_servers") or {}

        if not isinstance(toolsets_config, dict):
            raise HTTPException(status_code=400, detail="'toolsets' must be a mapping of toolset name to config")
        
        if not isinstance(mcp_servers_config, dict):
            raise HTTPException(status_code=400, detail="'mcp_servers' must be a mapping of server name to config")

        logging.info(f"Validating toolsets: {list(toolsets_config.keys())}, mcp_servers: {list(mcp_servers_config.keys())}")

        # 3. Merge MCP servers into the combined dict with type: "mcp"
        combined = dict(toolsets_config)
        for name, mcp_config in mcp_servers_config.items():
            if not isinstance(mcp_config, dict):
                raise HTTPException(status_code=400, detail=f"Config for MCP server '{name}' must be a mapping, got {type(mcp_config).__name__}")
            mcp_config["type"] = ToolsetType.MCP.value
            combined[name] = mcp_config

        if not combined:
            raise HTTPException(status_code=400, detail="No toolsets or mcp_servers found in the YAML config")

        # 4. Get already-loaded toolsets from the server executor (avoids reloading all builtins)
        executor = _CONFIG._server_tool_executor
        loaded_by_name = {t.name: t for t in executor.toolsets} if executor else {}

        # 5. Split into known (already-loaded) toolsets vs custom/MCP toolsets
        known_overrides = {}
        custom_dict = {}
        for name, cfg in combined.items():
            if not isinstance(cfg, dict):
                raise HTTPException(status_code=400, detail=f"Config for '{name}' must be a mapping, got {type(cfg).__name__}")
            if name in loaded_by_name:
                known_overrides[name] = cfg
                logging.info(f"Toolset '{name}' found in loaded toolsets")
            else:
                if cfg.get("type") is None:
                    cfg["type"] = ToolsetType.CUSTOMIZED.value
                cfg["enabled"] = cfg.get("enabled", True)
                custom_dict[name] = cfg
                logging.info(f"Toolset '{name}' is a custom/MCP toolset")

        toolsets_to_check = []
        results = []

        # 6. Handle known toolset overrides — deep copy only the matched toolsets, then apply config
        if known_overrides:
            try:
                override_toolsets = load_toolsets_from_config(known_overrides, strict_check=False)
                for override in override_toolsets:
                    if override.name in loaded_by_name:
                        toolset_copy = copy.copy(loaded_by_name[override.name])
                        toolset_copy.override_with(override)
                        toolset_copy.enabled = True
                        toolsets_to_check.append(toolset_copy)
                        logging.info(f"Merged config for toolset '{override.name}'")
            except Exception as e:
                logging.error(f"Failed to load toolset overrides: {e}", exc_info=True)
                for name in known_overrides:
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
