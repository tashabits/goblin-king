FROM node:22-alpine AS base
WORKDIR /app
COPY app.mjs .
USER 65532:65532

FROM base AS test
COPY test.mjs .
CMD ["node", "--test", "test.mjs"]

FROM base AS runtime
EXPOSE 8080
CMD ["node", "app.mjs"]
