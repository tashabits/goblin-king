FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY directory-ui/static ./directory-ui/static

RUN pip install --no-cache-dir .

ENV GOBLIN_KING_DIRECTORY_UI_STATIC_ROOT=/app/directory-ui/static

EXPOSE 8080

CMD ["goblin-king", "directory-ui", "run", "--host", "0.0.0.0", "--port", "8080"]
