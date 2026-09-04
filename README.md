# Database

Základ monorepa interní aplikace REAWOTE. Repozitář obsahuje React frontend,
FastAPI backend, samostatný Python worker a lokální PostgreSQL 18.

## Architektura

| Část | Technologie | Lokální adresa / role |
|---|---|---|
| `frontend/` | React, TypeScript, Vite | <http://localhost:5173> |
| `backend/` | Python 3.13, FastAPI, SQLAlchemy, Alembic | <http://localhost:8000> |
| `worker/` | Python 3.13 | samostatný proces bez integrační logiky |
| `database` | PostgreSQL 18 | interně `database:5432` |

Frontend volá backend přes cestu `/api`, kterou Vite v lokálním prostředí
proxyuje na backend. Endpoint `GET /health` kontroluje také dostupnost databáze.
Backend při startu automaticky spustí `alembic upgrade head`.

Notion, Google Cloud Storage, NAS a vytváření ZIPů nejsou součástí této fáze.

## Databázové API

První doménová část eviduje firmy, jejich publikované značky a projekty. Všechny
záznamy používají UUID a auditní časy `created_at` a `updated_at`. Projekt má stav
`NOT_STARTED`, `IN_PROGRESS` nebo `DONE`; výchozí stav je `NOT_STARTED`.

API poskytuje seznam, vytvoření, detail a částečnou aktualizaci:

- `/api/companies` a `/api/companies/{id}`;
- `/api/brands` a `/api/brands/{id}`;
- `/api/projects` a `/api/projects/{id}`.

Kolekce podporují `GET` a `POST`, detail podporuje `GET` a `PATCH`. Fyzické
mazání záměrně není dostupné; zneaktivnění firmy nebo značky se provádí přes
`PATCH` pole `is_active`. Duplicitní unikátní hodnota vrací `409 Conflict`,
neexistující záznam nebo nadřazená firma `404 Not Found` a nevalidní vstup
`422 Unprocessable Content`. Přesný kontrakt je dostupný v OpenAPI na `/docs`.

## Instalace a spuštění na Windows

Požadavky:

- Windows 10 nebo 11;
- Docker Desktop s Docker Compose v2 (doporučený WSL 2 backend);
- volné porty `5173` a `8000`.

V PowerShellu v kořeni repozitáře připravte lokální konfiguraci:

```powershell
Copy-Item .env.example .env
```

Hodnotu `POSTGRES_PASSWORD` v souboru `.env` změňte na vlastní lokální heslo.
Soubor `.env` je ignorovaný Gitem. Proměnné s prefixem `VITE_` jsou součástí
frontendového bundle, a proto do nich nikdy nepatří tajné údaje.

Celé prostředí spusťte příkazem:

```powershell
docker compose up --build
```

Po naběhnutí služeb otevřete <http://localhost:5173>. Stav backendu lze ověřit
také přímo na <http://localhost:8000/health> a OpenAPI dokumentaci na
<http://localhost:8000/docs>.

Služby zastavíte pomocí `Ctrl+C` a odstraníte kontejnery příkazem:

```powershell
docker compose down
```

Databázová data zůstávají v pojmenovaném Docker volume. Pro jejich úmyslné
odstranění použijte `docker compose down --volumes`.

## Testy a kontrola konfigurace

Kompletní kontrolu v kontejnerech spustíte na Windows:

```powershell
.\scripts\test.ps1
```

Skript ověří výslednou Compose konfiguraci, sestaví obrazy a spustí testy
backendu, workeru a frontendu. Jednotlivé kontroly lze spustit také ručně:

```powershell
docker compose config --quiet
docker compose build
docker compose run --rm --no-deps backend pytest
docker compose run --rm --no-deps worker pytest
docker compose run --rm --no-deps frontend npm run lint
docker compose run --rm --no-deps frontend npm run build
docker compose run --rm --no-deps frontend npm test
```

## Databázové migrace

Novou migraci vytvořte po přidání SQLAlchemy modelů:

```powershell
docker compose run --rm backend alembic revision --autogenerate -m "popis zmeny"
```

Aktuální migrace lze ručně aplikovat příkazem:

```powershell
docker compose run --rm backend alembic upgrade head
```

Výchozí migrace je úmyslně prázdná baseline. Navazující migrace vytváří tabulky
`companies`, `published_brands` a `projects` včetně cizích klíčů, unikátních
omezení a kontrolních omezení.
