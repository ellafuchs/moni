.PHONY: dev build-css clean

TAILWIND = bin/tailwindcss
PORT ?= 3000

dev: build-css
	@echo "Starting dev server..."
	@$(TAILWIND) -i web/tailwind/input.css -o public/dist/main.css --watch & \
	uv run python -m web.app

build-css:
	@$(TAILWIND) -i web/tailwind/input.css -o public/dist/main.css

clean:
	rm -rf __pycache__/ web/__pycache__/
