# One reproducible entry point per CI gate (`P0-10`).
#
# This file exists for the reason `ci.yml` already gives for keeping the ruff
# rule set in `pyproject.toml`: "a gate whose rule set is spelled in a workflow
# file is a gate nobody can reproduce locally". The same argument applies to the
# gates themselves. Eight jobs run on every pull request and, until this file,
# reproducing them meant reading the workflow and retyping eight commands --
# which is how a contributor ends up running a seventh of the suite, pushing,
# and learning the rest from a red check.
#
# Every recipe below is a command CI actually runs. Nothing here is invented,
# nothing is kinder than the pipeline, and `tests/unit/test_gate_parity.py`
# fails if the two drift apart. A Makefile that quietly runs less than CI is
# worse than no Makefile, because it is trusted.
#
# What is deliberately absent mirrors the workflow's own absences: no `format`
# target (`ruff format` would rewrite 53 of 134 files and belongs in its own
# change), no `docker`/`build` target (nothing is packaged yet -- the gateway,
# PDP client, critic bank and broker do not exist), and no `integration` or
# `replay` target for the same reason. `docs/review/2026-09-19-round-5-repository-gap-analysis.md`
# records each with the component that unblocks it.

PYTHON ?= python3
PYTEST := $(PYTHON) -m pytest

# Governance section 3, stage 2. Mirrors `MIN_COVERAGE` in `.github/workflows/ci.yml`;
# the floor also lives in `pyproject.toml` so a bare `pytest --cov` enforces it.
MIN_COVERAGE ?= 90

.DEFAULT_GOAL := help
.PHONY: help install gate gate-all lint typecheck test coverage resolver-coverage \
        mutation schema magic-values hard-rules docs security secrets-allowlist \
        schemas-current conventions clean

help: ## Show this help.
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install the package and its dev extra, as every CI job does.
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

## --- The eight gates, in the order CI runs them ----------------------------

lint: ## CI job `lint`: ruff and mypy --strict, both clean at zero.
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m mypy

typecheck: ## The mypy half alone, for a faster inner loop.
	$(PYTHON) -m mypy

test: ## The full suite without coverage instrumentation.
	$(PYTEST)

coverage: ## CI job `tests`: the suite with the trusted-computing-base floor.
	$(PYTEST) --cov=neuroharness --cov-branch \
		--cov-fail-under=$(MIN_COVERAGE) --cov-report=term-missing

resolver-coverage: ## Governance section 3, stage 2: the resolver at 100% branches.
	$(PYTEST) --cov=neuroharness.resolve --cov-branch \
		--cov-fail-under=100 --cov-report=term-missing

mutation: ## CI job `mutation-fixtures`: every active fixture kills its gate.
	$(PYTEST) -m mutation
	$(PYTEST) tests/unit/test_mutation_fixtures.py

schema: ## CI job `schema-conformance`: models and schemas agree both ways.
	$(PYTEST) tests/unit/test_schema_conformance.py tests/unit/test_schema_versions.py
	$(PYTEST) tests/unit/test_schema_patterns_are_generated.py

schemas-current: ## Regenerate-and-compare: the published patterns match Python.
	$(PYTHON) tools/render_schema_patterns.py --check

conventions: ## CI job `lint`: gate parity and the property-suite markers.
	$(PYTEST) tests/unit/test_gate_parity.py tests/unit/test_suite_conventions.py

magic-values: ## CI job `no-magic-values`: governing values are named constants.
	$(PYTEST) tests/unit/test_no_magic_values.py tests/unit/test_constitutional_constants.py

hard-rules: ## CI job `hard-rules-have-fixtures`: the gap register may only shrink.
	$(PYTEST) tests/unit/test_hard_rule_fixtures.py

docs: ## CI job `docs`: spec IDs, ADR index, scenarios and the skills.
	$(PYTEST) tests/unit/test_spec_id_consistency.py \
		tests/unit/test_adr_index.py \
		tests/unit/test_scenario_coverage.py \
		tests/unit/test_skills_are_current.py

secrets-allowlist: ## The gitleaks allowlist has not been widened. No binary needed.
	$(PYTEST) tests/unit/test_secret_scan_allowlist.py

security: secrets-allowlist ## CI job `security`: secrets and dependency advisories.
	@# Each tool's own exit status is the recipe's. An earlier version wrote
	@# `command -v gitleaks && gitleaks detect || echo "not installed"`, which
	@# reports success when gitleaks runs and *finds a secret*: the failing
	@# scan falls to the `||` branch and the echo exits 0. A local secret scan
	@# that goes green on a finding is worse than none, so absence is a
	@# refusal with the remedy, and presence lets the tool speak for itself.
	@command -v gitleaks >/dev/null 2>&1 || { \
		echo "gitleaks is not installed. CI pins 8.18.4; see .github/workflows/ci.yml"; \
		exit 1; }
	gitleaks detect --source . --redact --no-banner --verbose
	@$(PYTHON) -m pip_audit --version >/dev/null 2>&1 || { \
		echo "pip-audit is not installed. Run: $(PYTHON) -m pip install pip-audit"; \
		exit 1; }
	$(PYTHON) -m pip_audit --strict --progress-spinner=off .

gate: lint conventions coverage resolver-coverage mutation schema schemas-current \
      magic-values hard-rules docs secrets-allowlist ## Every gate needing no external binary.
	@echo 'Passed every gate that needs no external binary. `make gate-all` adds the'
	@echo 'gitleaks and pip-audit scans, which CI also blocks on.'

gate-all: gate security ## Every blocking CI job, including the external scans.
	@echo 'Passed every blocking gate, including the security scans.'

clean: ## Remove caches and coverage artefacts.
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov coverage.xml
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
