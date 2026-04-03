ifneq (,$(filter stt tts,$(firstword $(MAKECMDGOALS))))
  SVC_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  ifneq (,$(SVC_CMD))
    $(eval $(SVC_CMD):;@:)
  endif
endif

SUPERVISOR_HUB ?= $(HOME)/projects
SUPERVISORCTL  := $(SUPERVISOR_HUB)/scripts/supervisorctl.sh
SUPERVISOR_START := $(SUPERVISOR_HUB)/scripts/start.sh
HUB_PID        := $(SUPERVISOR_HUB)/supervisord.pid

define ensure_hub
	@if [ ! -f "$(HUB_PID)" ] || ! kill -0 $$(cat "$(HUB_PID)" 2>/dev/null) 2>/dev/null; then \
		echo "Hub supervisord not running, starting..."; \
		$(SUPERVISOR_START); \
	fi
endef

.PHONY: register tts stt install lint test

register:
	@echo "Registering voiceCLI with supervisor hub..."
	@mkdir -p "$(SUPERVISOR_HUB)/conf.d"
	@ln -sf "$(abspath supervisor/conf.d/voicecli_tts.conf)" "$(SUPERVISOR_HUB)/conf.d/voicecli_tts.conf"
	@ln -sf "$(abspath supervisor/conf.d/voicecli_stt.conf)" "$(SUPERVISOR_HUB)/conf.d/voicecli_stt.conf"
	@mkdir -p "$(HOME)/.local/state/voicecli/logs"
	@if [ -S "$(SUPERVISOR_HUB)/supervisor.sock" ]; then \
		$(SUPERVISORCTL) reread && $(SUPERVISORCTL) update; \
	fi
	@echo "Done. Run 'make tts' or 'make stt' to start services."

tts:
	$(ensure_hub)
ifeq ($(SVC_CMD),reload)
	@$(SUPERVISORCTL) restart voicecli_tts
else ifeq ($(SVC_CMD),logs)
	@$(SUPERVISORCTL) tail -f voicecli_tts
else ifeq ($(SVC_CMD),errlogs)
	@$(SUPERVISORCTL) tail -f voicecli_tts stderr
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
	@$(SUPERVISORCTL) tail -f voicecli_stt
else ifeq ($(SVC_CMD),errlogs)
	@$(SUPERVISORCTL) tail -f voicecli_stt stderr
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
