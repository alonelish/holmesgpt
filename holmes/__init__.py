# This is patched by github actions during release
__version__ = "0.0.0"


def __getattr__(name):
    if name == "get_version":
        from .version import get_version

        return get_version
    if name == "is_official_release":
        from .version import is_official_release

        return is_official_release
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
