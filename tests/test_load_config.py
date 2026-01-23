import os

import pytest

from holmes.utils.env import MissingEnvironmentVariableError, get_env_replacement


@pytest.mark.parametrize(
    "input_value, mock_environ, expected_output",
    [
        ("this is a plain string", {}, "this is a plain string"),
        ("{{ other_format.VAR }}", {}, "{{ other_format.VAR }}"),
        ("{{env.VAR}", {}, "{{env.VAR}"),
        ("{ env.VAR }}", {}, "{ env.VAR }}"),
        ("{{ foo.bar }}", {}, "{{ foo.bar }}"),
        ("{{ VAR }}", {}, "{{ VAR }}"),
        ("{{ env.MY_VAR }}", {"MY_VAR": "var_value_123"}, "var_value_123"),
        (
            "{{  env.MY_VAR_SPACED  }}",
            {"MY_VAR_SPACED": "spaced_value_456"},
            "spaced_value_456",
        ),
        (
            "prefix {{ env.MY_VAR }} suffix",
            {"MY_VAR": "var_value_789"},
            "prefix var_value_789 suffix",
        ),
        (
            "{{ env.FIRST_VAR }} {{ env.SECOND_VAR }}",
            {"FIRST_VAR": "first_val", "SECOND_VAR": "second_val"},
            "first_val second_val",
        ),
        ("{{ env.EMPTY_VAL_VAR }}", {"EMPTY_VAL_VAR": ""}, ""),
        (
            "{{ env.app.config.host }}",
            {"app.config.host": "localhost.localdomain"},
            "localhost.localdomain",
        ),
        (
            "foo_{{ env.MYKEY }}_bar",
            {"MYKEY": "my_value_for_mykey"},
            "foo_my_value_for_mykey_bar",
        ),
        (
            "this is a {{ env.MYKEY }} env var",
            {"MYKEY": "special"},
            "this is a special env var",
        ),
    ],
)
def test_get_env_replacement_successful(
    input_value, mock_environ, expected_output, monkeypatch
):
    """
    Tests various scenarios where get_env_replacement should return a value (or None)
    without raising an exception.
    """
    # monkeypatch.setattr(os, 'environ', mock_environ) # This replaces the whole dict
    # A better way for os.environ is to set/unset specific keys if needed, or use clear and update
    monkeypatch.setattr(os, "environ", mock_environ.copy())  # Ensure we use a copy

    actual_output = get_env_replacement(input_value)
    assert actual_output == expected_output


@pytest.mark.parametrize(
    "input_value, mock_environ, expected_env_var",
    [
        (
            "{{ env.NON_EXISTENT_VAR }}",
            {},  # Ensure the variable is not in the environment
            "NON_EXISTENT_VAR",
        ),
        (
            "{{ env. }}",
            {},
            "",  # Empty key
        ),
    ],
)
def test_get_env_replacement_exceptions(
    input_value,
    mock_environ,
    expected_env_var,
    monkeypatch,
):
    """
    Tests scenarios where get_env_replacement is expected to raise a
    MissingEnvironmentVariableError and log an error.
    """
    monkeypatch.setattr(os, "environ", mock_environ.copy())

    with pytest.raises(MissingEnvironmentVariableError) as exc_info:
        get_env_replacement(input_value)

    # Verify the exception contains helpful instructions
    error_message = str(exc_info.value)
    assert f"Environment variable '{expected_env_var}' is not set" in error_message
    assert "For CLI users:" in error_message
    assert "For Helm chart users" in error_message
    assert "additionalEnvVars:" in error_message
    assert "https://holmesgpt.dev" in error_message
