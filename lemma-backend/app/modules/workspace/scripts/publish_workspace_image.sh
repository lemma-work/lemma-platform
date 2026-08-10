#!/usr/bin/env bash
set -euo pipefail

# Build and push a prebuilt workspace image for e2e usage.
# Example:
#   ./app/modules/workspace/scripts/publish_workspace_image.sh \
#     ghcr.io/lemma-work/lemma-workspace:2026-07-23-arm64 \
#     linux/arm64/v8

IMAGE_TAG="${1:-}"
PLATFORM="${2:-linux/arm64/v8}"
BACKEND_DIR="$(cd "$(dirname "$0")/../../../../" && pwd)"
ROOT_DIR="$(cd "$BACKEND_DIR/.." && pwd)"
DOCKERFILE_PATH="$ROOT_DIR/lemma-backend/sandbox-images/Dockerfile.workspace"

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

docker build \
  --platform "$PLATFORM" \
  -f "$DOCKERFILE_PATH" \
  -t "$IMAGE_TAG" \
  "$ROOT_DIR"

echo "Pushing: $IMAGE_TAG"
docker push "$IMAGE_TAG"

echo "Done. Use this image as the sandbox manager default:"
echo "  export WORKSPACE_IMAGE=$IMAGE_TAG"
