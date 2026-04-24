SUPERVISOR_HUB ?= $(HOME)/projects
HUB_SERVICES   := tts stt
-include $(SUPERVISOR_HUB)/hub.mk

QUADLET_DIR        ?= $(HOME)/.config/containers/systemd
VOICECLI_NKEYS_DIR ?= $(HOME)/.voicecli/nkeys
NATS_AUTH_CONF     ?= /etc/nats/nkeys/voicecli-auth.conf

.PHONY: register tts stt install lint test quadlet-install quadlet-secrets-install

register:
	@echo "Registering voiceCLI with supervisor hub..."
	@$(HUB_GEN_MK) voicecli "$(abspath .)" tts stt
	$(call hub-link-conf,voicecli_tts,supervisor/conf.d/voicecli_tts.conf)
	$(call hub-link-conf,voicecli_stt,supervisor/conf.d/voicecli_stt.conf)
	@mkdir -p "$(HOME)/.local/state/voicecli/logs"
	$(hub_reread)
	@echo "Done. Run 'make tts' or 'make stt' to start services."

tts:
	$(ensure_hub)
	@$(HUB_SVC) voicecli_tts $(SVC_CMD)

stt:
	$(ensure_hub)
	@$(HUB_SVC) voicecli_stt $(SVC_CMD)

install:
	uv sync

lint:
	uv run ruff check .

test:
	uv run pytest

# ── Quadlet (ADR-055) ─────────────────────────────────────────────────────────

quadlet-install:  ## install Quadlet units to $(QUADLET_DIR) + reload
	@mkdir -p "$(QUADLET_DIR)"
	@rm -f "$(QUADLET_DIR)"/voicecli*.{network,volume,container}
	@cp deploy/quadlet/voicecli.network              "$(QUADLET_DIR)/voicecli.network"
	@cp deploy/quadlet/voicecli-data.volume          "$(QUADLET_DIR)/voicecli-data.volume"
	@cp deploy/quadlet/voicecli-models.volume        "$(QUADLET_DIR)/voicecli-models.volume"
	@cp deploy/quadlet/voicecli-nats.container       "$(QUADLET_DIR)/voicecli-nats.container"
	@cp deploy/quadlet/voicecli-stt.container        "$(QUADLET_DIR)/voicecli-stt.container"
	@cp deploy/quadlet/voicecli-tts.container        "$(QUADLET_DIR)/voicecli-tts.container"
	@systemctl --user daemon-reload
	@echo "Quadlet units installed."
	@echo "Next: run 'make quadlet-secrets-install' to (re)create Podman secrets."

quadlet-secrets-install:  ## (re)create Podman secrets from $(VOICECLI_NKEYS_DIR) + $(NATS_AUTH_CONF)
	@test -d "$(VOICECLI_NKEYS_DIR)" || { echo "ERROR: $(VOICECLI_NKEYS_DIR) not found. Run the seed-relocation runbook first (docs/DEPLOYMENT-quadlet.md)."; exit 1; }
	@test -f "$(NATS_AUTH_CONF)"     || { echo "ERROR: $(NATS_AUTH_CONF) not found. See docs/DEPLOYMENT-quadlet.md §Provisioning."; exit 1; }
	@podman secret create --replace voicecli-nats-auth "$(NATS_AUTH_CONF)"
	@podman secret create --replace voicecli-nats-stt  "$(VOICECLI_NKEYS_DIR)/voice-stt.seed"
	@podman secret create --replace voicecli-nats-tts  "$(VOICECLI_NKEYS_DIR)/voice-tts.seed"
	@echo "Podman secrets installed. Verify: podman secret ls"
