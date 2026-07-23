REGISTRY ?= ghcr.io/lemma-work
TAG ?= dev
PLATFORM ?= linux/amd64
PLATFORMS ?= linux/amd64,linux/arm64
PYTHON_IMAGE ?= python:3.14-slim
NODE_IMAGE ?= node:24-slim

MANAGER_IMAGE := $(REGISTRY)/lemma-agentbox
WORKSPACE_IMAGE := $(REGISTRY)/lemma-agentbox-workspace
FUNCTION_IMAGE := $(REGISTRY)/lemma-agentbox-function
TEST_WORKSPACE_IMAGE ?= agentbox-workspace:dev
TEST_FUNCTION_IMAGE ?= agentbox-function:dev
TEST_MANAGER_IMAGE ?= agentbox-manager:dev

.PHONY: build build-manager build-workspace build-function build-test-images
.PHONY: push push-manager push-workspace push-function
.PHONY: build-push print-images

print-images:
	@echo "manager: $(MANAGER_IMAGE):$(TAG)"
	@echo "workspace: $(WORKSPACE_IMAGE):$(TAG)"
	@echo "function: $(FUNCTION_IMAGE):$(TAG)"

## Build local single-platform images from the monorepo root context.
build: build-manager build-workspace build-function

build-manager:
	docker build \
		--platform $(PLATFORM) \
		-f agentbox/Dockerfile \
		--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE) \
		-t $(MANAGER_IMAGE):$(TAG) \
		..

build-workspace:
	docker build \
		--platform $(PLATFORM) \
		-f Dockerfile.workspace \
		--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE) \
		--build-arg NODE_IMAGE=$(NODE_IMAGE) \
		-t $(WORKSPACE_IMAGE):$(TAG) \
		..

build-function:
	docker build \
		--platform $(PLATFORM) \
		-f Dockerfile.function \
		--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE) \
		-t $(FUNCTION_IMAGE):$(TAG) \
		..

# Canonical names consumed by the real Docker conformance suite. Keeping this
# explicit prevents CI from accidentally reusing unrelated mutable local tags.
build-test-images:
	docker build \
		--platform $(PLATFORM) \
		-f agentbox/Dockerfile \
		--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE) \
		-t $(TEST_MANAGER_IMAGE) \
		..
	docker build \
		--platform $(PLATFORM) \
		-f Dockerfile.workspace \
		--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE) \
		--build-arg NODE_IMAGE=$(NODE_IMAGE) \
		-t $(TEST_WORKSPACE_IMAGE) \
		..
	docker build \
		--platform $(PLATFORM) \
		-f Dockerfile.function \
		--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE) \
		-t $(TEST_FUNCTION_IMAGE) \
		..

## Build and push multi-arch images.
push: push-manager push-workspace push-function

push-manager:
	docker buildx build \
		--platform $(PLATFORMS) \
		-f agentbox/Dockerfile \
		--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE) \
		-t $(MANAGER_IMAGE):$(TAG) \
		--push \
		..

push-workspace:
	docker buildx build \
		--platform $(PLATFORMS) \
		-f Dockerfile.workspace \
		--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE) \
		--build-arg NODE_IMAGE=$(NODE_IMAGE) \
		-t $(WORKSPACE_IMAGE):$(TAG) \
		--push \
		..

push-function:
	docker buildx build \
		--platform $(PLATFORMS) \
		-f Dockerfile.function \
		--build-arg PYTHON_IMAGE=$(PYTHON_IMAGE) \
		-t $(FUNCTION_IMAGE):$(TAG) \
		--push \
		..

build-push: push
