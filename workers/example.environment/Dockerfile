FROM python:3.12-slim

WORKDIR /worker
COPY worker.py /worker/worker.py

RUN pip install --no-cache-dir "redis>=5,<7"

ENTRYPOINT ["python", "/worker/worker.py"]
