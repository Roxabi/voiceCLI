---
title: VRAM Contention — Operator Guide
description: Managing CUDA OOM when TTS and STT daemons share a single GPU.
---

# VRAM Contention

TTS daemon (qwen-fast) ~7.4 GB + STT daemon ~2.2 GB fills the whole RTX 3080 (10 GB). Both running → `voicecli clone` (or any op needing extra VRAM alloc) fails CUDA OOM even though the model is already loaded.

## Fix: stop STT before clone, restart after

**M₁ (Quadlet):**
```bash
systemctl --user stop voicecli-stt
voicecli clone "text" -e qwen-fast
systemctl --user start voicecli-stt
```

**M₂ (native):**
```bash
pkill -f 'voicecli stt-serve'
voicecli clone "text" -e qwen-fast
voicecli stt-serve &
```

## Satellite vs socket daemon

`voicecli nats-serve` and `voicecli serve` / `stt-serve` compete for the same GPU. The VRAM-sequencing guard refuses to start a satellite if a live socket daemon is detected (exit 78). See [`docs/NATS-SERVE.md#vram-sequencing`](docs/NATS-SERVE.md#vram-sequencing) for full guard behavior and coexistence modes.
