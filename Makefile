.PHONY: install test lint run http

install:
	python -m pip install -e ".[dev]"

test:
	pytest
	python -m compileall -q src tests

lint:
	ruff check .

run:
	open-grocery-mcp

http:
	open-grocery-mcp --transport streamable-http --host 127.0.0.1 --port 8000
