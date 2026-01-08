---
name: toolset-development
description: Guide for developing HolmesGPT toolsets. Use this when asked to create new toolsets, modify existing toolsets, or implement API integrations.
---

## Toolset Architecture

- Each toolset is a YAML file defining available tools and their parameters
- Tools can be Python functions or bash commands with safety validation
- Toolsets are loaded dynamically and can be customized via config files
- **Important**: All toolsets MUST return detailed error messages from underlying APIs to enable LLM self-correction
  - Include the exact query/command that was executed
  - Include time ranges, parameters, and filters used
  - Include the full API error response (status code and message)
  - For "no data" responses, specify what was searched and where

## Thin API Wrapper Pattern for Python Toolsets

- Reference implementation: `servicenow_tables/servicenow_tables.py`
- Use `requests` library for HTTP calls (not specialized client libraries like `opensearchpy`)
- Simple config class with Pydantic validation
- Health check in `prerequisites_callable()` method
- Each tool is a thin wrapper around a single API endpoint

## Server-Side Filtering is Critical

- **Never return unbounded data from APIs** - this causes token overflow
- Always include filter parameters on tools that query collections (e.g., `index` parameter for Elasticsearch _cat APIs)
- Example problem: `opensearch_list_shards` returned ALL shards → 25K+ tokens on large clusters
- Example fix: `elasticsearch_cat` tool requires `index` parameter for shards/segments endpoints
- When server-side filtering is not possible, use `JsonFilterMixin` (see `json_filter_mixin.py`) to add `max_depth` and `jq` parameters for client-side filtering

## Toolset Config Backwards Compatibility

When renaming config fields in a toolset, maintain backwards compatibility using Pydantic's `extra="allow"`:

```python
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

Benefits of `extra="allow"` approach:
- Schema only shows current field names
- `model_dump()` returns clean output without deprecated fields
- Old configs still work (backwards compatible)
- Deprecation warnings guide users to update

See `prometheus/prometheus.py` PrometheusConfig for a complete example.

## File Structure Conventions

- Toolsets: `holmes/plugins/toolsets/{name}.yaml` or `{name}/`
- Prompts: `holmes/plugins/prompts/{name}.jinja2`
- Tests: Match source structure under `tests/`
