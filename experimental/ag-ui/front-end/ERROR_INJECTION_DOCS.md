# Error Injection Feature Documentation

## Overview

This feature allows testing error handling in the UI by injecting errors via URL parameters. Errors are injected at the **state management level** (not the UI level) and appear in all typical error display locations.

## Implementation Details

### 1. State Management Layer

The error injection is implemented at the state/store level in `MainContent.tsx`:

```typescript
// Error injection state - added at line 793-795
const [injectedError, setInjectedError] = React.useState<string | null>(null);
const hasProcessedErrorParam = React.useRef(false);
```

### 2. URL Parameter Parsing

On component mount, the URL is parsed for the `injectError` parameter (lines 797-810):

```typescript
React.useEffect(() => {
  if (hasProcessedErrorParam.current) return;

  const urlParams = new URLSearchParams(window.location.search);
  const errorParam = urlParams.get('injectError');

  if (errorParam) {
    hasProcessedErrorParam.current = true;
    const decodedError = decodeURIComponent(errorParam);
    setInjectedError(decodedError);
    console.log('Error injection activated from URL:', decodedError);
  }
}, []);
```

### 3. Error Injection Points

Errors are injected at two key points in the query execution flow:

#### A. Manual Query Execution (handleExecuteQuery - lines 722-733)

When user clicks "Execute" button:

```typescript
try {
  // Inject error at store level if URL parameter is present
  if (injectedError) {
    const error = new Error(injectedError);
    (error as any).responseData = {
      error: {
        type: 'InjectedError',
        reason: injectedError,
        injectedViaUrlParameter: true
      }
    };
    throw error;
  }

  // ... normal query execution follows
}
```

#### B. Triggered Query Execution (ChatAssistant trigger - lines 835-846)

When ChatAssistant triggers a query programmatically:

```typescript
try {
  // Inject error at store level if URL parameter is present
  if (injectedError) {
    const error = new Error(injectedError);
    (error as any).responseData = {
      error: {
        type: 'InjectedError',
        reason: injectedError,
        injectedViaUrlParameter: true
      }
    };
    throw error;
  }

  // ... normal query execution follows
}
```

### 4. Error Display Locations

Injected errors appear in all standard error display locations (lines 1208-1222):

1. **Error Icon**: ⚠️ emoji displayed prominently
2. **Error Message**: The injected error text
3. **Error Details**: JSON formatted details including:
   - Error type: 'InjectedError'
   - Error reason: The injected message
   - Flag: `injectedViaUrlParameter: true`

## Usage

### Basic Usage

Add the `injectError` URL parameter to inject a custom error:

```
http://localhost:3000/?app=metrics&injectError=Custom%20error%20message
```

### Examples

#### Example 1: Simple Error Message

```
http://localhost:3000/?app=metrics&injectError=Network%20timeout%20error
```

Result: When executing any query, shows "Network timeout error"

#### Example 2: Simulating API Error

```
http://localhost:3000/?app=logs&injectError=OpenSearch%20connection%20refused
```

Result: Shows "OpenSearch connection refused" error with details

#### Example 3: Complex Error Message

```
http://localhost:3000/?app=metrics&injectError=Prometheus%20query%20failed:%20too%20many%20samples
```

Result: Shows detailed error message as if from Prometheus API

### Testing Different Pages

The error injection works on all pages:

- **Metrics page**: `?app=metrics&injectError=...`
- **Logs page**: `?app=logs&injectError=...`
- **Traces page**: `?app=traces&injectError=...`

## Action Execution Flow Trace

### Complete Path of Query Execution:

```
1. User enters query in textarea (line 1165-1176)
   ↓
2. User clicks "Execute" button (line 1178-1184)
   ↓
3. handleExecuteQuery() called (line 701)
   ↓
4. Create new QueryResult object (line 709-713)
   ↓
5. Update pageResults state (line 716-719)
   ↓
6. **ERROR INJECTION POINT** (line 722-733)
   - Check if injectedError exists
   - If yes: throw error with responseData
   - If no: continue to step 7
   ↓
7. Execute actual query:
   - queryPrometheus() for metrics (line 739)
   - queryOpensearch() for logs (line 742)
   ↓
8. Handle results or errors:
   - Success: Update pageResults with data (line 768-771)
   - Error: Update pageResults with error + errorDetails (line 778-784)
   ↓
9. UI renders results (line 1205-1256)
   ↓
10. Error display (if error exists) (line 1208-1222)
    - Error icon: ⚠️
    - Error message text
    - Formatted error details (JSON)
```

### Alternative Path: ChatAssistant Triggered Query

```
1. ChatAssistant calls onExecutePromQLQuery() or onExecutePPLQuery()
   (in App.tsx lines 27-68)
   ↓
2. Sets triggerQuery state via setTriggerQuery()
   ↓
3. MainContent receives triggerQuery prop
   ↓
4. useEffect triggers on triggerQuery change (line 826-893)
   ↓
5. executeTriggeredQuery() called (line 832)
   ↓
6. **ERROR INJECTION POINT** (line 835-846)
   - Check if injectedError exists
   - If yes: throw error with responseData
   - If no: continue to step 7
   ↓
7. Execute query (queryPrometheus or queryOpensearch)
   ↓
8-10. Same as manual execution path
```

## Key Benefits

1. **Store-Level Injection**: Errors are injected at the state management level, not UI level
2. **Consistent Behavior**: Same error handling path as real API errors
3. **All Display Locations**: Errors appear wherever real errors would appear
4. **Easy Testing**: Simple URL parameter for QA and development testing
5. **No Code Changes**: Can test different errors without modifying code

## Implementation Notes

- Error injection is **one-time per page load** (controlled by `hasProcessedErrorParam.current`)
- The error persists for all queries until page reload
- Error details include `injectedViaUrlParameter: true` flag for debugging
- Works with both manual execution and ChatAssistant-triggered queries
- URL encoding required for special characters in error messages
