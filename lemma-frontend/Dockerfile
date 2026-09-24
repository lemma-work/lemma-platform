# syntax=docker/dockerfile:1
FROM node:24-alpine AS builder
WORKDIR /app/lemma-typescript
COPY lemma-typescript/package.json lemma-typescript/package-lock.json ./
RUN npm ci
COPY lemma-typescript/ ./

WORKDIR /app/lemma-frontend
COPY lemma-frontend/package.json lemma-frontend/package-lock.json ./
RUN npm ci
COPY lemma-frontend/ ./

ARG NEXT_PUBLIC_DATA=live
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_AUTH_URL
ARG NEXT_PUBLIC_SITE_URL=https://lemma.work
ARG NEXT_PUBLIC_ANALYTICS_INGEST_HOST=https://eu.i.posthog.com
ARG NEXT_PUBLIC_ANALYTICS_ASSETS_HOST=https://eu-assets.i.posthog.com
ARG NEXT_PUBLIC_APPS_DOMAIN_SUFFIX=
ENV NEXT_PUBLIC_SITE_URL=${NEXT_PUBLIC_SITE_URL} \
    NEXT_PUBLIC_ANALYTICS_INGEST_HOST=${NEXT_PUBLIC_ANALYTICS_INGEST_HOST} \
    NEXT_PUBLIC_ANALYTICS_ASSETS_HOST=${NEXT_PUBLIC_ANALYTICS_ASSETS_HOST} \
    NEXT_PUBLIC_DATA=${NEXT_PUBLIC_DATA} \
    NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_AUTH_URL=${NEXT_PUBLIC_AUTH_URL} \
    NEXT_PUBLIC_APPS_DOMAIN_SUFFIX=${NEXT_PUBLIC_APPS_DOMAIN_SUFFIX} \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-alpine AS runner
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=8080 HOSTNAME=0.0.0.0
WORKDIR /app/lemma-typescript
COPY lemma-typescript/package.json lemma-typescript/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=builder /app/lemma-typescript/dist ./dist

WORKDIR /app/lemma-frontend
COPY lemma-frontend/package.json lemma-frontend/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=builder /app/lemma-frontend/.next ./.next
COPY --from=builder /app/lemma-frontend/public ./public
COPY --from=builder /app/lemma-frontend/content ./content
COPY --from=builder /app/lemma-frontend/server.mjs ./server.mjs
COPY --from=builder /app/lemma-frontend/server ./server
COPY --from=builder /app/lemma-frontend/next.config.ts ./next.config.ts
USER node
EXPOSE 8080
CMD ["node", "server.mjs"]
