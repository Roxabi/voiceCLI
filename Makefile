SHELL := /bin/bash -o pipefail

QUADLET_DIR        ?= $(HOME)/.config/containers/systemd
VOICECLI_NKEYS_DIR ?= $(HOME)/.roxabi/voicecli/nkeys
DEPLOY_HOST        ?= roxabituwer
VOICECLI_SVCS      := voicecli-tts voicecli-stt
TTS_IMAGE          := ghcr.io/roxabi/voicecli-tts:staging
STT_IMAGE          := ghcr.io/roxabi/voicecli-stt:staging

.PHONY: register tts stt install lint test quadlet-install quadlet-secrets-install deploy

register:
	@echo "supervisor registration removed — use Quadlet: systemctl --user start voicecli-tts"

tts:
	@echo "Use: systemctl --user start voicecli-tts"

stt:
	@echo "Use: systemctl --user start voicecli-stt"

install:
	uv sync

lint:
	uv run ruff check .

test:
	uv run pytest

# ── Remote deploy (pull latest image + restart on DEPLOY_HOST) ───────────────

deploy:  ## pull latest staging images on $(DEPLOY_HOST) and restart voicecli-tts + voicecli-stt
	@[ -n "$(DEPLOY_HOST)" ] || { echo "Error: DEPLOY_HOST not set. Pass DEPLOY_HOST=<host> or set it in your environment."; exit 1; }
	@echo "Pulling images on $(DEPLOY_HOST)..."
	@ssh $(DEPLOY_HOST) "podman pull $(TTS_IMAGE) && podman pull $(STT_IMAGE)"
	@echo "Restarting $(VOICECLI_SVCS)..."
	@ssh $(DEPLOY_HOST) "systemctl --user restart $(VOICECLI_SVCS)"
	@ssh $(DEPLOY_HOST) "systemctl --user status $(VOICECLI_SVCS) --no-pager | grep -E 'voicecli|Active:'"

# ── Quadlet (ADR-055) ─────────────────────────────────────────────────────────

quadlet-install:  ## install Quadlet units to $(QUADLET_DIR) + reload
	@mkdir -p "$(QUADLET_DIR)"
	@mkdir -p "$(HOME)/.cache/huggingface" "$(HOME)/.cache/voicecli"
	@rm -f "$(QUADLET_DIR)"/voicecli*.{network,container}
	@for f in deploy/quadlet/*.container; do \
		cp "$$f" "$(QUADLET_DIR)/"; \
	done
	@systemctl --user daemon-reload
	@echo "Quadlet units installed."
	@echo "Next: run 'make quadlet-secrets-install' to (re)create Podman secrets."

quadlet-secrets-install:  ## (re)create Podman secrets from $(VOICECLI_NKEYS_DIR)
	@test -d "$(VOICECLI_NKEYS_DIR)" || { echo "ERROR: $(VOICECLI_NKEYS_DIR) not found. Run the seed-relocation runbook first (docs/QUADLET-DEPLOYMENT.md)."; exit 1; }
	@# Single shell block with EXIT trap — always attempts restart, even on failure.
	@# Prevents services being left stopped with a partial secret rotation.
	@set -e; \
	 SVCS="voicecli-stt voicecli-tts"; \
	 trap 'systemctl --user start $$SVCS 2>/dev/null || echo "WARNING: services not restarted automatically. Start manually: systemctl --user start $$SVCS"' EXIT; \
	 systemctl --user stop $$SVCS 2>/dev/null || true; \
	 podman secret create --replace voicecli-nats-stt  "$(VOICECLI_NKEYS_DIR)/voice-stt.seed"; \
	 podman secret create --replace voicecli-nats-tts  "$(VOICECLI_NKEYS_DIR)/voice-tts.seed"
	@echo "Podman secrets installed. Verify: podman secret ls"
