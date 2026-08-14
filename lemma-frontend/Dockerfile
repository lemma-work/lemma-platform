# syntax=docker/dockerfile:1

# Kept in step with /.nvmrc and both package.json `engines`. This image is
# what actually ships the frontend, and it was two majors ahead of every CI
# job that validates it -- so production ran a Node nothing had tested.
FROM node:24-alpine AS builder

WORKDIR /lemma-typescript
COPY lemma-typescript/package.json lemma-typescript/package-lock.json ./
RUN npm ci
COPY lemma-typescript/ ./
RUN npm run build

WORKDIR /app
COPY lemma-frontend/package.json lemma-frontend/package-lock.json ./
RUN npm ci
COPY lemma-frontend/ ./
# No retry. This used to loop three times because `next/font/google` fetched
# from fonts.googleapis.com during the build, which took down six of our last
# eight release runs. The fonts are vendored now (see app/layout.tsx), the build
# makes no network request for them, and a failure here is a real failure worth
# surfacing on the first attempt rather than three minutes later.
RUN npm run build

FROM node:24-alpine AS runner
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
