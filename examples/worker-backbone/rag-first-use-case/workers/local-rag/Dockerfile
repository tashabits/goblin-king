FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /worker
COPY workers/local-rag/worker.py /worker/worker.py
COPY fixtures /worker/fixtures

ENTRYPOINT ["python", "/worker/worker.py"]

