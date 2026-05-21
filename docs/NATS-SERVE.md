---
title: NATS Serve — Operator Guide
description: Running voicecli as a NATS queue-group satellite for distributed TTS/STT.
---

## Overview

`voicecli nats-serve {tts,stt}` runs voicecli as a long-lived NATS queue-group subscriber
instead of a local Unix-socket daemon. Requests arrive over NATS subject/reply patterns;
the satellite processes them and publishes the reply back to the hub. Multiple satellite
instances can subscribe to the same queue group — NATS load-balances across them automatically.

**When to choose this over `tts-serve` / `stt-serve`:**

| Scenario | Use |
|---|---|
| Hub and voicecli on the same host, no network boundary | `tts-serve` / `stt-serve` (Unix socket, lower latency) |
| Hub and voicecli on different hosts | `nats-serve` |
| You want to add satellite capacity without changing hub config | `nats-serve` (add more queue-group members) |
| Production deployment where hub is managed separately from GPU worker | `nats-serve` |

ADR-044 in the lyra repo formalised this decoupling to remove lyra's direct Unix-socket
dependency on voicecli, enabling each service to be deployed and restarted independently.

**Startup sequence:**
1. Validate env vars and file permissions (seed file `0600`).
2. Run the VRAM-sequencing guard (probe local socket daemon).
3. Connect to NATS and join the queue group (`tts-workers` / `stt-workers`).
4. Load the TTS/STT engine model into VRAM.
5. Begin accepting requests and publishing heartbeats.

Both subcommands are now available: `nats-serve tts` (Slice 1) and `nats-serve stt` (Slice 2).

---

## Environment variables

Precedence: `CLI flag > env var > voicecli.toml > hardcoded default`

All variables below are read at startup. Changes take effect only after a process restart.
Set them in a Quadlet `Environment=` line, in an env file, or export them in the shell
before running `voicecli nats-serve` (dev).

| Variable | Default | Purpose |
|---|---|---|
| `NATS_URL` | — (required) | NATS server URL, e.g. `nats://127.0.0.1:4222`. Accepted schemes: `nats://` (unencrypted, dev only — logs a warning) and `tls://`. WebSocket schemes (`ws://`, `wss://`) are intentionally rejected; use a NATS server with a TCP listener instead. |
| `NATS_NKEY_SEED_PATH` | — (required for nkey auth) | Path to the NKey seed file. **File permissions must be `0600`** — the satellite refuses to start if the file is world- or group-readable. |
| `NATS_CA_CERT` | — (optional) | Path to a PEM CA certificate for TLS verification |
| `VOICECLI_ENGINE` | from `voicecli.toml` | TTS engine override (`qwen`, `qwen-fast`, `chatterbox`, etc.) |
| `LYRA_TTS_ENGINE` | — | Alias for `VOICECLI_ENGINE`. Provided for the lyra#658 S3 cutover for backward compatibility. Takes the same values; `VOICECLI_ENGINE` wins if both are set. |
| `VOICECLI_MAX_CONCURRENT` | TTS: `1` / STT: `2` | Maximum requests processed in parallel. Keep at `1` for single-GPU hosts unless VRAM allows more. |
| `VOICECLI_HEARTBEAT_INTERVAL` | `5.0` | Seconds between heartbeat publishes. Must stay ≤ 5 per ADR-044 — the hub declares a satellite dead after two missed beats. |
| `VOICECLI_DRAIN_TIMEOUT` | `30` | Seconds to wait for in-flight requests to finish during graceful shutdown before exiting with code 3. |
| `VOICECLI_REJECT_WHEN_FULL` | unset | Set to `1` to return an error reply immediately when all slots are busy, rather than queuing the request. |
| `VOICECLI_ALLOW_COEXIST` | unset | Set to `1` to bypass the VRAM-sequencing guard on startup. See [VRAM sequencing](#vram-sequencing). |

---

## VRAM sequencing

Running a NATS satellite alongside a socket daemon (`tts-serve` / `stt-serve`) on the same
GPU risks CUDA OOM — both processes compete for the same VRAM budget. See the
[VRAM contention note in CLAUDE.md](../CLAUDE.md#vram-contention-on-rtx-3080-10-gb) for
measured numbers (TTS daemon: ~7.4 GB, STT daemon: ~2.2 GB on RTX 3080).

The intended deployment model is **either** the socket daemon **or** the NATS satellite on
a given host — not both. The guard enforces this automatically.

**On startup the satellite probes the socket:**

- TTS: `~/.local/share/voicecli/daemon.sock`
- STT: `~/.local/share/voicecli/stt-daemon.sock`

| Socket state | Behaviour |
|---|---|
| File absent | Proceed normally |
| File exists, `connect()` returns `ECONNREFUSED` (stale) | Log at INFO, ignore, proceed |
| File exists, daemon answers the probe (live) | **Refuse to start — exit 78** |

**To resolve exit 78:** stop the socket daemon before starting the NATS satellite.

```bash
# Quadlet (prod — M₁):
systemctl --user stop voicecli-tts       # stop socket daemon if running
systemctl --user start voicecli-tts      # start NATS satellite

# Native dev (M₂):
pkill -f 'voicecli serve' || true        # stop socket daemon if running
voicecli nats-serve tts                  # start NATS satellite
```

On local-dev machines or large-VRAM boxes (e.g. RTX 5070 Ti with 16 GB) where coexistence
is safe, bypass the guard with the flag or env var:

```bash
voicecli nats-serve tts --allow-coexist
# or via env var
VOICECLI_ALLOW_COEXIST=1 voicecli nats-serve tts
```

Do not set `VOICECLI_ALLOW_COEXIST=1` on the RTX 3080 (10 GB) production host —
it will allow OOM conditions during concurrent synthesis. See the
[VRAM contention gotcha](../CLAUDE.md#vram-contention-on-rtx-3080-10-gb) for details.

---

## Exit codes

Shutdown is triggered by `SIGTERM` or `SIGINT`. On receiving a signal the satellite stops
accepting new requests, waits up to `VOICECLI_DRAIN_TIMEOUT` seconds for in-flight work to
complete, then exits.

| Code | Meaning | Operator action |
|---|---|---|
| `0` | Clean shutdown; all in-flight requests drained within `--drain-timeout` | None — safe to restart |
| `3` | Drain timeout exceeded; at least one in-flight request did not finish within the window | Restart is safe; investigate slow synthesis if this recurs frequently — consider raising `VOICECLI_DRAIN_TIMEOUT` |
| `78` | VRAM-sequencing guard tripped — a live socket daemon was detected on startup | Stop the socket daemon (`voicecli tts-serve` / `voicecli stt-serve`) before restarting, OR pass `--allow-coexist` |

Any other non-zero exit code is an unhandled error — check the service logs for the Python
traceback (`journalctl --user -u voicecli-tts`).

In Quadlet, `Restart=on-failure RestartSec=10` handles restart throttling for all exit
codes including 3 and 78 — no extra configuration needed.

---

## [Historical] supervisord stanza (TTS)

> **Not recommended.** supervisord is the legacy deployment method.
> Use Quadlet (`deploy/quadlet/voicecli-tts.container`) for production.
> This section is kept for reference during migration only.

<details>
<summary>supervisord stanza (TTS)</summary>

```ini
[program:voicecli_nats_tts]
command=voicecli nats-serve tts
; VOICECLI_ALLOW_COEXIST is intentionally absent — do NOT set it on co-located GPU
; hosts (e.g. RTX 3080 10 GB). Setting it bypasses the VRAM-sequencing guard and
; will cause CUDA OOM under concurrent synthesis. See VRAM sequencing section above.
environment=NATS_URL="nats://127.0.0.1:4222",NATS_NKEY_SEED_PATH="/home/lyra/.voicecli/nkeys/voice-tts.seed",LYRA_TTS_ENGINE="qwen-fast"
autorestart=unexpected
exitcodes=0,3,78
stopsignal=TERM
stopwaitsecs=35
startsecs=15
user=lyra
stdout_logfile=/home/lyra/.local/state/lyra/logs/voicecli_nats_tts.log
stderr_logfile=/home/lyra/.local/state/lyra/logs/voicecli_nats_tts.err
```

Key: `autorestart=unexpected` + `exitcodes=0,3,78` is critical — without it supervisord
loop-restarts on exit 78. `stopwaitsecs=35` must exceed `VOICECLI_DRAIN_TIMEOUT` (30 s).

</details>

---

## Troubleshooting

### Quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| Process exits 78 immediately on startup | Live socket daemon (`tts-serve` / `stt-serve`) detected | Stop the socket daemon (`systemctl --user stop voicecli-tts` or `pkill -f 'voicecli serve'`), then restart; or pass `--allow-coexist` if coexistence is intentional |
| `PermissionError` referencing the seed file path | NKey seed file is not `0600` | `chmod 600 ~/.voicecli/nkeys/voice-tts.seed` |
| Replies never arrive at the hub / requests time out | Wrong `NATS_URL`, network partition, or mismatched queue group name | Verify `NATS_URL` is reachable from the satellite host; queue group names are `tts-workers` (TTS) and `stt-workers` (STT) |
| Heartbeats stop arriving during a synthesis | Concurrency contract violated (bug) | Report it — the spec guarantees heartbeats continue independently of in-flight synthesis |
| Hub logs `payload_too_large` | Reply WAV exceeds NATS server `max_payload` | Increase `max_payload` in the NATS server config, or shorten the synthesis text |
| Satellite starts but produces no output; logs show CUDA OOM | VRAM exhausted by coexisting processes | Stop other GPU-heavy daemons, reduce `VOICECLI_MAX_CONCURRENT`, or move to a host with more VRAM |
| Quadlet keeps restarting in a tight loop on exit 78 | VRAM guard firing continuously — socket daemon still running | Stop the socket daemon first; `RestartSec=10` in the Quadlet unit throttles restart loops |
| TLS handshake errors connecting to NATS | Missing or wrong CA certificate | Set `NATS_CA_CERT` to the PEM file for your internal CA |

### Checking liveness

The satellite logs its startup sequence to stdout. A healthy start looks like:

```
INFO  vram-guard: no live socket daemon detected — proceeding
INFO  nats: connected to nats://127.0.0.1:4222
INFO  engine: model loaded in 12.3s (qwen-fast)
INFO  nats-serve: joined queue group tts-workers — ready
```

If the process exits before the "ready" line, check `stderr_logfile` for the Python
traceback. The most common causes are: missing env vars, seed file permission error, NATS
unreachable, and the VRAM guard (exit 78).

### Confirming requests reach the satellite

Use the NATS CLI to publish a test request directly to the TTS subject and observe whether
the satellite picks it up:

```bash
nats req lyra.voice.tts.request '{"request_id":"test-1","text":"hello","engine":"qwen-fast"}' --server nats://127.0.0.1:4222
```

If no reply arrives within the timeout, the satellite is either not running, not connected
to the correct server, or stuck processing another request and `VOICECLI_REJECT_WHEN_FULL`
is not set.

---

## TTS request field coverage

ADR-044 freezes the TTS request envelope in `lyra/artifacts/plans/688-voicecli-contract-adr-plan.mdx`. The satellite handles every field in that schema — either by forwarding it to `api.generate`, applying it as control flow, or stamping it into the reply.

| Field | Type | Handling |
|---|---|---|
| `contract_version` | string | Defensive read — logged at WARN once per worker if ≠ `"1"`, request still processed. Outgoing replies always stamp `"1"`. |
| `request_id` | string | Required. Rejected with `malformed_request` if missing or not matching `^[A-Za-z0-9_-]{1,128}$`. Echoed in every reply. |
| `text` | string | Required. Rejected with `malformed_request` if missing, empty, or not a string. Forwarded as the first positional arg of `api.generate`. |
| `engine` | string | Optional; falls back to `default_engine` from satellite startup. **Per-request switching supported** — the satellite hot-swaps engines via LRU cache (see [Engine hot-swapping](#engine-hot-swapping)). Validated via `validate_nats_token`; unknown engine → `engine_unavailable`. |
| `language` | string | Forwarded to `api.generate(language=…)`. |
| `voice` | string | Forwarded to `api.generate(voice=…)`. |
| `speed` | float | Forwarded through `**kwargs`; `translate.py` strips for engines that do not consume it. |
| `exaggeration` | float | Forwarded through `**kwargs`. |
| `cfg_weight` | float | Forwarded through `**kwargs`. |
| `accent` | string | Forwarded through `**kwargs`; recomposed into an `instruct` string by `api._apply_config_defaults`. |
| `personality` | string | Forwarded through `**kwargs`; recomposed into the `instruct` string. |
| `emotion` | string | Forwarded through `**kwargs`; recomposed into the `instruct` string. |
| `chunked` | bool | Forwarded as a named `api.generate(chunked=…)` argument. Explicit `False` is preserved (not dropped). |
| `chunk_size` | int | Forwarded as `api.generate(chunk_size=…)`. Bounded 1–10 000 by `api._validate_tts_params`. |
| `segment_gap` | int (ms) | Forwarded as `api.generate(segment_gap=…)`. Bounded 0–30 000 ms. |
| `crossfade` | int (ms) | Forwarded as `api.generate(crossfade=…)`. Bounded 0–10 000 ms. |
| `fallback_language` | string | Control flow only. If primary synthesis raises `ValueError` and `fallback_language ≠ language`, the satellite retries once with `language = fallback_language` before returning `synthesis_failed`. Lyra normally resolves `fallback_language` client-side before dispatch; this branch is a defensive backstop. |

### Reply fields

| Field | Condition |
|---|---|
| `ok` | Always present. `true` on successful synthesis. |
| `contract_version` | Always `"1"`. |
| `request_id` | Echoed from the request. |
| `audio_b64` | Base64-encoded WAV bytes on success. |
| `mime_type` | `audio/wav` on success. |
| `duration_ms` | Computed from the WAV header; `0` if unreadable. |
| `waveform_b64` | Optional. 256-byte amplitude array (base64), computed from the generated WAV for Discord voice-message rendering. Omitted when the WAV is unreadable or the sample width is unsupported. |
| `error` | Error code on failure (`malformed_request`, `engine_unavailable`, `capacity_exceeded`, `synthesis_failed`). |

---

## Engine hot-swapping

The TTS satellite supports **per-request engine switching** — a single satellite can serve
requests for `qwen`, `qwen-fast`, `chatterbox`, `chatterbox-turbo`, and `voxtral` interchangeably.
The `model_registry` module manages an LRU cache with VRAM-aware eviction.

### How it works

1. Request arrives with `engine` field (or falls back to `default_engine`)
2. `model_registry.get(engine)` checks the LRU cache
3. **Cache hit** → returns cached engine (fast, no VRAM change)
4. **Cache miss** → loads engine, evicts LRU if cache full, checks VRAM before load
5. If VRAM insufficient even after full eviction → returns `engine_unavailable`

### Configuration

In `voicecli.toml`:

```toml
[nats]
max_cached_engines = 2  # keep N engines hot (default: 2)
```

Higher values keep more engines hot but require more VRAM. On a 10 GB GPU, 2 engines
is the practical limit (qwen-fast ~3.5 GB + chatterbox ~1.8 GB = ~5.3 GB steady-state).

### Heartbeat visibility

The satellite reports loaded engines in each heartbeat:

```json
{
  "model_loaded": ["qwen-fast", "chatterbox"],
  "vram_free_mb": 4200,
  "vram_status": "ok"
}
```

### VRAM eviction behavior

When a new engine is requested and VRAM is constrained:

| Condition | Action |
|-----------|--------|
| Cache has room | Load engine, add to cache |
| Cache full, VRAM OK | Evict LRU engine, load new one |
| Cache full, VRAM constrained | Evict LRU, check VRAM, repeat until space |
| All evicted, still insufficient | Return `engine_unavailable` |

The heartbeat `vram_status` field reflects current state:
- `"ok"` — >4 GB free
- `"constrained"` — 1–4 GB free
- `"critical"` — <1 GB free

### Example: multi-engine request flow

```bash
# Request 1: qwen-fast (loads into cache, ~30s cold start)
nats req lyra.voice.tts.request '{"request_id":"1","text":"hello","engine":"qwen-fast"}'

# Request 2: chatterbox (loads into cache, qwen-fast stays hot)
nats req lyra.voice.tts.request '{"request_id":"2","text":"hello","engine":"chatterbox"}'

# Request 3: qwen-fast (cache hit, instant)
nats req lyra.voice.tts.request '{"request_id":"3","text":"hello","engine":"qwen-fast"}'

# Request 4: voxtral (evicts LRU — chatterbox if qwen-fast was touched more recently)
nats req lyra.voice.tts.request '{"request_id":"4","text":"hello","engine":"voxtral"}'
```

---

## STT satellite

### CLI invocation

```bash
voicecli nats-serve stt [--model <model>]
```

Default model: `large-v3-turbo`. Override via `--model`, `VOICECLI_MODEL`, or `voicecli.toml [stt].model`.

Precedence: `CLI flag > VOICECLI_MODEL env > voicecli.toml [stt].model > large-v3-turbo`

### STT — environment variables

All variables in the [Environment variables](#environment-variables) section apply unchanged
(`NATS_URL`, `NATS_NKEY_SEED_PATH`, `NATS_CA_CERT`, `VOICECLI_MAX_CONCURRENT`,
`VOICECLI_REJECT_WHEN_FULL`, `VOICECLI_HEARTBEAT_INTERVAL`, `VOICECLI_DRAIN_TIMEOUT`,
`VOICECLI_ALLOW_COEXIST`). The one STT-specific variable is:

| Variable | Default | Purpose |
|---|---|---|
| `VOICECLI_MODEL` | `large-v3-turbo` | STT model name passed to faster-whisper. Replaces `VOICECLI_ENGINE` (which is TTS-only). |

### STT — defaults that differ from TTS

| Parameter | TTS | STT | Note |
|---|---|---|---|
| `--engine` / `--model` | `qwen-fast` (via `VOICECLI_ENGINE`) | `large-v3-turbo` (via `VOICECLI_MODEL`) | Different flag name; STT has no engine registry |
| `--max-concurrent` | `1` | `2` | Transcription is faster than synthesis and uses less VRAM per slot; `2` is safe on standalone-STT hosts |

All other parameters (`--heartbeat-interval`, `--drain-timeout`, `--reject-when-full`, `--allow-coexist`) share the same defaults as TTS.

### STT — NATS topology

| Item | Value |
|---|---|
| Request subject | `lyra.voice.stt.request` |
| Heartbeat subject | `lyra.voice.stt.heartbeat` |
| Queue group | `stt-workers` |
| Service name (heartbeat payload) | `stt_workers` |

### STT — per-request language overrides

The request payload may include any of the following optional fields to override language
detection for that single request. They do NOT mutate satellite state — each request is
independent.

| Field | Type | Purpose |
|---|---|---|
| `language` | `string` | Force a specific language (e.g. `"en"`, `"fr"`). Skips detection entirely. |
| `language_detection_threshold` | `float` | Minimum confidence to accept detected language (0–1). |
| `language_detection_segments` | `int` | Number of audio segments used for detection. |
| `language_fallback` | `string` | Language code to use when detection confidence is below threshold. |

### STT — reply schema

**Success:**

```json
{
  "ok": true,
  "contract_version": "1",
  "request_id": "<id>",
  "text": "<transcribed text>",
  "language": "<detected or forced language code>",
  "duration_seconds": 4.82
}
```

**Failure:**

```json
{
  "ok": false,
  "contract_version": "1",
  "request_id": "<id>",
  "error": "<error_code>"
}
```

Error codes:

| Code | Cause |
|---|---|
| `malformed_request` | Missing or invalid `request_id`, missing `audio_b64`, or `request_id` fails format validation |
| `audio_decode_failed` | `audio_b64` is not valid base64 |
| `transcription_failed` | faster-whisper raised an exception during inference |
| `model_load_failed` | Model could not be loaded into VRAM at startup |
| `capacity_exceeded` | All semaphore slots busy and `VOICECLI_REJECT_WHEN_FULL=1` |
| `payload_too_large` | Reply payload exceeds the NATS server `max_payload` limit |

### STT — VRAM-sequencing guard

Probe path: `~/.local/share/voicecli/stt-daemon.sock`

Behaviour is identical to the TTS guard — see [VRAM sequencing](#vram-sequencing). A live
STT socket daemon causes exit 78 unless `--allow-coexist` / `VOICECLI_ALLOW_COEXIST=1` is set.

```bash
# Quadlet (prod — M₁):
systemctl --user stop voicecli-stt       # stop socket daemon if running
systemctl --user start voicecli-stt      # start NATS satellite

# Native dev (M₂):
pkill -f 'voicecli stt-serve' || true    # stop socket daemon if running
voicecli nats-serve stt                  # start NATS satellite
```

> **Co-located GPU warning — RTX 3080 (10 GB) and similar hosts**
>
> On hosts where TTS and STT satellites share a single GPU (e.g. `roxabituwer`:
> TTS ~7.4 GB + STT ~2.2 GB = ~9.6 GB steady-state), set `VOICECLI_MAX_CONCURRENT=1`
> on the STT satellite. Pass `--max-concurrent 1` on the command line or set
> `Environment=VOICECLI_MAX_CONCURRENT=1` in the Quadlet unit. A second concurrent STT
> decode would overflow the remaining VRAM headroom and OOM. The default `2` is intended
> for hosts running the STT satellite standalone.

### [Historical] STT — Required supervisord stanza

> **Not recommended.** Use Quadlet (`deploy/quadlet/voicecli-stt.container`) for production.
> This section is kept for reference during migration only.

<details>
<summary>supervisord stanza (STT)</summary>

```ini
[program:voicecli_nats_stt]
command=voicecli nats-serve stt
; VOICECLI_ALLOW_COEXIST is intentionally absent — do NOT set it on co-located GPU
; hosts. Bypasses the VRAM-sequencing guard and risks OOM. See VRAM sequencing above.
environment=NATS_URL="nats://127.0.0.1:4222",NATS_NKEY_SEED_PATH="/home/lyra/.voicecli/nkeys/voice-stt.seed",VOICECLI_MODEL="large-v3-turbo",VOICECLI_MAX_CONCURRENT="1"
autorestart=unexpected
exitcodes=0,3,78
stopsignal=TERM
stopwaitsecs=35
startsecs=10
user=lyra
stdout_logfile=/home/lyra/.local/state/lyra/logs/voicecli_nats_stt.log
stderr_logfile=/home/lyra/.local/state/lyra/logs/voicecli_nats_stt.err
```

`VOICECLI_MAX_CONCURRENT="1"` targets co-located GPU. Remove or raise to `2` on standalone-STT hosts.

</details>

---

## Local real-hub E2E

The [`tests/e2e/docker-compose.nats.yml`](../tests/e2e/docker-compose.nats.yml) compose file
runs the satellite against a **stub hub** — fast, deterministic, no external dependencies,
and the fixture CI relies on. For a richer local smoke test against a real lyra hub image,
use the sibling file:

```
tests/e2e/docker-compose.real-hub.yml
```

This file is **not run in CI** (the lyra image is private and requires GHCR auth) — it is
provided for local operators who want an end-to-end sanity check with the actual hub
before deploying. The hub image is injected via env var so no credentials are baked into
the compose file:

```bash
# Pull the lyra hub image once (requires GHCR login)
echo "$GHCR_TOKEN" | docker login ghcr.io -u <user> --password-stdin
docker pull ghcr.io/roxabi/lyra:<digest>

# Render the NATS server config from its template
# (PUBKEY = the nkey public key derived from your seed file)
export REPO_ROOT="$(git rev-parse --show-toplevel)"
PUBKEY=$(nk -inkey "$REPO_ROOT/tests/e2e/fixtures/test.seed" -pubout) \
  envsubst < "$REPO_ROOT/tests/e2e/nats-server.conf.template" \
  > "$REPO_ROOT/tests/e2e/nats-server.conf"

# Lock down the seed file — required by the satellite (refuses to start otherwise)
# and by any well-behaved hub image. Editor defaults of 0644 will leak the seed.
chmod 600 "$REPO_ROOT/tests/e2e/fixtures/test.seed"

# Point the compose file at it and bring up the stack
export LYRA_HUB_IMAGE="ghcr.io/roxabi/lyra:<digest>"
export NATS_CONF_PATH="$REPO_ROOT/tests/e2e/nats-server.conf"
export SEED_PATH="$REPO_ROOT/tests/e2e/fixtures/test.seed"
docker compose -f tests/e2e/docker-compose.real-hub.yml up --abort-on-container-exit
```

The stack starts a NATS server, the lyra hub at `$LYRA_HUB_IMAGE`, and both voicecli
satellites (`nats-serve tts` + `nats-serve stt`) using the `mock` engine so no GPU is
required. The hub exercises the same request/reply contract as production, which makes
this useful for catching contract drift after lyra or voicecli changes.

If the hub image is not reachable (wrong tag, no GHCR auth, private image gated) the
compose stack will fail to pull — that is the expected failure mode and the reason this
file is out of scope for CI. Adding CI support would require private-image auth that is
tracked separately.

## CI Hygiene for Mock Engine

The `VOICECLI_ENABLE_MOCK_ENGINE` env var must **never** be present in a production
Docker build context. To prevent accidental leakage:

1. **Explicit unset before build:**
   ```bash
   unset VOICECLI_ENABLE_MOCK_ENGINE
   docker build -t voicecli:prod .
   ```

2. **Separate build stage:** Use a dedicated CI job for production builds that
   never runs E2E tests (which export the env var).

The mock engine lives at `src/voicecli/engines/mock.py` but is only registered in
the engine registry (and the STT transcribe short-circuit) when the
`VOICECLI_ENABLE_MOCK_ENGINE` env var is truthy. Production images leave the var
unset, so `mock` is absent from `voicecli.engine.available_engines()` and any
request carrying `engine: "mock"` is rejected with `engine_unavailable`. The
`mock_engine` pytest fixture sets the var for the test scope.

---

## Quadlet deployment (Podman + systemd)

For production hosts running Podman with systemd integration, voiceCLI provides
Quadlet unit files in `deploy/quadlet/`. These enable native systemd management
of containerized NATS satellites without manual podman commands.

### Files

| File | Purpose |
|---|---|
| `voicecli-tts.container` | TTS satellite as a systemd service |
| `voicecli-stt.container` | STT satellite as a systemd service |

### Installation

1. Copy Quadlet units to `~/.config/containers/systemd/` (user) or
   `/etc/containers/systemd/` (root):

   ```bash
   mkdir -p ~/.config/containers/systemd
   cp deploy/quadlet/*.container ~/.config/containers/systemd/
   ```

2. Create the NKey seed secrets (one per satellite):

   ```bash
   mkdir -p ~/.voicecli/nkeys && chmod 700 ~/.voicecli/nkeys
   printf 'SU...' > ~/.voicecli/nkeys/voice-tts.seed
   chmod 600 ~/.voicecli/nkeys/voice-tts.seed

   printf 'SU...' > ~/.voicecli/nkeys/voice-stt.seed
   chmod 600 ~/.voicecli/nkeys/voice-stt.seed
   ```

3. Register secrets with Podman (required for Quadlet `Secret=` directive):

   ```bash
   podman secret create voicecli-nats-tts ~/.voicecli/nkeys/voice-tts.seed
   podman secret create voicecli-nats-stt ~/.voicecli/nkeys/voice-stt.seed
   ```

4. Reload systemd and start the service(s):

   ```bash
   systemctl --user daemon-reload
   systemctl --user start voicecli-tts
   # or for STT:
   systemctl --user start voicecli-stt
   ```

### Pre-seeding models

On first run, each satellite downloads its model (~7 GB for TTS, ~2 GB for STT).
Model cache is stored in `~/.cache/huggingface/` on the host (bind-mounted into the
container). If the host already has models cached (e.g. from a dev box with 120G of
HF cache), the satellites reuse them immediately with no extra steps.

On a fresh production host, models auto-download on first use via `from_pretrained()`.
No pre-seeding step is required — re-download (~15G on M₁) is the chosen migration
strategy.

### Resource considerations

The TTS and STT satellites both require GPU access. On single-GPU hosts with
limited VRAM (e.g. RTX 3080 10 GB), run only one satellite at a time or use
`VOICECLI_MAX_CONCURRENT=1` on the STT satellite — set via `--max-concurrent 1` or
`Environment=VOICECLI_MAX_CONCURRENT=1` in the Quadlet unit.

Quadlet does not directly support `exitcodes=` for restart policy tuning. The
`Restart=on-failure` directive in the Quadlet files restarts on non-zero exits,
which includes exit codes 3 (drain timeout) and 78 (VRAM guard). This is
acceptable for containerized deployments where the orchestration layer handles
restart throttling.

### Image registry

Two production images are published from this repo:

| Image | Contents | Use Case |
|---|---|---|
| `ghcr.io/roxabi/voicecli-tts:staging` | TTS engines (qwen, chatterbox) + NATS | TTS satellite |
| `ghcr.io/roxabi/voicecli-stt:staging` | STT engine (faster-whisper) + NATS | STT satellite |

To run both TTS and STT on the same host, deploy two containers (one for each).

The Quadlet units reference the dedicated images:
- `voicecli-tts.container` → `ghcr.io/roxabi/voicecli-tts:staging`
- `voicecli-stt.container` → `ghcr.io/roxabi/voicecli-stt:staging`

For production, pin to a specific digest:

```ini
Image=ghcr.io/roxabi/voicecli-tts@sha256:<digest>
```

Build and push from the repo root:

```bash
podman build -f deploy/Dockerfile.tts -t ghcr.io/roxabi/voicecli-tts:staging .
podman push ghcr.io/roxabi/voicecli-tts:staging
```

### Drain-and-swap upgrade protocol

The voiceCLI workers hold GPU VRAM and may be mid-synthesis when a new image
lands. A blind `systemctl restart` interrupts in-flight requests and leaves
NATS clients without responders. Use the drain-and-swap sequence below to roll
out a new `:staging` image with zero dropped requests and a one-command
rollback path.

**Pre-swap — tag the running image as `:staging-prev`:**

```bash
# Capture the digest currently in use so we can roll back atomically.
CURRENT=$(podman inspect voicecli-tts --format '{{.ImageDigest}}')
podman tag "ghcr.io/roxabi/voicecli-tts@${CURRENT}" ghcr.io/roxabi/voicecli-tts:staging-prev
```

**Drain — let in-flight work finish before pulling:**

```bash
# Sends SIGTERM → satellite stops accepting new NATS requests, waits up to
# VOICECLI_DRAIN_TIMEOUT (default 30 s) for in-flight synthesis to complete,
# then exits with code 0 (clean) or 3 (timeout exceeded). Quadlet's
# Restart=on-failure does NOT fire on code 0, so the unit stays stopped.
systemctl --user stop voicecli-tts.service
systemctl --user stop voicecli-stt.service
```

Tail logs to confirm a clean drain:

```bash
journalctl --user -u voicecli-tts.service -n 50 | grep -E 'drain|exit'
# Expect: "drained N in-flight requests, exiting cleanly" → exit 0
```

**Swap — pull the new images and reload Quadlet:**

```bash
podman pull ghcr.io/roxabi/voicecli-tts:staging
podman pull ghcr.io/roxabi/voicecli-stt:staging
# Quadlet generates systemd units from .container files at daemon-reload time;
# re-run after pulling so the new image digest is picked up.
systemctl --user daemon-reload
systemctl --user start voicecli-tts.service voicecli-stt.service
```

**Verify — health + a smoke request:**

```bash
systemctl --user status voicecli-tts.service voicecli-stt.service
# Quick NATS round-trip from the host (requires nats-py + a user nkey):
nats req voicecli.tts.qwen.generate '{"text":"smoke","voice":"Cherry"}' --timeout 30s
```

**Rollback — if the new image regresses:**

```bash
systemctl --user stop voicecli-tts.service voicecli-stt.service
# Repoint the rolling tags back to the previous digests.
podman tag ghcr.io/roxabi/voicecli-tts:staging-prev ghcr.io/roxabi/voicecli-tts:staging
podman tag ghcr.io/roxabi/voicecli-stt:staging-prev ghcr.io/roxabi/voicecli-stt:staging
systemctl --user daemon-reload
systemctl --user start voicecli-tts.service voicecli-stt.service
```

The `:staging-prev` tags survive `podman pull :staging` (only the rolling tag
is overwritten), so a rollback is always one `podman tag` away until the next
swap re-tags `:staging-prev`.

---

## `dictate nats` — Cross-host client

`voicecli dictate nats` is the client counterpart to `nats-serve stt`. It records audio on the local machine and sends it to the remote STT satellite via NATS, then copies the transcription to the clipboard.

**Toggle semantics:** first invocation starts recording; second invocation stops, transcribes, and copies result.

### Required env vars

| Variable | Example | Purpose |
|---|---|---|
| `NATS_URL` | `nats://192.168.1.16:4222` | NATS server (must be reachable on LAN; port 4222 must be published) |
| `NATS_NKEY_SEED_PATH` | `~/.voicecli/nkeys/voice-client.seed` | `voice-client` NKey identity |

### ACL requirements

The `voice-client` identity needs:
- **publish:** `lyra.voice.stt.request`
- **subscribe:** `_inbox.voice-client.>` (inbox_prefix is hardcoded to `_inbox.voice-client` in `nats_stt_client.py` — required by ADR-051 normalized inbox ACL)

### Wrapper script + shortcut

```bash
# ~/.local/bin/voicecli-dictate-nats
#!/bin/bash
export NATS_URL="nats://192.168.1.16:4222"
export NATS_NKEY_SEED_PATH="$HOME/.voicecli/nkeys/voice-client.seed"
exec /home/mickael/.local/bin/voicecli dictate nats
```

Bind the script to a global shortcut. On COSMIC (Pop!_OS), register it in System Settings → Keyboard → Custom Shortcuts.
