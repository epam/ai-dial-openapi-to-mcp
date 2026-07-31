SRC_DIRS = src/dial_openapi_to_mcp tests
MYPY_DIRS = src/dial_openapi_to_mcp
FILES ?= $(SRC_DIRS)
POETRY ?= poetry
PYTHON ?= python3

# Any non-empty CI value (even 'false' or '0') means that CI is enabled
CI ?=

.PHONY: init_venv install install_dev format lint mypy pyright test test_cov test_cov_all \
	black black_check isort isort_check autoflake autoflake_check flake8

init_venv:
	$(if $(CI),,$(POETRY) env use $(PYTHON))

install: init_venv
	$(POETRY) install

install_dev: init_venv
	$(POETRY) install --with dev

format: install_dev
	$(POETRY) run autoflake $(FILES)
	$(POETRY) run black $(FILES)
	$(POETRY) run isort $(FILES)

lint: install_dev
	$(POETRY) check --lock
	$(POETRY) run flake8 $(SRC_DIRS)
	$(POETRY) run black $(SRC_DIRS) --check
	$(POETRY) run isort $(SRC_DIRS) --check-only --diff
	$(POETRY) run autoflake $(SRC_DIRS) --check
	$(POETRY) run mypy --show-error-codes $(MYPY_DIRS)
	$(POETRY) run pyright $(MYPY_DIRS)

mypy: install_dev
	$(POETRY) run mypy --show-error-codes $(MYPY_DIRS)

pyright: install_dev
	$(POETRY) run pyright $(MYPY_DIRS)

black: install_dev
	$(POETRY) run black $(FILES)

black_check: install_dev
	$(POETRY) run black $(FILES) --check

isort: install_dev
	$(POETRY) run isort $(FILES)

isort_check: install_dev
	$(POETRY) run isort $(FILES) --check-only --diff

autoflake: install_dev
	$(POETRY) run autoflake $(FILES)

autoflake_check: install_dev
	$(POETRY) run autoflake $(FILES) --check

flake8: install_dev
	$(POETRY) run flake8 $(FILES)

test: install_dev
	$(POETRY) run pytest tests -m "not integration" $(ARGS)

test_cov: install_dev
	$(POETRY) run pytest tests -m "not integration" --cov=src/dial_openapi_to_mcp --cov-report=term-missing --cov-fail-under=60 $(ARGS)

# Includes the real-server integration suite; slower, but the representative
# baseline for total coverage since much of server.py is only exercised end-to-end.
test_cov_all: install_dev
	$(POETRY) run pytest tests --cov=src/dial_openapi_to_mcp --cov-report=term-missing --cov-fail-under=70 $(ARGS)
