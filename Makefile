.PHONY: help install sync test lint format can-release

help:
	@echo "Tempo — local-first Ironman coaching agent"
	@echo ""
	@echo "Setup:"
	@echo "  make install        Install project + dev deps via uv"
	@echo ""
	@echo "Running:"
	@echo "  make sync           Pull latest intervals data (coach sync)"
	@echo "  make status         Print CTL/ATL/TSB summary (coach status)"
	@echo ""
	@echo "Quality gates:"
	@echo "  make test           Run pytest"
	@echo "  make lint           Ruff + pyright"
	@echo "  make format         Ruff format + fix"
	@echo "  make can-release    lint + test — gate for committing"

install:
	uv sync --all-groups

sync:
	uv run coach sync

status:
	uv run coach status

test:
	uv run pytest

lint:
	uv run ruff check
	uv run pyright src/tempo

format:
	uv run ruff format
	uv run ruff check --fix

can-release: lint test
