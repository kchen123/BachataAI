.PHONY: venv install dev migrate

venv:
	python -m venv venv

install:
	venv/Scripts/pip install -r requirements.txt

dev:
	venv/Scripts/python -m core.app

migrate:
	psql -f migrations/001_create_tables.sql
