"""Public markdown API for TTS scripts.

Re-exports the shared primitives from :mod:`voicecli.markdown_types` and
delegates parsing to the internal :mod:`voicecli._markdown_directives`
implementation. Existing call sites continue to import names from this module.
"""

from pathlib import Path

from voicecli.markdown_types import (
    Segment,
    TTSDocument,
    compose_instruct,
    parse_frontmatter,
    strip_markdown,
)

__all__ = [
    "Segment",
    "TTSDocument",
    "compose_instruct",
    "parse_frontmatter",
    "strip_markdown",
    "parse_md_file",
    "_parse_comment_kvs",
    "_parse_segments",
]


def parse_md_file(path: Path) -> TTSDocument:
    """Parse a .md file into a TTSDocument."""
    # Deferred import keeps voicecli.markdown free of any back-reference at
    # module load time; voicecli._markdown_directives now depends only on
    # voicecli.markdown_types, so the historical cycle is gone.
    from voicecli._markdown_directives import parse_md_file as _parse

    return _parse(path)


def _parse_comment_kvs(content: str) -> dict[str, str]:
    """Re-export from _markdown_directives (used by tests and any external callers)."""
    from voicecli._markdown_directives import _parse_comment_kvs as _fn

    return _fn(content)


def _parse_segments(body: str, defaults: dict) -> list[Segment]:
    """Re-export from _markdown_directives (used by tests and any external callers)."""
    from voicecli._markdown_directives import _parse_segments as _fn

    return _fn(body, defaults)
