{{/*
Define the LLM instructions for PagerDuty MCP
*/}}
{{- define "holmes.pagerdutyMcp.llmInstructions" -}}
{{- if .Values.mcpAddons.pagerduty.llmInstructions -}}
{{ .Values.mcpAddons.pagerduty.llmInstructions }}
{{- else -}}
This MCP server provides access to PagerDuty for incident management, on-call schedules, and service monitoring.

## When to Use This MCP Server

Use the PagerDuty MCP when investigating:
- Active incidents and their status, urgency, and timeline
- On-call schedules and escalation policies
- Service health and dependencies
- Alert grouping and event orchestration
- Change events that may correlate with incidents

## Investigation Workflow

1. **List incidents**: Start by listing recent incidents filtered by status (triggered, acknowledged, resolved)
2. **Get incident details**: Retrieve full incident details including alerts, log entries, and notes
3. **Check services**: Look up the affected service and its dependencies
4. **Review on-call**: Check who is on-call for the affected escalation policy
5. **Examine change events**: Look for recent change events that may have caused the incident
6. **Check schedules**: Review schedule overrides or gaps that might explain delayed response

## Important Guidelines

- Always check incident status before taking action - it may already be resolved
- Use log entries to understand the full timeline of an incident
- Cross-reference change events with incident start times to identify root causes
- Check escalation policies to understand the notification chain
- When multiple incidents are related, look for common services or dependencies
{{- end -}}
{{- end -}}
