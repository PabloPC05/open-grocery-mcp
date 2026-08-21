.PHONY: install install-browser test lint run run-writes run-browser-orders capture-gadis capture-froiz http

install:
	python -m pip install -e ".[dev]"

install-browser:
	python -m pip install -e ".[dev,browser]"

capture-gadis:
	python tools/capture_http_local.py --store gadis --output local-captures/gadis.json

capture-froiz:
	python tools/capture_http_local.py --store froiz --output local-captures/froiz.json

test:
	pytest
	python -m compileall -q src tests tools

lint:
	ruff check .

run:
	open-grocery-mcp

run-writes:
	open-grocery-mcp --allow-retailer-writes

run-browser-orders:
	open-grocery-mcp --allow-retailer-writes --allow-order-submission --allow-browser-order-submission

http:
	open-grocery-mcp --transport streamable-http --host 127.0.0.1 --port 8000
