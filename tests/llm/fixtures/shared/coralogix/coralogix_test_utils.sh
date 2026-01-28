#!/bin/bash
# Shared utilities for Coralogix eval tests
# Source this file at the start of before_test scripts:
#   source ../../shared/coralogix_test_utils.sh
#
# Required environment variables:
#   CORALOGIX_SEND_API_KEY - API key with SendData permissions (for ingestion)
#   CORALOGIX_API_KEY - API key with DataQuerying permissions (for queries)
#   CORALOGIX_DOMAIN - e.g., "eu2.coralogix.com"
#
# Note: Coralogix uses separate API keys for sending vs querying data.
# See: https://coralogix.com/docs/user-guides/account-management/api-keys/api-keys/

# Validate Coralogix environment variables
cx_validate_env() {
  local missing=()

  if [ -z "$CORALOGIX_SEND_API_KEY" ]; then
    missing+=("CORALOGIX_SEND_API_KEY")
  fi

  if [ -z "$CORALOGIX_API_KEY" ]; then
    missing+=("CORALOGIX_API_KEY")
  fi

  if [ -z "$CORALOGIX_DOMAIN" ]; then
    missing+=("CORALOGIX_DOMAIN")
  fi

  if [ ${#missing[@]} -gt 0 ]; then
    echo "❌ Missing required environment variables: ${missing[*]}"
    exit 1
  fi

  echo "✅ Coralogix environment validated"
}

# Get the ingestion endpoint for sending logs
# Usage: INGRESS_URL=$(cx_ingress_url)
cx_ingress_url() {
  echo "https://ingress.${CORALOGIX_DOMAIN}"
}

# Get the DataPrime query endpoint
# Usage: QUERY_URL=$(cx_query_url)
cx_query_url() {
  echo "https://ng-api-http.${CORALOGIX_DOMAIN}/api/v1/dataprime/query"
}

# Send logs to Coralogix via REST API
# Uses CORALOGIX_SEND_API_KEY (SendData permissions required)
# Usage: cx_send_logs "app-name" "subsystem-name" '[{"timestamp":..., "severity":1, "text":"..."}]'
cx_send_logs() {
  local app_name="$1"
  local subsystem_name="$2"
  local log_entries="$3"

  local ingress_url=$(cx_ingress_url)
  local payload=$(cat <<EOF
{
  "applicationName": "$app_name",
  "subsystemName": "$subsystem_name",
  "logEntries": $log_entries
}
EOF
)

  local response
  response=$(curl -sf -X POST "${ingress_url}/logs/v1/singles" \
    -H "Authorization: Bearer ${CORALOGIX_SEND_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$payload" 2>&1)
  local exit_code=$?

  if [ $exit_code -ne 0 ]; then
    echo "❌ Failed to send logs (curl exit code: $exit_code)"
    echo "Response: $response"
    return 1
  fi

  echo "✅ Logs sent successfully to $app_name/$subsystem_name"
  return 0
}

# Query Coralogix using DataPrime and return results
# Uses CORALOGIX_API_KEY (DataQuerying permissions required)
# Usage: RESULT=$(cx_query "source logs | lucene 'error' | limit 10")
cx_query() {
  local query="$1"
  local start_date="${2:-$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -v-1H '+%Y-%m-%dT%H:%M:%SZ')}"
  local end_date="${3:-$(date -u '+%Y-%m-%dT%H:%M:%SZ')}"

  local query_url=$(cx_query_url)

  curl -sf -X POST "$query_url" \
    -H "Authorization: Bearer ${CORALOGIX_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"query\": \"$query\", \"metadata\": {\"syntax\": \"QUERY_SYNTAX_DATAPRIME\", \"startDate\": \"$start_date\", \"endDate\": \"$end_date\"}}"
}

# Wait for logs to be queryable in Coralogix
# Usage: cx_wait_for_logs "search-term" [max_attempts] [sleep_interval]
cx_wait_for_logs() {
  local search_term="$1"
  local max_attempts="${2:-60}"
  local sleep_interval="${3:-5}"

  echo "⏳ Waiting for logs containing '$search_term' to be queryable..."

  for i in $(seq 1 $max_attempts); do
    local result=$(cx_query "source logs | lucene '$search_term' | limit 1")

    if echo "$result" | grep -q "$search_term"; then
      echo "✅ Logs are queryable after $((i * sleep_interval)) seconds"
      return 0
    fi

    echo "   Attempt $i/$max_attempts: Logs not yet available..."
    sleep $sleep_interval
  done

  echo "❌ Timeout waiting for logs after $((max_attempts * sleep_interval)) seconds"
  return 1
}

# Generate a unique verification code for anti-hallucination testing
# Usage: VERIFY_CODE=$(cx_generate_verify_code)
cx_generate_verify_code() {
  local code=$(cat /dev/urandom | tr -dc 'A-Z0-9' | fold -w 8 | head -n 1)
  echo "HOLMES-CX-${code}"
}

# Get current timestamp in Coralogix format (milliseconds since epoch)
# Usage: TIMESTAMP=$(cx_timestamp)
cx_timestamp() {
  # Returns milliseconds since epoch
  echo $(($(date +%s) * 1000))
}

# Get timestamp for N minutes ago in Coralogix format
# Usage: TIMESTAMP=$(cx_timestamp_minutes_ago 5)
cx_timestamp_minutes_ago() {
  local minutes="$1"
  local seconds=$((minutes * 60))
  echo $((($(date +%s) - seconds) * 1000))
}

# Combined setup: validate env
cx_setup() {
  cx_validate_env
}
