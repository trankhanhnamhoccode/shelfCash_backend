.PHONY: install install-kaggle run test fake-data smoke

install:
	python -m pip install -r requirements.txt

install-kaggle:
	python -m pip install -r requirements-kaggle.txt

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000

test:
	pytest -q

fake-data:
	python scripts/create_fake_excel.py

smoke: fake-data
	python scripts/smoke_test_api.py
