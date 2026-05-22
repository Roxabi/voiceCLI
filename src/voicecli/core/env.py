"""Environment variable utilities."""

import os
from typing import Final

TRUE_VALUES: Final = frozenset(("1", "true", "yes", "on"))


def coerce_bool_env(var: str, *, default: bool = False) -> bool:
    """Parse boolean env var — accepts 1/true/yes/on (case-insensitive).

    Args:
        var: Environment variable name.
        default: Value to return if the variable is unset.

    Returns:
        True if the variable value is in {"1", "true", "yes", "on"} (case-insensitive).
        False otherwise, including empty string or unset (returns default).
    """
    val = os.environ.get(var)
    if val is None:
        return default
    return val.lower() in TRUE_VALUES
