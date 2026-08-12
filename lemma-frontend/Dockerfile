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
# Retried because `next/font/google` fetches from fonts.googleapis.com during
# the build, so this step is only as reliable as that request. It has taken down
# six of our last eight release runs -- here on the emulated arm64 build, and in
# the host-pack script, always as "Error while requesting resource" and always
# green on a re-run.
#
# The real fix is to stop reaching for the network mid-build: vendor the eight
# families and use `next/font/local`. That is a typography change, not a
# packaging one.
RUN for attempt in 1 2 3; do \
        npm run build && break; \
        if [ "$attempt" = 3 ]; then echo "next build failed after 3 attempts" >&2; exit 1; fi; \
        echo "next build failed (attempt $attempt/3); retrying in $((attempt * 5))s" >&2; \
        sleep $((attempt * 5)); \
    done

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
