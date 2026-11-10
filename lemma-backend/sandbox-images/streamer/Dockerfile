# Builds lemma-streamer (and the streamcheck viewer) into /out.
#
#   docker build -f lemma-backend/sandbox-images/streamer/Dockerfile \
#     --target out -o type=local,dest=out/streamer lemma-backend/sandbox-images/streamer
#
# Dockerfile.workspace carries the same stage; this file is for building and
# testing the streamer on its own. trixie, because the workspace image is
# python:3.14-slim on trixie. x264 and libvpx are linked statically, so the
# binary needs only libc and libm at run time -- the same file runs on the
# Docker image and on the E2B template's Ubuntu.
ARG GO_IMAGE=golang:1.25-trixie@sha256:2c4c60ef415fbfa5e90300722293bef36c5e63fae17570ce18f580af933dbd73

FROM ${GO_IMAGE} AS build
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libvpx-dev \
        libx264-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY . .
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    go vet ./... \
    && go test ./... \
    && CGO_ENABLED=1 go build -trimpath -ldflags='-s -w' -o /out/lemma-streamer ./cmd/lemma-streamer \
    && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/streamcheck ./cmd/streamcheck

FROM scratch AS out
COPY --from=build /out/ /
