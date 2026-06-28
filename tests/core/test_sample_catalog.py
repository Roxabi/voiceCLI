"""Tests for sample_catalog.py."""

from __future__ import annotations

import json

import pytest

from voicecli.core import sample_catalog as cat


@pytest.fixture(autouse=True)
def _isolate_catalog(tmp_path, monkeypatch):
    samples = tmp_path / "samples"
    samples.mkdir()
    monkeypatch.setattr(cat, "SAMPLES_DIR", samples)
    monkeypatch.setattr(cat, "CATALOG_PATH", samples / "catalog.json")
    monkeypatch.setattr(cat, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr("voicecli.core.samples.SAMPLES_DIR", samples)
    yield


def test_catalog_revision_changes_when_sample_added(tmp_path) -> None:
    rev1 = cat.catalog_revision()
    (cat.SAMPLES_DIR / "a.wav").write_bytes(b"wav")
    cat.register_sample("a.wav")
    rev2 = cat.catalog_revision()
    assert rev1 != rev2


def test_list_catalog_entries_marks_local_cached() -> None:
    (cat.SAMPLES_DIR / "demo.wav").write_bytes(b"x")
    cat.register_sample("demo.wav")
    entries = cat.list_catalog_entries()
    assert entries[0]["id"] == "demo.wav"
    assert entries[0]["cached"] is True
    assert entries[0]["store_key"] is None
