ifneq (,$(filter stt tts,$(firstword $(MAKECMDGOALS))))
  SVC_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  ifneq (,$(SVC_CMD))
    $(eval $(SVC_CMD):;@:)
  endif
endif

SUPERVISORCTL := $(HOME)/lyra-stack/scripts/supervisorctl.sh
SUPERVISOR_START := $(HOME)/lyra-stack/scripts/start.sh
HUB_DIR := $(HOME)/lyra-stack
HUB_PID := $(HUB_DIR)/supervisord.pid

define ensure_hub
	@if [ ! -d "$(HUB_DIR)" ]; then \
		echo "Error: ~/lyra-stack not found. Set up lyra-stack first."; \
		exit 1; \
	fi
	@if [ ! -f "$(HUB_PID)" ] || ! kill -0 $$(cat "$(HUB_PID)" 2>/dev/null) 2>/dev/null; then \
		echo "Hub supervisord not running, starting..."; \
		$(SUPERVISOR_START); \
	fi
endef

.PHONY: register tts stt install lint test

register:
	@echo "Registering voiceCLI with global supervisor..."
	@if [ ! -d "$(HUB_DIR)" ]; then \
		echo "Error: ~/lyra-stack not found."; exit 1; \
	fi
	@ln -sf "$(abspath supervisor/conf.d/voicecli_tts.conf)" "$(HUB_DIR)/conf.d/voicecli_tts.conf"
	@ln -sf "$(abspath supervisor/conf.d/voicecli_stt.conf)" "$(HUB_DIR)/conf.d/voicecli_stt.conf"
	@mkdir -p supervisor/logs
	@if [ -S "$(HUB_DIR)/supervisor.sock" ]; then \
		$(SUPERVISORCTL) reread && $(SUPERVISORCTL) update; \
	fi
	@echo "Done. Run 'make tts' or 'make stt' to start services."

tts:
	$(ensure_hub)
ifeq ($(SVC_CMD),reload)
	@$(SUPERVISORCTL) restart voicecli_tts
else ifeq ($(SVC_CMD),logs)
	@tail -f $(HOME)/projects/voiceCLI/supervisor/logs/voicecli_tts.log
else ifeq ($(SVC_CMD),errlogs)
	@tail -f $(HOME)/projects/voiceCLI/supervisor/logs/voicecli_tts_error.log
else ifeq ($(SVC_CMD),stop)
	@$(SUPERVISORCTL) stop voicecli_tts
else ifeq ($(SVC_CMD),start)
	@$(SUPERVISORCTL) start voicecli_tts
else ifeq ($(SVC_CMD),status)
	@$(SUPERVISORCTL) status voicecli_tts
else
	@$(SUPERVISORCTL) start voicecli_tts
endif

stt:
	$(ensure_hub)
ifeq ($(SVC_CMD),reload)
	@$(SUPERVISORCTL) restart voicecli_stt
else ifeq ($(SVC_CMD),logs)
	@tail -f $(HOME)/projects/voiceCLI/supervisor/logs/voicecli_stt.log
else ifeq ($(SVC_CMD),errlogs)
	@tail -f $(HOME)/projects/voiceCLI/supervisor/logs/voicecli_stt_error.log
else ifeq ($(SVC_CMD),stop)
	@$(SUPERVISORCTL) stop voicecli_stt
else ifeq ($(SVC_CMD),start)
	@$(SUPERVISORCTL) start voicecli_stt
else ifeq ($(SVC_CMD),status)
	@$(SUPERVISORCTL) status voicecli_stt
else
	@$(SUPERVISORCTL) start voicecli_stt
endif

install:
	uv sync

lint:
	uv run ruff check .

test:
	uv run pytest
