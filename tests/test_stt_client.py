"""Tests for voicecli.stt_client and load_stt_config from voicecli.config."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# T9 — _send_request
# ---------------------------------------------------------------------------


class TestSendRequest:
    """Tests for stt_client._send_request — wire-protocol helper."""

    def test_success_returns_parsed_json(self):
        """Happy path: daemon responds with valid JSON → dict is returned."""
        from voicecli.stt_client import _send_request

        response_payload = json.dumps({"status": "ok", "state": "idle"}) + "\n"

        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [response_payload.encode(), b""]

        with patch("socket.socket", return_value=mock_sock):
            # Act
            result = _send_request("status")

        # Assert
        assert result == {"status": "ok", "state": "idle"}
        mock_sock.connect.assert_called_once()
        mock_sock.sendall.assert_called_once()
        mock_sock.close.assert_called_once()

    def test_send_request_sends_correct_action(self):
        """_send_request sends a JSON payload with the correct action field."""
        from voicecli.stt_client import _send_request

        response_payload = json.dumps({"status": "ok"}) + "\n"
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [response_payload.encode(), b""]

        with patch("socket.socket", return_value=mock_sock):
            _send_request("toggle")

        # Assert the payload sent contains the correct action
        sent_bytes = mock_sock.sendall.call_args[0][0]
        sent_obj = json.loads(sent_bytes.decode().strip())
        assert sent_obj == {"action": "toggle"}

    def test_connection_refused_returns_error_dict(self):
        """ConnectionRefusedError → error dict with daemon-not-running message."""
        from voicecli.stt_client import _send_request

        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError

        with patch("socket.socket", return_value=mock_sock):
            # Act
            result = _send_request("toggle")

        # Assert
        assert result == {"status": "error", "message": "STT daemon not running"}
        mock_sock.close.assert_called_once()

    def test_file_not_found_returns_error_dict(self):
        """FileNotFoundError (socket absent) → error dict."""
        from voicecli.stt_client import _send_request

        mock_sock = MagicMock()
        mock_sock.connect.side_effect = FileNotFoundError

        with patch("socket.socket", return_value=mock_sock):
            result = _send_request("status")

        assert result == {"status": "error", "message": "STT daemon not running"}
        mock_sock.close.assert_called_once()

    def test_os_error_returns_error_dict(self):
        """Generic OSError → error dict (covers other socket failures)."""
        from voicecli.stt_client import _send_request

        mock_sock = MagicMock()
        mock_sock.connect.side_effect = OSError("connection failed")

        with patch("socket.socket", return_value=mock_sock):
            result = _send_request("ping")

        assert result["status"] == "error"
        assert result["message"] == "STT daemon not running"

    def test_socket_closed_in_finally(self):
        """Socket.close() is always called — even on error — via finally block."""
        from voicecli.stt_client import _send_request

        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError

        with patch("socket.socket", return_value=mock_sock):
            _send_request("toggle")

        mock_sock.close.assert_called_once()

    def test_multipart_recv_assembled_correctly(self):
        """Response arriving in multiple recv() chunks is reassembled before parsing."""
        from voicecli.stt_client import _send_request

        full_payload = json.dumps({"status": "ok", "text": "hello"}) + "\n"
        # Split into two chunks
        chunk1 = full_payload[:10].encode()
        chunk2 = full_payload[10:].encode()

        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [chunk1, chunk2, b""]

        with patch("socket.socket", return_value=mock_sock):
            result = _send_request("toggle")

        assert result == {"status": "ok", "text": "hello"}


# ---------------------------------------------------------------------------
# T9 — send_toggle / send_status wrappers
# ---------------------------------------------------------------------------


class TestSendToggleAndStatus:
    """send_toggle and send_status are thin wrappers around _send_request."""

    def test_send_toggle_calls_send_request_with_toggle(self):
        """send_toggle() invokes _send_request('toggle') and returns its result."""
        from voicecli.stt_client import send_toggle

        expected = {"status": "ok", "state": "recording"}

        with patch("voicecli.stt_client._send_request", return_value=expected) as mock_req:
            result = send_toggle()

        mock_req.assert_called_once_with("toggle", timeout=60)
        assert result == expected

    def test_send_status_calls_send_request_with_status(self):
        """send_status() invokes _send_request('status') and returns its result."""
        from voicecli.stt_client import send_status

        expected = {"status": "ok", "state": "idle"}

        with patch("voicecli.stt_client._send_request", return_value=expected) as mock_req:
            result = send_status()

        mock_req.assert_called_once_with("status")
        assert result == expected


# ---------------------------------------------------------------------------
# T9 — notify
# ---------------------------------------------------------------------------


class TestNotify:
    """Tests for stt_client.notify — desktop notification via notify-send."""

    def test_notify_skipped_when_notify_send_missing(self):
        """notify() is a no-op when notify-send is not installed."""
        from voicecli.stt_client import notify

        with (
            patch("shutil.which", return_value=None),
            patch("subprocess.run") as mock_run,
        ):
            notify("Recording...")

        mock_run.assert_not_called()

    def test_notify_calls_subprocess_when_notify_send_present(self):
        """notify() calls subprocess.run with notify-send and correct args."""
        from voicecli.stt_client import _NOTIFY_REPLACE_ID, notify

        with (
            patch("shutil.which", return_value="/usr/bin/notify-send"),
            patch("subprocess.run") as mock_run,
        ):
            notify("Recording...", timeout=5000)

        mock_run.assert_called_once_with(
            [
                "notify-send",
                "-r",
                _NOTIFY_REPLACE_ID,
                "VoiceCLI",
                "Recording...",
                "-t",
                "5000",
            ],
            check=False,
            capture_output=True,
        )

    def test_notify_uses_replace_id_to_replace_previous_bubble(self):
        """notify() includes the stable replace-ID so bubbles replace each other."""
        from voicecli.stt_client import _NOTIFY_REPLACE_ID, notify

        with (
            patch("shutil.which", return_value="/usr/bin/notify-send"),
            patch("subprocess.run") as mock_run,
        ):
            notify("msg1")
            notify("msg2")

        # Both calls use the same replace-ID
        assert mock_run.call_count == 2
        for c in mock_run.call_args_list:
            args_list = c[0][0]
            assert "-r" in args_list
            idx = args_list.index("-r")
            assert args_list[idx + 1] == _NOTIFY_REPLACE_ID

    def test_notify_default_timeout_is_3000(self):
        """notify() uses 3000 ms as the default timeout."""
        from voicecli.stt_client import notify

        with (
            patch("shutil.which", return_value="/usr/bin/notify-send"),
            patch("subprocess.run") as mock_run,
        ):
            notify("hello")

        args_list = mock_run.call_args[0][0]
        idx = args_list.index("-t")
        assert args_list[idx + 1] == "3000"

    def test_notify_survives_subprocess_exception(self):
        """notify() swallows exceptions from subprocess.run silently."""
        from voicecli.stt_client import notify

        with (
            patch("shutil.which", return_value="/usr/bin/notify-send"),
            patch("subprocess.run", side_effect=OSError("display not found")),
        ):
            # Should not raise
            notify("message")


# ---------------------------------------------------------------------------
# T9 — auto_paste
# ---------------------------------------------------------------------------


class TestAutoPaste:
    """Tests for stt_client.auto_paste — type text via xdotool."""

    def test_auto_paste_skipped_when_xdotool_missing(self):
        """auto_paste() is a no-op when xdotool is not installed."""
        from voicecli.stt_client import auto_paste

        with (
            patch("shutil.which", return_value=None),
            patch("subprocess.run") as mock_run,
        ):
            auto_paste("hello world")

        mock_run.assert_not_called()

    def test_auto_paste_calls_xdotool_with_correct_args(self):
        """auto_paste() calls xdotool type with the provided text."""
        from voicecli.stt_client import auto_paste

        with (
            patch("shutil.which", return_value="/usr/bin/xdotool"),
            patch("subprocess.run") as mock_run,
            patch("time.sleep"),  # skip the 150ms wait
        ):
            auto_paste("hello world")

        mock_run.assert_called_once_with(
            ["xdotool", "type", "--clearmodifiers", "--", "hello world"],
            check=False,
            capture_output=True,
        )

    def test_auto_paste_sleeps_before_typing(self):
        """auto_paste() calls time.sleep(0.15) before invoking xdotool."""
        from voicecli.stt_client import auto_paste

        with (
            patch("shutil.which", return_value="/usr/bin/xdotool"),
            patch("subprocess.run"),
            patch("time.sleep") as mock_sleep,
        ):
            auto_paste("text")

        mock_sleep.assert_called_once_with(0.15)

    def test_auto_paste_survives_subprocess_exception(self):
        """auto_paste() swallows exceptions from xdotool silently."""
        from voicecli.stt_client import auto_paste

        with (
            patch("shutil.which", return_value="/usr/bin/xdotool"),
            patch("subprocess.run", side_effect=OSError("no display")),
            patch("time.sleep"),
        ):
            # Should not raise
            auto_paste("text")


# ---------------------------------------------------------------------------
# T9 — hotkey_loop
# ---------------------------------------------------------------------------


def _make_pynput_mock(mock_global_hotkeys_cls):
    """Build a (pynput_mock, keyboard_mock) pair where pynput.keyboard is wired correctly.

    ``from pynput import keyboard`` resolves the ``keyboard`` attribute from the
    ``pynput`` module object in sys.modules, *not* ``sys.modules['pynput.keyboard']``.
    Both must be set consistently.
    """
    mock_keyboard = MagicMock()
    mock_keyboard.GlobalHotKeys = mock_global_hotkeys_cls
    pynput_mock = MagicMock()
    pynput_mock.keyboard = mock_keyboard
    return pynput_mock, mock_keyboard


class TestHotkeyLoop:
    """Tests for stt_client.hotkey_loop — pynput GlobalHotKeys wrapper."""

    def _make_listener(self):
        """Return a mock listener that exits via KeyboardInterrupt on join()."""
        inst = MagicMock()
        inst.__enter__ = MagicMock(return_value=inst)
        inst.__exit__ = MagicMock(return_value=False)
        inst.join.side_effect = KeyboardInterrupt
        return inst

    def test_hotkey_loop_constructs_global_hotkeys_with_correct_format(self):
        """hotkey_loop converts 'alt+space' to '<alt>+<space>' for GlobalHotKeys."""
        from voicecli.stt_client import hotkey_loop

        mock_global_hotkeys_cls = MagicMock(return_value=self._make_listener())
        pynput_mock, mock_keyboard = _make_pynput_mock(mock_global_hotkeys_cls)

        with (
            patch.dict("sys.modules", {"pynput": pynput_mock, "pynput.keyboard": mock_keyboard}),
            patch("voicecli.stt_client.send_toggle", return_value={"status": "error"}),
        ):
            hotkey_loop(hotkey="alt+space")

        # Assert GlobalHotKeys was constructed with the pynput-formatted hotkey
        call_args = mock_global_hotkeys_cls.call_args
        hotkeys_dict = call_args[0][0]
        assert "<alt>+<space>" in hotkeys_dict

    def test_hotkey_loop_multi_key_combo_formatted_correctly(self):
        """hotkey_loop converts 'ctrl+shift+d' to '<ctrl>+<shift>+<d>'."""
        from voicecli.stt_client import hotkey_loop

        mock_global_hotkeys_cls = MagicMock(return_value=self._make_listener())
        pynput_mock, mock_keyboard = _make_pynput_mock(mock_global_hotkeys_cls)

        with (
            patch.dict("sys.modules", {"pynput": pynput_mock, "pynput.keyboard": mock_keyboard}),
            patch("voicecli.stt_client.send_toggle", return_value={"status": "error"}),
        ):
            hotkey_loop(hotkey="ctrl+shift+d")

        hotkeys_dict = mock_global_hotkeys_cls.call_args[0][0]
        assert "<ctrl>+<shift>+d" in hotkeys_dict

    def test_hotkey_loop_callback_triggers_send_toggle(self):
        """The callback registered with GlobalHotKeys calls send_toggle()."""
        import sys

        toggle_calls = []

        def fake_send_toggle():
            toggle_calls.append(1)
            return {"status": "error", "message": "STT daemon not running"}

        def fake_global_hotkeys(hotkeys_dict):
            # Fire the hotkey callback once during join() to simulate a key press,
            # then raise KeyboardInterrupt to let the loop exit cleanly.
            on_hotkey_fn = list(hotkeys_dict.values())[0]

            def join_side_effect():
                on_hotkey_fn()
                raise KeyboardInterrupt

            inst = MagicMock()
            inst.__enter__ = MagicMock(return_value=inst)
            inst.__exit__ = MagicMock(return_value=False)
            inst.join.side_effect = join_side_effect
            return inst

        mock_keyboard = MagicMock()
        mock_keyboard.GlobalHotKeys = fake_global_hotkeys
        pynput_mock = MagicMock()
        pynput_mock.keyboard = mock_keyboard

        # Evict any cached pynput modules so our patch.dict takes effect cleanly.
        for key in list(sys.modules):
            if key == "pynput" or key.startswith("pynput."):
                del sys.modules[key]

        with (
            patch.dict("sys.modules", {"pynput": pynput_mock, "pynput.keyboard": mock_keyboard}),
            patch("voicecli.stt_client.send_toggle", side_effect=fake_send_toggle),
        ):
            from voicecli.stt_client import hotkey_loop

            hotkey_loop(hotkey="alt+space")

        assert len(toggle_calls) == 1, "send_toggle() should have been called once"


# ---------------------------------------------------------------------------
# T10 — load_stt_config
# ---------------------------------------------------------------------------


class TestLoadSttConfig:
    """Tests for config.load_stt_config."""

    def test_returns_default_hotkey_when_no_config_file(self):
        """Returns {'hotkey': 'alt+space'} when no voicecli.toml is found."""
        from voicecli.config import load_stt_config

        with patch("voicecli.config._find_config", return_value=None):
            result = load_stt_config()

        assert result == {"hotkey": "alt+space"}

    def test_reads_stt_table_from_toml(self, tmp_path):
        """Reads [stt] table values from a real toml file via tmp_path."""
        from voicecli.config import load_stt_config

        toml_file = tmp_path / "voicecli.toml"
        toml_file.write_text('[stt]\nhotkey = "ctrl+shift+d"\n')

        result = load_stt_config(config=toml_file)

        assert result["hotkey"] == "ctrl+shift+d"

    def test_custom_hotkey_parsed_correctly(self, tmp_path):
        """A non-default hotkey value from toml is parsed and returned."""
        from voicecli.config import load_stt_config

        toml_file = tmp_path / "voicecli.toml"
        toml_file.write_text('[stt]\nhotkey = "alt+h"\n')

        result = load_stt_config(config=toml_file)

        assert result["hotkey"] == "alt+h"

    def test_missing_stt_table_falls_back_to_defaults(self, tmp_path):
        """When [stt] table is absent, returns default {'hotkey': 'alt+space'}."""
        from voicecli.config import load_stt_config

        toml_file = tmp_path / "voicecli.toml"
        toml_file.write_text('[defaults]\nlanguage = "French"\n')

        result = load_stt_config(config=toml_file)

        assert result == {"hotkey": "alt+space"}

    def test_model_key_in_stt_table_is_parsed(self, tmp_path):
        """The 'model' key from [stt] is also parsed if present."""
        from voicecli.config import load_stt_config

        toml_file = tmp_path / "voicecli.toml"
        toml_file.write_text('[stt]\nhotkey = "alt+space"\nmodel = "large-v3"\n')

        result = load_stt_config(config=toml_file)

        assert result["hotkey"] == "alt+space"
        assert result["model"] == "large-v3"

    def test_explicit_config_path_skips_walk_up(self, tmp_path):
        """Passing config= explicitly uses that file, bypassing _find_config."""
        from voicecli.config import load_stt_config

        toml_file = tmp_path / "custom.toml"
        toml_file.write_text('[stt]\nhotkey = "ctrl+space"\n')

        with patch("voicecli.config._find_config") as mock_find:
            result = load_stt_config(config=toml_file)

        # _find_config should NOT be called when config= is provided
        mock_find.assert_not_called()
        assert result["hotkey"] == "ctrl+space"

    def test_stt_table_with_only_model_preserves_default_hotkey(self, tmp_path):
        """If [stt] only has model (no hotkey), the default hotkey is preserved."""
        from voicecli.config import load_stt_config

        toml_file = tmp_path / "voicecli.toml"
        toml_file.write_text('[stt]\nmodel = "large-v3-turbo"\n')

        result = load_stt_config(config=toml_file)

        assert result["hotkey"] == "alt+space"
        assert result["model"] == "large-v3-turbo"
