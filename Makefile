SHELL := /bin/bash
export PATH := $(HOME)/.local/bin:$(PATH)

PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; elif [ -x "$(HOME)/tmp/venv-infra/bin/python" ]; then echo "$(HOME)/tmp/venv-infra/bin/python"; else echo python3; fi)
PYTEST ?= $(PYTHON) -m pytest

FRONTEND_DIRS := \
	cymbal/frontends/customer-loan-portal-web \
	cymbal/frontends/internal-bank-los-web \
	godview/god-view-sre-web

.PHONY: help test-unit test-contract local-up local-down test-local-e2e test-gcp-e2e drills lint-brands lint-neutrality

help:
	@echo "God-View v4 Quality Gate Targets:"
	@echo "  make test-unit        Run L1 hermetic unit tests (Python + frontends)"
	@echo "  make test-contract    Run L2 contract tests and frontend typechecks"
	@echo "  make local-up         Boot local docker-compose + kind cluster + seed + local git"
	@echo "  make local-down       Tear down local docker-compose and kind cluster"
	@echo "  make test-local-e2e   Run L3 local environment end-to-end tests"
	@echo "  make test-gcp-e2e     Run L4 deployed GCP end-to-end verification tests"
	@echo "  make drills           Run L5 chaos & business-logic drill suite"
	@echo "  make lint-brands      Enforce I-01 vehicle-finance domain cleanliness"
	@echo "  make lint-neutrality  Enforce I-02 zero hardcoded tenant knowledge in godview/ and shared/"

test-unit:
	GODVIEW_ENV=hermetic $(PYTEST) -m "not contract and not local_e2e and not gcp and not drill" -q
	@for dir in $(FRONTEND_DIRS); do \
		if [ -f "$$dir/package.json" ]; then \
			if [ ! -d "$$dir/node_modules" ]; then \
				echo "ERROR: $$dir/node_modules is missing; run 'npm ci' in $$dir" >&2; \
				exit 1; \
			fi; \
			echo "Running unit tests in $$dir..."; \
			(cd "$$dir" && npm test -- --run); \
		fi; \
	done

test-contract:
	GODVIEW_ENV=hermetic $(PYTEST) -m contract -q
	@for dir in $(FRONTEND_DIRS); do \
		if [ -f "$$dir/package.json" ]; then \
			if [ ! -d "$$dir/node_modules" ]; then \
				echo "ERROR: $$dir/node_modules is missing; run 'npm ci' in $$dir" >&2; \
				exit 1; \
			fi; \
			echo "Running typecheck in $$dir..."; \
			(cd "$$dir" && npm run typecheck); \
		fi; \
	done

local-up:
	@command -v docker >/dev/null 2>&1 || { echo "ERROR: docker is not installed on this machine (Decision A19)"; exit 1; }
	@command -v kind >/dev/null 2>&1 || { echo "ERROR: kind is not installed on this machine (Decision A19)"; exit 1; }
	bash local-env/git/init-local-git.sh
	docker compose -f docker-compose.yml -f local-env/docker-compose.override.yml up -d --build
	kind get clusters | grep -qx "godview" || kind create cluster --name godview --config local-env/kind/cluster.yaml
	docker build -t cymbal/kyc-model-armor-svc:local cymbal/services/kyc-model-armor-svc
	docker build -t cymbal/underwriting-los-svc:local cymbal/services/underwriting-los-svc
	kind load docker-image --name godview cymbal/kyc-model-armor-svc:local cymbal/underwriting-los-svc:local
	cp local-env/kind/backend_override.tf.tpl infra/terraform/workloads/backend_override.tf
	trap 'rm -f infra/terraform/workloads/backend_override.tf' EXIT; \
	terraform -chdir=infra/terraform/workloads init -reconfigure -input=false && \
	terraform -chdir=infra/terraform/workloads apply -auto-approve -input=false \
		-var="provider_mode=kind" \
		-var="kube_context=kind-godview" \
		-var="service_type=NodePort" \
		-var='images={"kyc-model-armor-svc"="cymbal/kyc-model-armor-svc:local","underwriting-los-svc"="cymbal/underwriting-los-svc:local"}'
	bash local-env/seed/seed.sh

local-down:
	-rm -f infra/terraform/workloads/backend_override.tf infra/terraform/workloads/kind-terraform.tfstate*
	-kind delete cluster --name godview
	-docker compose -f docker-compose.yml -f local-env/docker-compose.override.yml down -v

test-local-e2e:
	$(PYTEST) -m local_e2e -q

test-gcp-e2e:
	$(PYTEST) -p infra.tests.test_gcp_e2e -m gcp infra/tests/test_gcp_e2e.py -q

drills:
	$(PYTEST) -p infra.tests.test_gcp_e2e -m drill infra/tests/test_drills.py -q

lint-brands:
	$(PYTHON) shared/tools/lint_brands.py

lint-neutrality:
	$(PYTHON) shared/tools/lint_neutrality.py
