FROM docker.io/library/python:3.12-slim

WORKDIR /srv/open-models
COPY web ./web
COPY catalog.yaml ./
COPY configs ./configs
COPY scripts ./scripts

RUN pip install --no-cache-dir -r web/requirements.txt \
    && rm -rf web/tests web/pytest.ini web/.pytest_cache

WORKDIR /srv/open-models/web
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
