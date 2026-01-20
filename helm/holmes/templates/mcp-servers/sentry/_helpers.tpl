{{/*
Define the LLM instructions for Sentry MCP
*/}}
{{- define "holmes.sentryMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.sentry.llmInstructions -}}
{{ .Values.mcpAddons.sentry.llmInstructions }}
{{- else -}}
This MCP server provides access to Sentry's error tracking, issue management, and observability platform.

## Key Capabilities

- **Organizations & Projects**: List and explore Sentry organizations, projects, and teams
- **Issues & Events**: Search, analyze, and manage error issues with full event details
- **DSNs**: Retrieve Data Source Names for SDK configuration
- **Releases**: Track and manage release information
- **AI Analysis**: Leverage Sentry's Seer AI for automated issue investigation

## When to Use This MCP Server

**Always use it when you see:**
- Application errors, exceptions, or crashes mentioned by the user
- References to error tracking, crash reporting, or exception monitoring
- Requests to investigate production errors or issues
- Performance monitoring or error rate analysis needs
- Questions about error trends, frequency, or patterns

## Investigation Workflow

### Error Investigation
When investigating application errors:
1. List organizations and identify the relevant project
2. Search for issues matching the error description or stack trace
3. Get detailed issue information including events and breadcrumbs
4. Analyze error frequency, affected users, and first/last seen times
5. Check for related issues or regression patterns
6. Use Seer AI analysis when available for automated root cause insights

### Performance Analysis
When analyzing performance issues:
1. Search for issues with performance-related tags
2. Look at error rates and response time correlations
3. Check release correlation to identify problematic deployments
4. Review breadcrumbs to understand the sequence of events

### Release Impact Assessment
When checking release health:
1. List recent releases for the project
2. Check issue frequency before and after release
3. Identify new issues introduced by the release
4. Look for regression patterns in existing issues

## Important Guidelines

- Always specify the organization and project context when querying
- Use filters to narrow down issues by time range, environment, or tags
- Check issue status (resolved, unresolved, ignored) before investigation
- Look at event breadcrumbs for context on what led to the error
- Correlate errors with deployment times using release information
- When multiple issues are related, investigate the root cause first
{{- end -}}
{{- end -}}
