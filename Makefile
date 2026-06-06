IMAGE    := ghcr.io/platypod/mediarvester
VERSION  ?= latest
BUILDER  := platypod-multiarch

.PHONY: builder build dev dev-api dev-ui

builder:  ## Create the multi-arch buildx builder (once per machine)
	docker buildx inspect $(BUILDER) >/dev/null 2>&1 || \
	  docker buildx create --name $(BUILDER) --driver docker-container --bootstrap
	docker buildx use $(BUILDER)

build: builder  ## Build multi-arch image (linux/amd64 + linux/arm64) and push to GHCR
	docker buildx build \
	  --platform linux/amd64,linux/arm64 \
	  -t $(IMAGE):$(VERSION) \
	  -t $(IMAGE):latest \
	  --push \
	  .

dev-api:  ## Run the FastAPI backend in dev mode (hot-reload)
	mkdir -p data downloads
	uvicorn main:app --app-dir src --host 0.0.0.0 --port 8080 --reload

dev-ui:  ## Run the Vite dev server (proxies /api to localhost:8080)
	cd frontend && npm run dev

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
