FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY repository-ui/static ./repository-ui/static

RUN pip install --no-cache-dir .

ENV GOBLIN_KING_REPOSITORY_UI_STATIC_ROOT=/app/repository-ui/static

EXPOSE 8080

CMD ["goblin-king", "repository-ui", "run", "--host", "0.0.0.0", "--port", "8080"]
