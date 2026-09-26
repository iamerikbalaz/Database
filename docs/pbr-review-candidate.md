# PBR: přehled verze k review

Pracovní větev: `codex/autonomous-pbr-completion`. Základ je skutečný
`origin/main` na `88a1f99d748d2a0edbb1fce509e13d18bfc03908`; poslední porovnání
neukázalo změnu main. Větev nevychází z odstraněného experimentálního auth UI.
Přesný poslední checkpoint, běhy a image identity jsou v
[průběžném záznamu](autonomous-pbr-progress.md).

Jde o verzi pro review a ověření konkrétních PBR kontraktů v izolovaném prostředí.
Produkční nasazení, živé integrace a správnost vůči skutečnému online importéru
nejsou potvrzené. Žádný merge do main ani nasazení neproběhlo.

## Nová verze: projekty, firmy, katalog a ZIP/CSV (26. 9.)

Materials vyhledává i poznámky, detail rovnou ukazuje náhled a nabídka publikace
pracuje s aktuálně filtrovanými nebo vybranými materiály přímo v Materials.
Projects a Companies mají filtry, přímé editace a potvrzované hromadné změny.
Catalog má taby kategorií/kolekcí, zkratky, data vytvoření a filtry; přímo lze měnit
zkratku a Active. Stabilní názvy mají výslovnou akci vytvoření náhradního záznamu.

Do vlastní testovací databáze je importováno **251 projektů s cestami na R:**.
**14 složek** vyžaduje ruční kontrolu názvu/duplicit. Původní materiály zůstaly bez
projektu. Vzniklo 109 firem podle výrobců pro testování rozhraní a 18 značek je mezi
nimi rozděleno; právní údaje nejsou doplněné odhadem.

ZIP/CSV lze zkoušet na odděleném materiálu vyhledatelném přes **#zip-test**.
Jeho skutečně vytvořený ZIP a CSV prošly kontrolou obsahu a otisků. Historie dávky
je v Materials → Publication history. Toto ověřuje lokální syntetické zdroje;
napojení živého read-only workeru na skutečná NAS data a finální online importér
zbývá ověřit. Nahrávání je vypnuté. NAS ani původních 100 materiálů se neměnily.

## Nová verze: editovatelný seznam materiálů (25. 9.)

Materials nyní obsahuje miniatury, přímé editace v řádku, volbu a šířky sloupců,
výběr všech vyfiltrovaných záznamů a hromadné změny s výsledkem každé položky.
Jednotný Status doplňuje Checked (no / OK / Correction), binární Published a Note.
Kategorie mají názvy a kódy z dodaného Excelu. Podrobná pravidla a hranice popisuje
[nový přehled tabulky](material-table.md); výsledky testů uvádí nejnovější checkpoint.

Testovací databáze se 100 materiály byla před migrací zálohována. Kontrola otisků
potvrdila zachování všech dřívějších řádků včetně uživatelských změn. Katalog má
75 dodaných kategorií a jednu původní položku. Soubory na NAS se neměnily.
Na R100 zatím chybí živý worker: běžné databázové editace fungují, ale přechod
Done a plán změny identity propojené složky stále vyžadují jeho bezpečné připojení.

## Doplnění: test 100 skutečných materiálů (25. 9.)

Rozhraní nově používá [dodanou vizuální identitu REAWOTE](brand-interface.md):
originální logo, lokální Poppins, světlé panely a modro-levandulovou paletu.
Změna zahrnuje celou společnou navigaci, formuláře, tabulky, galerii a přihlášení.
Nejnovější testy této změny jsou v prvním checkpointu průběžného záznamu.

Byla připravena podmnožina **100 položek, 18 výrobců a 7 kategorií** se shodou
Excelu a složek na uživatelem určeném disku R:. V nové izolované databázi jsou
jejich původní identity a cesty, bez projektu. Prošlo opakování importu bez
duplicit, restart PostgreSQL a zobrazení/vyhledávání v prohlížeči. NAS se neměnil.
Nové názvy zahrnují název materiálu; historické názvy zůstávají přesně zachovány.
Migrace 0026 dovoluje historické záznamy bez projektu; pozdější přiřazení je možné.

Plný frontend: **951 prošlo**, PostgreSQL: **464 prošlo** (auth 27/27), plný Linux
worker/packaging: **822 prošlo**, browser: **24 + 24 po restartu**. Podrobný rozsah
a opravované nálezy uvádí [checkpoint](autonomous-pbr-progress.md).

Galerie je nyní přímo v Materials: přepínání seznam/galerie, čtyři velikosti,
malé popisky a šipky pro další PNG. Přednost má FABRIC_1.png, potom SPHERE_1.png.
Samostatné porovnání dvou materiálů bylo odstraněno podle upřesnění uživatele.
Testovací instance má 280 skutečných PNG v datované lokální kopii pro 95 materiálů;
zbývajících pět nemá PNG/PREVIEW. To umožňuje vizuální ověření bez zápisu na NAS.
Prošlo vykreslení všech 95 náhledů, šipky, čtyři velikosti, filtry i mobilní zobrazení.
Závěrečný browser běh celé aplikace prošel **24 + 24 po restartu**; dvě dřívější
nestabilní chyby mimo galerii a přesná měření lokální kopie zaznamenává checkpoint.

**Omezení:** další vlastnosti Excelu dosud nejsou převzaté; Docker zatím nemá
živý přístup k R: pro aktualizaci náhledů a kontrolu textur. Přejmenování názvu současně
se složkou a vytváření fyzických složek u výrobce je další implementační úkol.
Testovací aplikace proto nemá povolené zápisy do zdrojů. Aktuální stav,
výběrová pravidla a následující kroky jsou v [přehledu testu](historical-r100-acceptance.md).

Následující tabulka popisuje předchozí review kandidát; výše uvedené doplnění
nahrazuje jeho dřívější tvrzení o chybějícím historickém testovacím datasetu.

## Co lze ověřit

| Oblast | Implementované chování | Hranice ověření |
| --- | --- | --- |
| Úvodní přehled | Dashboard s počty dostupných materiálů, filtry stavů, hledáním, stránkováním a odkazy podle role | Jedno čtení současného seznamu; stav Done neznamená schválení/publikaci. Velké produkční objemy nejsou ověřené. |
| Účty a přístup | Session, CSRF, role/přidělení, povinná změna hesla, správa účtů a obnova přístupu, nové auth UI | Skutečné session a PostgreSQL souběhy; produkční HTTPS/reverse proxy není nasazená. |
| Materiály a zdroje | Propojení složky, Done, inventář, technická kontrola, oddělená schválení, reopen a invalidace | Syntetické soubory a obrázky; reálný NAS se neměnil. |
| Identita a historie | Řízené změny identity, obnova přerušených filesystemových operací, archivace/obnova, audit a stránkování | Izolované Linux roots, databázové závody a browser restart; produkční úložiště nebylo testovací cíl. |
| Obsah a import | Kategorie, kolekce, verzovaný obsah, CSV/XLSX import, seznam/galerie s filtry | 100 historických záznamů a cest v izolované DB; datované náhledy 95 materiálů. Další Excel vlastnosti a živé NAS připojení zbývají. |
| Publikační příprava | Schválené neměnné CSV, ZIP pravidla, lokální packaging, řízený start/obnova/uzavření a stahování podle proof | Skutečný converter, HTTP, Linux recovery a download SHA-256; skutečný online import není ověřen. |
| Lokální úklid | Ověřený úklid dočasné práce/neúplných kopií, samostatné ADMIN odstranění přijaté kopie, trvalé potvrzení odstranění | Skutečný browser/worker, ztracená odpověď a restart; odstranění je ve výchozím stavu vypnuté. |
| GCS | Konfigurovatelný transport, řízené staging joby, oprávnění, potvrzený obsah, obnovitelné požadavky a UI | Offline kontrakty; browser používá vypnuté GCS. Žádné skutečné cloudové objekty nebyly zapsané. |
| Notion a AI | Konfigurovatelné čtení Notion, porovnání a selektivní místní převzetí, historie AI návrhů/adopce a oddělený volitelný generátor | Syntetické kontrakty; žádný živý Notion zápis ani skutečné volání AI poskytovatele. |
| Obnova běžných formulářů | Atomický záznam úspěšného uložení, opakování stejného požadavku a čtení výsledku po ztrátě odpovědi | Reálné účty/role a browser scénáře; obnovu chrání aktuální serverové oprávnění. |

## Předchozí ověření před importem R: vzorku

- PostgreSQL, 19. 9.: **463 prošlo**, včetně **27/27 auth**, bez přeskočených testů.
- API odstranění a stahování, 19. 9.: **48 prošlo**, včetně dokončení již otevřeného přenosu.
- Frontend, 25. 9.: celá sada **949 prošlo**; po oddělení načítání Dashboardu
  **53 relevantních testů prošlo**, lint, build a kontrola E2E typů jsou zelené.
- Skutečný browser, 25. 9.: **24/24 na čistých datech + 24/24 po restartu**, bez přeskočení.
  Čtyři nové snímky Dashboardu na desktopu/mobilu byly zkontrolovány; na 390 px nic nepřetéká.
- Linux worker, jeho souběhy, skutečné pády procesů, HTTP a privátní runtime mají
  samostatně doložené běhy v [checkpointu](autonomous-pbr-progress.md). Rozsahy
  testů a opravená chybná očekávání jsou zaznamenané bez slučování do jednoho
  neověřitelného souhrnného počtu.

Závěrečný browser běh: `ae824fd0-546e-405c-aa80-75b66baec976`. Kontrola chráněných
prostředků prošla, vlastní kontejnery a sítě byly odstraněny a volumes zachovány.
API checkpoint je `1ed3b44`, odstranění lokální kopie/UI je v `776a36a`.
Na předání `8d86c72` navazuje [Dashboard](production-dashboard.md); jde o frontendovou
změnu bez nové migrace. Samostatné PostgreSQL/Linux sady se pro ni neopakovaly.
Při uzavření tohoto dřívějšího checkpointu neběžely testovací ani vývojové úlohy.

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

Main obsahuje migrace 0001–0006; pracovní větev přidává 0007–0026.
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
- Kompletní Excel pro pozdější ostrý import. Současný workbook už poskytl první
  stovku skutečných záznamů; zbývá přístup workeru k R: a mapování dalších vlastností.
- Samostatně povolené testovací cíle a přístupy pro živé GCS/Notion/AI ověření.
  Tajné údaje nepatří do chatu, fixture, manifestu, screenshotu ani commitu.
- Produkční topologie, požadavky na výkon a samostatně schválený postup nasazení
  a obnovy. Vícegigabajtové přenosy a provozní objemy nejsou potvrzené.

**První doporučený další úkol:** připojit izolovaný worker k R: pouze pro čtení
a ověřit živou aktualizaci náhledů a textury u připravené stovky materiálů.
3D modely a HDRI zůstávají mimo tento PBR rozsah. Draft PR nebyl vytvořen; GitHub
CLI ani přímý GitHub konektor nebyly dostupné. Vlastní vzdálená větev slouží jako
podklad k review.
