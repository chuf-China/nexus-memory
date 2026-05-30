.PHONY: install test demo benchmark publish clean

install:
	pip install -e .

test:
	python -m pytest tests/ -v

demo:
	python demo.py

benchmark:
	python demo.py --benchmark

publish:
	pip install build twine
	python -m build
	twine check dist/*
	twine upload dist/*

clean:
	rm -rf dist/ build/ *.egg-info __pycache__ /tmp/nexus_demo.db /tmp/nexus_benchmark.db
