# Dynamic Approval for HTTP Toolsets: Architecture Investigation

## Problem Statement

The bash toolset has a whitelist of allowed command prefixes plus the ability for users to approve new commands on the fly ("Yes, and don't ask again for X commands"). HTTP toolsets need a similar mechanism, but the challenge is that **bash is a single toolset** while **HTTP is a toolset type** — there can be many independent HTTP toolset instances (e.g., `confluence-api`, `github-api`, `internal-service`), each with their own endpoints, auth, and trust boundaries.

## Current State

### Bash Toolset Approval (Reference)

The bash toolset uses a three-layer approval system:

1. **Static whitelist** — Built-in allow/deny lists (`CORE_ALLOW_LIST`, `EXTENDED_ALLOW_LIST`) defined in `holmes/plugins/toolsets/bash/common/default_lists.py`
2. **Persistent CLI approvals** — Stored in `~/.holmes/bash_approved_prefixes.yaml`, loaded at startup
3. **Session approvals** — Stored in conversation message metadata (`bash_session_approved_prefixes`), valid for current conversation only

When the LLM wants to run `helm list` and it's not in the allow list:
- `Tool.invoke()` calls `requires_approval()` → returns `APPROVAL_REQUIRED`
- User sees: "Yes" / "Yes, and don't ask again for `helm` commands" / "No"
- If "don't ask again", the prefix `helm` is saved and future `helm *` commands auto-approve

The key insight: **bash approval works on command prefixes** (e.g., `kubectl get`, `helm`). The "unit of approval" is a prefix string.

### HTTP Toolset Current Security

HTTP toolsets use a **pre-configured whitelist** defined in YAML:
```yaml
endpoints:
  - hosts: ["*.atlassian.net"]
    paths: ["/wiki/rest/api/*"]
    methods: ["GET"]
```

There is **no dynamic approval** — if a URL matches the whitelist, it executes; if not, it returns an error. There's no way for users to approve a new endpoint on the fly.

### Generic Approval Infrastructure

The codebase already has generic approval plumbing:

- `Tool.requires_approval(params, context)` — virtual method, returns `Optional[ApprovalRequirement]`
- `ApprovalRequirement(needs_approval, reason, prefixes_to_save)` — though `prefixes_to_save` is bash-specific
- `ToolApprovalDecision(tool_call_id, approved, save_prefixes)` — the user's response
- `_handle_tool_call_approval()` in `tool_calling_llm.py` — generic approval flow
- `process_tool_decisions()` — server-side approval processing
- Session metadata injection via `extra_metadata` on tool call messages

## The Design Challenge

For bash, the "unit of trust" is a **command prefix** — simple, flat, easy to reason about.

For HTTP toolsets, what is the equivalent "unit of trust"? Several dimensions are in play:

| Dimension | Example | Granularity |
|-----------|---------|-------------|
| Host | `api.example.com` | Coarse |
| Host + Path pattern | `api.example.com/v1/users/*` | Medium |
| Host + Path + Method | `GET api.example.com/v1/users/*` | Fine |
| Exact URL | `GET api.example.com/v1/users/123` | Too fine |
| Toolset instance | `confluence-api` (all endpoints) | Very coarse |

## Architectural Options

### Option A: Endpoint Pattern Approval (Recommended)

**Concept**: The "unit of approval" is an **endpoint pattern** — a `(method, host_pattern, path_pattern)` tuple, analogous to how bash uses command prefixes.

**How it works**:

1. HTTP toolset starts with configured whitelist (existing behavior, unchanged)
2. When the LLM calls an HTTP tool with a URL **not in the whitelist**, instead of returning an error, `HttpRequest.requires_approval()` returns `APPROVAL_REQUIRED` with a suggested endpoint pattern
3. User sees approval menu similar to bash:
   - "Yes" — one-time approval
   - "Yes, and allow `GET *.example.com/api/*` in this session" — session approval
   - "No"
4. Approved patterns stored in session metadata (like bash's `bash_session_approved_prefixes`)

**Pattern derivation**: When the LLM requests `GET https://api.example.com/v1/users/123`, the system suggests the pattern `GET api.example.com/v1/users/*` (generalizing the trailing path segment). Multiple heuristics could be offered.

**Changes required**:

```
ApprovalRequirement
  + endpoint_patterns_to_save: Optional[List[EndpointApprovalPattern]]

EndpointApprovalPattern
  method: str
  host_pattern: str
  path_pattern: str

ToolApprovalDecision
  + save_endpoint_patterns: Optional[List[EndpointApprovalPattern]]

HttpRequest.requires_approval()
  → Check URL against whitelist
  → If not matched, derive suggested pattern, return APPROVAL_REQUIRED

HttpToolset
  + session_approved_endpoints: List[EndpointApprovalPattern]  (injected from context)
  + match_endpoint() now also checks session_approved_endpoints
```

**Session storage** (analogous to `bash_session_approved_prefixes`):
```json
{
  "http_session_approved_endpoints": [
    {"method": "GET", "host": "api.example.com", "path": "/v1/users/*"}
  ]
}
```

**Persistent storage** (analogous to `~/.holmes/bash_approved_prefixes.yaml`):
```yaml
# ~/.holmes/http_approved_endpoints.yaml
- toolset: confluence-api
  method: GET
  host: "*.atlassian.net"
  path: "/wiki/rest/api/*"
```

**Pros**:
- Direct parallel to bash prefix approval — same UX patterns, same mental model
- Fine-grained: user approves specific method+host+path patterns
- Works across all HTTP toolset instances uniformly
- Reuses existing approval infrastructure (`requires_approval`, `_handle_tool_call_approval`, session metadata)
- Each toolset instance manages its own approved patterns (no cross-toolset leakage)

**Cons**:
- Pattern derivation from a URL is more complex than bash prefix derivation
- Need to decide how aggressive to generalize (just the path? the host too?)
- Auth is not part of the pattern — approved endpoints use the toolset's configured auth, or no auth (security consideration)

**Auth question**: When a user approves a new endpoint pattern dynamically, what auth should be used? Options:
- (a) Use the auth from the closest matching configured endpoint (if same host matches an existing endpoint's host pattern)
- (b) No auth (the user is accepting responsibility)
- (c) Prompt the user for which auth config to use
- Recommendation: **(a)** — match auth from the most specific configured endpoint that shares the same host pattern. If no host matches at all, use no auth and warn the user.

---

### Option B: Approval Mode Toggle Per Toolset

**Concept**: Instead of a static whitelist, each HTTP toolset can be configured in one of three modes:

```yaml
toolsets:
  confluence-api:
    type: http
    config:
      approval_mode: "whitelist"    # default, current behavior
      # approval_mode: "approve_all"  # every request needs approval
      # approval_mode: "open"         # no restrictions (trust the LLM)
      endpoints: [...]
```

- **`whitelist`** (default): Current behavior. Only whitelisted endpoints execute.
- **`approve_all`**: Every HTTP request requires user approval, regardless of whitelist.
- **`open`**: No URL restrictions. LLM can call any URL. (Dangerous but simple for internal tools.)

**How it works in `approve_all` mode**:
1. LLM calls `http_confluence_api_request` with any URL
2. `requires_approval()` always returns `APPROVAL_REQUIRED`
3. User sees the full URL/method/body and approves or denies
4. No "remember" option — every call is individually approved

**Changes required**:
```
HttpToolsetConfig
  + approval_mode: Literal["whitelist", "approve_all", "open"] = "whitelist"

HttpRequest.requires_approval()
  if approval_mode == "approve_all": return APPROVAL_REQUIRED
  if approval_mode == "open": return None  (no approval needed)
  if approval_mode == "whitelist": existing behavior
```

**Pros**:
- Very simple to implement — just a mode flag
- `approve_all` gives full control without complex pattern matching
- Good for high-security environments where every external call should be reviewed

**Cons**:
- `approve_all` is tedious — user approves every single request, no "remember" mechanism
- `open` is the opposite extreme — no safety net
- No learning/accumulation of trust over a session
- Doesn't solve the "approve on the fly" problem — it's either all-approval or no-approval

---

### Option C: Layered Endpoint Scopes (Most Flexible)

**Concept**: Introduce a multi-tier endpoint configuration with different trust levels:

```yaml
toolsets:
  my-api:
    type: http
    config:
      endpoints:
        # Tier 1: Auto-approved (current whitelist behavior)
        - hosts: ["api.example.com"]
          paths: ["/v1/health", "/v1/status"]
          methods: ["GET"]
          approval: auto

        # Tier 2: Pre-configured but requires approval
        - hosts: ["api.example.com"]
          paths: ["/v1/users/*", "/v1/admin/*"]
          methods: ["GET", "POST"]
          approval: required

        # Tier 3: Catch-all for the host — approval required, can be "remembered"
        - hosts: ["api.example.com"]
          paths: ["*"]
          methods: ["*"]
          approval: ask
```

**Three approval tiers**:
- **`auto`** — Execute immediately (current whitelist behavior)
- **`required`** — Always ask, never remember (for sensitive endpoints)
- **`ask`** — Ask on first use, user can say "don't ask again" (dynamic approval with memory)

**How it works**:
1. URL matching tries endpoints in order
2. If matched with `auto` → execute immediately
3. If matched with `required` → always prompt, no save option
4. If matched with `ask` → prompt with "Yes / Yes and remember / No"
5. If no match → blocked (or could fall through to Option A's dynamic behavior)

**Changes required**:
```
EndpointConfig
  + approval: Literal["auto", "required", "ask"] = "auto"

HttpRequest.requires_approval()
  match_result = self._toolset.match_endpoint(url)
  if match_result.approval == "auto": return None
  if match_result.approval == "required": return ApprovalRequirement(needs_approval=True, can_remember=False)
  if match_result.approval == "ask": return ApprovalRequirement(needs_approval=True, can_remember=True)
```

**Pros**:
- Maximum flexibility — different trust levels for different endpoints
- Admin can pre-configure which endpoints are safe vs. sensitive vs. exploratory
- Naturally extends the existing endpoint config
- The `ask` tier gives the bash-like "approve and remember" UX
- Works well for known APIs where some endpoints are read-only safe and others are mutation-heavy

**Cons**:
- More complex configuration — users need to understand three tiers
- Still requires endpoints to be pre-defined (doesn't handle truly unknown URLs)
- Ordering matters — first match wins, which can be confusing

---

### Option D: Hybrid (A + C)

**Concept**: Combine Options A and C — use tiered endpoint configuration for known APIs, plus dynamic pattern approval for unknown URLs.

```yaml
toolsets:
  my-api:
    type: http
    config:
      allow_dynamic_endpoints: true   # Enable Option A behavior for non-matching URLs
      endpoints:
        - hosts: ["api.example.com"]
          paths: ["/v1/health"]
          methods: ["GET"]
          approval: auto

        - hosts: ["api.example.com"]
          paths: ["/v1/admin/*"]
          methods: ["POST"]
          approval: required
```

**Flow**:
1. URL matches a configured endpoint → use its `approval` tier (auto/required/ask)
2. URL doesn't match any configured endpoint AND `allow_dynamic_endpoints: true`:
   - Derive a pattern from the URL
   - Return `APPROVAL_REQUIRED` with suggested pattern
   - User can approve for session or persist
3. URL doesn't match AND `allow_dynamic_endpoints: false` → blocked (current behavior)

**Pros**:
- Best of both worlds — structured config for known APIs, flexibility for discovery
- Backwards compatible — `allow_dynamic_endpoints` defaults to `false`
- Solves the original problem: LLM can explore new endpoints and user approves on the fly

**Cons**:
- Most complex to implement
- Auth for dynamically approved endpoints needs careful handling

---

## Comparison Matrix

| Criterion | A: Pattern Approval | B: Mode Toggle | C: Layered Scopes | D: Hybrid (A+C) |
|-----------|--------------------|-----------------|--------------------|------------------|
| **Implementation complexity** | Medium | Low | Medium | High |
| **UX similarity to bash** | High | Low | Medium | High |
| **Handles unknown URLs** | Yes | Only in `open` mode | No (pre-configured only) | Yes |
| **Granular trust levels** | Yes (per pattern) | No (per toolset) | Yes (per endpoint) | Yes (both) |
| **"Remember" capability** | Yes | No | Yes (for `ask` tier) | Yes |
| **Backwards compatible** | Yes | Yes | Yes | Yes |
| **Cross-toolset isolation** | Natural (per instance) | Natural | Natural | Natural |
| **Config complexity** | Low | Low | Medium | Medium-High |

## Shared Implementation Concerns

### 1. Generalizing `ApprovalRequirement`

Currently `ApprovalRequirement` has `prefixes_to_save: Optional[List[str]]` which is bash-specific. This should be generalized:

```python
class ApprovalRequirement(BaseModel):
    needs_approval: bool
    reason: str = ""
    # Generic approval data — toolset-specific
    approval_data: Optional[Dict[str, Any]] = None
    # Backward compat
    prefixes_to_save: Optional[List[str]] = None
```

Or, separate the bash-specific and http-specific fields:
```python
class ApprovalRequirement(BaseModel):
    needs_approval: bool
    reason: str = ""
    prefixes_to_save: Optional[List[str]] = None           # bash
    endpoint_patterns_to_save: Optional[List[dict]] = None  # http
```

The second approach is simpler and avoids over-abstraction.

### 2. Generalizing Session Storage

Currently session approved data is stored as `bash_session_approved_prefixes` in message metadata. For HTTP toolsets, a parallel key is needed:

```json
{
  "bash_session_approved_prefixes": ["helm", "docker ps"],
  "http_session_approved_endpoints": [
    {
      "toolset": "confluence-api",
      "method": "GET",
      "host": "*.atlassian.net",
      "path": "/wiki/rest/api/content/*"
    }
  ]
}
```

The extraction function `extract_bash_session_prefixes()` needs a generalized counterpart or a refactor into `extract_session_approvals()` that returns both bash prefixes and HTTP endpoint patterns.

### 3. Generalizing `ToolApprovalDecision`

```python
class ToolApprovalDecision(BaseModel):
    tool_call_id: str
    approved: bool
    save_prefixes: Optional[List[str]] = None                    # bash
    save_endpoint_patterns: Optional[List[EndpointPattern]] = None  # http
```

### 4. Auth for Dynamically Approved Endpoints

When a user dynamically approves `GET api.example.com/v2/newpath/*`, which auth config should be used?

**Proposed rule**: Walk the toolset's configured endpoints and find one whose host pattern matches. Use that endpoint's auth. If multiple match, use the most specific (fewest wildcards). If none match, use no auth and log a warning.

This is reasonable because:
- Dynamic approval typically extends an existing API (same host, new path)
- Auth is usually per-host, not per-path
- Requiring the user to configure auth on-the-fly would be impractical

### 5. Persistent Storage

For CLI users who want "don't ask again" to persist across sessions:

```yaml
# ~/.holmes/http_approved_endpoints.yaml
endpoints:
  - toolset: confluence-api
    method: GET
    host: "*.atlassian.net"
    path: "/wiki/rest/api/content/*"
  - toolset: github-api
    method: GET
    host: "api.github.com"
    path: "/repos/*/issues"
```

Loaded at startup similar to `load_cli_bash_tools_approved_prefixes()`.

### 6. Interactive CLI Menu

For the CLI (`interactive.py`), the approval prompt would look like:

```
⚠️  HTTP request requires approval:
    GET https://api.example.com/v2/analytics/dashboard

    1. Yes (one-time)
    2. Yes, and allow GET api.example.com/v2/analytics/* in this session
    3. Yes, and allow GET api.example.com/v2/analytics/* permanently
    4. No, and tell Holmes what to do differently
```

For the server API, the `pending_approvals` response already supports this — it just needs to include the suggested patterns in the approval metadata.

## Recommendation

**Start with Option A (Endpoint Pattern Approval)**, as it:
- Directly mirrors the bash approval UX users already know
- Addresses the core problem (approving new endpoints on the fly)
- Has moderate implementation complexity
- Is fully backwards compatible (existing whitelist-only configs keep working)
- Can be extended to Option D later by adding `approval` tiers to `EndpointConfig`

**Implementation order**:
1. Add `requires_approval()` to `HttpRequest` — check whitelist, return `APPROVAL_REQUIRED` for non-matching URLs
2. Add endpoint pattern derivation logic (URL → suggested pattern)
3. Generalize `ApprovalRequirement` and `ToolApprovalDecision` with endpoint pattern fields
4. Add session storage extraction (`extract_http_session_approved_endpoints()`)
5. Wire up `HttpToolset.match_endpoint()` to check session-approved patterns
6. Add persistent storage (`~/.holmes/http_approved_endpoints.yaml`)
7. Update interactive CLI menu for HTTP approval prompts
8. (Future) Add `approval` field to `EndpointConfig` for Option C layered scopes
