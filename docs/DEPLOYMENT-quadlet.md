---
title: voiceCLI — Quadlet deployment
description: Production deployment on M₁ via Podman Quadlet (ADR-055).
---

# voiceCLI — Quadlet Deployment

Production deploys voiceCLI as systemd-managed Podman containers per
[ADR-055 — Quadlet ecosystem conventions](../../lyra/docs/architecture/adr/055-quadlet-ecosystem-conventions.mdx).

voiceCLI is the second project to adopt the pattern (after Lyra); the shared
deploy library and image-naming convention come from ADR-055 D1 / D5. The
`nats-container.conf` is snapshotted from Lyra at commit time (not
bind-mounted live) to avoid cross-repo runtime coupling — re-sync by hand if
Lyra updates upstream.

## Topology

| Unit | Purpose |
|---|---|
| `voicecli.network`         | Per-project bridge (ADR-055 D3) — isolates voiceCLI workers until Phase 4 consolidation |
| `voicecli-models.volume`   | Named volume for HuggingFace + voicecli model caches |
| `voicecli-nats.container`  | NATS server — port 4224 on host (ADR-055 D2 Phase 2 window). JetStream disabled (request/reply only). |
| `voicecli-stt.container`   | STT worker — subscribes to `lyra.voice.stt.request` queue group (namespace matches Lyra's ACL matrix) |
| `voicecli-tts.container`   | TTS worker — subscribes to `lyra.voice.tts.request` queue group |

Workers connect to NATS via `nats://voicecli-nats:4222` over `voicecli.network`.
The host exposes NATS on `127.0.0.1:4224` so Lyra (during Phase 3 cutover) and
debug tooling can reach it.

## Provisioning

### 1. NATS nkey seeds

Seeds live at `~/.voicecli/nkeys/voice-{stt,tts}.seed` on the host (ADR-055 D4).
They are created once by Lyra's `gen-nkeys.sh`; relocation from the old
`~/.lyra/nkeys/` path is covered by the runbook below.

### 2. Build the voicecli-auth.conf

`auth.conf` carries the voice-stt / voice-tts pubkeys for voicecli-nats to
enforce ACLs. It must live at `~/.voicecli/nkeys/voicecli-auth.conf`
(user-owned, rootless — matches ADR-055 D4). **Today this file is hand-crafted**
— a `gen-nkeys.sh` flag to emit the voicecli subset is a tracked follow-up
(see [lyra#TBD](https://github.com/Roxabi/lyra/issues); update this link when
the issue is filed).

Until that ships, extract the two voice-* blocks from Lyra's
`/etc/nats/nkeys/auth.conf` by hand:

```bash
# Starting point: Lyra's full auth.conf already contains the voice-* pubkeys
sudo cat /etc/nats/nkeys/auth.conf    # inspect the blocks you want

# Compose the voicecli subset (rootless target)
mkdir -p ~/.voicecli/nkeys
cat > ~/.voicecli/nkeys/voicecli-auth.conf <<'EOF'
authorization {
  default_permissions: {
    publish:   { deny: [">"] }
    subscribe: { deny: [">"] }
  }
  users: [
    { nkey: "U<voice-stt pubkey>", permissions: { subscribe: { allow: ["lyra.voice.stt.request"] }, publish: { allow: ["lyra.voice.stt.heartbeat"] }, allow_responses: true } }
    { nkey: "U<voice-tts pubkey>", permissions: { subscribe: { allow: ["lyra.voice.tts.request"] }, publish: { allow: ["lyra.voice.tts.heartbeat"] }, allow_responses: true } }
  ]
}
EOF
chmod 600 ~/.voicecli/nkeys/voicecli-auth.conf
```

Canonical ACLs are in Lyra's `deploy/nats/acl-matrix.json` under the
`voice-stt` and `voice-tts` identities.

### 3. Install Quadlet units

```bash
make quadlet-install
# copies deploy/quadlet/*.{network,container,volume} → ~/.config/containers/systemd/
# reloads systemd --user
```

### 4. Podman secrets

```bash
make quadlet-secrets-install
# creates: voicecli-nats-auth, voicecli-nats-stt, voicecli-nats-tts
# stops services before replace and restarts after (avoids mid-rotation mismatch)
```

### 5. Start services

```bash
systemctl --user start voicecli-nats voicecli-stt voicecli-tts
systemctl --user status voicecli-{nats,stt,tts}
```

## Deploy pipeline

**Phase 2 (now):** auto-deploy on M₁ runs via Lyra's timer
(`lyra-deploy.timer` → `lyra-deploy.service`). Lyra's `deploy.sh` iterates
over enrolled repos including voiceCLI, pulls, tests, and restarts services.
This orchestration is **temporary** — the ADR-055 D6 end state is autonomous
per-project deploys.

**Phase 3 exit criterion:** once voiceCLI is fully Quadlet-deployed and the
cutover checklist below is complete, add `voicecli-deploy.{timer,service}`
units and remove voiceCLI from Lyra's `deploy.sh` iteration. This is tracked
as a follow-up (see [lyra#TBD](https://github.com/Roxabi/lyra/issues)).

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

- [ ] `~/.voicecli/nkeys/voice-{stt,tts}.seed` exists, 0600
- [ ] `~/.voicecli/nkeys/voicecli-auth.conf` exists, 0600, contains voice-stt + voice-tts blocks
- [ ] `make quadlet-install` ran without error; units in `~/.config/containers/systemd/`
- [ ] `make quadlet-secrets-install` ran; `podman secret ls` shows voicecli-nats-{auth,stt,tts}
- [ ] `systemctl --user start voicecli-nats` succeeds; health probe clean
- [ ] `systemctl --user start voicecli-stt voicecli-tts` succeeds; containers report `Running`
- [ ] `UserNS=keep-id` maps voicecli image `appuser` UID → host UID correctly (verify with `podman exec voicecli-stt id`)
- [ ] End-to-end: Lyra hub publishes a `voice.stt.request`, receives a reply
- [ ] Disable + remove old supervisord confs: `supervisorctl stop voicecli_stt voicecli_tts && rm supervisor/conf.d/voicecli_{stt,tts}.conf`
- [ ] Remove voiceCLI from Lyra's `deploy.sh` EXTRA_REPOS once autonomous `voicecli-deploy.timer` is in place

## Phase 4 consolidation (future)

When Lyra's Quadlet NATS migrates from port 4223 → 4222 and becomes the
shared bus, voiceCLI workers flip `NATS_URL` from `nats://voicecli-nats:4222`
→ `nats://lyra-nats:4222` (requires joining `roxabi.network`), and
`voicecli-nats.container` + `voicecli-nats-auth` secret are retired. The
voice-stt / voice-tts pubkeys move into Lyra's unified `auth.conf`.

## References

- [ADR-055 — Quadlet ecosystem conventions](../../lyra/docs/architecture/adr/055-quadlet-ecosystem-conventions.mdx)
- [Lyra's QUADLET-ADOPTION.md](../../lyra/docs/QUADLET-ADOPTION.md) — project-agnostic adoption template
- [ADR-054 — Podman secrets](../../lyra/docs/architecture/adr/054-podman-secrets.mdx)
