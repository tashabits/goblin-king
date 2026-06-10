FROM node:22-alpine

WORKDIR /worker
COPY worker.mjs /worker/worker.mjs

ENTRYPOINT ["node", "/worker/worker.mjs"]
