# Error Display Locations

## Overview

This document shows all locations where injected errors will appear in the UI.

## 1. Main Results Section (Primary Error Display)

**File**: `MainContent.tsx` lines 1208-1222

**Visual Components**:
- ⚠️ Error Icon
- Error Message Text (large, prominent)
- Expandable Error Details (JSON formatted)

**CSS Classes**:
- `.error-message` - Main error container
- `.error-icon` - Warning emoji
- `.error-text` - Error message text
- `.error-details` - Expandable details section
- `.error-details-label` - "Response Details:" label
- `.error-response-container` - Container for JSON response
- `.error-response` - Pre-formatted JSON display

**Code**:
```tsx
{currentResult.error ? (
  <div className="error-message">
    <span className="error-icon">⚠️</span>
    <div className="error-text">{currentResult.error}</div>
    {currentResult.errorDetails && (
      <div className="error-details">
        <div className="error-details-label">Response Details:</div>
        <div className="error-response-container">
          <pre className="error-response">
            {JSON.stringify(currentResult.errorDetails, null, 2)}
          </pre>
        </div>
      </div>
    )}
  </div>
) : ...}
```

## 2. ChatAssistant Context (AI Error Awareness)

**File**: `MainContent.tsx` lines 296-315

**Purpose**: Passes error information to ChatAssistant so it can understand and respond to errors

**Context Items Sent**:
1. **Error Description**: `${selectedPage} query error`
2. **Error Value**: The error message (with detailed response for logs)
3. **Error Details**: Full JSON error response (for non-logs pages)

**Code**:
```typescript
if (currentResult.error) {
  let errorValue = currentResult.error;

  // For logs, include detailed error response in the same entry
  if (selectedPage === 'logs' && currentResult.errorDetails) {
    errorValue += `\n\nDetailed error response: ${JSON.stringify(currentResult.errorDetails, null, 2)}`;
  }

  context.push({
    description: `${selectedPage} query error`,
    value: errorValue
  });

  // Add detailed error response for non-logs pages only
  if (selectedPage !== 'logs' && currentResult.errorDetails) {
    context.push({
      description: `${selectedPage} error response`,
      value: JSON.stringify(currentResult.errorDetails)
    });
  }
}
```

**Benefit**: The AI assistant can see errors and potentially help debug them

## 3. Browser Console (Debug Information)

**File**: `MainContent.tsx` line 808

**Output**: Logs when error injection is activated

```typescript
console.log('Error injection activated from URL:', decodedError);
```

**Benefit**: Confirms error injection is working during development

## 4. State Management Layer (pageResults)

**File**: `MainContent.tsx` lines 778-784 (handleExecuteQuery) and 872-883 (triggered queries)

**Structure**:
```typescript
const errorResult: QueryResult = {
  id: Date.now().toString(),
  query: triggerQuery,
  timestamp: new Date(),
  error: error.message || 'An error occurred',
  errorDetails: error.responseData || error
};

setPageResults(prev => ({
  ...prev,
  [selectedPage]: errorResult
}));
```

**Benefit**: Errors are stored at the state level, making them accessible to all components

## Error Propagation Flow

```
URL Parameter (injectError)
    ↓
State Management (injectedError state)
    ↓
Query Execution (throw error with responseData)
    ↓
Error Handling (catch block)
    ↓
State Update (pageResults with error & errorDetails)
    ↓
    ├─→ UI Display (error-message component)
    ├─→ ChatAssistant Context (error awareness)
    └─→ Console Log (debug information)
```

## Testing Checklist

When testing error injection, verify errors appear in:

- [ ] Main results section with ⚠️ icon
- [ ] Error message is displayed prominently
- [ ] Error details are shown in JSON format
- [ ] ChatAssistant receives error in context (check if it mentions the error)
- [ ] Browser console shows "Error injection activated" message
- [ ] Error persists across query re-executions
- [ ] Error appears for both manual execution and ChatAssistant triggers

## Example Error Display

When you use:
```
?app=metrics&injectError=Prometheus%20connection%20timeout
```

You will see:

```
⚠️ Prometheus connection timeout

Response Details:
{
  "error": {
    "type": "InjectedError",
    "reason": "Prometheus connection timeout",
    "injectedViaUrlParameter": true
  }
}
```

And in ChatAssistant context:
```
metrics query error: Prometheus connection timeout
metrics error response: {"error": {"type": "InjectedError", ...}}
```
