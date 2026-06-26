REGISTRY ?= ghcr.io/lemma-work
TAG ?= dev
PLATFORM ?= linux/amd64
PLATFORMS ?= linux/amd64,linux/arm64
PYTHON_IMAGE ?= python:3.14.4-slim
NODE_IMAGE ?= node:25.9.0-slim
# Changes every commit so a runtime build always re-resolves the @latest npm tools
# and reinstalls the current lemma SDK/CLI source (no stale Docker layer cache).
CACHE_BUST ?= $(shell git rev-parse --short HEAD 2>/dev/null || date +%s)

MANAGER_IMAGE := $(REGISTRY)/agentbox-manager
RUNTIME_IMAGE := $(REGISTRY)/agentbox-runtime

.PHONY: build build-manager build-runtime
.PHONY: push push-manager push-runtime
.PHONY: build-push print-images

print-images:
	@echo "manager: $(MANAGER_IMAGE):$(TAG)"
	@echo "runtime: $(RUNTIME_IMAGE):$(TAG)"

## Build local single-platform images. The runtime image uses the monorepo root context.
build: build-manager build-runtime

build-manager:
	docker build \
		--platform $(PLATFORM) \
		-f Dockerfile \
		--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE) \
		-t $(MANAGER_IMAGE):$(TAG) \
		.

build-runtime:
	docker build \
		--platform $(PLATFORM) \
		-f Dockerfile.runtime \
		--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE) \
		--build-arg NODE_IMAGE=$(NODE_IMAGE) \
		--build-arg CACHE_BUST=$(CACHE_BUST) \
		-t $(RUNTIME_IMAGE):$(TAG) \
		../..

## Build and push multi-arch images.
push: push-manager push-runtime

push-manager:
	docker buildx build \
		--platform $(PLATFORMS) \
		-f Dockerfile \
		--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE) \
		-t $(MANAGER_IMAGE):$(TAG) \
		--push \
		.

push-runtime:
	docker buildx build \
		--platform $(PLATFORMS) \
		-f Dockerfile.runtime \
		--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE) \
		--build-arg NODE_IMAGE=$(NODE_IMAGE) \
		--build-arg CACHE_BUST=$(CACHE_BUST) \
		-t $(RUNTIME_IMAGE):$(TAG) \
		--push \
		../..

build-push: push
