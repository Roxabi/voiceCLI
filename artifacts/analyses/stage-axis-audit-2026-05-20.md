# voiceCLI — Stage-Axis Decomposition Audit

**Date:** 2026-05-20
**Auditor:** Claude (read-only)
**Reference framework:** lyra #1277 stage-axis decomposition strategy
**Source doc:** `~/projects/lyra/artifacts/analyses/1277-stage-axis-refactor-strategy.mdx`

---

## Verdict Summary

voiceCLI does **not** exhibit the lyra #1277 N×M cascade in its active, damaging form. The codebase grew per-engine (target axis) as expected, but two key decisions absorbed most cross-cutting concerns:

1. The `ENGINE_CAPS` matrix in `engine_caps.py` + `translate.py` — an **already-implemented stage-axis decomposition** for the M-capability concern. N engines × M capabilities = N+M code paths, not N×M.
2. A `SynthesisPort` protocol layer extracted in ADR-059.

A bounded N×M site **does** exist in the two Chatterbox engine files (`_generate_segmented`, `_generate_chunked` verbatim copies), but the cascade is latent, not active — no sibling-fix churn in commit history. The biggest structural risks: dual-protocol TTS path (Unix socket daemon + NATS adapter) producing structural asymmetry between TTS and STT at the daemon layer, and `api.py` at 1 047 LOC hosting config resolution + input resolution + chunked helpers + public API together. Neither has triggered a cascade yet; quality gates (300-line limit, import-linter) are applying drag pressure.

---

## Section 1 — Axis of Decomposition

### File map

**Per-engine axis (engines/)**

| File | LOC | Concerns accreted |
|---|---|---|
| `engines/qwen.py` | 218 | model load (2 variants), CUDA guard, segmented gen, voice clone, speaker select |
| `engines/qwen_fast.py` | 150 | model load override, CUDA graph warmup, segmented gen override, API translation |
| `engines/chatterbox.py` | 186 | model load, eager-attention fixup, chunked gen, segmented gen, lang-id resolution |
| `engines/chatterbox_turbo.py` | 171 | model load, chunked gen, segmented gen (identical except language override removed) |
| `engines/voxtral.py` | 200 | model-dir resolution, model load, single-chunk gen, segmented gen, voice-lang dispatch |
| `engines/mock.py` | 71 | minimal shim |

**Per-modality grouping (TTS vs STT)**

- TTS: `engine.py`, `engine_caps.py`, `translate.py`, `daemon.py`, `daemon_protocol.py`, `adapters/synthesis.py`, `nats/tts_adapter.py`, `nats/_tts_runner.py`
- STT: `transcribe.py`, `stt_daemon.py`, `stt_client.py`, `nats/stt_adapter.py`, `nats/_stt_runner.py`, `nats_stt_client.py`, `nats/_audio_utils.py`
- Shared: `api.py`, `cli.py`, `config.py`, `markdown*.py`, `utils.py`, `ports/`

**Per-concern horizontal files**

`engine_caps.py` (179 LOC, capability matrix), `translate.py` (173 LOC, stage translator), `daemon_protocol.py` (318 LOC, wire protocol + sanitization), `nats/_validation.py` (241 LOC, pure validation), `ports/` (3 Protocol/ABC files), `utils.py` (253 LOC, audio helpers).

### ENGINE_CAPS matrix — stage-axis attempt

**Yes, and it largely succeeds.** `engine_caps.py:7-68` defines a 5-engine × 13-capability matrix. `translate.py:88-173` consumes it: single `translate_for_engine(doc, engine)` strips or adapts fields based on capability flags rather than per-engine conditional blocks. Canonical stage-axis pattern — N engines × M capabilities = **N+M code paths, not N×M**. Adding a new capability requires one row per engine in `engine_caps.py` and one branch in `translate.py`.

**Verdict: Mixed — engine files are target-axis; the translate/caps layer is stage-axis.** The stage-axis portion covers the most expensive concern (parameter translation). Remaining per-engine duplication (model loading, segmented generation) is bounded.

---

## Section 2 — Cascade Symptoms

### `f"...{exc}"` and `str(exc)` — 34 occurrences

Most are terminal-facing (CLI, `stt_daemon.py`) — `f"Error: {e}"` shown directly to user. **Bus-bound sites:**

- `daemon_protocol.py:311`: `send_json(conn, {"status": "error", "message": str(exc)})` — Unix socket boundary, untyped error surface
- `stt_daemon.py:450`, `stt_daemon.py:543`: same `str(exc)` → JSON pattern
- `nats/_tts_runner.py:137,155`: `str(exc)[:200]` — correctly capped, structured log field. Safe.
- `nats/tts_adapter.py:33`: `_safe_reason()` helper — explicitly caps and sanitizes. Correct.
- `engine.py:59`: `msg = str(exc)` — pattern-match on CUDA error text before re-raise. Correct.

**Active sites: 3 raw `str(exc)` over Unix socket (`daemon_protocol.py:311`, `stt_daemon.py:450,543`).** Not cascading — no sibling expansion visible.

### `except Exception` counts

| File | Count |
|---|---|
| `stt_daemon.py` | 13 |
| `cli_doctor.py` | 8 |
| `clipboard.py` | 4 |
| `stt_client.py` / `nats_stt_client.py` / `nats_recorder.py` | 3 each |

`stt_daemon.py:13` is structural — synchronous state machine in threads, each catch is a distinct failure mode (OOM retry, toggle handler, recording thread, transcription loop). No `except Exception: pass`. Defensive terminal-tier handling.

### `__init_subclass__`, class-attr overrides

**No `__init_subclass__` usage found.** One class-attribute override worth noting:

- `TTSEngine._small: bool = False` (`ports/tts.py:16`) — mutated post-construction via `eng._small = True` at `api.py:727` and `adapters/synthesis.py:143,201,249`.
- **Qwen-only knob** exposed as public attribute on base ABC, polluting all non-Qwen engines with a meaningless attribute.
- DEBT slug: `adapter-magic-constants` / `protocol-private-ducktyping`.
- Latent cascade: if a second engine needs a similar flag, it gets added to the ABC or a new mutation site proliferates.

### Duplicated helpers across engine files

**`_generate_chunked`** — byte-for-byte identical:
- `chatterbox.py:40-50` ≡ `chatterbox_turbo.py:34-44`
- Only difference: containing class `_load_model()` impl
- N=2 duplication today

**`_generate_segmented`** — near-identical:
- `chatterbox.py:52-90` vs `chatterbox_turbo.py:46-82`
- Only semantic difference: `chatterbox.py:79` has `if seg.language is not None: kw["language_id"] = _resolve_language(seg.language)`; `chatterbox_turbo.py` lacks it (Turbo is English-only)
- `concat_audio` call, gap/xfade comprehensions, loop structure — **verbatim identical**
- **76 LOC of duplication** across 2 files

**`_load_model`** — each of 5 engines has its own. **Not structurally duplicated** — distinct library imports, kwargs, object types. Per-engine by necessity.

---

## Section 3 — Quantitative

### Files >300 LOC

| File | LOC | Note |
|---|---|---|
| `api.py` | 1 047 | Exempted |
| `cli.py` | 821 | Exempted |
| `stt_daemon.py` | 797 | Exempted |
| `cli_dictate.py` | 486 | Over threshold |
| `transcribe.py` | 344 | Over threshold |
| `daemon_protocol.py` | 318 | Over threshold |
| `adapters/synthesis.py` | 298 | Just under |
| `nats/_validation.py` | 241 | Under |

Three largest files partially refactored already — `0df3b8b` "split oversized modules to meet 300-line file-length gate", `3a0d59a` "split cli.py god object into sub-apps". **Gate is catching and draining residual complexity.**

### Sibling-fix patterns in last 50 commits

- `37aafc2` ("refactor(nats): extract envelope validation + run loops from adapters #153") — affected `tts_adapter.py` **AND** `stt_adapter.py` simultaneously. Correct response to a structural finding: both adapters shared the shape, both refactored together. **Not a cascade — coordinated architectural refactor.**
- `a434740` ("fix(nats): strip TTS newlines, surface param validation errors") — TTS only, no STT sibling.
- Engine files appear in **only 1 commit** in last 50 (`qwen.py` once). **Zero sibling-fix churn across TTS engines.**
- `6af8a18` (PR #152, sample recording validation) — `samples.py` only.

**No N×M cascade pattern in commit history.** The one true two-adapter change (PR #153) was architectural refactoring, not a bug-fix cascade.

---

## Section 4 — DEBT Inventory

### `artifacts/debt/` directory

**Does not exist.** `artifacts/` contains `frames/`, `plans/`, `specs/`. No formal debt tracking. Debt managed implicitly via commit messages, ADRs, and file-length gate.

### Inline debt markers

`grep -rn 'TODO|FIXME|XXX|HACK|DEBT'` across `src/`: **0 occurrences.** Codebase actively cleared of inline markers.

### `noqa` annotations — 23 total

- 12× `noqa: PLC0415` — deferred imports inside function bodies (intentional, documented in CLAUDE.md)
- 5× `noqa: F401` — re-export anchors
- 3× `noqa: BLE001` — broad exception in runner loops (intentional terminal handlers)
- 2× `noqa: E402` — module-level imports after module-level code
- 1× `noqa: F811` — `api.py:970` shadowed import of `TranscriptionResult` (low severity)

**Overall: no abstract debt buckets, no DEBT slugs, very low noqa density.**

---

## Section 5 — Composition vs Inheritance for Capabilities

### Retry logic — **ONCE**

`stt_daemon.py:506-546` — 3-attempt OOM-recovery loop with exponential backoff (5s → 10s → 20s). Not shared with TTS. TTS has no retry — daemon queue serializes requests, VRAM check at `engine.py:30-49` raises before attempting load. **No retry duplication.**

### Audio format conversion / concat / WAV write — **ONCE each**

- `concat_audio` — `utils.py:172-232`. All 4 engine `_generate_segmented` methods import and call it.
- `split_sentences` — `utils.py:125-130`. `chatterbox.py:11` + `chatterbox_turbo.py:11` import it.
- `sf.write()` — called in-line in every engine's `generate`/`clone` return path. Single-line, not duplicated helper.

### Sample-rate handling — appropriate per-engine

Three distinct approaches, each correct:
- Qwen: SR returned by model `wavs, sr = gen_fn(...)`
- Chatterbox: SR from `self._load_model().sr` attribute
- Voxtral: hardcoded `_SAMPLE_RATE = 48000`

**Not duplicated helpers** — each appropriate to how engine returns SR.

### Model warmup — **asymmetric**

- TTS: lazy load on first `generate`/`clone` via `_load_model()` in each engine. No shared warmup.
- STT: explicit warmup via `api.warmup_model()` → `transcribe._load_model()`, called by NATS STT runner at `_stt_runner.py:101`.
- No TTS equivalent via NATS (pre-warm done via `model_registry.get()` in `tts_adapter.py:126`).

**Structural asymmetry between modalities on warmup path — latent cascade risk.**

### TTSEngine ABC — capability contract, not pipeline contract

`ports/tts.py` defines 3-method ABC: `generate`, `clone`, `list_voices`. **Does not enforce a stage pipeline.** No `preprocess → generate → postprocess` decomposition. Engines free to implement all steps inline.

---

## Section 6 — Result[T,E] vs Raising

### Engine boundaries — raise on error

All engines raise: `RuntimeError` (CUDA), `ValueError` (bad voice/param), `NotImplementedError` (Voxtral clone). No typed `Result[T,E]`. `cuda_guard` catches and re-raises as `RuntimeError` with CUDA-tagged message (`engine.py:52-62`).

### Daemon (Unix socket) — **3-tier untyped leak**

`daemon_protocol.py:309-316`:
```python
except Exception as exc:
    send_json(conn, {"status": "error", "message": str(exc)})
```

Raw exception string sent as untyped JSON `"message"` field. Client (`adapters/synthesis.py:63`) reads `resp.get("message", "unknown error")` and logs it, returns `None` on failure. `api.py:762` turns `None` into a `RuntimeError`. **3-tier leak: exception → str → JSON → None → RuntimeError.** Untyped throughout.

STT daemon `stt_daemon.py:450,543` — same `str(exc)` → JSON pattern.

### TTS vs STT — consistent at socket, divergent at NATS

**Unix socket:** structurally consistent (same `send_json/recv_json`, same `{"status": "error", "message": str(exc)}` shape).

**NATS:** both adapters use typed Pydantic response models (`TtsResponse`, `SttResponse` from `roxabi_contracts`) and internal error codes (`"malformed_request"`, `"engine_unavailable"`, `"capacity_exceeded"`, `"synthesis_failed"`, `"transcription_failed"`). **No raw string propagation over NATS. NATS path is better-typed than Unix socket.**

**Divergence:** TTS NATS runner (`_tts_runner.py:127-157`) has language-fallback retry on `api.ParamValidationError`. STT runner (`_stt_runner.py:120-125`) catches same error but does not retry. **If STT grows similar fallback, logic must be written again.**

---

## Section 7 — Cross-Repo Coupling with Lyra

### pyproject.toml dependencies

`roxabi-nats`: direct dep under `[project.optional-dependencies] nats`. Source: `git = "https://github.com/Roxabi/lyra.git", subdirectory = "packages/roxabi-nats", branch = "staging"`.

`roxabi-contracts`: **NOT declared as direct dependency** in `pyproject.toml`. Transitive via `roxabi-nats` (`uv.lock:1697-1699`).

But voiceCLI imports from it directly:
- `nats/tts_adapter.py:13-14`
- `nats/stt_adapter.py:12-13`
- `nats_stt_client.py:17-18`

**Undeclared direct dependency.** If `roxabi-nats` stops re-exporting transitively, voiceCLI NATS layer breaks silently.

### NATS subject layout — **3 separate literals**

- `nats/tts_adapter.py:36-37`: `SUBJECT = "lyra.voice.tts.request"`, `HEARTBEAT_SUBJECT = "lyra.voice.tts.heartbeat"`
- `nats/stt_adapter.py:26-27`: `SUBJECT = "lyra.voice.stt.request"`, `HEARTBEAT_SUBJECT = "lyra.voice.stt.heartbeat"`
- `nats_stt_client.py:23`: `SUBJECT = "lyra.voice.stt.request"` — third copy

No shared constant. lyra hub calling these subjects must agree on same strings. `roxabi-contracts` contains Pydantic models but **not** subject strings. **DEBT slug: `adapter-magic-constants`** (string literals hardcoded in N sibling files, no SSoT).

### Inline NATS adapter pattern

Both `TtsNatsAdapter` and `SttNatsAdapter` inherit from `NatsAdapterBase` (roxabi-nats). Base handles connection, drain, heartbeat dispatch. Each adds: request ID validation, capacity semaphore, executor threading, pre-warm, `handle()` dispatch. **Architecturally sound** — base does heavy lifting.

`_err_tts` / `_err_stt` are structurally identical except for Pydantic response type. If llmCLI/imageCLI follow same pattern, each will have own `_err_X` function. Mild smell but not cascade risk since functions are trivial.

---

## Section 8 — Findings + Recommendations

### Finding 1 — Chatterbox sibling duplication (latent cascade)

**Evidence:** `engines/chatterbox.py:40-90` and `engines/chatterbox_turbo.py:34-82`. `_generate_chunked` verbatim identical (10 LOC × 2 = 20). `_generate_segmented` 97% identical (38 LOC × 2 = 76, 1-line diff: language override).
**Risk:** Latent. N=3 Chatterbox variants → cascade site.
**Recommendation:** `ChatterboxBase` mixin: extract `_generate_chunked` + `_generate_segmented` (without language override); `ChatterboxEngine` overrides for language dispatch, `ChatterboxTurboEngine` inherits as-is. ~76 LOC saved.

### Finding 2 — `_small` class-attribute mutation (protocol-private-ducktyping)

**Evidence:** `ports/tts.py:16` declares `_small: bool = False` on ABC. `api.py:727`, `adapters/synthesis.py:143,201,249` mutate it post-construction: `eng._small = True`.
**Risk:** Active but bounded. Adding new Qwen-family parameter (e.g., precision `bf16` vs `fp16`) likely creates second class-attribute mutation site. ABC becomes bag of engine-specific knobs.
**Recommendation:** Move `_small` from `TTSEngine` to `QwenEngine` class attribute. Configure via constructor argument, not post-construction mutation.

### Finding 3 — NATS subject strings as magic constants (adapter-magic-constants)

**Evidence:** `nats/tts_adapter.py:36`, `nats/stt_adapter.py:26`, `nats_stt_client.py:23`. Three separate literals defining `lyra.voice.{tts,stt}.request`. No SSoT in `roxabi-contracts`.
**Risk:** Latent. Subject rename in lyra requires manual sync per literal site. Import-linter allowlist at `b8af4c7` suggests team awareness.
**Recommendation:** File lyra issue: promote subjects to `roxabi_contracts.voice.SUBJECTS` (mirror of `roxabi_contracts.llm.SUBJECTS`). Then voiceCLI imports rather than hardcodes.

### Finding 4 — `api.py` complexity residue at 1 047 LOC

**Evidence:** `api.py:1-1048` hosts 3 validation helpers, compound TTS param validator, output path validation, config resolution, input resolution, ref resolution, chunked generation helpers (`_emit_chunk`, `_generate_chunked`, `_clone_chunked` — 77 LOC, 95% overlap between chunked/clone), 3 public API functions + 3 async wrappers + 2 utility functions.
**Risk:** Active complexity residue contained within one file. `_generate_chunked` vs `_clone_chunked` is N=2 duplication at the API orchestration level.
**Recommendation:** Extract chunked-generation logic into `api/chunked.py` module. Parameterize over generation vs clone via callable or kwargs.

### Finding 5 — `stt_daemon.py` dual protocol paths

**Evidence:** `stt_daemon.py` at 797 LOC implements: PyAudio probe, WAV framing, recording state machine, parecord subprocess path, PyAudio direct path, OOM retry, JSON socket protocol (`_send_json`, `_recv_json` inline), transcription dispatch.
**Risk:** Latent. Unlike TTS daemon which extracted protocol to `daemon_protocol.py` (commit `0df3b8b`), `stt_daemon.py` retains wire-protocol inline. If `daemon_protocol.py` format ever changes, `stt_daemon.py` copies must update separately.
**Recommendation:** `stt_daemon.py._send_json/_recv_json` → import from `daemon_protocol`. ~25 LOC saved.

---

### Should voiceCLI follow the stage-axis pivot?

**Short answer: No — the pivot is already partially complete. Urgent action is consolidation, not restructuring.**

voiceCLI has already implemented the most valuable stage-axis element: `ENGINE_CAPS` + `translate_for_engine` absorbs the entire M-capability × N-engine matrix. This **is** what lyra #1277 recommends as the fix.

Remaining N×M surface is small:
- N=2 (Chatterbox variants) × M=2 concerns (chunked gen, segmented gen) = 4 sites
- N=2 (daemon/NATS) × M=1 (wire protocol) = asymmetry, not duplication

**On splitting TTS vs STT into separate projects:** Not recommended. Both share `utils.py`, `config.py`, `cli.py`, `markdown.py`, `api.py`, all port/adapter infra. VRAM interaction between TTS and STT daemons is a real operational concern best managed from a unified codebase.

2 stages worth extracting (if growth continues to N=3+ engines):
1. **`ChatterboxBase` mixin** — collapses N=2 verbatim duplication before N=3.
2. **`daemon_wire.py`** — unify `send_json`/`recv_json` between `daemon_protocol.py` and `stt_daemon.py`.

LOC estimate: net saving ~106 LOC. More important for reducing future cascade surface than current size.

---

## Top 3 Actions

1. **Declare `roxabi-contracts` as direct dependency** in `pyproject.toml` (`[project.optional-dependencies] nats`). Used directly at 4 import sites. An undeclared direct dep is silent breakage risk. One-line fix, zero risk.

2. **Extract `ChatterboxBase` mixin** — `engines/chatterbox.py:40-90` to shared mixin, `ChatterboxTurboEngine` inherits, `ChatterboxEngine` overrides `_generate_segmented` for language dispatch. Collapses verbatim N=2 duplication before N=3 Chatterbox variant arrives.

3. **Unify daemon wire protocol** — move `stt_daemon.py`'s `_send_json`/`_recv_json` (inline at ~lines 130-155) to import from `daemon_protocol.py`. Closes latent divergence (Finding 5), reduces `stt_daemon.py` by ~25 LOC.

---

## Cross-repo notes

- **Diverges from llmCLI on `roxabi-contracts` declaration**: llmCLI declares it as direct dep, voiceCLI does not. **Converge to llmCLI's model.**
- **Diverges from llmCLI on NATS subject ownership**: llmCLI imports `roxabi_contracts.llm.SUBJECTS`; voiceCLI hardcodes 3 literals. **File lyra issue to promote `voice.SUBJECTS` upstream.**
- voiceCLI's `ENGINE_CAPS` + `translate.py` pattern is **a reusable model** for imageCLI (which has 9 engines × ~8 quant/face/format concerns and would benefit from a capability matrix instead of per-engine accretion).
