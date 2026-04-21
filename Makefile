SUPERVISOR_HUB ?= $(HOME)/projects
HUB_SERVICES   := tts stt
-include $(SUPERVISOR_HUB)/hub.mk

.PHONY: register tts stt install lint test

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
