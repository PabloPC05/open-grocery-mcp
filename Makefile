.PHONY: install install-browser test lint run run-writes http

install:
	python -m pip install -e ".[dev]"

install-browser:
	python -m pip install -e ".[dev,browser]"

test:
	pytest
	python -m compileall -q src tests

lint:
	ruff check .

run:
	open-grocery-mcp

run-writes:
	open-grocery-mcp --allow-retailer-writes

http:
	open-grocery-mcp --transport streamable-http --host 127.0.0.1 --port 8000
