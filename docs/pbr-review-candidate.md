# PBR: přehled verze k review

Pracovní větev: `codex/autonomous-pbr-completion`. Základ je skutečný
`origin/main` na `88a1f99d748d2a0edbb1fce509e13d18bfc03908`; poslední porovnání
neukázalo změnu main. Větev nevychází z odstraněného experimentálního auth UI.
Přesný poslední checkpoint, běhy a image identity jsou v
[průběžném záznamu](autonomous-pbr-progress.md).

Jde o verzi pro review a ověření konkrétních PBR kontraktů v izolovaném prostředí.
Produkční nasazení, živé integrace a správnost vůči skutečnému online importéru
nejsou potvrzené. Žádný merge do main ani nasazení neproběhlo.

## Co lze ověřit

| Oblast | Implementované chování | Hranice ověření |
| --- | --- | --- |
| Účty a přístup | Session, CSRF, role/přidělení, povinná změna hesla, správa účtů a obnova přístupu, nové auth UI | Skutečné session a PostgreSQL souběhy; produkční HTTPS/reverse proxy není nasazená. |
| Materiály a zdroje | Propojení složky, Done, inventář, technická kontrola, oddělená schválení, reopen a invalidace | Syntetické soubory a obrázky; reálný NAS se neměnil. |
| Identita a historie | Řízené změny identity, obnova přerušených filesystemových operací, archivace/obnova, audit a stránkování | Izolované Linux roots, databázové závody a browser restart; produkční úložiště nebylo testovací cíl. |
| Obsah a import | Kategorie, kolekce, verzovaný obsah, CSV/XLSX import, galerie a porovnání | Skutečné syntetické workbooky, PNG a browser; reprezentativní historická data chybějí. |
| Publikační příprava | Schválené neměnné CSV, ZIP pravidla, lokální packaging, řízený start/obnova/uzavření a stahování podle proof | Skutečný converter, HTTP, Linux recovery a download SHA-256; skutečný online import není ověřen. |
| Lokální úklid | Ověřený úklid dočasné práce/neúplných kopií, samostatné ADMIN odstranění přijaté kopie, trvalé potvrzení odstranění | Skutečný browser/worker, ztracená odpověď a restart; odstranění je ve výchozím stavu vypnuté. |
| GCS | Konfigurovatelný transport, řízené staging joby, oprávnění, potvrzený obsah, obnovitelné požadavky a UI | Offline kontrakty; browser používá vypnuté GCS. Žádné skutečné cloudové objekty nebyly zapsané. |
| Notion a AI | Konfigurovatelné čtení Notion, porovnání a selektivní místní převzetí, historie AI návrhů/adopce a oddělený volitelný generátor | Syntetické kontrakty; žádný živý Notion zápis ani skutečné volání AI poskytovatele. |
| Obnova běžných formulářů | Atomický záznam úspěšného uložení, opakování stejného požadavku a čtení výsledku po ztrátě odpovědi | Reálné účty/role a browser scénáře; obnovu chrání aktuální serverové oprávnění. |

## Poslední ověření

- PostgreSQL: **463 prošlo**, včetně **27/27 auth**, bez přeskočených testů.
- API odstranění a stahování: **48 prošlo**, včetně dokončení již otevřeného přenosu.
- Frontend: celá sada **935 prošlo**; po závěrečném oddělení načítání komponenty
  **64 relevantních testů prošlo**, lint, build a kontrola E2E typů jsou zelené.
- Skutečný browser: **23/23 na čistých datech + 23/23 po restartu**, bez přeskočení.
  Šest snímků odstranění na desktopu/mobilu bylo zkontrolováno; na 390 px nic nepřetéká.
- Linux worker, jeho souběhy, skutečné pády procesů, HTTP a privátní runtime mají
  samostatně doložené běhy v [checkpointu](autonomous-pbr-progress.md). Rozsahy
  testů a opravená chybná očekávání jsou zaznamenané bez slučování do jednoho
  neověřitelného souhrnného počtu.

Závěrečný browser běh: `d2817df1-6b1b-4262-a6b2-9eb6f9c682af`. Kontrola chráněných
prostředků prošla, vlastní kontejnery a sítě byly odstraněny a volumes zachovány.
API checkpoint je `1ed3b44`, UI/E2E checkpoint `776a36a`; tento dokument je navazující
předávací záznam. Pro tuto verzi nyní neběží žádná testovací ani vývojová úloha na pozadí.

## Doporučené pořadí review

1. [Autentizace](auth-backend-foundation.md), [autorizace](authorization.md),
   [obnova formulářů](resource-commands.md) a chování při odebrání přístupu.
2. Neměnné inventáře, schválení a identity; porovnat
   [workflow](material-approvals.md) s reprezentativním pracovním postupem týmu.
3. [CSV dávky](publication-batches.md), [packaging](packaging-actions.md),
   [stahování](packaging-downloads.md) a
   [odstranění lokální kopie](packaging-retirement-api.md).
4. [GCS](gcs-staging-controls.md), [Notion](notion-adoption.md),
   [historický import](historical-import.md) a skutečné chybějící vstupy níže.
5. Nasazovací konfigurace, migrace, obnova zálohy a teprve potom samostatné
   rozhodnutí o merge/nasazení. Bezpečnostní kontroly testů nevypínat.

## Bezpečné zopakování ověření

Pracovní kopie pro tento běh je
`C:\Database\Database\tmp\autonomous-pbr-completion`. Původní checkout se
nemění. Z této kopie lze použít:

```powershell
.\scripts\test.ps1 -PostgresqlOnly
.\scripts\test-packaging.ps1
.\scripts\test-packaging-service.ps1
```

Pro browser se musí zachovat vlastní jmenný prostor:

```powershell
$env:E2E_PROJECT_NAME = 'reawote-e2e-auto-01a0a64d'
$env:E2E_KEEP_SUCCESS_ARTIFACTS = '1'
.\scripts\test-demo-e2e.ps1
```

Spouštět jednotlivě, s dostupným lokálním Docker Desktop CLI; browser nekombinovat
s jinou mutací jeho prostředků. Runner vytváří vlastní syntetické zdroje a
ověřuje skutečný endpoint, mounty, vlastníka volumes a chráněné prostředky.
Přímé spuštění Playwrightu nemá capability manifest a musí být odmítnuté.
[Podrobný postup](demo-e2e.md) vysvětluje restart se zachovanými daty a výsledky.

Frontend z adresáře `frontend`: `npm.cmd test`, `npm.cmd run lint`,
`npm.cmd run build`; E2E typy ověřuje
`node_modules/.bin/tsc --project tsconfig.e2e.json --noEmit`.
Výsledek každé sady má svůj rozsah: adaptér s umělým potvrzením neprokazuje
skutečné vytvoření ani odstranění souboru a zelené testy neprokazují úplnost produktu.

## Migrace, provoz a návrat

Main obsahuje migrace 0001–0006; pracovní větev přidává 0007–0025.
Existující migrace zůstávají neměnné. Čistý upgrade, upgrade z předchozího schématu,
Alembic current/heads/check a relevantní souběhy ověřují izolované PostgreSQL testy.
Nespouštět nový backend nad existující databází jen kvůli ukázce: startup provádí
`alembic upgrade head`. Produkční migrace vyžaduje samostatné nasazovací rozhodnutí.

Nové API pro odstranění potřebuje schéma 0025 a kompatibilní packaging službu.
Obě strany mají samostatně vypnutý `PACKAGING_RETIREMENT_ENABLED`. Při zapínání
musí mít shodný kontrakt a zachované privátní journals/artefakty. Vypnutí flagu
zachová historii a blokování dalších stažení již vyřazené kopie; nevrací smazaná data.
Naplněný retirement ledger odmítá downgrade. Starší worker nesmí nahradit nový
trvalý záznam odstranění původním READY záznamem. Při potížích zachovat důkazy a opravit kompatibilní
verzi; návrat k libovolnému staršímu kódu není bezpečný databázový rollback.

Zdrojový NAS, původní/demo databáze, obnovovací prostředky a záloha
`C:\REAWOTE-Backups\REAWOTE_2026-09-15_10-13-36` zůstaly nedotčené.
Kopie zálohy mimo počítač dosud není potvrzená. Testovací volumes se uchovávají;
nepoužívat plošný Docker prune ani hromadné mazání těchto prostředků.

## Konkrétní zbývající vstupy

- Skutečný kontrakt online importéru a jedno referenční PBR zadání s očekávaným
  CSV, manifestem, ZIP strukturou a výsledkem importu. Bez toho nelze potvrdit
  kompatibilitu ani zavést automatické označení materiálu jako publikovaného.
- Pravidla a ověřitelné potvrzení dokončeného online importu pro ruční potvrzení
  publikace a případný automatický úklid po importu. Lokální odstranění tuto
  informaci nenahrazuje.
- Reprezentativní historický workbook a zdrojová složka pro porovnání skutečných
  dat s implementovanými konzervativními importními pravidly.
- Samostatně povolené testovací cíle a přístupy pro živé GCS/Notion/AI ověření.
  Tajné údaje nepatří do chatu, fixture, manifestu, screenshotu ani commitu.
- Produkční topologie, požadavky na výkon a samostatně schválený postup nasazení
  a obnovy. Vícegigabajtové přenosy a provozní objemy nejsou potvrzené.

**První doporučený další úkol:** předat jeden referenční PBR materiál a konkrétní
testovací importér, pak ověřit skutečný výstup od CSV/ZIP až po potvrzení importu.
3D modely a HDRI zůstávají mimo tento PBR rozsah. Draft PR nebyl vytvořen, protože
v tomto prostředí není dostupný nástroj pro jeho vytvoření; vlastní vzdálená větev
slouží jako podklad k review.
