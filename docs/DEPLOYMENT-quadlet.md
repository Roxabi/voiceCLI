---
title: voiceCLI — Quadlet deployment
description: Production deployment on M₁ via Podman Quadlet (ADR-055).
---

# voiceCLI — Quadlet Deployment

Production deploys voiceCLI as systemd-managed Podman containers per
[ADR-055 — Quadlet ecosystem conventions](../../lyra/docs/architecture/adr/055-quadlet-ecosystem-conventions.mdx).

voiceCLI is the second project to adopt the pattern (after Lyra); the shared
deploy library and image-naming convention come from ADR-055 D1 / D5.

## Topology

| Unit | Purpose |
|---|---|
| `voicecli-models.volume`   | Named volume for HuggingFace + voicecli model caches |
| `voicecli-stt.container`   | STT worker — subscribes to `lyra.voice.stt.request` queue group (namespace matches Lyra's ACL matrix) |
| `voicecli-tts.container`   | TTS worker — subscribes to `lyra.voice.tts.request` queue group |

Workers run on `roxabi.network` (shared with lyra) and connect to the lyra-managed
NATS server via `NATS_URL=nats://lyra-nats:4222` (no TLS). voiceCLI no longer ships
its own NATS container — NATS is provided by the `lyra` project
(`lyra-nats.container` Quadlet unit in that repo).

The retired `voicecli.network` and `voicecli-nats.container` units have been removed.
The `voicecli-nats-auth` secret is no longer needed.

## Provisioning

### 1. Ensure lyra-nats is running

voiceCLI workers depend on the shared NATS server managed by the `lyra` project.
Before starting voiceCLI, confirm it is up:

```bash
systemctl --user status lyra-nats
```

If it is not running, start it from the lyra repo first (`systemctl --user start lyra-nats`).

### 2. NATS nkey seeds

Seeds live at `~/.voicecli/nkeys/voice-{stt,tts}.seed` on the host (ADR-055 D4).
They are created once by Lyra's `gen-nkeys.sh`; relocation from the old
`~/.lyra/nkeys/` path is covered by the runbook below.

Canonical ACLs are in Lyra's `deploy/nats/acl-matrix.json` under the
`voice-stt` and `voice-tts` identities.

### 3. Install Quadlet units

```bash
make quadlet-install
# copies deploy/quadlet/*.{container,volume} → ~/.config/containers/systemd/
# reloads systemd --user
```

### 4. Podman secrets

Two seed secrets are required (no `voicecli-nats-auth` — that secret was part of
the retired per-project NATS topology):

```bash
make quadlet-secrets-install
# creates: voicecli-nats-stt, voicecli-nats-tts
# stops services before replace and restarts after (avoids mid-rotation mismatch)
```

### 5. Start services

```bash
systemctl --user start voicecli-tts voicecli-stt
systemctl --user status voicecli-{tts,stt}
```

## Deploy pipeline

### Automatic (recommended)

Since #929, prod uses `podman auto-update` to automatically pull new images from GHCR. The timer fires every 5 minutes:

```bash
systemctl --user is-active podman-auto-update.timer  # verify timer is active
podman auto-update --dry-run                          # check pending updates
```

Containers with `Label=io.containers.autoupdate=registry` pull new digests from `ghcr.io/roxabi/voicecli:staging` and restart automatically. No manual intervention after a staging merge.

See [Lyra's container-publishing.md](../../lyra/docs/ops/container-publishing.md#auto-update-flow) for full details.

### Manual fallback

`scripts/deploy-quadlet.sh` sources the shared library from
`~/.local/lib/roxabi/deploy-lib.sh` (installed by Lyra's
`make quadlet-install-deploy-lib` — single SSoT per ADR-055 D5) and runs the
standard pipeline: `git pull` → `uv sync` → `pytest` → `podman build
localhost/voicecli:latest` → `systemctl --user restart voicecli-stt voicecli-tts`.

Per-project variables live at the top of `scripts/deploy-quadlet.sh` (PROJECT,
IMAGE, ADAPTER_SERVICES, etc.) and map directly onto the library's interface.

## Seed-relocation runbook (one-time, from supervisord era)

Only needed once when flipping a previously-supervisord-deployed M₁ to Quadlet.

```bash
# 1. Stop supervisord workers
supervisorctl stop voicecli_stt voicecli_tts

# 2. Move seeds from old Lyra-owned path to voiceCLI-owned path (ADR-055 D4)
mkdir -p ~/.voicecli/nkeys
mv ~/.lyra/nkeys/voice-stt.seed ~/.voicecli/nkeys/
mv ~/.lyra/nkeys/voice-tts.seed ~/.voicecli/nkeys/
chmod 600 ~/.voicecli/nkeys/*.seed

# 3. Let the deploy timer roll forward — it restarts supervisord workers
#    pointing at the new path. No config edit required (PR #104 already shipped).

# 4. (Later, at Quadlet cutover) follow Provisioning above + disable supervisord confs.
```

## Cutover checklist (supervisord → Quadlet)

- [ ] `systemctl --user status lyra-nats` — lyra-nats is up and healthy
- [ ] `~/.voicecli/nkeys/voice-{stt,tts}.seed` exists, 0600
- [ ] `make quadlet-install` ran without error; units in `~/.config/containers/systemd/`
- [ ] `make quadlet-secrets-install` ran; `podman secret ls` shows voicecli-nats-{stt,tts}
- [ ] `systemctl --user start voicecli-tts voicecli-stt` succeeds; containers report `Running`
- [ ] `UserNS=keep-id` maps voicecli image `appuser` UID → host UID correctly (verify with `podman exec voicecli-stt id`)
- [ ] End-to-end: Lyra hub publishes a `voice.stt.request`, receives a reply
- [ ] Disable + remove old supervisord confs: `supervisorctl stop voicecli_stt voicecli_tts && rm supervisor/conf.d/voicecli_{stt,tts}.conf`
- [ ] Remove voiceCLI from Lyra's `deploy.sh` EXTRA_REPOS once autonomous `voicecli-deploy.timer` is in place

## Ecosystem note

The big-bang NATS consolidation that produced this topology is documented in the
sibling Lyra repo:
[`docs/ops/bigbang-nats-consolidation.md`](https://github.com/Roxabi/lyra/blob/staging/docs/ops/bigbang-nats-consolidation.md).

That runbook covers the full cutover sequence, rollback procedure, and the
post-cutover cleanup steps for both repos (Lyra #919 / voiceCLI #107).

## References

- [ADR-055 — Quadlet ecosystem conventions](../../lyra/docs/architecture/adr/055-quadlet-ecosystem-conventions.mdx)
- [Lyra's QUADLET-ADOPTION.md](../../lyra/docs/QUADLET-ADOPTION.md) — project-agnostic adoption template
- [ADR-054 — Podman secrets](../../lyra/docs/architecture/adr/054-podman-secrets.mdx)
