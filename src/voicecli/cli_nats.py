"""NATS serve sub-app — TTS and STT satellite CLI commands."""

from pathlib import Path
from typing import Annotated, Optional

import typer

from voicecli.nats.config import _probe_socket_daemon

nats_app = typer.Typer(help="NATS subscriber satellites for hub-driven voice.")


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
    """Subscribe to lyra.voice.tts.request and reply with synthesized audio."""
    import asyncio
    import logging
    import os

    from voicecli.config import load_nats_config
    from voicecli.nats.config import _resolve_engine
    from voicecli.nats.tts_adapter import TtsNatsAdapter

    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("voicecli.nats-serve.tts")

    # Load NATS config and configure model_registry
    nats_cfg = load_nats_config()
    from voicecli.model_registry import model_registry

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

    adapter = TtsNatsAdapter(
        default_engine=resolved_engine,
        max_concurrent=max_concurrent,
        reject_when_full=reject_when_full,
        heartbeat_interval=heartbeat_interval,
        drain_timeout=drain_timeout,
    )

    try:
        asyncio.run(adapter.run(nats_url))
    except asyncio.TimeoutError:
        log.error("drain timeout exceeded; some requests may have been dropped")
        raise typer.Exit(3)


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
    """Subscribe to lyra.voice.stt.request and reply with transcription."""
    import asyncio
    import logging
    import os

    from voicecli.nats.config import _resolve_model
    from voicecli.nats.stt_adapter import SttNatsAdapter

    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("voicecli.nats-serve.stt")

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

    adapter = SttNatsAdapter(
        default_model=resolved_model,
        max_concurrent=max_concurrent,
        reject_when_full=reject_when_full,
        heartbeat_interval=heartbeat_interval,
        drain_timeout=drain_timeout,
    )

    try:
        asyncio.run(adapter.run(nats_url))
    except asyncio.TimeoutError:
        log.error("drain timeout exceeded; some requests may have been dropped")
        raise typer.Exit(3)
