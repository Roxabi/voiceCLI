"""Telegram helper — send audio, messages, and poll for replies via the Bot API.

CLI usage:
  python -m voicecli.tg send <file.mp3> <caption>
  python -m voicecli.tg message <text>
  python -m voicecli.tg poll <timeout_seconds> <after_update_id>
  python -m voicecli.tg parse <ranking_string>
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path


# ---------------------------------------------------------------------------
# Config loading (mirrors config.py walk-up logic)
# ---------------------------------------------------------------------------


def _find_config() -> Path | None:
    """Walk up from CWD to $HOME looking for voicecli.toml."""
    home = Path.home().resolve()
    current = Path.cwd().resolve()
    while True:
        candidate = current / "voicecli.toml"
        if candidate.is_file():
            return candidate
        if current == home or current.parent == current:
            return None
        current = current.parent


def load_telegram_config() -> dict[str, str]:
    """Load [telegram] section from voicecli.toml.

    Returns a dict with keys ``bot_token`` and ``chat_id``.
    Raises ``RuntimeError`` if the config file or section is missing.
    """
    path = _find_config()
    if path is None:
        raise RuntimeError("voicecli.toml not found (searched from CWD to $HOME)")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    tg = data.get("telegram")
    if not tg:
        raise RuntimeError("[telegram] section missing from voicecli.toml")
    bot_token = tg.get("bot_token", "")
    chat_id = str(tg.get("chat_id", ""))
    if not bot_token or not chat_id:
        raise RuntimeError("[telegram] section must have bot_token and chat_id")
    return {"bot_token": bot_token, "chat_id": chat_id}


# ---------------------------------------------------------------------------
# Core API helpers
# ---------------------------------------------------------------------------


def _api_url(bot_token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}/{method}"


def _curl(*args: str) -> dict:
    """Run curl with the given args and return parsed JSON response."""
    result = subprocess.run(
        ["curl", "--silent", "--show-error", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from Telegram: {result.stdout[:200]}") from exc


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def send_audio(file_path: str, caption: str) -> dict:
    """Send an audio file to the configured Telegram chat.

    Args:
        file_path: Path to the audio file (MP3 or WAV).
        caption: Caption shown below the audio in Telegram.

    Returns:
        Telegram API response dict.
    """
    cfg = load_telegram_config()
    url = _api_url(cfg["bot_token"], "sendAudio")
    return _curl(
        "-X",
        "POST",
        url,
        "-F",
        f"chat_id={cfg['chat_id']}",
        "-F",
        f"audio=@{file_path}",
        "-F",
        f"caption={caption}",
    )


def send_message(text: str) -> dict:
    """Send a text message to the configured Telegram chat.

    Args:
        text: Message text (supports Markdown parse_mode).

    Returns:
        Telegram API response dict.
    """
    cfg = load_telegram_config()
    url = _api_url(cfg["bot_token"], "sendMessage")
    return _curl(
        "-X",
        "POST",
        url,
        "-F",
        f"chat_id={cfg['chat_id']}",
        "-F",
        f"text={text}",
    )


def poll(timeout_seconds: int, after_update_id: int) -> dict:
    """Long-poll for a reply from the configured chat.

    Blocks until a message arrives from the configured chat_id or until
    ``timeout_seconds`` elapses.

    Args:
        timeout_seconds: How long to wait for a reply.
        after_update_id: Only look at updates with update_id > this value.

    Returns:
        Dict ``{"text": "...", "update_id": 123}`` on success.
        Dict ``{"text": null, "update_id": after_update_id}`` on timeout.
    """
    cfg = load_telegram_config()
    chat_id = str(cfg["chat_id"])
    bot_token = cfg["bot_token"]
    offset = after_update_id + 1
    url = (
        f"https://api.telegram.org/bot{bot_token}/getUpdates"
        f"?offset={offset}&timeout={timeout_seconds}&allowed_updates=message"
    )
    response = _curl(url)
    if not response.get("ok"):
        raise RuntimeError(f"Telegram getUpdates error: {response}")
    updates = response.get("result", [])
    for update in updates:
        msg = update.get("message", {})
        from_chat = str(msg.get("chat", {}).get("id", ""))
        if from_chat == chat_id:
            text = msg.get("text", "")
            return {"text": text, "update_id": update["update_id"]}
    # No matching update in this long-poll window
    last_id = updates[-1]["update_id"] if updates else after_update_id
    return {"text": None, "update_id": last_id}


def parse_ranking(ranking_str: str) -> list[str]:
    """Parse a ranking string into an ordered list of identifiers.

    Handles formats such as:
      "2>1>3", "c2>c1", "e2", "2", "2 > 1", "2,1,3"

    Letter prefixes (a–z) are stripped; only the trailing digit(s) are kept.

    Args:
        ranking_str: Raw ranking string from the user.

    Returns:
        List of digit-only identifiers in ranked order.
    """
    # Normalise separators: replace ">" and "," with spaces
    normalised = re.sub(r"[>,]", " ", ranking_str)
    tokens = normalised.split()
    result: list[str] = []
    for token in tokens:
        # Strip leading letter prefix(es), keep trailing digits
        match = re.search(r"\d+", token)
        if match:
            result.append(match.group())
    return result


# ---------------------------------------------------------------------------
# CLI dispatcher
# ---------------------------------------------------------------------------


def _cmd_send(args: list[str]) -> None:
    if len(args) < 2:
        print("Usage: tg send <file.mp3> <caption>", file=sys.stderr)
        sys.exit(1)
    file_path, caption = args[0], " ".join(args[1:])
    response = send_audio(file_path, caption)
    print(json.dumps(response))


def _cmd_message(args: list[str]) -> None:
    if not args:
        print("Usage: tg message <text>", file=sys.stderr)
        sys.exit(1)
    text = " ".join(args)
    response = send_message(text)
    print(json.dumps(response))


def _cmd_poll(args: list[str]) -> None:
    if len(args) < 2:
        print("Usage: tg poll <timeout_seconds> <after_update_id>", file=sys.stderr)
        sys.exit(1)
    timeout = int(args[0])
    after_id = int(args[1])
    result = poll(timeout, after_id)
    print(json.dumps(result))


def _cmd_parse(args: list[str]) -> None:
    if not args:
        print('Usage: tg parse "<ranking_string>"', file=sys.stderr)
        sys.exit(1)
    ranking_str = " ".join(args)
    result = parse_ranking(ranking_str)
    print(json.dumps(result))


_COMMANDS: dict[str, object] = {
    "send": _cmd_send,
    "message": _cmd_message,
    "poll": _cmd_poll,
    "parse": _cmd_parse,
}


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m voicecli.tg``."""
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in _COMMANDS:
        print(
            "Usage: python -m voicecli.tg <command> [args]\nCommands: send, message, poll, parse",
            file=sys.stderr,
        )
        sys.exit(1)
    cmd = args[0]
    handler = _COMMANDS[cmd]
    handler(args[1:])  # type: ignore[operator]


if __name__ == "__main__":
    main()
