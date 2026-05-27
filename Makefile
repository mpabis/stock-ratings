.PHONY: install test daily

install:
	python -m pip install -e . pytest

test:
	python -m pytest

daily:
	python -m stock_rating.pipeline.daily
