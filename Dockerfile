FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY examples /app/examples
COPY workers /app/workers
COPY goblin-images.json goblin-king-api.json goblin-king-project.json /app/

RUN pip install --no-cache-dir -e .

ENTRYPOINT ["goblin-king"]
