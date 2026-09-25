# Database

Základ monorepa interní aplikace REAWOTE. Repozitář obsahuje React frontend,
FastAPI backend, samostatný Python worker a lokální PostgreSQL 18.

## Architektura

| Část | Technologie | Lokální adresa / role |
|---|---|---|
| `frontend/` | React, TypeScript, Vite | <http://localhost:5173> |
| `backend/` | Python 3.13, FastAPI, SQLAlchemy, Alembic | <http://localhost:8000> |
| `worker/` | Python 3.13 | interní API pro kontrolu zdrojů, řízené změny identity a náhledy |
| `database` | PostgreSQL 18 | interně `database:5432` |

Frontend volá backend přes cestu `/api`, kterou Vite v lokálním prostředí
proxyuje na backend. Endpoint `GET /health` kontroluje také dostupnost databáze.
Backend při startu automaticky spustí `alembic upgrade head`.

Bezpečnostní kontrakt backendového přihlášení, session cookie, CSRF ochrany a
provisioningu prvního administrátora popisuje
[`docs/auth-backend-foundation.md`](docs/auth-backend-foundation.md). Doménové
endpointy vyžadují aktivní přihlášení a serverově ověřují role i přidělení
materiálů. Správu účtů a obnovu přístupu popisuje
[`docs/authorization.md`](docs/authorization.md).

Kontrakt propojeni slozky materialu a specializovane operace Mark as Done je v
[`docs/material-folder-api.md`](docs/material-folder-api.md).

Aktuální funkce a skutečně provedené testy shrnuje
[`docs/autonomous-pbr-progress.md`](docs/autonomous-pbr-progress.md).
[Přehled verze k review](docs/pbr-review-candidate.md) uvádí rozsah, bezpečné
opakování testů, migrační hranice a konkrétní zbývající vstupy. Kontrakty:

- [Úvodní přehled materiálů a stavů](docs/production-dashboard.md).
- [Inventář a znovuotevření materiálu](docs/source-inventory.md).
- [Technická kontrola a schválení](docs/material-approvals.md).
- [Řízené změny identity a obnova operace](docs/identity-operations.md).
- [Kategorie, kolekce a verzovaný obsah](docs/catalog-content.md).
- [Schválení uloženého obsahu](docs/content-approvals.md).
- [Seznam materiálů a galerie náhledů](docs/preview-gallery.md).
- [Vizuální styl REAWOTE, logo a lokální fonty](docs/brand-interface.md).
- [Schválené publikační dávky a uložené CSV](docs/publication-batches.md).
- [Spouštění, obnova a uzavření ZIP packagingu](docs/packaging-actions.md).
- [Ověřené stahování historických balíčků](docs/packaging-downloads.md).
- [Výslovné odstranění lokálního balíčku a obnova přerušené operace](docs/packaging-retirement-api.md).
- [Rezervace dávky pro GCS a ochrana materiálů](docs/gcs-upload-jobs.md).
- [Režimy přístupu ke GCS a obnova tokenů](docs/gcs-credentials.md).
- [Auditní historie změn firem](docs/company-history.md).
- [Historie značek, projektů, profilů a materiálů](docs/resource-history.md).
- [Historie úspěšných změn přístupu k účtům](docs/account-security-history.md).
- [Stránkování starších materiálových a katalogových historií](docs/history-pagination.md).
- [Obnovení běžného uložení po ztrátě odpovědi](docs/resource-commands.md).
- [Ochrana HTTP odpovědí a logů při databázové chybě](docs/database-error-redaction-plan.md).
- [Archivace a obnovení PBR materiálů](docs/material-archive-contract.md).
- [Řízené převzetí údajů firmy z Notion](docs/notion-adoption.md).

Ukládání CSV, lokální ZIP packaging a rezervace interní GCS dávky jsou implementované.
Řízené uploady a read-only obnova mají backend a ovládání na stránce Publication
([postup](docs/gcs-staging-controls.md)); ověřené běhy uvádí průběžný checkpoint.
Notion má konfigurovatelné čtení výslovně propojené firmy a porovnání pro ADMIN
([konfigurace a ovládání](docs/notion-reader-plan.md)); ve výchozím stavu je vypnuté.
Převzetí vybraných údajů ukládá místní změnu a auditní historii atomicky.
Automatická synchronizace a potvrzení online importu zbývají.
GCS transport má offline kontraktové testy; živé GCS ani Notion nebyly ověřeny.
Kontroly souborů pracují přes nakonfigurovaný worker; změny identity jsou ve
výchozí konfiguraci vypnuté a vyžadují nastavení podle provozního kontraktu.

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

Pro místní HTTP nastavte současně `AUTH_COOKIE_SECURE=false` a
`AUTH_ALLOW_INSECURE_COOKIE=true` a ponechte CORS origin pouze na loopback adrese.
Pro sdílené prostředí zůstává vyžadováno HTTPS a Secure cookie. Po startu nové
vlastní databáze založte prvního administrátora interaktivně podle auth dokumentace;
výchozí přihlašovací účet ani heslo aplikace nevytváří.

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

Skript ověří lokální Linux Docker, vytvoří nový projekt `reawote-test-<GUID>`,
sestaví obrazy a spustí testy backendu, skutečného PostgreSQL, workeru a frontendu.
Běžnou ani demo databázi nepoužívá. Po běhu odstraní své kontejnery a síť;
vlastní testovací volume ponechá a vypíše jeho přesný název. Porty aplikace
jsou v základním Compose vázané na `127.0.0.1`; proměnné portů obsahují pouze čísla.
Jednotlivé kontroly lze spustit také ručně proti vlastní testovací konfiguraci:

```powershell
docker compose config --quiet
docker compose build
docker compose run --rm --no-deps backend pytest
docker compose run --rm --no-deps worker pytest
docker compose run --rm --no-deps frontend npm run lint
docker compose run --rm --no-deps frontend npm run build
docker compose run --rm --no-deps frontend npm test
```

Skutečný browserový tok Material Done vyžaduje Node.js 22 nebo novější a Docker
Desktop přepnutý na Linux containers. Závislosti připravte tak, aby vás příklad
vždy vrátil do kořene repozitáře:

```powershell
Push-Location frontend
try { npm.cmd ci } finally { Pop-Location }
```

Potom E2E spusťte jediným podporovaným write-capable příkazem z kořene:

```powershell
.\scripts\test-demo-e2e.ps1
```

Runner nepoužívá běžné ani demo databázové volume. Podrobnosti a bezpečnostní
invarianty jsou v [`docs/demo-e2e.md`](docs/demo-e2e.md). Přímé
`npx playwright test` není podporovaný vstup: bez krátkodobého manifestu runneru
selže před requestem a zápisem fixture. Při selhání zůstane trace, screenshot a
sanitizovaná diagnostika v `.e2e-artifacts/<run-guid>`; úspěšný běh vypíše
potvrzení, že E2E kontejnery a pouze jeho GUID run adresář byly odstraněny a
dedikovaný E2E volume zůstal zachován.

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
