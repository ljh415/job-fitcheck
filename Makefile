.PHONY: deploy down logs release

deploy:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api

release:
	@if [ -z "$(v)" ]; then echo "Usage: make release v=0.x.x"; exit 1; fi
	@if [ "$$(git branch --show-current)" != "main" ]; then echo "Error: main 브랜치에서만 릴리즈 가능"; exit 1; fi
	git tag -a $(v) -m "$(v)"
	git push origin main --tags
