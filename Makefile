PYTHON_SOURCES = src
JS_SOURCES = resources

# ============================================================================
# Defaults
# ============================================================================

.PHONY: default
default: check

.PHONY: check
check: lint test

.PHONY: check-all
check-all: lint test-all coverage

.PHONY: fmt
fmt: ruff-fix ruff-fmt biome-fix

# ============================================================================
# Install / Dependency management
# ============================================================================

.PHONY: install
install:
	uv sync --all-packages --all-extras
	cd $(INFRA_DIR) && uv sync --frozen
	bun install

.PHONY: install-js
install-js:
	bun install

.PHONY: update
update:
	uv lock -U

.PHONY: lock
lock:
	uv lock

# ============================================================================
# Linting — Python
# ============================================================================

.PHONY: ruff
ruff:
	uv run ruff check $(PYTHON_SOURCES)

.PHONY: ruff-fix
ruff-fix:
	uv run ruff check --fix $(PYTHON_SOURCES)

.PHONY: ruff-fmt
ruff-fmt:
	uv run ruff format $(PYTHON_SOURCES)

.PHONY: ruff-fmt-check
ruff-fmt-check:
	uv run ruff format --check --diff $(PYTHON_SOURCES)

.PHONY: mypy
mypy:
	uv run mypy $(PYTHON_SOURCES)

.PHONY: pyright
pyright:
	uv run pyright

.PHONY: type-check
type-check: mypy pyright

.PHONY: slotscheck
slotscheck:
	uv run slotscheck -m cert_ra

.PHONY: codespell
codespell:
	uv run codespell

# ============================================================================
# Linting — JavaScript / TypeScript
# ============================================================================

.PHONY: biome
biome:
	bunx biome check $(JS_SOURCES)

.PHONY: biome-fix
biome-fix:
	bunx biome check --write $(JS_SOURCES)

# ============================================================================
# Linting — Aggregate
# ============================================================================

# Runs the prek-managed hooks: ruff, ruff-fmt, mypy, codespell, biome, uv lock
.PHONY: pre-commit
pre-commit:
	uv run prek run --all-files

.PHONY: lint-py
lint-py: ruff ruff-fmt-check type-check slotscheck codespell canonical-helpers

.PHONY: lint-js
lint-js: biome

.PHONY: canonical-helpers
canonical-helpers:
	$(call header,"Verifying single-lookup-path invariant for OIDC SSO state tables")
	uv run python tools/lint/canonical_helper_check.py

# Full lint pass — pre-commit covers ruff/fmt/mypy/codespell/biome,
# so we only add the extra Python checks that aren't in the hook config.
.PHONY: lint
lint: pre-commit pyright slotscheck canonical-helpers

# ============================================================================
# Testing
# ============================================================================

.PHONY: test
test:
	uv run pytest --no-cov -n auto --quiet

.PHONY: test-all
test-all:
	uv run pytest --no-cov --quiet

.PHONY: pytest
pytest:
	uv run pytest

.PHONY: coverage
coverage:
	uv run pytest --cov-report=html --quiet

# ============================================================================
# Build
# ============================================================================

.PHONY: build
build:
	SOURCE_DATE_EPOCH=315532800 uv build 2>&1 | tee build.log
	sed -n 's/.*cert_ra-\([^ -]*\)-py3-none-any\.whl.*/\1/p' build.log > .version

.PHONY: build-js
build-js:
	bun run build

# ============================================================================
# Docker
# ============================================================================

DOCKER    = docker compose -f docker/docker-compose.yml
DOCKER_UP = $(DOCKER) up --build
INFRA_SERVICES   = db migrator temporal temporal-ui
API_SERVICES     = db migrator temporal temporal-ui app
WORKER_SERVICES  = db migrator temporal temporal-ui metrics-worker alerts-worker

.PHONY: docker-infra
docker-infra:
	$(DOCKER_UP) -d $(INFRA_SERVICES)

.PHONY: docker-infra-live
docker-infra-live:
	$(DOCKER_UP) $(INFRA_SERVICES)

.PHONY: docker-api
docker-api:
	$(DOCKER_UP) -d $(API_SERVICES)

.PHONY: docker-api-live
docker-api-live:
	$(DOCKER_UP) $(API_SERVICES)

.PHONY: docker-workers
docker-workers:
	$(DOCKER_UP) -d $(WORKER_SERVICES)

.PHONY: docker-workers-live
docker-workers-live:
	$(DOCKER_UP) $(WORKER_SERVICES)

.PHONY: docker-up
docker-up:
	$(DOCKER_UP) -d

.PHONY: docker-live
docker-live:
	$(DOCKER_UP)

.PHONY: docker-down
docker-down:
	$(DOCKER) down

# ============================================================================
# Dev servers
# ============================================================================

.PHONY: dev-js
dev-js:
	bun run dev

.PHONY: fill-manual
fill-manual:
	uv run python scripts/seed_manual_metrics.py

.PHONY: fill-manual-tokens
fill-manual-tokens:
	uv run python scripts/seed_manual_metrics.py scripts/seed_manual_metrics_tokens.csv

.PHONY: fill-manual-governance
fill-manual-governance:
	uv run python scripts/seed_manual_metrics_governance.py

.PHONY: fill-dummy
fill-dummy:
	uv run python scripts/fill_dummy_data.py

.PHONY: alerts-worker
alerts-worker:
	uv run python -m cert_ra.alerts.worker

# ============================================================================
# Requirements export
# ============================================================================

.PHONY: requirements
requirements:
	uv export --color never --no-editable --no-dev --no-emit-project --no-emit-workspace | grep -v './' > requirements.txt

.PHONY: requirements-all
requirements-all: requirements

# ============================================================================
# Security / Audit
# ============================================================================

.PHONY: sec-check
sec-check:
	@uv run --all-groups --with pip-audit pip-audit -l --desc --format markdown --ignore-vuln CVE-2025-53000 2>/dev/null

# List outdated run time packages (excluding installation tools)
.PHONY: list-outdated
list-outdated:
	@uv pip list --outdated \
		--exclude pip --exclude wheel --exclude setuptools

# ============================================================================
# Plugins
# ============================================================================

.PHONY: update-installer-plugin
update-installer-plugin: build
	cp pyproject.toml uv.lock .version "$$(find dist/ -type f -name "*$$(cat .version)*.whl" | head -n 1)" influxdb3_plugins/plugin_dependencies_installer/
	# add ".py" to all files influxdb3_plugins/plugin_dependencies_installer besides .gitignore and __init__.py
	find influxdb3_plugins/plugin_dependencies_installer/ -type f ! -name '*.py' ! -name '.gitignore' ! -name '__init__.py' -exec mv {} {}.py \;

# ============================================================================
# Infrastructure (CDK) — see infra/
# ============================================================================

INFRA_DIR = infra
CDK_ENV ?= staging

.PHONY: infra-install
infra-install:
	cd $(INFRA_DIR) && uv sync --frozen

.PHONY: infra-synth
infra-synth:
	cd $(INFRA_DIR) && CDK_ENV=$(CDK_ENV) bunx cdk synth

.PHONY: infra-diff
infra-diff:
	cd $(INFRA_DIR) && CDK_ENV=$(CDK_ENV) bunx cdk diff

.PHONY: infra-deploy
infra-deploy:
	cd $(INFRA_DIR) && CDK_ENV=$(CDK_ENV) bunx cdk deploy --all

.PHONY: infra-test
infra-test:
	cd $(INFRA_DIR) && uv run pytest

.PHONY: infra-lint
infra-lint:
	cd $(INFRA_DIR) && uv run ruff check . && uv run ruff format --check --diff . && uv run mypy . && uv run pyright

.PHONY: infra-fmt
infra-fmt:
	cd $(INFRA_DIR) && uv run ruff check --fix . && uv run ruff format .

# ============================================================================
# Clean
# ============================================================================

.PHONY: clean
clean:
	rm -rf .pytest_cache
	rm -rf .mypy_cache
	rm -rf .coverage
	rm -f requirements*.txt
	find -type d -name '__pycache__' | xargs --no-run-if-empty rm -rf
	find -type d -name '*.egg-info' | xargs --no-run-if-empty rm -rf
	find -type d -name 'cdk.out' | xargs --no-run-if-empty rm -rf

.PHONY: cleanall
cleanall: clean
	rm -rf .venv dist .eggs node_modules
