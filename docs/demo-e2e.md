# Material Done browser E2E

Playwright test ověřuje skutečný řetězec Vite frontend → FastAPI backend →
PostgreSQL → worker. Mock API se nepoužívá. Test přes skutečné API založí firmu,
publikovanou značku, projekt, procesora a tři materiály a v Chromium provede:

- povinnou změnu počátečního hesla a ověření revokace původní session;
- nové přihlášení, načtení skutečné serverové session a serverový logout;
- validní preflight, link, Done, current metadata, snapshot a reload;
- chybějící `metadata.txt` jako neblokující warning;
- blokovaný identity mismatch;
- klientské odmítnutí absolutní cesty a `..` bez preflight requestu;
- kontrolu veřejných odpovědí, UI a browser console na únik raw obsahu, host path
  nebo neočekávanou chybu.

## Spuštění

Požadavky jsou Docker Desktop s Compose v2 přepnutý na Linux containers,
Node.js 22 nebo novější a npm. Závislosti nainstalujte z kořene repozitáře takto:

```powershell
Push-Location frontend
try { npm.cmd ci } finally { Pop-Location }
```

Samotný E2E běh má jediný podporovaný write-capable vstup:

```powershell
.\scripts\test-demo-e2e.ps1
```

Runner podle potřeby doinstaluje Playwright Chromium a používá náhodné volné
porty publikované výhradně na `127.0.0.1`. Skript `npm.cmd run test:e2e` pouze
přesměruje na tentýž PowerShell runner. Přímé `npx playwright test` není
podporované: bez krátkodobého manifestu a capability tokenu skončí konfigurace
před jakýmkoli requestem nebo zápisem fixture.

Každý běh vytvoří vlastního syntetického administrátora přes oficiální
`python -m app.auth.cli`. Náhodné počáteční a nové heslo se předají pouze přes
standardní vstup a krátkodobé procesní prostředí Playwrightu; nejsou v argumentech
příkazu, manifestu ani souboru. Všechny scénáře používají skutečné auth endpointy
a browser requesty se nemockují ani neinterceptují.

### Životní cyklus přihlašovacích údajů

Import `auth-credentials.ts`, konfigurace, reporter ani registrace testů nečtou
a nemažou přihlašovací environment. Řídicí Playwright proces si jej ponechá pro
vytvoření všech workerů. Až automatická worker fixture `authCredentials` načte
v každém workeru vlastní immutable kopii; ve svém `finally` hned odstraní
`E2E_AUTH_EMAIL`, `E2E_AUTH_INITIAL_PASSWORD` a `E2E_AUTH_PASSWORD` z prostředí
tohoto workeru, i když validace selže. Opakovaný import znovu nic nenačítá.

Browser startup závisí přes `launchOptions` na dokončení této fixture. Browser
dostane navíc explicitně filtrované prostředí bez auth proměnných,
`POSTGRES_PASSWORD`, manifestu a capability tokenu. Nejde o globální cache
řídicího procesu a žádný soubor s heslem nevzniká. PowerShell runner odstraní
auth proměnné a capability token ihned po Playwrightu a znovu ve vnějším
`finally`; staré zděděné credential hodnoty neobnovuje.

Bootstrap nadále volá oficiální CLI. Heslo předá přes privátní UTF-8 stdin
procesu, bez shell pipe nebo argumentu s heslem. Obě výstupní větve procesu se
současně zachytí a zahodí: ani `GetPassWarning`, prompt, echo nebo raw chyba
se nevypisují. Selhání má pouze vlastní kód `E2E_BOOTSTRAP_FAILED` a obecný text.

## Izolace a cleanup

Runner používá pouze Compose projekt `reawote-e2e`, volume
`reawote-e2e-postgres-data` a nový označený adresář
`<repo>/.e2e-data/runs/<GUID>`. Worker dostane jen read-only bind jeho podadresáře
`materials`; NAS se nepřipojuje a PostgreSQL heslo vznikne náhodně pouze v
procesním prostředí. UNC a network-drive repozitáře jsou odmítnuty; na Windows
musí být repozitář na lokálním `Fixed` disku.

Před startem se kontroluje vyrenderovaný Compose model včetně převodu logického
klíče `postgres_data` na engine název `reawote-e2e-postgres-data`; po startu se
ověří i skutečný container mount. Před každou Docker mutací runner odmítne
vzdálený context/`DOCKER_HOST` a vypíše pouze bezpečný název lokálního contextu.
Procesní mutex zabrání souběžnému běhu ještě před první Docker mutací.

Databázový volume se při cleanupu nemaže. Před každým během runner nejprve ověří
jeho Compose ownership labely a skutečný mount PostgreSQL kontejneru a potom
otočí náhodné heslo pevně dané E2E role a obnoví pouze schéma `public` v databázi
`reawote_e2e`. SQL se předává `psql` přes standardní vstup, takže heslo není v
argumentech procesu. Runner nepoužívá
`docker volume rm` ani `docker compose down --volumes`; kontejnery a síť odstraní
pomocí `docker compose down --remove-orphans` a volume zachová pro další běh.

Před každým vytvořením, zápisem a cleanupem se kontrolují všechny existující
komponenty cesty. Symlink, junction, mount point nebo jiný `ReparsePoint` cleanup
zastaví a runner vypíše přesnou cestu pro ruční kontrolu. `finally` odstraňuje jen
konkrétní GUID run adresář a nikdy nadřazené `.e2e-data`. Stav projektů `reawote`,
`reawote-demo` a volumes `reawote_postgres_data`,
`reawote-demo-postgres-data` se porovná před a po běhu.

Kvůli ochraně hesel, session cookie a CSRF tokenu jsou Playwright trace,
screenshoty i video vypnuté. Při selhání může v
`<repo>/.e2e-artifacts/<run-guid>` zůstat pouze textová diagnostika složená
z vlastních bezpečných kódů a výsledků kontroly logů; žádný raw log nebo
Exception.message se nepřebírá. Reporter zachovává pouze šest povolených názvů
scénářů, fázi, vlastní error code, povolený relativní soubor a řádek, endpoint
bez ID/query a HTTP status, případně kategorii browser console. Nezahrnuje
headers, body, stack, cookie, CSRF, obsah inputu nebo environment.

Automatická test fixture před zápisem frameworkového `error-context.md` nahradí
veřejné `TestInfo.errors` čistými objekty s bezpečnými kódy. Zachová počet chyb
i failed stav. Samotné vypnutí trace totiž zápisu chybového kontextu nebrání.
Tato fixture nezávisí na page/context; její teardown běží po nich a před
artifact recorderem. Credentials fixture může selhat ještě před ní, a proto
vždy vyhazuje pouze bezpečné `E2E_CREDENTIALS_MISSING`/`E2E_CREDENTIALS_INVALID`.
Dočasné interní soubory Playwrightu patří do
vždy uklízeného run-data adresáře, nikoli mezi zachované diagnostické artefakty.
Po úspěchu se tento konkrétní artifact adresář odstraní. Cleanup ověříte závěrem
`Cleanup removed only project ...; volume ... was preserved.` a nulovým návratovým kódem; navíc lze spustit
`npm.cmd run test:e2e:helpers`, který kontroluje path a cleanup invarianty bez
Dockeru.

## Regrese infrastruktury bez Dockeru

Z `frontend/` spusťte `npm.cmd run test:e2e:infrastructure`,
`npm.cmd run test:e2e:helpers`, `npm.cmd run test:e2e:guard` a
`npm.cmd run test:e2e:list`. Node regresní testy používají nativní TypeScript
type stripping (ověřeno na Node 24.20). List funguje bez auth credentials.

Lifecycle regrese spouští samostatný discovery/controller proces, více child
workerů, skutečný Playwright se dvěma workery a simulované browser child
procesy. Ověřuje také skutečně existující artefakty po úmyslné chybě v těle
testu, afterEach i teardownu; prohledá celé stdout/stderr a všechny artefakty
na unikátní syntetická tajemství. Tyto infrastrukturní probes nevolají aplikaci,
nepoužívají její databázi a nejsou náhradou šesti skutečných browser scénářů.

Skutečné ověření vyžaduje dva běhy `scripts/test-demo-e2e.ps1`, každý **6/6
passed**. Reporter odmítne dílčí nebo skipped běh i při jinak nulovém exit code.
Pokud Docker chybí, runner vrátí `E2E_DOCKER_UNAVAILABLE`; runtime, skutečná
browser console, Docker logy a zachování volume po běhu pak nejsou ověřené.
