FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y docker.io \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY examples /app/examples
COPY workers /app/workers
COPY goblin-images.json demo-goblins.json demo-images.json goblin-king-api.json goblin-king-project.json /app/

RUN pip install --no-cache-dir -e .

ENTRYPOINT ["goblin-king"]
