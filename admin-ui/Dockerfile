FROM node:24-alpine AS build

WORKDIR /ui
COPY package*.json ./
RUN npm install
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /ui/dist /usr/share/nginx/html/admin

ENV API_UPSTREAM=api:8000
EXPOSE 8080
