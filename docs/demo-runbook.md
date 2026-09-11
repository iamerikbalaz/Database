# Lokální demo prostředí REAWOTE

Tento runbook připravuje izolované demo pro první interní prezentaci. Demo
nepoužívá produkční NAS, Notion ani Google Storage. Obsahuje pouze smyšlené
databázové záznamy a malé textové fixtures pod `.demo-data/`.

## Co demo izoluje

- Compose projekt má pevný název `reawote-demo`.
- PostgreSQL používá výhradně pojmenovaný volume
  `reawote-demo-postgres-data`.
- Výchozí porty `15173`, `18000` a `18080` nekolidují s běžným vývojem.
- Worker vidí pouze `<repo>/.demo-data/materials` jako `/demo-materials` a bind
  mount je read-only z pohledu kontejneru.
- `.env.demo.example` obsahuje pouze fiktivní lokální hodnoty. Externí
  integrace jsou prázdné a služby je ani nedostávají.
- `demo-down.ps1` pouze zastaví kontejnery. Nepoužívá `down --volumes` a nemaže
  databázový volume ani fixtures.

Adresář `.demo-data/` je generovaný a ignorovaný Gitem. Skripty neprovádějí
rekurzivní mazání. Každý zápis kontroluje kanonickou cestu bezprostředně před
operací, odmítá `..` a všechny existující reparse pointy (symlink, junction i
mount point) od skutečného kořene repozitáře až k cíli. Nové adresáře vznikají
po jedné úrovni a každá úroveň se po vytvoření znovu ověří. Tento runbook
záměrně neposkytuje reset skript.

## Požadavky

- Windows PowerShell 5.1 nebo PowerShell 7;
- spuštěný Docker Desktop s Docker Compose v2;
- volné výchozí porty `15173`, `18000` a `18080`.

Volitelně lze vytvořit ignorovaný `.env.demo` pro ostatní lokální demo hodnoty:

```powershell
Copy-Item .env.demo.example .env.demo
```

Skript použije `.env.demo`, pokud existuje; jinak bezpečně použije přímo
`.env.demo.example`. Název Compose projektu, demo volume a hostitelský adresář
fixtures nelze přes tento soubor přesměrovat na běžné vývojové nebo produkční
úložiště.

Volitelné porty se zadávají pouze jako celá čísla přes `DEMO_*` proměnné:

```powershell
$env:DEMO_FRONTEND_PORT = "15173"
$env:DEMO_BACKEND_PORT = "18000"
$env:DEMO_WORKER_PORT = "18080"
```

Hodnoty musí být bez whitespace v rozsahu `1`–`65535`. Host, dvojtečka, URL,
`*` ani další text nejsou povoleny. `BACKEND_PORT`, `FRONTEND_PORT` a
`WORKER_PORT` v Compose env nejsou uživatelské vstupy; demo skripty je pro
každé volání Compose dočasně přepíší bezpečným bindem na `127.0.0.1` a původní
procesní hodnoty následně obnoví.

## Spuštění a seed

Z kořene repozitáře spusťte přesně:

```powershell
.\scripts\demo-up.ps1
.\scripts\demo-seed.ps1
.\scripts\demo-status.ps1
```

`demo-up.ps1` ještě před prvním Docker příkazem validuje všechny tři číselné
porty a výsledné URL. Následně ověří základní konfiguraci a načte sloučenou
konfiguraci jako JSON. Před `up` odmítne jakýkoli publikovaný port, jehož
`host_ip` není přesně `127.0.0.1`, neočekávané mapování portů nebo jiný worker
bind mount. Potom sestaví a spustí služby, počká na healthchecky a explicitně
provede `alembic upgrade head`.

Pokud po pokusu o `up` selže pozdější kontrola nebo migrace, `finally` provede
jen `docker compose down --remove-orphans` pro pevný projekt `reawote-demo`.
Nepoužije `-v`; demo databázový volume zůstane zachovaný a projektu `reawote`
se nedotkne.
`demo-seed.ps1` bezpečně znovu použije záznamy nalezené podle stabilních demo
klíčů. Pokud najde více shod nebo konflikt klíče, skončí chybou místo výběru
náhodného záznamu.

Seed vytvoří:

- firmu `Demo Company`;
- značku `DEMO Published Brand`, prefix `DEMO_SAFE`, identifikátor
  `demo-reawote-brand-v1`;
- projekt `DEMO-001`;
- aktivního zpracovatele `Demo Processor` s adresou v rezervované doméně
  `example.invalid`;
- tři PBR materiály a tři lokální složky s prázdným adresářem `16K`:
  - validní JSON `metadata.txt` s barvou a rozměry;
  - chybějící `metadata.txt`;
  - nesouhlasící název složky pro negativní preflight.

Konkrétní UUID, technické identity, relativní cesty a odkazy skript vypíše a
uloží do `.demo-data/demo-records.json`. Druhé spuštění seedu nevytváří další
záznamy a znovu ověří všechny tři read-only preflighty přímo na workeru i přes
veřejný backendový endpoint. Negativní případ je bezpečná existující složka;
worker ji přečte, zatímco backend správně odmítne nesoulad jejího názvu s
technickou identitou materiálu.

## URL

Při výchozí konfiguraci:

- aplikace: <http://localhost:15173>
- seznam materiálů: <http://localhost:15173/materials>
- backend Swagger UI: <http://localhost:18000/docs>
- backend health: <http://localhost:18000/health>
- worker health: <http://localhost:18080/health>

Pokud jste změnili porty v `.env.demo`, skripty vypíší odpovídající URL.

## Prezentační scénář v prohlížeči

Současný frontend zobrazuje entity, materiály, bezpečný folder preflight,
propojení složky, Done i metadata historii. Swagger UI lze ponechat otevřené
jen jako volitelný pohled na kontrakt; prezentační tok se provádí v aplikaci.

1. Otevřete URL firmy vypsané seed skriptem. Stránka ukazuje firmu, publikovanou
   značku a projekt.
2. Otevřete URL validního materiálu. Detail ukazuje projekt, značku a aktivního
   zpracovatele `Demo Processor`.
3. Na detailu validního materiálu zadejte jeho relativní cestu ze state souboru
   do pole `Relative folder path` a zvolte `Check folder`:

   ```json
   {"folder_path":"demo-library/DEMO_SAFE_0001_G03"}
   ```

   Přesná identita může být po dříve přerušeném seedu jiná; vždy použijte
   hodnotu `materials.valid.folder_path` ze state souboru. Očekávaný výsledek
   je `identity_matches=true`, `metadata_status=VALID` a `can_continue=true`.
4. Stejným UI ověřte materiál `missing_metadata`. Výsledek má
   `metadata_status=MISSING`, ale `identity_matches=true` a `can_continue=true`.
5. U materiálu `identity_mismatch` spusťte `Check folder`. Výsledek má
   `identity_matches=false`, `can_continue=false`, chybu
   `TECHNICAL_IDENTITY_MISMATCH` a neaktivní `Link folder`.
6. Vraťte se k validnímu materiálu, znovu zkontrolujte cestu a potvrďte
   `Link folder`.
7. Potvrďte `Mark as Done`. Detail ukáže stav `done`, current metadata a právě
   vytvořený immutable snapshot; raw obsah souboru se veřejně nevrací.
8. Obnovte stránku. Propojená složka, stav `done`, metadata i snapshot historie
   zůstanou zobrazené.

`mark-done` je doménově jednorázová operace a opakování správně vrací `409`.
Po již provedené prezentaci lze stále demonstrovat preflighty a zobrazit uložená
metadata i historii. Pro další živý průchod lze použít dosud nedokončený případ
`missing_metadata`; chybějící metadata jsou záměrně neblokující.

## Zastavení a opětovné spuštění

Zastavení bez smazání dat:

```powershell
.\scripts\demo-down.ps1
```

Opětovné spuštění se zachovanými daty:

```powershell
.\scripts\demo-up.ps1
.\scripts\demo-seed.ps1
```

Stejná UUID i snapshot historie zůstanou v dedikovaném volume. Nepoužívejte
`docker compose down --volumes`; pro běžný demo provoz není potřeba žádný
destruktivní reset.

## Ruční ověřovací příkazy

Demo ověření používá pouze demo skripty, oba Compose soubory, explicitní env
file a pevný název projektu `reawote-demo`:

```powershell
.\scripts\demo-tests.ps1
.\scripts\demo-up.ps1
.\scripts\demo-seed.ps1
.\scripts\demo-seed.ps1
.\scripts\demo-status.ps1
.\scripts\demo-down.ps1
.\scripts\demo-up.ps1
.\scripts\demo-seed.ps1
.\scripts\demo-status.ps1
.\scripts\demo-down.ps1
git diff --check
```

Pro kontrolu persistence spusťte `demo-down.ps1`, poté znovu `demo-up.ps1` a
`demo-seed.ps1`. Výpis a `.demo-data/demo-records.json` musí obsahovat stejná
UUID. `demo-status.ps1` skončí nenulově při chybějícím, zastaveném nebo
nezdravém kontejneru, nefunkčním health endpointu, chybějícím volume,
neočekávaném databázovém mountu nebo worker mountu bez `RW=false`. Úspěch vždy
končí zprávou `All required REAWOTE demo status and safety checks passed.`

Přesná automatická kontrola persistence a zachování volume:

```powershell
$before = Get-Content -Raw .demo-data/demo-records.json | ConvertFrom-Json
$beforeIds = @(
    $before.company.id
    $before.published_brand.id
    $before.project.id
    $before.processor.id
    $before.materials.valid.id
    $before.materials.missing_metadata.id
    $before.materials.identity_mismatch.id
)

.\scripts\demo-down.ps1
docker volume inspect reawote-demo-postgres-data --format '{{.Name}}'
.\scripts\demo-up.ps1
.\scripts\demo-seed.ps1
.\scripts\demo-status.ps1

$after = Get-Content -Raw .demo-data/demo-records.json | ConvertFrom-Json
$afterIds = @(
    $after.company.id
    $after.published_brand.id
    $after.project.id
    $after.processor.id
    $after.materials.valid.id
    $after.materials.missing_metadata.id
    $after.materials.identity_mismatch.id
)
if (Compare-Object $beforeIds $afterIds) {
    throw "Demo UUID persistence check failed."
}
Write-Host "Demo UUIDs and dedicated database volume persisted."
.\scripts\demo-down.ps1
```

`demo-status.ps1` v tomto postupu současně ověřuje, že runtime port bindings
jsou jen na `127.0.0.1`, worker mount je bind a read-only a databáze používá
výhradně `reawote-demo-postgres-data`.

### Samostatná regresní kontrola hlavního vývojového prostředí

Následující příkaz není součást izolovaného demo postupu:

```powershell
.\scripts\test.ps1
```

Používá běžný Compose projekt `reawote` a může během testů krátce spustit nebo
zastavit jeho databázovou službu. Spouštějte jej samostatně, až po kontrole
stavu hlavního vývojového prostředí. Demo skripty tento příkaz nevolají.

## Automatický browserový E2E test

Frontend již obsahuje integrované ovládání preflight/link/Done i metadata
historii. Automatický průchod nepoužívá persistentní demo projekt popsaný výše;
spouští vlastní disposable projekt `reawote-e2e` podle
[`docs/demo-e2e.md`](demo-e2e.md). Tím zůstává jednorázová operace Done
opakovatelná a `reawote-demo-postgres-data` se nemění.
