.PHONY: test test-cov lint format install-dev lock

# Même emplacement de venv que run.sh (AI-Helper/venv).
PYTHON ?= venv/bin/python

install-dev:
	cd backend && python3 -m venv .venv && \
		.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

test:
	cd backend && $(CURDIR)/$(PYTHON) -m pytest -v

test-cov:
	cd backend && $(CURDIR)/$(PYTHON) -m pytest --cov=. --cov-report=term-missing --cov-report=html

lint:
	cd backend && $(CURDIR)/$(PYTHON) -m ruff check .

format:
	cd backend && $(CURDIR)/$(PYTHON) -m black . || echo "black not installed, skipping"

# Régénère backend/requirements.lock.txt depuis requirements.txt (venv vierge).
lock:
	@tmpdir=$$(mktemp -d); \
		python3 -m venv $$tmpdir/venv && \
		$$tmpdir/venv/bin/pip install -q -r backend/requirements.txt && \
		$$tmpdir/venv/bin/pip freeze > backend/requirements.lock.txt && \
		rm -rf $$tmpdir; \
		echo "✅ backend/requirements.lock.txt régénéré"