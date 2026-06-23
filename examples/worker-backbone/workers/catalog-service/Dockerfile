FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /service
COPY service.py /service/service.py

ENTRYPOINT ["python", "/service/service.py"]

