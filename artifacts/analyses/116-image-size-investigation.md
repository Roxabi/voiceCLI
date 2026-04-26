# voiceCLI Image Size Investigation (#116)

**Date:** 2026-04-26
**Image:** `localhost/voicecli:local`
**Measured size:** 19.7 GB
**Spec target:** 8 GB
**Delta:** +11.7 GB over target

---

## 1. Layer breakdown

```
podman history localhost/voicecli:local
```

| Layer | Size | Source |
|---|---|---|
| ml-base base layers (cuda + cudnn + torch + flash-attn) | 12.7 GB | `ghcr.io/roxabi/ml-base:cu128-py312-torch2.7.1` |
| `COPY --from=builder /app /app` | **6.98 GB** | our venv + src |
| `apt-get install libportaudio2 ca-certificates` | 6.19 MB | runtime apt |
| `groupadd voicecli` + entrypoint copy | ~30 KB | — |
| **Total** | **19.7 GB** | |

→ Bloat lives in `/app`, copied wholesale from builder.

---

## 2. `/app` breakdown

```
podman run --rm --entrypoint sh localhost/voicecli:local -c 'du -sh /app/.venv /app/src'
```

| Path | Size |
|---|---|
| `/app/.venv` | **6.6 GB** |
| `/app/src` | 1.1 MB |

→ Bloat is exclusively in the venv.

---

## 3. Top venv site-packages

```
du -sh /app/.venv/lib/python3.12/site-packages/* | sort -h | tail -20
```

| Package | Size | In ml-base? | Verdict |
|---|---|:---:|---|
| `nvidia/*` | **4.3 GB** | ✅ | **DUPLICATED** |
| `triton` | **641 MB** | ✅ | **DUPLICATED** |
| `pkuseg` | 184 MB | ❌ | legit (Qwen ZH tokenizer) |
| `llvmlite` | 162 MB | ❌ | legit (numba) |
| `transformers` | 115 MB | ❌ | legit |
| `scipy` | 114 MB | ❌ | legit |
| `cuda` (cuda-python) | 106 MB | ❌ | legit |
| `av.libs` | 82 MB | ❌ | legit |
| `onnx` | 78 MB | ❌ | legit |
| `ctranslate2.libs` | 75 MB | ❌ | legit |
| `sympy` | 74 MB | ❌ | legit |
| `pandas` | 73 MB | ❌ | legit |
| `ctranslate2` | 60 MB | ❌ | legit |
| `gradio` | 57 MB | ❌ | review (UI dep — needed?) |
| `onnxruntime` | 53 MB | ❌ | legit |
| `sklearn` | 47 MB | ❌ | legit |
| `numpy` | 43 MB | ❌ | legit |
| `av`, `perth`, `numba` | ~30 MB ea | ❌ | legit |

**Duplicated total: ~5 GB.**

---

## 4. nvidia/* breakdown

```
du -sh /app/.venv/lib/python3.12/site-packages/nvidia/*
```

| Sub-pkg | Size | In ml-base? |
|---|---|:---:|
| `cudnn` | 1005 MB | ✅ |
| `cublas` | 830 MB | ✅ |
| `cusparselt` | 432 MB | ❌ (not present in ml-base) |
| `nccl` | 410 MB | ✅ |
| `cusolver` | 387 MB | ✅ |
| `cusparse` | 371 MB | ✅ |
| `cufft` | 269 MB | ✅ |
| `cuda_nvrtc` | 212 MB | ✅ |
| `nvshmem` | 195 MB | ❌ (not present in ml-base) |
| `curand` | 133 MB | ✅ |
| `nvjitlink` | 90 MB | ✅ |
| `cuda_cupti` | 41 MB | ✅ |
| `cuda_runtime` | 5 MB | ✅ |
| `cufile` | 3.2 MB | ✅ |
| `nvtx` | 0.4 MB | ✅ |

ml-base has 14/16. Missing: `cusparselt` (432 MB), `nvshmem` (195 MB) — pulled by torch 2.7.1 metadata but **ml-base's torch 2.7.1 install works without them** (verified: `import torch` succeeds in ml-base alone). These are optional accelerator libs.

---

## 5. Root cause

`uv sync --frozen --no-install-package torch --no-install-package torchaudio` excludes torch + torchaudio themselves from install, but **does NOT propagate the exclusion to their transitive deps**. The torch wheel's metadata declares `nvidia-cudnn-cu12`, `nvidia-cublas-cu12`, etc. as deps — uv resolves and installs them as standalone packages even though torch is skipped.

Same for `triton` (a torch dep).

The `--system-site-packages` venv flag makes the venv *able to see* ml-base's torch at `/usr/local/lib/python3.12/dist-packages/`, but uv's resolver doesn't know that and installs the wheels anyway.

---

## 6. Verification

ml-base alone has working torch + flash-attn:

```
podman run --rm ghcr.io/roxabi/ml-base:cu128-py312-torch2.7.1 \
  python -c "import torch, flash_attn; print(torch.__version__)"
# 2.7.1+cu128
```

ml-base's nvidia/triton at:
```
/usr/local/lib/python3.12/dist-packages/nvidia/{cublas,cudnn,...}
/usr/local/lib/python3.12/dist-packages/triton/
```

→ Already on `sys.path` for any venv created with `--system-site-packages`.

---

## 7. Proposed fix

Add to both `uv sync` invocations in `Dockerfile`:

```dockerfile
--no-install-package triton \
--no-install-package nvidia-cublas-cu12 \
--no-install-package nvidia-cuda-cupti-cu12 \
--no-install-package nvidia-cuda-nvrtc-cu12 \
--no-install-package nvidia-cuda-runtime-cu12 \
--no-install-package nvidia-cudnn-cu12 \
--no-install-package nvidia-cufft-cu12 \
--no-install-package nvidia-cufile-cu12 \
--no-install-package nvidia-curand-cu12 \
--no-install-package nvidia-cusolver-cu12 \
--no-install-package nvidia-cusparse-cu12 \
--no-install-package nvidia-cusparselt-cu12 \
--no-install-package nvidia-nccl-cu12 \
--no-install-package nvidia-nvjitlink-cu12 \
--no-install-package nvidia-nvtx-cu12 \
--no-install-package nvidia-nvshmem-cu12
```

**Expected outcome:** 19.7 GB → ~14.7 GB (−5 GB).

**Risk:** Low. ml-base's torch already works without `cusparselt`/`nvshmem`. All other nvidia-* libs are present in ml-base at the system site-packages path, which the venv inherits via `--system-site-packages`.

---

## 8. Why we still won't hit 8 GB

| Component | Size | Avoidable? |
|---|---|---|
| ml-base | 12.7 GB | No (cuda + cudnn + torch + flash-attn baseline) |
| Legit extras (transformers, scipy, pandas, ctranslate2, onnx, gradio, etc.) | ~1.3 GB | Partially (review `gradio` dep) |
| Project src + entrypoint | ~1 MB | No |
| **Floor** | **~14 GB** | |

The 8 GB spec target was likely set assuming a thinner base or a different install profile (e.g. STT-only without TTS extras). To approach 8 GB we would need:
- Mode-specific images (TTS-only ≠ STT-only ≠ all-extras)
- Or strip extras and install at runtime (defeats container immutability)
- Or trim ml-base itself (drop flash-attn? cuDNN?)

---

## 9. Side investigation prompts

If running this independently, the key checks are:

1. **Confirm the bloat:**
   ```
   podman history localhost/voicecli:local --format "{{.Size}}\t{{.CreatedBy}}" | head
   ```
2. **Confirm `nvidia/*` duplication:**
   ```
   podman run --rm --entrypoint sh localhost/voicecli:local -c \
     'du -sh /app/.venv/lib/python3.12/site-packages/nvidia/*'
   ```
3. **Confirm ml-base provides them:**
   ```
   podman run --rm --entrypoint sh ghcr.io/roxabi/ml-base:cu128-py312-torch2.7.1 -c \
     'ls /usr/local/lib/python3.12/dist-packages/nvidia/'
   ```
4. **Test the fix:** patch Dockerfile per §7, rebuild, re-measure. Verify:
   ```
   podman run --rm voicecli:local python -c \
     "import torch; print(torch.cuda.is_available()); import flash_attn"
   ```

---

## 10. Open questions

- Is `gradio` (57 MB) actually used by voiceCLI runtime, or only by chatterbox dev tools?
- Could we add `nvidia-cusparselt-cu12` + `nvidia-nvshmem-cu12` to ml-base instead of accepting them in voicecli's venv? (~627 MB savings if they're shared across consumers)
- Does uv have a `--no-install-deps-of` or `--inherit-system-package` flag we could use instead of enumerating 16 packages? (worth checking uv 0.11+ changelog)
