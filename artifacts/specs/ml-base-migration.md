# Spec: `roxabi-ml-base` shared base image + voiceCLI migration

**Status:** Draft → ready for `/dev` (F-lite)
**Owner:** Mickael
**Date:** 2026-04-26
**Tier:** F-lite (multi-domain: infra + Python + Docker + CI)

---

## Problem

voiceCLI builds are slow:
- E2E tests: **~3 min** per run (use mock engine, but still pull ~5GB torch+CUDA)
- Staging build: **~5 min**
- Prod build: **~5 min**

Root cause: `torch`, `torchaudio`, `qwen-tts`, `chatterbox-tts`, `faster-whisper` are **required** dependencies → every Docker build (including mock-engine E2E) pulls full ML stack (~5GB).

imageCLI (planned dockerization) will hit the same problem with diffusers + transformers + torch.

---

## Goal

Single shared CUDA + torch + flash-attn base image (`ghcr.io/roxabi/ml-base`) used by voiceCLI (now) and imageCLI (later). Plus Python deps refactored into core + extras so E2E can install without torch.

### Success criteria

| Build | Before | After (target) |
|---|---|---|
| voiceCLI E2E | ~3 min | **<30 s** |
| voiceCLI staging | ~5 min | **<1 min** on cache hit |
| voiceCLI prod | ~5 min | **<1 min** on cache hit |
| Cross-project torch layer dedup on M₁ hub | ~10 GB | **~5 GB** |

---

## Architecture

```
ghcr.io/roxabi/ml-base:cu128-py312-torch2.7.1     ← built once per torch bump
    │
    ├── voicecli-tts:staging      ← FROM ml-base + .[tts,nats] + app
    ├── voicecli-stt:staging      ← FROM ml-base + .[stt,nats] + app
    └── imagecli:staging (later)  ← FROM ml-base + .[gen,nats] + app

(E2E images bypass: FROM python:3.12-slim, no ml-base, no torch)
```

### Base image stack

| Layer | Contents | Size |
|---|---|---|
| OS | `nvidia/cuda:12.8.1-cudnn9-runtime-ubuntu24.04` | ~2.8 GB |
| Python 3.12 | Ubuntu 24.04 native + uv | +~50 MB |
| System libs | ffmpeg, libsndfile1, portaudio19-dev, espeak-ng, build-essential | +~200 MB |
| Torch | `torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1` (cu128) | +~3 GB |
| Scientific | numpy, scipy, pillow, soundfile | +~100 MB |
| flash-attn | pre-compiled (5–10 min one-time CI cost) | +~200 MB |
| **Total** | | **~6 GB** |

### GPU compatibility

- ✅ Ampere sm_86 (RTX 3080 — M₁ hub)
- ✅ Blackwell sm_120 (RTX 5070 Ti — M₂ dev)
- CUDA 12.8 covers both. cu128 torch wheels include sm_120 kernels.

---

## Deliverables

### PR #1 — `roxabi/roxabi-ml-base` (new repo)

| File | Purpose |
|---|---|
| `Dockerfile` | Base image build |
| `.github/workflows/build.yml` | Build + push on main, weekly cron, manual dispatch |
| `README.md` | Tag matrix, usage from downstream projects, rebuild process |
| `LICENSE` | MIT |
| `.gitignore` | Standard |

#### Tags published

| Tag | Use case |
|---|---|
| `cu128-py312-torch2.7.1` | exact pin (downstream prod uses this) |
| `cu128-py312-torch2.7` | floating minor patch |
| `2026.04` | monthly snapshot |
| `latest` | dev convenience (never use in prod Dockerfiles) |
| `buildcache` | internal, GHA registry cache |

#### Rebuild triggers

| Event | Action |
|---|---|
| New torch release | Manual `workflow_dispatch` with new `torch_version` arg |
| CUDA / cuDNN security patch | Cron weekly catches it |
| flash-attn update | Manual or cron |
| Downstream incompatibility | Pin to older tag, fix base, bump |

### PR #2 — `roxabi/voiceCLI` migration

| File | Change |
|---|---|
| `pyproject.toml` | Split deps: core + `[tts]` `[stt]` `[mock]` `[nats]` extras. Remove torch from core. |
| `Dockerfile` | Rewrite: `FROM ghcr.io/roxabi/ml-base:cu128-py312-torch2.7.1` + `.[tts,stt,nats]` |
| `Dockerfile.e2e` | New: `FROM python:3.12-slim` + `.[mock,nats]` (no torch) |
| `deploy/entrypoint.sh` | Verify mode switch still works (tts \| stt) |
| `.github/workflows/*.yml` | Add registry cache, separate jobs per image variant |
| `tests/e2e/Dockerfile` (or compose) | Point to `Dockerfile.e2e` |
| `README.md`, `docs/` | Update install: `uv sync --extra tts` etc. |
| `CHANGELOG.md` | Document breaking change (users now need extras) |

#### `pyproject.toml` deps split (target)

```toml
[project]
dependencies = [
    "typer>=0.12",
    "soundfile",
    "lameenc>=1.7",
    "pyaudio>=0.2.14",
]
# torch / torchaudio / torchvision REMOVED — provided by ml-base

[project.optional-dependencies]
tts  = ["qwen-tts", "faster-qwen3-tts", "chatterbox-tts"]
stt  = ["faster-whisper>=1.2"]
mock = []  # E2E uses core only — no torch
voxtral = ["voxtral-tts[int4]", "scipy"]
hotkey  = ["pynput>=1.7"]
overlay = ["PyGObject>=3.42", "pycairo>=1.25"]
nats = ["roxabi-nats", "nats-py>=2.6,<3", "nkeys>=0.1", "nvidia-ml-py>=12,<14"]
all  = ["voicecli[tts,stt,nats]"]
```

#### `Dockerfile` (prod TTS/STT)

```dockerfile
ARG ML_BASE_TAG=cu128-py312-torch2.7.1
FROM ghcr.io/roxabi/ml-base:${ML_BASE_TAG}

WORKDIR /app

# Constraint torch to base-image version (prevents engines from upgrading torch)
RUN echo "torch==2.7.1\ntorchaudio==2.7.1\ntorchvision==0.22.1" > /tmp/constraints.txt

COPY pyproject.toml uv.lock ./
COPY src/ ./src/

RUN uv pip install --system --break-system-packages \
      --constraint /tmp/constraints.txt \
      ".[tts,stt,nats]"

COPY deploy/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
```

Final size target: **~7 GB** (6 GB base + ~1 GB engine wheels).

#### `Dockerfile.e2e` (mock E2E)

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      libsndfile1 portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY tests/e2e/ ./tests/e2e/

RUN uv pip install --system --break-system-packages ".[mock,nats]"

CMD ["pytest", "tests/e2e/", "-v"]
```

Final size target: **~150 MB**. Install time: **~30 s**.

---

## Local-first verification (mandatory before any CI/CD work)

> **Critical:** test the entire flow on M₂ (RTX 5070 Ti dev box) **before** writing GHA workflows. CI/CD adds debugging latency — local iteration is 10× faster. Only push to GHA when local manual flow is green.

### Phase A — `ml-base` local build & test (M₂)

```bash
# 1. Clone the new repo skeleton locally (or work in a /tmp dir initially)
cd /tmp && mkdir ml-base-poc && cd ml-base-poc
# write Dockerfile manually first

# 2. Build locally
podman build -t ml-base:local .
# → expect 5–10 min (flash-attn compile)

# 3. Verify torch + CUDA on Blackwell
podman run --rm --gpus all ml-base:local python -c "
import torch
print('torch:', torch.__version__)
print('cuda:', torch.cuda.is_available())
print('device:', torch.cuda.get_device_name(0))
print('capability:', torch.cuda.get_device_capability(0))
"
# → expect: cuda True, sm_120 (5070 Ti) or sm_86 (3080)

# 4. Verify flash-attn imports
podman run --rm --gpus all ml-base:local python -c "
import flash_attn
print('flash-attn:', flash_attn.__version__)
"
```

✅ Pass criteria: torch ✓ + CUDA ✓ + flash-attn ✓ on M₂.

### Phase B — voiceCLI Dockerfile local build (M₂)

```bash
cd ~/projects/voiceCLI
# checkout F-lite worktree first
git worktree add .worktrees/ml-base-migration

# 1. Build prod image against local ml-base
podman build --build-arg ML_BASE_TAG=local -t voicecli-tts:local .

# 2. Smoke test inside container
podman run --rm --gpus all voicecli-tts:local voicecli --help
podman run --rm --gpus all voicecli-tts:local python -c "
from voicecli import api
from voicecli.engines.qwen import QwenEngine
print('qwen import OK')
"

# 3. End-to-end: clone a known sample
podman run --rm --gpus all \
  -v ~/.voicecli:/root/.voicecli \
  voicecli-tts:local \
  voicecli generate "Bonjour, ceci est un test." -e qwen-fast
```

✅ Pass criteria: image builds + voiceCLI imports + generates one sample without OOM.

### Phase C — Dockerfile.e2e local build (M₂)

```bash
podman build -f Dockerfile.e2e -t voicecli-e2e:local .
# → expect <60 s

podman run --rm voicecli-e2e:local
# → expect E2E suite green in <30 s install + test runtime
```

✅ Pass criteria: image <200 MB, install <30 s, E2E green.

### Phase D — VRAM contention test on M₁ (RTX 3080 10 GB)

Manually pull/run on M₁ to verify Ampere path before automated rollout.

```bash
# On M₁
podman pull ghcr.io/roxabi/ml-base:cu128-py312-torch2.7.1
podman run --rm --gpus all ghcr.io/roxabi/ml-base:cu128-py312-torch2.7.1 \
  python -c "import torch; print(torch.cuda.is_available())"
```

✅ Pass criteria: image runs on Ampere sm_86, no missing kernels.

### Only after Phases A–D pass: write GHA workflows.

---

## Sequencing (~4 h work session)

| Step | Hours | Gate |
|---|---|---|
| 1. Create `roxabi-ml-base` repo skeleton (Dockerfile only, no workflow yet) | 0.5 | — |
| 2. **Phase A** local build + test on M₂ | 0.75 | torch ✓ flash-attn ✓ on Blackwell |
| 3. voiceCLI worktree + dep split (`pyproject.toml`) | 0.5 | `uv sync --extra tts` works |
| 4. voiceCLI new `Dockerfile` (FROM local ml-base) | 0.25 | builds |
| 5. **Phase B** voiceCLI smoke test on M₂ | 0.5 | clone sample green |
| 6. New `Dockerfile.e2e` | 0.25 | builds |
| 7. **Phase C** E2E local | 0.5 | suite green <30 s install |
| 8. Push `ml-base` repo to GitHub + add GHA workflow + trigger build | 0.5 | image lands in GHCR |
| 9. **Phase D** verify on M₁ Ampere | 0.25 | runs |
| 10. Update voiceCLI Dockerfile to use GHCR `ml-base` tag (not local) | 0.1 | builds |
| 11. voiceCLI GHA workflow updates (registry cache, split jobs) | 0.5 | CI green |
| 12. Quadlet test on M₂ before pushing M₁ | 0.25 | Quadlet starts |
| 13. Docs + CHANGELOG | 0.25 | — |
| **Total** | **~4 h** | |

---

## Risks

| # | Risk | Mitigation | Severity |
|---|---|---|---|
| 1 | Engine deps (qwen-tts, chatterbox-tts) pull conflicting torch versions | `--constraint constraints.txt` pinning torch in voiceCLI Dockerfile | 🔴 high |
| 2 | flash-attn version incompatible with torch 2.7.1 | Pin compatible flash-attn version in base; verify in Phase A | 🟡 medium |
| 3 | Mock engine missing for `[mock]` extra → E2E can't run | Verify `tests/e2e/` has it (commit b3f7182 suggests yes) before starting | 🟡 medium |
| 4 | M₁ Quadlet pulls new image, breaks prod | Tag previous image as `:staging-prev` before swap; atomic rollback | 🟡 medium |
| 5 | imageCLI's `torch>=2.11` pin blocks reuse | Verify the pin (likely typo for `>=2.7`); fix in imageCLI repo (out of scope this PR pair) | 🟡 medium (deferred) |
| 6 | GHCR rate limits / quota on `ml-base` push | First build is one-off; subsequent rebuilds rare | 🟢 low |
| 7 | `uv pip install --break-system-packages` warnings | Accept; or add a venv layer | 🟢 low |
| 8 | flash-attn compile time blows out CI budget | Build is cached forever per torch version; only re-runs on bumps | 🟢 low |

---

## Rollback plan

| Failure point | Rollback |
|---|---|
| `ml-base` build fails | No prod impact — base only used by new voiceCLI image |
| voiceCLI new image broken on M₁ | `podman pull ghcr.io/roxabi/voicecli-tts:staging-prev` and update Quadlet `Image=` line |
| Dep conflict surfaces post-merge | Revert PR #2 (PR #1 stays — no harm) |
| Engine throws CUDA mismatch | Pin engine deps to known-good versions in `[tts]` extra |

---

## Out of scope (this PR pair)

- imageCLI dockerization → separate issue, after this lands
- llmCLI changes → not needed (no torch)
- lyra changes → not needed (no torch)
- Brain (LLM) integration → deferred entirely
- New STT/TTS engines (Parakeet, NeuTTS, etc.) → deferred
- flash-attn version updates → handled via `workflow_dispatch`

---

## Decisions locked

| Decision | Choice |
|---|---|
| Base image | `nvidia/cuda:12.8.1-cudnn9-runtime-ubuntu24.04` |
| Python | 3.12 (Ubuntu 24.04 native) |
| Torch | 2.7.1 cu128 (Blackwell-ready) |
| flash-attn in v1? | ✅ Yes (bake into base) |
| Local-test first? | ✅ Yes (Phases A–D before any CI work) |
| Sequencing | All-at-once PR pair (`ml-base` + voiceCLI together) |
| Worktree | F-lite mandatory per global rules |

---

## References

- `~/projects/voiceCLI/CLAUDE.md` — project conventions
- `~/projects/voiceCLI/docs/NATS-SERVE.md` — NATS satellite + Quadlet docs
- `~/projects/voiceCLI/deploy/quadlet/` — current production unit files
- Commit `b3f7182` — "test(e2e): use pre-built container image" (relevant prior work)
- Commit `f68cc55` — "fix(docker): drop --extra voxtral" (relevant prior work)
- Commit `d654f00` — "fix(docker): drop uv run from entrypoint" (relevant prior work)
