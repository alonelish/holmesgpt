from enum import Enum
import json
import logging
import time
from typing import Any, Optional, Tuple
from urllib.parse import urljoin

import requests  # type: ignore


class CoralogixTier(str, Enum):
    FREQUENT_SEARCH = "TIER_FREQUENT_SEARCH"
    ARCHIVE = "TIER_ARCHIVE"


def get_dataprime_base_url(domain: str) -> str:
    return f"https://ng-api-http.{domain}"


def _get_auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _extract_query_id(obj: Any) -> Optional[str]:
    """
    Normalize queryId formats returned by Coralogix.
    Examples:
      {"queryId": {"queryId": "abc"}}
      {"queryId": "abc"}
    """
    if not isinstance(obj, dict):
        return None

    if "queryId" not in obj:
        return None

    q = obj["queryId"]
    if isinstance(q, dict):
        return q.get("queryId") or q.get("id")
    if isinstance(q, str):
        return q
    return None


def execute_http_query(
    domain: str, api_key: str, query: dict[str, Any]
) -> Tuple[requests.Response, str]:
    base_url = get_dataprime_base_url(domain).rstrip("/") + "/"
    url = urljoin(base_url, "api/v1/dataprime/query")
    response = requests.post(
        url,
        headers=_get_auth_headers(api_key),
        json=query,
        timeout=(10, 120),
        stream=True,  # <--- IMPORTANT
    )
    return response, url


def _read_ndjson_response_text(
    response: requests.Response, max_lines: int = 10000
) -> str:
    """
    Reads an NDJSON HTTP response safely.

    Why:
    - DataPrime direct HTTP can return NDJSON where queryId arrives before result.
    - Using response.text may yield only a partial buffer (e.g., just the queryId line).
    - iter_lines() consumes the streamed response properly.

    Returns:
      Full NDJSON as a single string separated by newlines.
    """
    lines: list[str] = []
    try:
        for i, line in enumerate(response.iter_lines(decode_unicode=True)):
            if line:
                lines.append(line)
            if i >= max_lines:
                break
    finally:
        # Ensure the connection can be released back to the pool
        try:
            response.close()
        except Exception:
            pass

    return "\n".join(lines)


def _parse_ndjson_response(
    response_text: str, allow_empty_results: bool = False
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    """
    Parses DataPrime direct HTTP NDJSON response.
    Returns a dict: {"query_id": "...", "results": [<result objs>], "raw_result_lines": [...]}

    Args:
        response_text: The NDJSON response text
        allow_empty_results: If True, return success even if no results found (for health checks)
    """
    lines = [ln for ln in response_text.splitlines() if ln.strip()]
    if not lines:
        return None, "Empty response"

    query_id: Optional[str] = None
    results: list[Any] = []
    raw_result_lines: list[str] = []

    for ln in lines:
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue

        if not isinstance(obj, dict):
            continue

        # Check for queryId first, but don't skip the line - it might also contain results
        if "queryId" in obj:
            q = obj["queryId"]
            if isinstance(q, dict) and "queryId" in q and isinstance(q["queryId"], str):
                query_id = q["queryId"]
            elif isinstance(q, str):
                query_id = q

        # result lines can be {"result": {...}} (typical)
        if "result" in obj:
            raw_result_lines.append(ln)
            results.append(obj["result"])
            continue

        # Some responses may include data under other keys; keep it if it looks like a batch/result
        if any(k in obj for k in ("results", "batches", "records")):
            raw_result_lines.append(ln)
            results.append(obj)
            continue

        # If the line has queryId but also other keys, it might be a combined response
        # Check if there are other keys besides queryId that might indicate results
        if "queryId" in obj and len(obj) > 1:
            # This line has queryId + other data, treat the whole object as a result
            raw_result_lines.append(ln)
            results.append(obj)
            continue

    if not results and not allow_empty_results:
        msg = "No result lines found in NDJSON response."
        if query_id:
            msg += f" Query ID: {query_id}"
        msg += f" Raw lines count: {len(lines)}"
        if lines:
            msg += f" First line preview: {lines[0][:200]}"
        return None, msg

    return {
        "query_id": query_id,
        "results": results,
        "raw_result_lines": raw_result_lines,
    }, None


def _drop_raw_result_lines(obj: Any) -> Any:
    """
    Remove raw_result_lines recursively to avoid redundant payload bloat.
    Keeps other fields (e.g., userData) intact.
    """
    if isinstance(obj, dict):
        if "raw_result_lines" in obj:
            obj = dict(obj)
            obj.pop("raw_result_lines", None)
        for k, v in list(obj.items()):
            obj[k] = _drop_raw_result_lines(v)
    elif isinstance(obj, list):
        obj = [_drop_raw_result_lines(v) for v in obj]
    return obj


def _build_query_dict(
    dataprime_query: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    tier: Optional[CoralogixTier] = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"syntax": "QUERY_SYNTAX_DATAPRIME"}
    if start_date:
        metadata["startDate"] = start_date
    if end_date:
        metadata["endDate"] = end_date
    if tier:
        metadata["tier"] = tier.value

    return {"query": dataprime_query, "metadata": metadata}


def _submit_background_query(
    domain: str, api_key: str, query: dict[str, Any]
) -> Tuple[Optional[str], Optional[str]]:
    base_url = get_dataprime_base_url(domain).rstrip("/") + "/"
    url = urljoin(base_url, "api/v1/dataprime/background-query/submit")
    response = requests.post(
        url,
        headers=_get_auth_headers(api_key),
        json=query,
        timeout=(10, 10),
    )

    if response.status_code != 200:
        body = (getattr(response, "text", "") or "").strip()
        return (
            None,
            f"Background submit failed: status_code={response.status_code}, {body}\nURL: {url}",
        )

    try:
        payload = response.json()
    except Exception:
        payload = {}

    query_id = _extract_query_id(payload)
    if not query_id:
        return None, f"Background submit did not return queryId. Response: {payload}"

    return query_id, None


def _poll_background_query(
    domain: str,
    api_key: str,
    query_id: str,
    max_attempts: int,
    poll_interval_seconds: float,
) -> Tuple[bool, Optional[str]]:
    base_url = get_dataprime_base_url(domain).rstrip("/") + "/"
    status_url = urljoin(
        base_url, f"api/v1/dataprime/background-query/status/{query_id}"
    )

    for attempt in range(max_attempts):
        try:
            resp = requests.get(
                status_url, headers=_get_auth_headers(api_key), timeout=(10, 15)
            )
        except requests.RequestException as e:
            return False, f"Background status check failed: {e}"

        if resp.status_code != 200:
            body = (getattr(resp, "text", "") or "").strip()
            return False, f"Background status returned {resp.status_code}: {body}"

        try:
            payload = resp.json()
        except Exception:
            payload = {}

        status = None
        # Coralogix docs use "status" today; be defensive in case of "state" or "queryStatus".
        for key in ("status", "state", "queryStatus"):
            candidate = payload.get(key)
            if isinstance(candidate, str):
                status = candidate.upper()
                break

        if status in ("COMPLETED", "DONE", "FINISHED", "SUCCESS"):
            return True, None
        if status in ("FAILED", "ERROR", "CANCELED", "CANCELLED"):
            return (
                False,
                f"Background query failed with status={status}. Payload: {payload}",
            )

        time.sleep(poll_interval_seconds)

    return (
        False,
        f"Background query did not complete after {max_attempts} attempts. Last payload: {payload}",
    )


def _download_background_data(
    domain: str, api_key: str, query_id: str
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    base_url = get_dataprime_base_url(domain).rstrip("/") + "/"
    data_url = urljoin(base_url, f"api/v1/dataprime/background-query/data/{query_id}")
    try:
        resp = requests.get(
            data_url, headers=_get_auth_headers(api_key), timeout=(10, 120), stream=True
        )
    except requests.RequestException as e:
        return None, f"Background data download failed: {e}"

    if resp.status_code != 200:
        body = (
            _read_ndjson_response_text(resp, max_lines=2000).strip()
            or (getattr(resp, "text", "") or "").strip()
        )
        return None, f"Background data returned {resp.status_code}: {body}"

    raw = _read_ndjson_response_text(resp).strip()
    parsed, parse_err = _parse_ndjson_response(raw, allow_empty_results=True)
    if parse_err:
        return (
            None,
            f"Background data parse error: {parse_err}. Raw preview: {raw[:500]}",
        )

    return parsed, None


def _run_background_query(
    domain: str,
    api_key: str,
    query_dict: dict[str, Any],
    max_attempts: int,
    poll_interval_seconds: float,
) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    query_id, submit_err = _submit_background_query(domain, api_key, query_dict)
    if submit_err:
        return None, submit_err
    if not query_id:
        return None, "Background submit did not return a queryId"

    ok, status_err = _poll_background_query(
        domain, api_key, query_id, max_attempts, poll_interval_seconds
    )
    if not ok:
        return None, status_err

    return _download_background_data(domain, api_key, query_id)


def execute_dataprime_query(
    domain: str,
    api_key: str,
    dataprime_query: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    tier: Optional[CoralogixTier] = None,
    max_poll_attempts: int = 60,
    poll_interval_seconds: float = 1.0,
) -> Tuple[Optional[str], Optional[str]]:
    try:
        query_dict = _build_query_dict(dataprime_query, start_date, end_date, tier)
        response, submit_url = execute_http_query(domain, api_key, query_dict)

        if response.status_code != 200:
            # For compiler errors, Coralogix typically returns 400 with human-readable text.
            # Because we used stream=True, safely read the body:
            body = _read_ndjson_response_text(response, max_lines=2000).strip()
            if not body:
                # Fall back without re-consuming the stream
                try:
                    body = (getattr(response, "text", "") or "").strip()
                except Exception:
                    body = ""
            return (
                None,
                f"Failed to submit query: status_code={response.status_code}, {body}\nURL: {submit_url}",
            )

        # Read full NDJSON (queryId line + potential result lines)
        raw = _read_ndjson_response_text(response).strip()
        if not raw:
            return None, f"Empty 200 response from query submission\nURL: {submit_url}"

        parsed, parse_err = _parse_ndjson_response(raw, allow_empty_results=True)
        if parse_err:
            debug_info = (
                f"Query submission:\n"
                f"URL: {submit_url}\n"
                f"Response status: {response.status_code}\n"
                f"Response body (first 2000 chars): {raw[:2000]}\n\n"
            )
            return None, f"{debug_info}{parse_err}"

        # If direct endpoint returned queryId but no results (common when the server forces async),
        # fall back to the background-query workflow.
        if parsed and parsed.get("query_id") and not parsed.get("results"):
            bg_parsed, bg_err = _run_background_query(
                domain=domain,
                api_key=api_key,
                query_dict=query_dict,
                max_attempts=max_poll_attempts,
                poll_interval_seconds=poll_interval_seconds,
            )
            if bg_parsed:
                return json.dumps(_drop_raw_result_lines(bg_parsed), indent=2), None
            if bg_err:
                # Some tenants do not expose background-query endpoints (404). In that case,
                # return the original parsed payload (queryId + empty results) instead of erroring.
                if "404" in bg_err or "Not found" in bg_err:
                    return json.dumps(_drop_raw_result_lines(parsed), indent=2), None
                return None, (
                    f"Direct query returned no result lines (queryId={parsed.get('query_id')}). "
                    f"Background-query fallback also failed: {bg_err}"
                )

        return json.dumps(_drop_raw_result_lines(parsed), indent=2), None

    except requests.RequestException as e:
        logging.error("HTTP error executing DataPrime query", exc_info=True)
        return None, f"HTTP error: {e}"
    except Exception as e:
        logging.error("Failed to execute DataPrime query", exc_info=True)
        return None, str(e)


def health_check(domain: str, api_key: str) -> Tuple[bool, str]:
    query_dict = _build_query_dict("source logs | limit 1")
    response, submit_url = execute_http_query(
        domain=domain, api_key=api_key, query=query_dict
    )

    if response.status_code != 200:
        body = _read_ndjson_response_text(response, max_lines=2000).strip()
        if not body:
            body = (getattr(response, "text", "") or "").strip()
        return (
            False,
            f"Failed with status_code={response.status_code}. {body}\nURL: {submit_url}",
        )

    raw = _read_ndjson_response_text(response).strip()
    if not raw:
        return False, f"Health check got empty 200 response\nURL: {submit_url}"

    parsed, err = _parse_ndjson_response(raw, allow_empty_results=True)
    if err:
        return (
            False,
            f"Health check got 200 but could not parse results: {err}\nURL: {submit_url}\nResponse preview: {raw[:500]}",
        )

    # If the direct endpoint returned only queryId, try the background API to be sure we can fetch data.
    if parsed and parsed.get("query_id") and not parsed.get("results"):
        bg_parsed, bg_err = _run_background_query(
            domain=domain,
            api_key=api_key,
            query_dict=query_dict,
            max_attempts=10,
            poll_interval_seconds=1.0,
        )
        if bg_err:
            # Accept tenants that return 404/Not found for background endpoints and treat as successful
            # empty-result health check.
            if "404" in bg_err or "Not found" in bg_err:
                return True, ""
            return False, (
                "Health check received queryId but no results from direct endpoint, "
                f"and background-query fallback failed: {bg_err}"
            )
        parsed = bg_parsed

    return True, ""
