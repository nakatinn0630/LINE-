.PHONY: help sync test lint check deploy install-agent restart stop start logs status rollback genkey tunnel

APP_LABEL := com.line-personal-ai.agent
TARGET    := gui/$(shell id -u)/$(APP_LABEL)
LOG_DIR   := $(HOME)/Library/Logs/line-personal-ai

help:
	@echo "make sync           install/update dependencies"
	@echo "make test           run the test suite"
	@echo "make lint           run ruff"
	@echo "make check          report which Keychain secrets resolve"
	@echo "make genkey         print a fresh master key for the Keychain"
	@echo "make install-agent  render and load the launchd agent (once per machine)"
	@echo "make deploy         sync -> test -> restart agent -> verify through the tunnel"
	@echo "make rollback REF=<git-ref>   redeploy an earlier commit"
	@echo "make restart|start|stop|status|logs   manage the running agent"

sync:
	uv sync --extra dev

test:
	uv run pytest -q

lint:
	uv run ruff check .

check:
	uv run python -m app.tools check

genkey:
	@uv run python -m app.tools genkey

install-agent:
	./scripts/install-agent.sh

deploy:
	./scripts/deploy.sh

rollback:
	@test -n "$(REF)" || (echo "usage: make rollback REF=<git-ref>" >&2; exit 2)
	./scripts/rollback.sh "$(REF)"

restart:
	launchctl kickstart -k $(TARGET)

start:
	launchctl kickstart $(TARGET)

stop:
	launchctl bootout $(TARGET)

status:
	@launchctl print $(TARGET) | sed -n '1,20p'

logs:
	tail -f $(LOG_DIR)/agent.log

tunnel:
	cloudflared tunnel run line-personal-ai
