# Material Done browser E2E

Playwright test ověřuje skutečný řetězec Vite frontend → FastAPI backend →
PostgreSQL → worker. Mock API se nepoužívá. Test přes skutečné API založí firmu,
publikovanou značku, projekt, procesora a tři materiály a v Chromium provede:

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

Při selhání zůstane Playwright trace, screenshot a sanitizované syntetické logy v
`<repo>/.e2e-artifacts/<run-guid>`; credentials ani raw metadata se neukládají.
Po úspěchu se tento konkrétní artifact adresář odstraní. Cleanup ověříte závěrem
`Cleanup removed only project ...; volume ... was preserved.` a nulovým návratovým kódem; navíc lze spustit
`npm.cmd run test:e2e:helpers`, který kontroluje path a cleanup invarianty bez
Dockeru.
