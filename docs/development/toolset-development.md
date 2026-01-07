# Toolset Development Guide

This guide covers patterns and best practices for developing toolsets in HolmesGPT.

## Overview

Toolsets are modular collections of tools that allow HolmesGPT to interact with external systems. Each toolset is defined as a YAML file (or Python module) that specifies available tools and their parameters.

**Key locations:**

- Toolsets: `holmes/plugins/toolsets/{name}.yaml` or `{name}/`
- Reference implementation: `servicenow_tables/servicenow_tables.py`

## Thin API Wrapper Pattern

Python toolsets should follow the "thin API wrapper" pattern:

- Use `requests` library for HTTP calls (not specialized client libraries like `opensearchpy`)
- Simple config class with Pydantic validation
- Health check in `prerequisites_callable()` method
- Each tool is a thin wrapper around a single API endpoint

```python
from pydantic import BaseModel
import requests

class MyToolsetConfig(BaseModel):
    url: str
    api_key: str = ""

class MyToolset:
    def __init__(self, config: MyToolsetConfig):
        self.config = config

    def prerequisites_callable(self) -> bool:
        """Health check - return True if service is accessible."""
        try:
            resp = requests.get(f"{self.config.url}/health", timeout=5)
            return resp.status_code == 200
        except:
            return False

    def get_resource(self, resource_id: str) -> str:
        """Thin wrapper around single API endpoint."""
        resp = requests.get(
            f"{self.config.url}/api/resources/{resource_id}",
            headers={"Authorization": f"Bearer {self.config.api_key}"}
        )
        return resp.text
```

## Error Message Requirements

All toolsets MUST return detailed error messages from underlying APIs to enable LLM self-correction:

- Include the exact query/command that was executed
- Include time ranges, parameters, and filters used
- Include the full API error response (status code and message)
- For "no data" responses, specify what was searched and where

**Example:**

```python
def query_metrics(self, query: str, start: str, end: str) -> str:
    resp = requests.get(
        f"{self.url}/api/v1/query_range",
        params={"query": query, "start": start, "end": end}
    )
    if resp.status_code != 200:
        return f"Error querying Prometheus: {resp.status_code} - {resp.text}\n" \
               f"Query: {query}\nTime range: {start} to {end}"

    data = resp.json()
    if not data.get("data", {}).get("result"):
        return f"No data found for query '{query}' in time range {start} to {end}"

    return json.dumps(data)
```

## Server-Side Filtering

**Never return unbounded data from APIs** - this causes token overflow.

- Always include filter parameters on tools that query collections
- Example problem: `opensearch_list_shards` returned ALL shards → 25K+ tokens on large clusters
- Example fix: `elasticsearch_cat` tool requires `index` parameter for shards/segments endpoints

When server-side filtering is not possible, use `JsonFilterMixin` (see `json_filter_mixin.py`) to add `max_depth` and `jq` parameters for client-side filtering.

```python
# BAD: Returns all shards
def list_shards(self) -> str:
    return self.client.cat.shards()

# GOOD: Requires index filter
def list_shards(self, index: str) -> str:
    """List shards for a specific index."""
    return self.client.cat.shards(index=index)
```

## Config Backwards Compatibility

When renaming config fields, maintain backwards compatibility using Pydantic's `extra="allow"`:

```python
from pydantic import BaseModel, ConfigDict, model_validator
import logging

# ✅ DO: Use extra="allow" to accept deprecated fields without polluting schema
class MyToolsetConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Only define current field names in schema
    new_field_name: int = 10

    @model_validator(mode="after")
    def handle_deprecated_fields(self):
        extra = self.model_extra or {}
        deprecated = []

        # Map old names to new names
        if "old_field_name" in extra:
            self.new_field_name = extra["old_field_name"]
            deprecated.append("old_field_name -> new_field_name")

        if deprecated:
            logging.warning(f"Deprecated config names: {', '.join(deprecated)}")
        return self

# ❌ DON'T: Define deprecated fields in schema with Optional[None]
class BadConfig(BaseModel):
    new_field_name: int = 10
    old_field_name: Optional[int] = None  # Pollutes schema, shows in model_dump()
```

**Benefits of `extra="allow"` approach:**

- Schema only shows current field names
- `model_dump()` returns clean output without deprecated fields
- Old configs still work (backwards compatible)
- Deprecation warnings guide users to update

See `prometheus/prometheus.py` PrometheusConfig for a complete example.

## Testing Toolsets

New toolsets require integration tests. See [Evaluations](evaluations/index.md) for how to create tests that verify toolset behavior.
