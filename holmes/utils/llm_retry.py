"""Retry logic for handling transient LLM API errors.

This module provides retry functionality for network-related errors that can occur
when calling LLM APIs, such as RemoteProtocolError, connection timeouts, and
server overload errors.
"""

import logging
import random
import time
from typing import Callable, TypeVar

import httpx
from openai import APIConnectionError, APITimeoutError, InternalServerError

from holmes.common.env_vars import (
    LLM_MAX_RETRIES,
    LLM_RETRY_BASE_DELAY,
    LLM_RETRY_MAX_DELAY,
)

T = TypeVar("T")

# Exceptions that are considered transient and worth retrying
RETRYABLE_EXCEPTIONS = (
    APIConnectionError,  # Wraps httpx.RemoteProtocolError and other connection errors
    APITimeoutError,  # Request timeout
    InternalServerError,  # Server-side errors (500)
    httpx.RemoteProtocolError,  # Server disconnected without response
    httpx.ConnectError,  # Connection failed
    httpx.ReadError,  # Error reading response
    httpx.TimeoutException,  # Various timeout errors
    ConnectionError,  # Generic connection errors
    TimeoutError,  # Generic timeout errors
)


def is_retryable_error(error: Exception) -> bool:
    """Check if an error is a transient error that should be retried.

    Args:
        error: The exception to check

    Returns:
        True if the error is transient and should be retried
    """
    # Direct instance check
    if isinstance(error, RETRYABLE_EXCEPTIONS):
        return True

    # Check error message for common transient error patterns
    error_str = str(error).lower()
    transient_patterns = [
        "server disconnected",
        "connection reset",
        "connection refused",
        "connection aborted",
        "connection timed out",
        "read timed out",
        "remote end closed connection",
        "broken pipe",
        "network unreachable",
        "temporary failure",
        "service unavailable",
        "bad gateway",
        "overloaded",
    ]

    return any(pattern in error_str for pattern in transient_patterns)


def retry_on_network_error(
    func: Callable[..., T],
    *args,
    max_retries: int = LLM_MAX_RETRIES,
    base_delay: float = LLM_RETRY_BASE_DELAY,
    max_delay: float = LLM_RETRY_MAX_DELAY,
    **kwargs,
) -> T:
    """Execute a function with retry logic for transient network errors.

    Uses exponential backoff with jitter for retry delays.

    Args:
        func: The function to execute
        *args: Positional arguments to pass to the function
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds for exponential backoff
        max_delay: Maximum delay in seconds between retries
        **kwargs: Keyword arguments to pass to the function

    Returns:
        The result of the function call

    Raises:
        The original exception if all retries are exhausted or if the error is not retryable
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e

            # Check if this error is retryable
            if not is_retryable_error(e):
                raise

            # Check if we have retries left
            if attempt >= max_retries:
                logging.error(
                    f"LLM API call failed after {attempt + 1} attempts: {type(e).__name__}: {e}"
                )
                raise

            # Calculate delay with exponential backoff and jitter
            delay = min(base_delay * (2**attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)  # 10% jitter
            actual_delay = delay + jitter

            logging.warning(
                f"LLM API call failed with transient error (attempt {attempt + 1}/{max_retries + 1}): "
                f"{type(e).__name__}: {e}. Retrying in {actual_delay:.1f}s..."
            )

            time.sleep(actual_delay)

    # Should not reach here, but just in case
    if last_error:
        raise last_error
    raise RuntimeError("Unexpected state in retry_on_network_error")
