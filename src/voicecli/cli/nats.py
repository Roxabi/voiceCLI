"""NATS serve sub-app — TTS and STT satellite CLI commands."""

import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse
from typing import Annotated, Optional

import typer

nats_app = typer.Typer(help="NATS subscriber satellites for hub-driven voice.")

_VALID_NATS_SCHEME = re.compile(r"(nats|tls)://")


def _check_nats_url(nats_url: str, log: logging.Logger) -> None:
    """Validate NATS_URL scheme; exit 2 on invalid, warn on non-TLS."""
    m = _VALID_NATS_SCHEME.match(nats_url)
    if not m:
        log.error("NATS_URL scheme invalid: got %r — must start with nats:// or tls://", nats_url)
        raise typer.Exit(2)
    if not urlparse(nats_url).hostname:
        log.error("NATS_URL has no host: got %r", nats_url)
        raise typer.Exit(2)
    if m.group(1) != "tls":
        log.warning("NATS_URL uses non-TLS scheme %r — traffic is unencrypted", nats_url)


@nats_app.command("tts")
def nats_serve_tts(
    engine: Annotated[Optional[str], typer.Option("--engine", "-e")] = None,
    max_concurrent: Annotated[
        int, typer.Option("--max-concurrent", envvar="VOICECLI_MAX_CONCURRENT")
    ] = 1,
    reject_when_full: Annotated[
        bool, typer.Option("--reject-when-full", envvar="VOICECLI_REJECT_WHEN_FULL")
    ] = False,
    heartbeat_interval: Annotated[
        float, typer.Option("--heartbeat-interval", envvar="VOICECLI_HEARTBEAT_INTERVAL")
    ] = 5.0,
    drain_timeout: Annotated[
        float, typer.Option("--drain-timeout", envvar="VOICECLI_DRAIN_TIMEOUT")
    ] = 30.0,
    allow_coexist: Annotated[
        bool, typer.Option("--allow-coexist", envvar="VOICECLI_ALLOW_COEXIST")
    ] = False,
) -> None:
    """Subscribe to the TTS request subject and reply with synthesized audio."""
    import asyncio

    from voicecli.core.config import apply_nats_env_from_config, load_nats_config
    from voicecli.runtime.model_registry import model_registry
    from voicecli.adapters.nats.config import _probe_socket_daemon, _resolve_engine
    from voicecli.adapters.nats.synthesize_adapter import TtsNatsAdapter

    apply_nats_env_from_config()

    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("voicecli.adapters.nats-serve.tts")

    nats_cfg = load_nats_config()
    model_registry.configure(max_cached=nats_cfg["max_cached_engines"])
    log.info("model_registry configured: max_cached=%d", model_registry._max_cached)

    resolved_engine = _resolve_engine(engine)

    sock_path = Path("~/.local/share/voicecli/daemon.sock").expanduser()
    state = _probe_socket_daemon(sock_path)
    if state == "live":
        if allow_coexist:
            log.warning("coexisting with live socket daemon at %s", sock_path)
        else:
            log.error(
                "live socket daemon at %s — refusing to start (use --allow-coexist or stop the daemon)",
                sock_path,
            )
            raise typer.Exit(78)
    elif state == "stale":
        log.info("stale socket file at %s ignored", sock_path)

    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        log.error("NATS_URL env var is required")
        raise typer.Exit(2)
    _check_nats_url(nats_url, log)

    adapter = TtsNatsAdapter(
        default_engine=resolved_engine,
        max_concurrent=max_concurrent,
        reject_when_full=reject_when_full,
        heartbeat_interval=heartbeat_interval,
        drain_timeout=drain_timeout,
    )

    asyncio.run(adapter.run(nats_url))


@nats_app.command("stt")
def nats_serve_stt(
    model: Annotated[Optional[str], typer.Option("--model", "-m", envvar="VOICECLI_MODEL")] = None,
    max_concurrent: Annotated[
        int, typer.Option("--max-concurrent", envvar="VOICECLI_MAX_CONCURRENT")
    ] = 2,
    reject_when_full: Annotated[
        bool, typer.Option("--reject-when-full", envvar="VOICECLI_REJECT_WHEN_FULL")
    ] = False,
    heartbeat_interval: Annotated[
        float, typer.Option("--heartbeat-interval", envvar="VOICECLI_HEARTBEAT_INTERVAL")
    ] = 5.0,
    drain_timeout: Annotated[
        float, typer.Option("--drain-timeout", envvar="VOICECLI_DRAIN_TIMEOUT")
    ] = 30.0,
    allow_coexist: Annotated[
        bool, typer.Option("--allow-coexist", envvar="VOICECLI_ALLOW_COEXIST")
    ] = False,
) -> None:
    """Subscribe to the STT request subject and reply with transcription."""
    import asyncio

    from voicecli.core.config import apply_nats_env_from_config
    from voicecli.adapters.nats.config import _probe_socket_daemon, _resolve_model
    from voicecli.adapters.nats.transcribe_adapter import SttNatsAdapter

    apply_nats_env_from_config()

    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("voicecli.adapters.nats-serve.stt")

    resolved_model = _resolve_model(model)

    sock_path = Path("~/.local/share/voicecli/stt-daemon.sock").expanduser()
    state = _probe_socket_daemon(sock_path)
    if state == "live":
        if allow_coexist:
            log.warning("coexisting with live socket daemon at %s", sock_path)
        else:
            log.error(
                "live socket daemon at %s — refusing to start (use --allow-coexist or stop the daemon)",
                sock_path,
            )
            raise typer.Exit(78)
    elif state == "stale":
        log.info("stale socket file at %s ignored", sock_path)

    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        log.error("NATS_URL env var is required")
        raise typer.Exit(2)
    _check_nats_url(nats_url, log)

    adapter = SttNatsAdapter(
        default_model=resolved_model,
        max_concurrent=max_concurrent,
        reject_when_full=reject_when_full,
        heartbeat_interval=heartbeat_interval,
        drain_timeout=drain_timeout,
    )

    asyncio.run(adapter.run(nats_url))
