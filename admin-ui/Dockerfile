FROM node:24-alpine AS build

WORKDIR /ui
COPY package*.json ./
RUN npm install
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY docker-entrypoint.d/20-admin-config.sh /docker-entrypoint.d/20-admin-config.sh
COPY --from=build /ui/dist /usr/share/nginx/html/admin

ENV API_UPSTREAM=api:8000
ENV DEPLOYMENT_SCOPE=docker
ENV LONG_HELLO_URL=http://long-hello:8080
RUN chmod +x /docker-entrypoint.d/20-admin-config.sh
EXPOSE 8080
