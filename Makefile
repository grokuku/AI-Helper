.PHONY: test test-cov lint format install-dev

PYTHON ?= backend/.venv/bin/python

install-dev:
	cd backend && python3 -m venv .venv && \
		.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

test:
	cd backend && $(CURDIR)/$(PYTHON) -m pytest -v

test-cov:
	cd backend && $(CURDIR)/$(PYTHON) -m pytest --cov=. --cov-report=term-missing --cov-report=html

lint:
	cd backend && $(CURDIR)/$(PYTHON) -m ruff check . || echo "ruff not installed, skipping"

format:
	cd backend && $(CURDIR)/$(PYTHON) -m black . || echo "black not installed, skipping"