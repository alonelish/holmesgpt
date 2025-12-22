"""
Minimal stub for the `autoevals` package used in tests.
Provides a no-op LLMClassifier and init() so imports succeed without the real dependency.
"""


class _EvalResult:
    def __init__(self, score: float = 0.0, metadata=None):
        self.score = score
        self.metadata = metadata or {}


class LLMClassifier:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __call__(self, *args, **kwargs):
        return _EvalResult(score=0.0, metadata={"rationale": "stubbed autoevals"})


def init(*args, **kwargs):
    return None
