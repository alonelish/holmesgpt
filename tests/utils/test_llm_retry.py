"""Tests for LLM retry logic."""

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, BadRequestError, InternalServerError

from holmes.utils.llm_retry import is_retryable_error, retry_on_network_error


class TestIsRetryableError:
    """Tests for is_retryable_error function."""

    def test_remote_protocol_error_is_retryable(self):
        """RemoteProtocolError should be retryable."""
        error = httpx.RemoteProtocolError("Server disconnected without sending a response.")
        assert is_retryable_error(error) is True

    def test_connect_error_is_retryable(self):
        """ConnectError should be retryable."""
        error = httpx.ConnectError("Connection refused")
        assert is_retryable_error(error) is True

    def test_read_error_is_retryable(self):
        """ReadError should be retryable."""
        error = httpx.ReadError("Error reading response")
        assert is_retryable_error(error) is True

    def test_timeout_exception_is_retryable(self):
        """TimeoutException should be retryable."""
        error = httpx.TimeoutException("Request timed out")
        assert is_retryable_error(error) is True

    def test_api_connection_error_is_retryable(self):
        """APIConnectionError should be retryable."""
        error = APIConnectionError(request=None)
        assert is_retryable_error(error) is True

    def test_api_timeout_error_is_retryable(self):
        """APITimeoutError should be retryable."""
        error = APITimeoutError(request=None)
        assert is_retryable_error(error) is True

    def test_internal_server_error_is_retryable(self):
        """InternalServerError should be retryable."""
        # InternalServerError requires a response object, so we test via message pattern
        error = Exception("service unavailable")
        assert is_retryable_error(error) is True

    def test_connection_error_is_retryable(self):
        """Generic ConnectionError should be retryable."""
        error = ConnectionError("Connection reset by peer")
        assert is_retryable_error(error) is True

    def test_timeout_error_is_retryable(self):
        """Generic TimeoutError should be retryable."""
        error = TimeoutError("Connection timed out")
        assert is_retryable_error(error) is True

    def test_bad_request_error_not_retryable(self):
        """BadRequestError should NOT be retryable (client error)."""
        # BadRequestError requires specific constructor args
        error = Exception("BadRequest: Invalid parameters")
        # Unless it contains a retryable pattern
        assert is_retryable_error(error) is False

    def test_value_error_not_retryable(self):
        """ValueError should NOT be retryable."""
        error = ValueError("Invalid argument")
        assert is_retryable_error(error) is False

    def test_generic_exception_not_retryable(self):
        """Generic Exception should NOT be retryable."""
        error = Exception("Something went wrong")
        assert is_retryable_error(error) is False

    def test_error_message_pattern_server_disconnected(self):
        """Error with 'server disconnected' message should be retryable."""
        error = Exception("The server disconnected unexpectedly")
        assert is_retryable_error(error) is True

    def test_error_message_pattern_connection_reset(self):
        """Error with 'connection reset' message should be retryable."""
        error = Exception("Connection reset by peer")
        assert is_retryable_error(error) is True

    def test_error_message_pattern_overloaded(self):
        """Error with 'overloaded' message should be retryable."""
        error = Exception("Server is overloaded")
        assert is_retryable_error(error) is True


class TestRetryOnNetworkError:
    """Tests for retry_on_network_error function."""

    def test_successful_call_no_retry(self):
        """Function that succeeds on first try should not retry."""
        call_count = 0

        def success_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = retry_on_network_error(success_func, max_retries=3)
        assert result == "success"
        assert call_count == 1

    def test_retries_on_transient_error_then_succeeds(self):
        """Function should retry on transient error and succeed."""
        call_count = 0

        def eventually_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.RemoteProtocolError("Server disconnected")
            return "success"

        result = retry_on_network_error(
            eventually_succeeds, max_retries=3, base_delay=0.01, max_delay=0.1
        )
        assert result == "success"
        assert call_count == 2

    def test_raises_after_max_retries_exhausted(self):
        """Function should raise after all retries are exhausted."""
        call_count = 0

        def always_fails():
            nonlocal call_count
            call_count += 1
            raise httpx.RemoteProtocolError("Server disconnected")

        with pytest.raises(httpx.RemoteProtocolError):
            retry_on_network_error(
                always_fails, max_retries=2, base_delay=0.01, max_delay=0.1
            )

        # Should have tried 3 times (initial + 2 retries)
        assert call_count == 3

    def test_does_not_retry_non_retryable_error(self):
        """Function should NOT retry on non-retryable errors."""
        call_count = 0

        def fails_with_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("Invalid input")

        with pytest.raises(ValueError):
            retry_on_network_error(
                fails_with_value_error, max_retries=3, base_delay=0.01, max_delay=0.1
            )

        # Should have only tried once
        assert call_count == 1

    def test_passes_args_and_kwargs(self):
        """Function should receive args and kwargs correctly."""

        def func_with_params(a, b, c=None):
            return f"{a}-{b}-{c}"

        result = retry_on_network_error(func_with_params, "x", "y", c="z", max_retries=1)
        assert result == "x-y-z"
