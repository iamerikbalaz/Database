# Authentication and Material Done browser E2E

Playwright test ověřuje skutečný řetězec Vite frontend → FastAPI backend →
PostgreSQL → worker. Mock API se nepoužívá. Test přes skutečné API založí firmu,
publikované značky, projekt, procesora a osm materiálů a v Chromium provede:

- validní preflight, link, Done, current metadata, snapshot a reload;
- chybějící `metadata.txt` jako neblokující warning;
- rozměr nepodporovaný přesností databáze jako neblokující warning;
- odmítnutí anonymních požadavků, vynucenou změnu hesla, skutečnou session,
  serverové oprávnění zpracovatele, CSRF a odhlášení s revokací;
- administrátorské vytvoření účtu, vydání a reset přístupu, změnu role,
  deaktivaci a revokaci sessions přes skutečné UI;
- uložení inventáře, invalidaci po změně materiálu, reopen s důvodem,
  zachování metadata snapshotu a auditní historie po restartu;
- blokovaný identity mismatch;
- technickou kontrolu skutečných PNG map a samostatná technická/publikační schválení;
- řízenou změnu identity na syntetických Linux souborech, včetně zachování UUID a historie;
- kategorie/kolekce, verzovaný publikační obsah, jeho schválení a invalidaci po úpravě;
- skutečné dekódování náhledů, přepínání obrázků a porovnání dvou materiálů;
- schválený historický CSV export a jeho neměnnost po úpravě obsahu;
- skutečné lokální balení, obnovu stejného požadavku po ztracené odpovědi,
  jediný potvrzený worker příkaz a uzavření neodeslané rezervace;
- historii balení a změn ZIP pravidel po restartu;
- stažení skutečného ZIPu s kontrolou velikosti a SHA-256 před restartem i po něm,
  i po následné změně obsahu a ZIP pravidla;
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

Privátní packaging služba používá pouze interní síť `<projekt>_packaging_private`,
nepublikuje žádný port a běží jako UID 65532 s read-only kořenem a omezenými
prostředky. Syntetické zdroje má připojené jen pro čtení. Samostatný nový volume
`<projekt>-packaging-<GUID bez pomlček>` obsahuje workspace, hotové artefakty a
journal; runner odmítne jeho předchozí existenci. Token vzniká náhodně pro daný
běh, neukládá se do manifestu a diagnostika jej odstraňuje.
Před startem a po startu/restartu se kontrolují skutečné mounty, UID, síť,
read-only režim a vlastnictví volume. Čisté guard testy bez Dockeru jsou
v `scripts/test-packaging-e2e-helpers.ps1`.

Oba workery se restartují spolu s aplikací. Druhý browser průchod kontroluje
zachovanou databázovou historii balení i skutečně stažené ZIP bajty proti uloženému
hashi. Packaging volume se při cleanupu zachová,
stejně jako izolované identity source/journal volumes. Tyto prostředky nejsou
produkční data a runner nemaže ani starší volumes svých předchozích běhů.

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
Po úspěchu se tento konkrétní artifact adresář standardně odstraní. Pro vizuální
kontrolu syntetického UI lze před během nastavit
`$env:E2E_KEEP_SUCCESS_ARTIFACTS = '1'`: uchová pouze artifact adresář daného běhu.
Cleanup kontejnerů, sítě a vstupních fixtures proběhne stejně; trasy a ochrana
existujících databází zůstávají ověřované. Po kontrole lze proměnnou odstranit.
Cleanup ověříte závěrem
`Cleanup removed only project ...; volume ... was preserved.` a nulovým návratovým kódem; navíc lze spustit
`npm.cmd run test:e2e:helpers`, který kontroluje path a cleanup invarianty bez
Dockeru.
