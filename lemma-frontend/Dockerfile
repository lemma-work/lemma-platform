# syntax=docker/dockerfile:1

FROM node:26-alpine AS builder

WORKDIR /lemma-typescript
COPY lemma-typescript/package.json lemma-typescript/package-lock.json ./
RUN npm ci
COPY lemma-typescript/ ./
RUN npm run build

WORKDIR /app
COPY lemma-frontend/package.json lemma-frontend/package-lock.json ./
RUN npm ci
COPY lemma-frontend/ ./
RUN npm run build

FROM node:26-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV PORT=8080
ENV HOSTNAME=0.0.0.0

# Runtime env vars (no build args needed!)
# These are injected at container startup via docker-compose
ENV NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_AUTH_URL=""
ENV NEXT_PUBLIC_SITE_URL=""
ENV NEXT_PUBLIC_SESSION_TOKEN_DOMAIN=""

EXPOSE 8080

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/public ./app/public
COPY --from=builder /app/.next/static ./app/.next/static

# Copy entrypoint script for runtime config generation
COPY lemma-frontend/docker-entrypoint.sh ./
RUN chmod +x ./docker-entrypoint.sh

ENTRYPOINT ["./docker-entrypoint.sh"]
