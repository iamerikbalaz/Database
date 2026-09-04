# Prompt pro převzetí vývoje v ChatGPT / Codexu

Níže uvedený text vložte jako úvodní zadání do nové instance společně s technickou specifikací, repozitářem a uvedenými vzorovými soubory.

---

Přebíráš vývoj interního systému REAWOTE pro evidenci firem, projektů a PBR materiálů a pro přípravu publikačních dávek do online knihovny.

## Povinné podklady

Než cokoli změníš, přečti celé tyto podklady:

1. `REAWOTE_technicke_zadani_databaze_a_publikace.md`
2. `readme.txt`
3. `zipovací skript textury.zip`, včetně obou souborů:
   - `converted_before 4.3.2026/rename.sh`
   - `converted_after 4.3.2026/rename.sh`
4. ukázku produkčního `metadata.json` s poli `FOLDER`, `TEXTURE_SIZE`, `COLOR`, `RESOLUTIONS`, `SOURCE` a `WARNINGS`
5. ukázku generovaného webového manifestu `metadata(2).json` s `WEB_APP_PART` a `DESKTOP_APP_PART`
6. ukázkové publikační CSV
7. celý existující repozitář, jeho README, migrační systém, testy, konfigurační soubory a případné `AGENTS.md`

Pokud některý podklad chybí, pokračuj všude, kde to jde bezpečně, a přesně popiš blokovanou část. Nevymýšlej si chování online importeru ani GCS strukturu.

## Nejdůležitější doménová pravidla

- Firma je objednatel a může mít více publikovaných značek.
- Každá publikovaná značka patří právě jedné firmě.
- Projekt patří jedné firmě a sdružuje PBR assety.
- Asset patří projektu a právě jedné publikované značce. Objednatel projektu a vlastník značky se mohou lišit.
- Každý asset má neměnné interní UUID.
- Prefix a čtyřmístná číselná řada se řídí publikovanou značkou.
- Jednou použité číslo se nikdy nepoužije znovu.
- Při změně značky se přidělí nové číslo a řízeně se přejmenuje složka, mapy i odkazy v produkčním `metadata.json`.
- `Done` znamená dokončenou práci zpracovatele. Chybějící nebo nesouhlasící metadata vytvoří varování, ale nesmějí `Done` zablokovat.
- `Published` a detailní publikační stav jsou oddělené od produkčního stavu.
- Více kategorií i více tagů se v ostrém CSV spojuje dvojtečkou `:`. V databázi zůstávají jako jednotlivé hodnoty.
- Odvozená rozlišení a ZIPy jsou dočasné. Po úspěchu i chybě se lokálně odstraní a při dalším pokusu znovu vygenerují.
- `SOURCE`, `PREVIEW`, master mapy a kořenový produkční `metadata.json` se při běžném workflow nesmí odstranit ani měnit.

## Dva odlišné `metadata.json`

Neslučuj je a nikdy jeden tiše nepřepisuj druhým:

1. Produkční `metadata.json` již existuje ve zdrojovém rootu novějších assetů a obsahuje identitu, rozměry, barvu, mapy a zdrojové odkazy.
2. Webový manifest `metadata.json` generuje ZIP worker a obsahuje `WEB_APP_PART` a `DESKTOP_APP_PART`.

Zachovej současný webový manifest na kořeni výstupního assetu a na kořeni každého ZIPu. Produkční metadata, pokud existují, zkopíruj beze změny do vnitřní složky konkrétního rozlišení vedle map:

```text
<ZIP_ROOT>/metadata.json                    # webový manifest
<ZIP_ROOT>/<RESOLUTION>/metadata.json       # produkční metadata, nově a jen pokud existují
```

Současný generátor dává do webového manifestu koncovou čárku. Nový výstup musí zachovat stejná pole a strukturu, ale musí být validní JSON a projít standardním parserem.

## Historická ZIP politika

Existují dvě větve, které musí zůstat podporované:

- `LEGACY_BEFORE_2026_03_04`: před archivací rekurzivně nastaví timestamps na `2026-01-01 00:00`;
- `CURRENT_FROM_2026_03_04`: timestamps tímto způsobem nemění.

Výchozí pravidlo: podle modification time nejvyšší zdrojové složky `nK` použij legacy větev pro čas před `2026-03-04 00:00:00` a current větev pro čas na hranici nebo po ní. Zvolenou politiku ulož k assetu a snapshotuj do každého publikačního jobu. Umožni auditovaný ruční override. Před produkčním nasazením nech vlastníka procesu potvrdit inkluzivitu data 4. 3. 2026.

## Pracovní postup

### 1. Nejprve audit, bez změn

- Zjisti použitý stack, architekturu, databázi, způsob migrací, job queue, autentizaci a testovací framework.
- Najdi všechny části pracující s názvy assetů, filesystemem, CSV, ZIPy, GCS a online importerem.
- Porovnej oba Bash skripty a zdokumentuj současnou výstupní strukturu.
- Ověř, zda existují reálné fixture adresáře nebo snapshoty ZIPů. Nic nemaž a nepřejmenovávej.
- Zkontroluj stav pracovního stromu a zachovej všechny cizí/nepříbuzné změny.
- Vrať stručný audit: co již existuje, co je použitelné, co chybí a jaké části jsou rizikové.

### 2. Potom navrhni implementační plán

Rozděl práci do malých ověřitelných změn v tomto pořadí:

1. datový model a migrace z Notion schématu;
2. atomický čítač značky a založení assetu;
3. filesystem registry a neblokující `Done` validace;
4. dry-run a transakční přejmenování/rebrand s rollbackem;
5. packaging worker s oběma historickými politikami;
6. oba typy metadat na nekolidujících cestách;
7. CSV exporter s `:` pro categories/tags;
8. GCS publikační dávky a stavový model;
9. AI content API a schvalování;
10. hardening, oprávnění, audit a provozní dokumentace.

U každé fáze uveď změněné soubory, migrace, testy, rollback a nevyřešené závislosti. Teprve potom implementuj.

### 3. Implementační pravidla

- Přizpůsob se existujícím konvencím repozitáře. Nepřepisuj celý systém bez důvodu.
- Názvy cest nikdy neskládej neescapovaným shellovým řetězcem.
- Všechny cílové cesty kanonikalizuj a ověř, že zůstávají v povoleném rootu.
- Pro změny identity používej dry-run, exkluzivní lock, collision check, dočasné názvy, journal a rollback.
- Pro mutující API používej idempotency keys.
- Zdrojová data jsou read-only s výjimkou uživatelem potvrzeného rename/rebrand workflow.
- Neodhaduj vztah projektu k firmě. Současná Notion `Projects` databáze jej nemá.
- Neodhaduj párování existujícího online assetu. Dokud není prozkoumán importer, blokuj pouze tuto konkrétní aktualizační operaci, ne ostatní vývoj.
- Zachovej přesně devět polí ostrého CSV a jejich pořadí.
- Ukládej categories/tags jako seznamy nebo relace; `:` použij pouze při exportu.
- Nikdy neloguj GCS secrets.
- Všechny změny identity, schválení, manuální overrides a publikační pokusy audituj.

## Povinné testy

Vytvoř automatické testy minimálně pro:

- souběžnou alokaci čísel jedné značky;
- zákaz opětovného použití čísla;
- `Done` s validním, chybějícím, nevalidním a nesouhlasícím produkčním JSON;
- zachování ručně přepsané hodnoty;
- rename dry-run bez mutace;
- rename/rebrand s uměle vyvolaným selháním a rollbackem;
- asset před hranicí a asset na/po hranici 4. 3. 2026;
- přesnou legacy normalizaci timestampů;
- validitu generovaného webového manifestu;
- přítomnost produkčních metadat ve všech ZIPech, pokud zdroj existuje;
- absenci falešných metadat a pouze warning, pokud zdroj neexistuje;
- zabránění kolizi obou `metadata.json`;
- CSV s jednou i více kategoriemi a tagy;
- dvojtečku uvnitř jedné hodnoty jako validační chybu;
- středník, uvozovky, nový řádek a diakritiku v CSV;
- cleanup dočasného workspace po úspěchu i chybě;
- stav `PUBLISHED_UPDATE_REQUIRED` po změně již publikovaného assetu.

Pro ZIP část vytvoř golden fixtures pro obě historické větve. Porovnávej strukturu, názvy, obsah manifestů, kontrolní sumy relevantních souborů a timestamps, nikoli pouze exit code.

## Co máš průběžně vracet

Po každé fázi napiš:

- co bylo dokončeno;
- které soubory a migrace byly změněny;
- jaké testy proběhly a s jakým výsledkem;
- co zůstává blokované a jaký přesný podklad je potřeba;
- jak lze změnu bezpečně vrátit.

Nevydávej práci za hotovou, dokud neproběhnou relevantní testy a není doložen skutečný výstup. Pokud narazíš na rozpor mezi technickou specifikací a existujícím produkčním chováním, zastav pouze dotčenou změnu, ukaž konkrétní důkaz a navrhni nejmenší kompatibilní řešení.

## První úkol v nové instanci

Začni pouze read-only auditem repozitáře a všech uvedených podkladů. Poté vytvoř implementační plán podle výše uvedených fází. Zatím neměň produkční data, GCS, Notion ani online knihovnu.

---

