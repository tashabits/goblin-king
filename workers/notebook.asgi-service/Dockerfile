FROM python:3.12-slim

WORKDIR /runner

COPY runner.py /runner/runner.py

RUN pip install --no-cache-dir "uvicorn>=0.30,<1"

EXPOSE 8080

ENTRYPOINT ["python", "/runner/runner.py"]
