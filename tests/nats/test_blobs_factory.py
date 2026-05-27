"""Unit tests for voicecli.adapters.nats.blobs.get_blobstore() (issue #144).

Covers the public contract of the module-level singleton factory:
- Singleton caching: consecutive calls return the same instance.
- ADR-068 enforcement: non-'http' backend raises ValueError mentioning ADR-068.
- Missing env vars: absent BLOBSTORE_URL / BLOBSTORE_BEARER_TOKEN raise KeyError.

Every test resets blobs._INSTANCE via an autouse fixture to prevent
cross-test pollution from the module-level cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generator

import pytest

from voicecli.adapters.nats import blobs


# ---------------------------------------------------------------------------
# Fake HttpBlobStore — avoids touching real httpx internals at test time
# ---------------------------------------------------------------------------


@dataclass
class _FakeHttpBlobStore:
    """Minimal stand-in that records constructor args without network I/O."""

    base_url: str
    token: str


# ---------------------------------------------------------------------------
# Autouse fixture: reset module-level singleton before each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_blobstore_instance(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Clear blobs._INSTANCE before (and after) each test.

    The factory caches its result at module level. Without this reset, a test
    that successfully constructs a store will pollute every subsequent test in
    the same pytest worker — e.g. missing-env tests would skip construction
    entirely and return the cached instance instead of raising.
    """
    monkeypatch.setattr(blobs, "_INSTANCE", None)
    yield
    monkeypatch.setattr(blobs, "_INSTANCE", None)


# ---------------------------------------------------------------------------
# Fixture: patch HttpBlobStore with the fake so we don't touch httpx
# ---------------------------------------------------------------------------


@pytest.fixture()
def _fake_http_blobstore(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace HttpBlobStore with _FakeHttpBlobStore for construction tests."""
    monkeypatch.setattr(blobs, "HttpBlobStore", _FakeHttpBlobStore)


# ===========================================================================
# TestSingletonCaching
# ===========================================================================


class TestSingletonCaching:
    def test_consecutive_calls_return_same_instance(
        self, monkeypatch: pytest.MonkeyPatch, _fake_http_blobstore: None
    ) -> None:
        """Two consecutive get_blobstore() calls must return the identical object."""
        # Arrange
        monkeypatch.setenv("BLOBSTORE_BACKEND", "http")
        monkeypatch.setenv("BLOBSTORE_URL", "http://lyra-hub:8080")
        monkeypatch.setenv("BLOBSTORE_BEARER_TOKEN", "secret-token")

        # Act
        a = blobs.get_blobstore()
        b = blobs.get_blobstore()

        # Assert — same identity, not just equality
        assert a is b

    def test_singleton_records_correct_url(
        self, monkeypatch: pytest.MonkeyPatch, _fake_http_blobstore: None
    ) -> None:
        """The constructed store carries the URL from BLOBSTORE_URL."""
        # Arrange
        monkeypatch.setenv("BLOBSTORE_BACKEND", "http")
        monkeypatch.setenv("BLOBSTORE_URL", "http://hub.example.com:9000")
        monkeypatch.setenv("BLOBSTORE_BEARER_TOKEN", "tok")

        # Act
        store = blobs.get_blobstore()

        # Assert
        assert isinstance(store, _FakeHttpBlobStore)
        assert store.base_url == "http://hub.example.com:9000"

    def test_singleton_records_correct_token(
        self, monkeypatch: pytest.MonkeyPatch, _fake_http_blobstore: None
    ) -> None:
        """The constructed store carries the token from BLOBSTORE_BEARER_TOKEN."""
        # Arrange
        monkeypatch.setenv("BLOBSTORE_BACKEND", "http")
        monkeypatch.setenv("BLOBSTORE_URL", "http://hub.example.com:9000")
        monkeypatch.setenv("BLOBSTORE_BEARER_TOKEN", "my-bearer-tok")

        # Act
        store = blobs.get_blobstore()

        # Assert
        assert isinstance(store, _FakeHttpBlobStore)
        assert store.token == "my-bearer-tok"


# ===========================================================================
# TestADR068Enforcement
# ===========================================================================


class TestADR068Enforcement:
    def test_flat_fs_backend_raises_value_error_mentioning_adr068(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BLOBSTORE_BACKEND='flat-fs' must raise ValueError naming ADR-068.

        Negative-test: if the guard ('if backend != "http"') is removed from
        get_blobstore(), this test fails — the guard is load-bearing.
        """
        # Arrange
        monkeypatch.setenv("BLOBSTORE_BACKEND", "flat-fs")

        # Act + Assert
        with pytest.raises(ValueError, match="ADR-068"):
            blobs.get_blobstore()

    def test_empty_backend_raises_value_error_mentioning_adr068(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing / empty BLOBSTORE_BACKEND (defaults to '') must also raise.

        'flat-fs' is the canonical non-http value tested above; '' exercises the
        default branch (os.environ.get returns '' when the var is unset).
        """
        # Arrange — remove BLOBSTORE_BACKEND entirely
        monkeypatch.delenv("BLOBSTORE_BACKEND", raising=False)

        # Act + Assert
        with pytest.raises(ValueError, match="ADR-068"):
            blobs.get_blobstore()

    def test_arbitrary_non_http_backend_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Any value other than 'http' triggers the ADR-068 guard."""
        # Arrange
        monkeypatch.setenv("BLOBSTORE_BACKEND", "local-disk")

        # Act + Assert
        with pytest.raises(ValueError, match="ADR-068"):
            blobs.get_blobstore()


# ===========================================================================
# TestMissingEnvVars
# ===========================================================================


class TestMissingEnvVars:
    def test_missing_blobstore_url_raises_key_error(
        self, monkeypatch: pytest.MonkeyPatch, _fake_http_blobstore: None
    ) -> None:
        """BLOBSTORE_URL absent → KeyError.

        os.environ["BLOBSTORE_URL"] raises KeyError when the key does not exist;
        get_blobstore() must not swallow it.
        """
        # Arrange
        monkeypatch.setenv("BLOBSTORE_BACKEND", "http")
        monkeypatch.delenv("BLOBSTORE_URL", raising=False)
        monkeypatch.setenv("BLOBSTORE_BEARER_TOKEN", "tok")

        # Act + Assert
        with pytest.raises(KeyError):
            blobs.get_blobstore()

    def test_missing_blobstore_bearer_token_raises_key_error(
        self, monkeypatch: pytest.MonkeyPatch, _fake_http_blobstore: None
    ) -> None:
        """BLOBSTORE_BEARER_TOKEN absent → KeyError.

        Symmetric to the URL test — both env vars are mandatory for http backend.
        """
        # Arrange
        monkeypatch.setenv("BLOBSTORE_BACKEND", "http")
        monkeypatch.setenv("BLOBSTORE_URL", "http://hub:8080")
        monkeypatch.delenv("BLOBSTORE_BEARER_TOKEN", raising=False)

        # Act + Assert
        with pytest.raises(KeyError):
            blobs.get_blobstore()
