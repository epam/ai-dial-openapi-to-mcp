SRC_DIRS = src/dial_openapi_to_mcp tests
MYPY_DIRS = src/dial_openapi_to_mcp
FILES ?= $(SRC_DIRS)
PYTHON ?= python

.PHONY: install install_dev format lint mypy test test_cov black black_check isort isort_check autoflake autoflake_check flake8

install:
	uv sync

install_dev:
	uv sync --all-extras

format: install_dev
	uv run autoflake $(FILES)
	uv run black $(FILES)
	uv run isort $(FILES)

lint: install_dev
	uv lock --check
	uv run flake8 $(SRC_DIRS)
	uv run black $(SRC_DIRS) --check
	uv run isort $(SRC_DIRS) --check-only --diff
	uv run autoflake $(SRC_DIRS) --check
	uv run mypy --show-error-codes $(MYPY_DIRS)

mypy: install_dev
	uv run mypy --show-error-codes $(MYPY_DIRS)

black: install_dev
	uv run black $(FILES)

black_check: install_dev
	uv run black $(FILES) --check

isort: install_dev
	uv run isort $(FILES)

isort_check: install_dev
	uv run isort $(FILES) --check-only --diff

autoflake: install_dev
	uv run autoflake $(FILES)

autoflake_check: install_dev
	uv run autoflake $(FILES) --check

flake8: install_dev
	uv run flake8 $(FILES)

test: install_dev
	uv run pytest tests -m "not integration" $(ARGS)

test_cov: install_dev
	uv run pytest tests -m "not integration" --cov=src/dial_openapi_to_mcp --cov-report=term-missing $(ARGS)
