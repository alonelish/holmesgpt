import fnmatch
from typing import List, Optional


def model_matches_list(model: str, model_list: List[str]) -> bool:
    """
    Check if a model matches any pattern in a list of model patterns.

    Args:
        model: The name of an LLM model (e.g., "azure/gpt", "openai/gpt-4o")
        model_list: List of model patterns that may include wildcards
                   (e.g., ["azure/*", "*/mistral", "openai/gpt-*"])

    Returns:
        True if the model matches any pattern in the list, False otherwise
    """
    for pattern in model_list:
        if fnmatch.fnmatchcase(model, pattern):
            return True
    return False


def resolve_anthropic_code_mode(
    *preferences: Optional[bool], default: bool
) -> bool:
    """
    Resolve Anthropic code mode preference in priority order.

    Returns the first non-None value from preferences, otherwise the provided default.
    """
    for preference in preferences:
        if preference is not None:
            return preference
    return default
