#!/bin/sh
# Read config from ConfigMap
SERVICE_URL=$(cat /config/service_url)
TIMEOUT=$(cat /config/timeout)
RETRY_COUNT=$(cat /config/retry_count)
LOG_LEVEL=$(cat /config/log_level)

echo "[$LOG_LEVEL] Starting network connectivity test"
echo "[$LOG_LEVEL] Target service: $SERVICE_URL"
echo "[$LOG_LEVEL] Timeout: ${TIMEOUT}s, Retries: $RETRY_COUNT"

attempt=1
while true; do
  echo "[$LOG_LEVEL] Attempt $attempt: Connecting to $SERVICE_URL..."
  
  # Try to connect (will fail since service doesn't exist)
  if wget -O- "$SERVICE_URL" -T "$TIMEOUT" 2>&1 | grep -q "Connection refused\|timeout\|No route to host"; then
    echo "[ERROR] Network connection failed: Unable to reach $SERVICE_URL"
    echo "[ERROR] Network error details: Connection timeout after ${TIMEOUT}s"
    echo "[ERROR] Pod network status: Unable to establish TCP connection"
  else
    echo "[$LOG_LEVEL] Connection successful"
  fi
  
  # Simulate some network metrics
  echo "[INFO] Network stats: packets_sent=$((attempt * 10)), packets_lost=$((attempt * 2))"
  echo "[INFO] Latency: ${TIMEOUT}00ms (timeout threshold exceeded)"
  
  attempt=$((attempt + 1))
  sleep 10
done
