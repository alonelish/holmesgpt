# CLAUDE.md

Guidance for Claude Code when working with this repository.

## Repository Overview

HolmesGPT is an AI-powered troubleshooting agent that connects to observability platforms (Kubernetes, Prometheus, Grafana, etc.) to diagnose issues using an agentic tool-calling loop.

## Quick Reference

```bash
# Setup
poetry install && poetry run pre-commit install

# Testing
make test-without-llm                    # Unit/integration tests
poetry run pytest -m 'llm' -k "test_name" --no-cov  # LLM eval

# Code quality
make check                               # Pre-commit checks
poetry run ruff format && poetry run ruff check --fix
```

## Architecture

**Key directories:**

- `holmes/main.py` - CLI entry point (Typer-based)
- `holmes/config.py` - Configuration system
- `holmes/core/` - Investigation engine, LLM integration, tool management
- `holmes/plugins/toolsets/` - Toolset definitions (YAML or Python)
- `holmes/plugins/prompts/` - Jinja2 templates
- `tests/llm/fixtures/` - LLM evaluation test cases

**Investigation flow:** Load question → Select toolsets → LLM calls tools → Analyze → Return conclusions

## Critical Conventions

**Code style:**

- ALWAYS place Python imports at the top of the file
- ALWAYS use `git commit -s` to sign off commits (DCO required)
- Use Ruff for formatting, mypy for type checking

**Toolsets must:**

- Return detailed error messages (query, params, full API response) for LLM self-correction
- Never return unbounded data - always include filter parameters
- See `docs/development/toolset-development.md` for patterns

**LLM tests:**

- Use `-k "test_name"` flag, NOT full test path with brackets
- Set env vars BEFORE poetry command: `MODEL=gpt-4.1 poetry run pytest ...`
- Use dedicated namespace per test: `app-<testid>`
- Only use valid tags from `pyproject.toml`

## Agent Skills

Detailed instructions are available as [Agent Skills](https://docs.github.com/en/copilot/concepts/agents/agent-skills) in `.github/skills/`:

- **eval-development** - Creating and debugging LLM evaluation tests
- **toolset-development** - Building toolsets and API integrations

Skills are loaded automatically when relevant to your task.

## Detailed Documentation

- **Evaluations:** `docs/development/evaluations/` - Running, adding, and debugging evals
- **Toolset development:** `docs/development/toolset-development.md` - API wrapper patterns, config backwards compatibility
- **Configuration:** `docs/reference/` - Environment variables, Helm config
- **Toolsets:** `docs/data-sources/builtin-toolsets/` - Individual toolset docs

## Common Mistakes to Avoid

- Running evals with full test path syntax (fails with env vars)
- Using `:latest` container tags in eval manifests
- Adding tags that don't exist in `pyproject.toml`
- Returning unbounded API data from toolsets
- Bare `kubectl wait` immediately after resource creation (race condition)

## Security

- All tools have read-only access by design
- Never commit secrets - use environment variables
- RBAC permissions are respected for Kubernetes access
