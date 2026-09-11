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

Požadavky jsou Docker Desktop s Compose v2, Node.js a npm. Z kořene repozitáře:

```powershell
Set-Location frontend
npm.cmd ci
Set-Location ..
.\scripts\test-demo-e2e.ps1
```

Runner podle potřeby doinstaluje Playwright Chromium a používá náhodné volné
porty publikované výhradně na `127.0.0.1`.

## Izolace a cleanup

Runner používá pouze Compose projekt `reawote-e2e`, volume
`reawote-e2e-postgres-data` a označený adresář
`<system-temp>/reawote-e2e-data`. Worker dostane jen read-only bind jeho
podadresáře `materials`; NAS se nepřipojuje a PostgreSQL heslo vznikne náhodně
pouze v procesním prostředí.

Před startem se kontroluje vyrenderovaný Compose model. Před každým odstraněním
se ověří přesný název projektu, ownership labely volume/kontejnerů/networku,
kanonická temp cesta a ownership marker. `finally` odstraní pouze tyto E2E
prostředky i při selhání testu. Stav projektů `reawote`, `reawote-demo` a volumes
`reawote_postgres_data`, `reawote-demo-postgres-data` se porovná před a po běhu.
