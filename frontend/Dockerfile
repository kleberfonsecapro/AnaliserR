FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# FEAT-003: o nome do cache do service worker muda a cada build, então uma
# versão nova invalida a shell antiga em vez de servir bundle velho.
RUN sed -i "s/__BUILD__/$(date +%s)/" dist/sw.js

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

# SEC-005: nginx sem root (porta 8080; o compose publica 8091 para ele).
RUN chown -R nginx:nginx /etc/nginx /var/cache/nginx /var/log/nginx \
    && touch /var/run/nginx.pid && chown nginx:nginx /var/run/nginx.pid
USER nginx

EXPOSE 8080
