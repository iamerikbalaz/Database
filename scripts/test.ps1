$ErrorActionPreference = "Stop"

docker compose config --quiet
docker compose build
docker compose run --rm --no-deps backend pytest
docker compose run --rm --no-deps worker pytest
docker compose run --rm --no-deps frontend npm run lint
docker compose run --rm --no-deps frontend npm run build
docker compose run --rm --no-deps frontend npm test
