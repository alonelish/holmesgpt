class Span:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def start_span(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def log(self, *args, **kwargs):
        return None


class SpanTypeAttribute:
    SCORE = "score"


class Dataset:
    def __init__(self, *args, **kwargs):
        pass


class Experiment:
    def __init__(self, *args, **kwargs):
        pass


class ReadonlyExperiment:
    def __init__(self, *args, **kwargs):
        pass
