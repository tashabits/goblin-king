FROM python:3.12-alpine AS base
WORKDIR /app
COPY app.py .
USER 65532:65532

FROM base AS test
COPY test_app.py .
CMD ["python", "-m", "unittest", "-v"]

FROM base AS runtime
EXPOSE 8080
CMD ["python", "app.py"]
