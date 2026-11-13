PY ?= python3

.PHONY: install lint test check run docker

install:
	$(PY) -m pip install -e '.[dev]'

lint:
	ruff check .
	ruff format --check .
	mypy
	$(PY) scripts/check.py

test:
	$(PY) -m pytest -q

check: lint test

run:
	APP_ENV=dev FLAGS_PATH=deploy/flags.json PYTHONPATH=src $(PY) -m livesvc

docker:
	docker build -t livesvc:dev .
