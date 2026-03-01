"""Tests for voicecli.markdown — parsing, directives, segments."""

import pytest
from pathlib import Path

from voicecli.markdown import (
    _parse_comment_kvs,
    compose_instruct,
    parse_frontmatter,
    strip_markdown,
    parse_md_file,
    _parse_segments,
    Segment,
)


# ── _parse_comment_kvs ──────────────────────────────────────────────


class TestParseCommentKvs:
    def test_single_key(self):
        assert _parse_comment_kvs(' emotion: "Passionnée" ') == {"emotion": "Passionnée"}

    def test_multi_key(self):
        result = _parse_comment_kvs('emotion: "Passionnée", speed: "Rapide"')
        assert result == {"emotion": "Passionnée", "speed": "Rapide"}

    def test_commas_inside_quotes(self):
        result = _parse_comment_kvs('emotion: "Passionnée, mais contenue", speed: "Lent"')
        assert result == {"emotion": "Passionnée, mais contenue", "speed": "Lent"}

    def test_unquoted_value(self):
        result = _parse_comment_kvs("exaggeration: 0.8")
        assert result == {"exaggeration": "0.8"}

    def test_mixed_quoted_unquoted(self):
        result = _parse_comment_kvs('emotion: "Triste", exaggeration: 0.5')
        assert result == {"emotion": "Triste", "exaggeration": "0.5"}

    def test_single_quotes(self):
        result = _parse_comment_kvs("emotion: 'Joyeuse'")
        assert result == {"emotion": "Joyeuse"}

    def test_unicode(self):
        result = _parse_comment_kvs('emotion: "Émouvant 🎭"')
        assert result == {"emotion": "Émouvant 🎭"}

    def test_empty(self):
        assert _parse_comment_kvs("") == {}
        assert _parse_comment_kvs("   ") == {}

    def test_three_keys(self):
        result = _parse_comment_kvs('accent: "Français", speed: "Lent", emotion: "Calme"')
        assert result == {"accent": "Français", "speed": "Lent", "emotion": "Calme"}


# ── compose_instruct ────────────────────────────────────────────────


class TestComposeInstruct:
    def test_all_parts(self):
        result = compose_instruct("Français", "Chaleureuse", "Rapide", "Joyeuse")
        assert result == "Français. Chaleureuse. Rapide. Joyeuse"

    def test_some_parts(self):
        result = compose_instruct(accent="Français", emotion="Triste")
        assert result == "Français. Triste"

    def test_none(self):
        assert compose_instruct() is None

    def test_empty_strings_ignored(self):
        assert compose_instruct("", None, "", None) is None


# ── parse_frontmatter ───────────────────────────────────────────────


class TestParseFrontmatter:
    def test_basic(self):
        content = "---\nlanguage: fr\nvoice: Lyra\n---\nHello world"
        meta, body = parse_frontmatter(content)
        assert meta == {"language": "fr", "voice": "Lyra"}
        assert body == "Hello world"

    def test_quoted_values(self):
        content = '---\nemotion: "Passionnée"\nspeed: \'Rapide\'\n---\nText'
        meta, body = parse_frontmatter(content)
        assert meta["emotion"] == "Passionnée"
        assert meta["speed"] == "Rapide"

    def test_no_frontmatter(self):
        content = "Just some text"
        meta, body = parse_frontmatter(content)
        assert meta == {}
        assert body == "Just some text"


# ── _parse_segments + merge ─────────────────────────────────────────


class TestParseSegmentsMerge:
    def test_single_override_inherits_rest(self):
        """Override emotion only → accent/personality/speed inherited from defaults."""
        defaults = {
            "accent": "Français",
            "personality": "Chaleureuse",
            "speed": "Normal",
            "emotion": "Neutre",
        }
        body = '<!-- emotion: "Joyeuse" -->\nBonjour le monde!'
        segments = _parse_segments(body, defaults)
        assert len(segments) == 1
        seg = segments[0]
        assert seg.accent == "Français"
        assert seg.personality == "Chaleureuse"
        assert seg.speed == "Normal"
        assert seg.emotion == "Joyeuse"
        # instruct should be composed from all 4 parts
        assert "Joyeuse" in seg.instruct
        assert "Français" in seg.instruct

    def test_multi_key_override(self):
        """Multi-key directive on single line."""
        defaults = {"accent": "Français", "speed": "Normal"}
        body = '<!-- emotion: "Triste", speed: "Lent" -->\nTexte ici.'
        segments = _parse_segments(body, defaults)
        assert len(segments) == 1
        seg = segments[0]
        assert seg.emotion == "Triste"
        assert seg.speed == "Lent"
        assert seg.accent == "Français"  # inherited

    def test_instruct_bypass(self):
        """Explicit instruct directive bypasses composition."""
        defaults = {"accent": "Français", "emotion": "Neutre"}
        body = '<!-- instruct: "Read this like a robot" -->\nBeep boop.'
        segments = _parse_segments(body, defaults)
        assert len(segments) == 1
        assert segments[0].instruct == "Read this like a robot"

    def test_independent_segments(self):
        """Two segments with different overrides."""
        defaults = {"accent": "Français"}
        body = (
            '<!-- emotion: "Joyeuse" -->\nPremier segment.\n\n'
            '<!-- emotion: "Triste" -->\nDeuxième segment.'
        )
        segments = _parse_segments(body, defaults)
        assert len(segments) == 2
        assert segments[0].emotion == "Joyeuse"
        assert segments[1].emotion == "Triste"
        # Both inherit accent
        assert segments[0].accent == "Français"
        assert segments[1].accent == "Français"

    def test_backward_compat_consecutive(self):
        """Two consecutive single-key comments accumulate (old format)."""
        defaults = {}
        body = (
            '<!-- emotion: "Passionnée" -->\n'
            '<!-- speed: "Rapide" -->\n'
            "Texte après deux directives."
        )
        segments = _parse_segments(body, defaults)
        assert len(segments) == 1
        assert segments[0].emotion == "Passionnée"
        assert segments[0].speed == "Rapide"

    def test_no_directives(self):
        """Body without directives → single segment with defaults."""
        defaults = {"accent": "Français"}
        segments = _parse_segments("Juste du texte.", defaults)
        assert len(segments) == 1
        assert segments[0].accent == "Français"

    def test_float_directive(self):
        defaults = {}
        body = '<!-- exaggeration: 0.8 -->\nTexte.'
        segments = _parse_segments(body, defaults)
        assert segments[0].exaggeration == 0.8

    def test_multi_key_with_float(self):
        defaults = {}
        body = '<!-- emotion: "Intense", exaggeration: 0.9 -->\nTexte.'
        segments = _parse_segments(body, defaults)
        assert segments[0].emotion == "Intense"
        assert segments[0].exaggeration == pytest.approx(0.9)


# ── strip_markdown ──────────────────────────────────────────────────


class TestStripMarkdown:
    def test_headers(self):
        assert strip_markdown("# Title") == "Title"
        assert strip_markdown("## Subtitle") == "Subtitle"

    def test_bold(self):
        assert strip_markdown("**bold**") == "bold"

    def test_links(self):
        assert strip_markdown("[click](http://example.com)") == "click"

    def test_paralinguistic_tags(self):
        result = strip_markdown("Hello [laugh] world")
        assert "[laugh]" in result

    def test_paragraphs_joined(self):
        result = strip_markdown("First paragraph.\n\nSecond paragraph.")
        assert ". " in result


# ── parse_md_file (integration) ─────────────────────────────────────


class TestParseMdFile:
    def test_full_document(self, tmp_path: Path):
        md = tmp_path / "test.md"
        md.write_text(
            '---\nlanguage: fr\naccent: "Accent français"\n'
            'personality: "Chaleureuse"\nspeed: "Normal"\n'
            'emotion: "Neutre"\n---\n\n'
            '<!-- emotion: "Passionnée", speed: "Rapide et haché" -->\n'
            "Voici le plan d'action.\n\n"
            '<!-- emotion: "Calme" -->\n'
            "Et maintenant, la conclusion.\n",
            encoding="utf-8",
        )
        doc = parse_md_file(md)
        assert doc.language == "fr"
        assert len(doc.segments) == 2

        seg1 = doc.segments[0]
        assert seg1.emotion == "Passionnée"
        assert seg1.speed == "Rapide et haché"
        assert seg1.accent == "Accent français"  # inherited

        seg2 = doc.segments[1]
        assert seg2.emotion == "Calme"
        assert seg2.speed == "Normal"  # back to default
        assert seg2.accent == "Accent français"  # inherited

    def test_no_frontmatter(self, tmp_path: Path):
        md = tmp_path / "plain.md"
        md.write_text("Just plain text.\n", encoding="utf-8")
        doc = parse_md_file(md)
        assert len(doc.segments) == 1
        assert "plain text" in doc.segments[0].text

    def test_backward_compat_single_key(self, tmp_path: Path):
        md = tmp_path / "old.md"
        md.write_text(
            '---\nlanguage: fr\n---\n\n'
            '<!-- emotion: "Triste" -->\n'
            "Texte triste.\n",
            encoding="utf-8",
        )
        doc = parse_md_file(md)
        assert len(doc.segments) == 1
        assert doc.segments[0].emotion == "Triste"
