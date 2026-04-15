"""RED-phase tests for voicecli.nats.reply.build_reply (issue #42).

These tests FAIL until voicecli.nats.reply is implemented.
They define the contract: every reply dict carries contract_version == "1"
and the appropriate fields for success vs error responses.
"""

from __future__ import annotations

from voicecli.nats.reply import CONTRACT_VERSION, build_reply


class TestBuildReply:
    def test_build_reply_stamps_contract_version_on_success(self) -> None:
        """Success reply includes contract_version == "1" and round-trips request_id."""
        # Arrange / Act
        result = build_reply(
            ok=True,
            request_id="r-1",
            text="hi",
            language="en",
            duration_seconds=0.5,
        )

        # Assert
        assert result["contract_version"] == "1"
        assert result["request_id"] == "r-1"
        assert result["ok"] is True

    def test_build_reply_stamps_contract_version_on_error(self) -> None:
        """Error reply includes contract_version == "1" and the error string; no audio_b64."""
        # Arrange / Act
        result = build_reply(ok=False, request_id="r-1", error="model_load_failed")

        # Assert
        assert result["contract_version"] == "1"
        assert result["error"] == "model_load_failed"
        assert "audio_b64" not in result

    def test_build_reply_handles_empty_request_id(self) -> None:
        """build_reply preserves an empty request_id without coercing it."""
        # Arrange / Act
        result = build_reply(ok=False, request_id="", error="malformed_request")

        # Assert
        assert result["request_id"] == ""

    def test_stamps_override_caller_kwargs(self) -> None:
        """Stamps (contract_version, ok, request_id) overwrite colliding caller fields."""
        # Arrange — pass spoofed stamp values via **fields; explicit ok/request_id are stamps
        # Note: Python rejects duplicate explicit kwargs at parse time, so contract_version
        # is the primary guard exercised here (a caller sneaking it in via **fields).
        fields = {"contract_version": "spoof", "extra": "data"}
        reply = build_reply(ok=True, request_id="r", **fields)

        # Assert — stamps must win over caller-supplied kwargs
        assert reply["contract_version"] == CONTRACT_VERSION
        assert reply["ok"] is True
        assert reply["request_id"] == "r"
