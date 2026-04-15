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
Set them in the supervisord `environment=` line (comma-separated `KEY="value"` pairs) or
export them in the shell environment before running `voicecli nats-serve`.

| Variable | Default | Purpose |
|---|---|---|
| `NATS_URL` | — (required) | NATS server URL, e.g. `nats://nats.internal:4222` |
| `NATS_NKEY_SEED_PATH` | — (required for nkey auth) | Path to the NKey seed file. **File permissions must be `0600`** — the satellite refuses to start if the file is world- or group-readable. |
| `NATS_CA_CERT` | — (optional) | Path to a PEM CA certificate for TLS verification |
| `VOICECLI_ENGINE` | from `voicecli.toml` | TTS engine override (`qwen`, `qwen-fast`, `chatterbox`, etc.) |
| `LYRA_TTS_ENGINE` | — | Alias for `VOICECLI_ENGINE`. Provided for the lyra#658 S3 cutover so existing supervisord `environment=` lines keep working without renaming. Takes the same values; `VOICECLI_ENGINE` wins if both are set. |
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
# From ~/projects/lyra (or wherever your supervisord Makefile lives)
make tts stop                            # stop voicecli_tts (socket daemon)
supervisorctl start voicecli_nats_tts   # start NATS satellite
```

On local-dev machines or large-VRAM boxes (e.g. RTX 5070 Ti with 16 GB) where coexistence
is safe, bypass the guard with the flag or env var:

```bash
voicecli nats-serve tts --allow-coexist
# or via env var (suitable for supervisord environment= lines)
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

Any other non-zero exit code is an unhandled error — check `stderr_logfile` for the Python
traceback.

supervisord's default `exitcodes` is `0`, which means it treats exit 3 and exit 78 as
unexpected and will attempt a restart. Always override with `exitcodes=0,3,78` as shown in
[Required supervisord stanza](#required-supervisord-stanza).

---

## Required supervisord stanza

`autorestart=unexpected` combined with `exitcodes=0,3,78` is **critical**. Without it,
supervisord treats exit 78 as unexpected and loop-restarts the process — causing a tight
restart loop on misconfigured hosts. Always include all three exit codes together.

Copy this block into your supervisord `conf.d/` directory and fill in host-specific values:

```ini
[program:voicecli_nats_tts]
command=voicecli nats-serve tts
environment=NATS_URL="nats://nats.internal:4222",NATS_NKEY_SEED_PATH="/home/lyra/.lyra/nkeys/voicecli-tts.seed",LYRA_TTS_ENGINE="qwen-fast"
autorestart=unexpected
exitcodes=0,3,78
stopsignal=TERM
stopwaitsecs=35
startsecs=15
user=lyra
stdout_logfile=/home/lyra/.local/state/lyra/logs/voicecli_nats_tts.log
stderr_logfile=/home/lyra/.local/state/lyra/logs/voicecli_nats_tts.err
```

Key fields:

| Field | Guidance |
|---|---|
| `autorestart=unexpected` | Restart on non-zero exits not listed in `exitcodes`; do NOT use `autorestart=true` |
| `exitcodes=0,3,78` | All three intentional exits: 0 (clean), 3 (drain timeout exceeded), 78 (VRAM guard tripped) — without 3, supervisord loop-restarts the process whenever the drain window is exceeded during shutdown |
| `stopsignal=TERM` | Sends SIGTERM on `supervisorctl stop`, triggering graceful drain before exit |
| `stopwaitsecs=35` | Must exceed `VOICECLI_DRAIN_TIMEOUT` (default 30 s) by a margin; 35 s gives the satellite time to drain before supervisord sends SIGKILL |
| `startsecs=15` | GPU model load time; lower on fast NVMe + large VRAM, raise if startup OOM observed |
| `user=lyra` | Match the user that owns the NKey seed file and log directory |

For the STT satellite stanza, see [STT — Required supervisord stanza](#stt--required-supervisord-stanza) below.

---

## Troubleshooting

### Quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| Process exits 78 immediately on startup | Live socket daemon (`tts-serve` / `stt-serve`) detected | Stop the socket daemon via supervisorctl, then restart; or pass `--allow-coexist` if coexistence is intentional |
| `PermissionError` referencing the seed file path | NKey seed file is not `0600` | `chmod 600 /path/to/voicecli-tts.seed` |
| Replies never arrive at the hub / requests time out | Wrong `NATS_URL`, network partition, or mismatched queue group name | Verify `NATS_URL` is reachable from the satellite host; queue group names are `tts-workers` (TTS) and `stt-workers` (STT) |
| Heartbeats stop arriving during a synthesis | Concurrency contract violated (bug) | Report it — the spec guarantees heartbeats continue independently of in-flight synthesis |
| Hub logs `payload_too_large` | Reply WAV exceeds NATS server `max_payload` | Increase `max_payload` in the NATS server config, or shorten the synthesis text |
| Satellite starts but produces no output; logs show CUDA OOM | VRAM exhausted by coexisting processes | Stop other GPU-heavy daemons, reduce `VOICECLI_MAX_CONCURRENT`, or move to a host with more VRAM |
| supervisord keeps restarting the process in a tight loop | `autorestart=true` or `exitcodes` missing 78 or 3 | Set `autorestart=unexpected` and use `exitcodes=0,3,78` — see [Required supervisord stanza](#required-supervisord-stanza) |
| TLS handshake errors connecting to NATS | Missing or wrong CA certificate | Set `NATS_CA_CERT` to the PEM file for your internal CA |

### Checking liveness

The satellite logs its startup sequence to stdout. A healthy start looks like:

```
INFO  vram-guard: no live socket daemon detected — proceeding
INFO  nats: connected to nats://nats.internal:4222
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
nats req lyra.voice.tts.request '{"request_id":"test-1","text":"hello","engine":"qwen-fast"}' --server nats://nats.internal:4222
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
| `engine` | string | Optional; falls back to `default_engine` from satellite startup. Validated via `validate_nats_token`; unknown engine → `engine_unavailable`. |
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
make stt stop                            # stop voicecli_stt (socket daemon)
supervisorctl start voicecli_nats_stt   # start NATS satellite
```

> **Co-located GPU warning — RTX 3080 (10 GB) and similar hosts**
>
> On hosts where TTS and STT satellites share a single GPU (e.g. `roxabituwer`:
> TTS ~7.4 GB + STT ~2.2 GB = ~9.6 GB steady-state), set `VOICECLI_MAX_CONCURRENT=1`
> on the STT satellite:
>
> ```ini
> environment=...,VOICECLI_MAX_CONCURRENT="1"
> ```
>
> or pass `--max-concurrent 1` on the command line. A second concurrent STT decode would
> overflow the remaining VRAM headroom and OOM. The default `2` is intended for hosts
> running the STT satellite standalone.

### STT — Required supervisord stanza

Same rules as TTS (`autorestart=unexpected`, `exitcodes=0,3,78`) — see
[Required supervisord stanza](#required-supervisord-stanza) for rationale.

```ini
[program:voicecli_nats_stt]
command=voicecli nats-serve stt
environment=NATS_URL="nats://nats.internal:4222",NATS_NKEY_SEED_PATH="/home/lyra/.lyra/nkeys/voicecli-stt.seed",VOICECLI_MODEL="large-v3-turbo",VOICECLI_MAX_CONCURRENT="1"
autorestart=unexpected
exitcodes=0,3,78
stopsignal=TERM
stopwaitsecs=35
startsecs=10
user=lyra
stdout_logfile=/home/lyra/.local/state/lyra/logs/voicecli_nats_stt.log
stderr_logfile=/home/lyra/.local/state/lyra/logs/voicecli_nats_stt.err
```

`VOICECLI_MAX_CONCURRENT="1"` is set here because this example targets a co-located GPU
host (TTS + STT on the same GPU). Remove or raise to `2` on standalone-STT hosts.
