"""
Lightweight shim for pytest-shared-session-scope to satisfy local test runs.
It behaves as a no-op decorator with simple sentinel tokens.
"""


class _Token:
    def __init__(self, label: str):
        self.label = label

    def __repr__(self) -> str:
        return f"<{self.label}>"


SetupToken = type("SetupToken", (), {"FIRST": _Token("SetupToken.FIRST")})
CleanupToken = type("CleanupToken", (), {"LAST": _Token("CleanupToken.LAST")})


def shared_session_scope_json():
    def decorator(func):
        return func

    return decorator
