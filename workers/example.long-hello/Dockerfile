FROM python:3.12-slim

WORKDIR /worker
COPY service.py /worker/service.py

EXPOSE 8080

ENTRYPOINT ["python", "/worker/service.py"]
