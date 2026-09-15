# Authentication and Material Done browser E2E

Playwright test ověřuje skutečný řetězec Vite frontend → FastAPI backend →
PostgreSQL → worker. Mock API se nepoužívá. Test přes skutečné API založí firmu,
publikovanou značku, projekt, procesora a čtyři materiály a v Chromium provede:

- validní preflight, link, Done, current metadata, snapshot a reload;
- chybějící `metadata.txt` jako neblokující warning;
- rozměr nepodporovaný přesností databáze jako neblokující warning;
- odmítnutí anonymních požadavků, vynucenou změnu hesla, skutečnou session,
  serverové oprávnění zpracovatele, CSRF a odhlášení s revokací;
- administrátorské vytvoření účtu, vydání a reset přístupu, změnu role,
  deaktivaci a revokaci sessions přes skutečné UI;
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

Pro tuto autonomní pracovní kopii vždy nejprve nastavte
`$env:E2E_PROJECT_NAME = 'reawote-e2e-auto-01a0a64d'`; původní výchozí E2E
volume na hostu je chráněný. Jméno musí být `reawote-e2e` nebo jeho povolený
suffix; volume se odvozuje jako `<projekt>-postgres-data`.

Runner provede dvě celé sady scénářů. Mezi nimi restartuje aplikaci nad
nezměněnou databází a fixtures. Druhá sada ověřuje zachované heslo, DONE,
propojení složky a jediný snapshot; nepoužije reset ani opakované seedování.
Před zcela novým během se vlastní izolované schéma resetuje podle níže
uvedených kontrol. Údaje pro autentizaci vznikají náhodně pro každý běh,
zůstávají v paměti/procesním prostředí a nejsou součástí manifestu.

Runner podle potřeby doinstaluje Playwright Chromium a používá náhodné volné
porty publikované výhradně na `127.0.0.1`. Skript `npm.cmd run test:e2e` pouze
přesměruje na tentýž PowerShell runner. Přímé `npx playwright test` není
podporované: bez krátkodobého manifestu a capability tokenu skončí konfigurace
před jakýmkoli requestem nebo zápisem fixture.

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

Při selhání zůstane screenshot a sanitizované syntetické logy v
`<repo>/.e2e-artifacts/<run-guid>`; credentials ani raw metadata se neukládají.
Playwright trace je vypnutý, protože obsahuje cookies a těla auth požadavků.
Po úspěchu se tento konkrétní artifact adresář odstraní. Cleanup ověříte závěrem
`Cleanup removed only project ...; volume ... was preserved.` a nulovým návratovým kódem; navíc lze spustit
`npm.cmd run test:e2e:helpers`, který kontroluje path a cleanup invarianty bez
Dockeru.
