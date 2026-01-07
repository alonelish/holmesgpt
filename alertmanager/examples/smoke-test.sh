#!/bin/bash
# Smoke test for AI-Alertmanager

set -e

ALERTMANAGER_URL="${ALERTMANAGER_URL:-http://localhost:9093}"

echo "🧪 Running AI-Alertmanager smoke tests..."
echo "Target: $ALERTMANAGER_URL"
echo ""

# Test 1: Health check
echo "Test 1: Health check"
response=$(curl -s "$ALERTMANAGER_URL/healthz")
if echo "$response" | grep -q "healthy"; then
    echo "✅ Health check passed"
else
    echo "❌ Health check failed"
    exit 1
fi
echo ""

# Test 2: Readiness check
echo "Test 2: Readiness check"
response=$(curl -s "$ALERTMANAGER_URL/readyz")
if echo "$response" | grep -q "ready"; then
    echo "✅ Readiness check passed"
else
    echo "❌ Readiness check failed"
    exit 1
fi
echo ""

# Test 3: Get status
echo "Test 3: Get status"
response=$(curl -s "$ALERTMANAGER_URL/api/v2/status")
if echo "$response" | grep -q "cluster"; then
    echo "✅ Status endpoint passed"
else
    echo "❌ Status endpoint failed"
    exit 1
fi
echo ""

# Test 4: Post alert
echo "Test 4: Post alert"
alert_payload='[{
  "labels": {
    "alertname": "TestAlert",
    "severity": "info",
    "test": "true"
  },
  "annotations": {
    "summary": "This is a test alert",
    "description": "Testing AI-Alertmanager functionality"
  },
  "startsAt": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
}]'

response=$(curl -s -X POST "$ALERTMANAGER_URL/api/v2/alerts" \
    -H "Content-Type: application/json" \
    -d "$alert_payload")

if echo "$response" | grep -q "success"; then
    echo "✅ Post alert passed"
else
    echo "❌ Post alert failed"
    echo "Response: $response"
    exit 1
fi
echo ""

# Test 5: Get alerts
echo "Test 5: Get alerts"
sleep 1  # Wait for alert to be processed
response=$(curl -s "$ALERTMANAGER_URL/api/v2/alerts")
if echo "$response" | grep -q "TestAlert"; then
    echo "✅ Get alerts passed"
else
    echo "❌ Get alerts failed"
    echo "Response: $response"
    exit 1
fi
echo ""

# Test 6: Get alert groups
echo "Test 6: Get alert groups"
response=$(curl -s "$ALERTMANAGER_URL/api/v2/alerts/groups")
if echo "$response" | grep -q "alerts"; then
    echo "✅ Get alert groups passed"
else
    echo "❌ Get alert groups failed"
    exit 1
fi
echo ""

# Test 7: Get receivers
echo "Test 7: Get receivers"
response=$(curl -s "$ALERTMANAGER_URL/api/v2/receivers")
if echo "$response" | grep -q "default"; then
    echo "✅ Get receivers passed"
else
    echo "❌ Get receivers failed"
    exit 1
fi
echo ""

# Test 8: Get all investigations
echo "Test 8: Get all investigations"
response=$(curl -s "$ALERTMANAGER_URL/api/v2/investigations")
if [ $? -eq 0 ]; then
    echo "✅ Get investigations passed"
else
    echo "❌ Get investigations failed"
    exit 1
fi
echo ""

echo "🎉 All smoke tests passed!"
echo ""
echo "Note: AI investigation happens in the background."
echo "To check investigation status, query alerts again after a few seconds:"
echo "  curl $ALERTMANAGER_URL/api/v2/alerts"
