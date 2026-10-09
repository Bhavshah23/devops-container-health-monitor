.PHONY: setup up down logs test demo-event

-include .env
export

setup:
	cp -n .env.example .env || true

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f monitor

test:
	pytest -q

demo-event:
	curl -X POST http://localhost:$${HOST_PORT:-5001}/webhook/events \
		-H "Content-Type: application/json" \
		-H "X-Webhook-Secret: $${WEBHOOK_SECRET:-change-this-lab-secret}" \
		-d '{"container":"payment-api","status":"critical","details":{"message":"HTTP 503 threshold exceeded"}}'
