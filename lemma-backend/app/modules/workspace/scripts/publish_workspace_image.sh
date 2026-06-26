#!/usr/bin/env bash
set -euo pipefail

# Build and push a prebuilt workspace image for e2e usage.
# Example:
#   ./app/modules/workspace/scripts/publish_workspace_image.sh \
#     ghcr.io/lemma-work/workspace-server-arm64:2026-02-18-arm64 \
#     linux/arm64/v8

IMAGE_TAG="${1:-}"
PLATFORM="${2:-linux/arm64/v8}"
BACKEND_DIR="$(cd "$(dirname "$0")/../../../../" && pwd)"
ROOT_DIR="$(cd "$BACKEND_DIR/.." && pwd)"
DOCKERFILE_PATH="$ROOT_DIR/agentbox/Dockerfile.runtime"

if [[ -z "$IMAGE_TAG" ]]; then
  echo "Usage: $0 <image-tag> [platform]"
  exit 1
fi

if [[ ! -f "$DOCKERFILE_PATH" ]]; then
  echo "workspace runtime Dockerfile not found at: $DOCKERFILE_PATH"
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required"
  exit 1
fi

echo "Building workspace image: $IMAGE_TAG"
echo "Platform: $PLATFORM"

# Changes every commit so the build re-resolves the @latest npm tools and
# reinstalls the current lemma SDK/CLI source instead of reusing a stale layer.
CACHE_BUST="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || date +%s)"

docker build \
  --platform "$PLATFORM" \
  -f "$DOCKERFILE_PATH" \
  --build-arg CACHE_BUST="$CACHE_BUST" \
  -t "$IMAGE_TAG" \
  "$ROOT_DIR"

echo "Pushing: $IMAGE_TAG"
docker push "$IMAGE_TAG"

echo "Done. Use this image as the sandbox manager default:"
echo "  export AGENTBOX_RUNTIME_IMAGE=$IMAGE_TAG"
echo "  export AGENTBOX_PLATFORM=$PLATFORM"
