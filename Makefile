# Finance Tracker — common operations
# Set RAW once to your Drive data home (Windows Git Bash example below).
RAW ?= $(HOME)/Google Drive/Personal/Datasachi/Finance/raw/wealthsimple
MONTH ?= $(shell date +%Y-%m)
export PYTHONPATH := src

install:
	pip install -r requirements.txt

test:
	python -m pytest tests/ -q

ingest:
	python -m tracker.pipeline ingest "$(RAW)" $(MONTH)

report:
	streamlit run app/main.py

rebuild:  ## wipe warehouse and re-ingest every month folder found in RAW
	rm -f data/warehouse.duckdb
	for d in "$(RAW)"/*/*/ ; do \
	  m=$$(basename $$d); \
	  python -m tracker.pipeline ingest "$(RAW)" $$m ; \
	done
