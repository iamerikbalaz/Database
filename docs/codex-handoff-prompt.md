# REAWOTE – technické zadání interní databáze PBR materiálů a publikačního procesu

Verze zadání: 0.9  
Datum: 3. 9. 2026  
Stav: připraveno k technickému rozpracování; otevřené body jsou uvedeny na konci

## 1. Cíl

Vytvořit interní systém, který propojí:

- firmy, publikované značky a projekty;
- produkční evidenci PBR materiálů;
- bezpečnou správu názvů složek a map;
- načítání produkčního `metadata.json` při dokončení materiálu;
- generování publikačního CSV;
- spuštění existujícího Bash procesu pro vytvoření nižších rozlišení a ZIPů;
- nahrání výsledné dávky na Google Cloud Storage (GCS), odkud ji současná online administrace importuje do knihovny REAWOTE;
- evidenci publikačního stavu a historie změn.

Systém nesmí narušit současnou konvenci názvů ani strukturu balíčků. Se zdrojovými daty smí manipulovat pouze při výslovně potvrzeném přejmenování materiálu, změně hlavní kategorie, změně publikované značky/výrobce nebo volitelném přesunu celé složky.

## 2. Podklady a zdroje pravdy

### 2.1 Notion

Struktura firem byla načtena z databáze [Customers database](https://app.notion.com/p/276a8a19ae7b80aba126f25514c2a8b2?pvs=204) pod stránkou [Customers & companies](https://app.notion.com/p/277a8a19ae7b8086a704f0287fd14ef2?pvs=204).

Struktura projektů byla načtena z databáze [Projects](https://app.notion.com/p/ecc508db1de64c248194190a723b1319?pvs=204) a jejího [Project Template](https://app.notion.com/p/7b5e8bd1ce89480785b9c97ee8054353?pvs=204).

### 2.2 Přiložené technické podklady

- `zipovací skript textury.zip`
  - `converted_before 4.3.2026/rename.sh`
  - `converted_after 4.3.2026/rename.sh`
- `readme.txt`
- produkční `metadata.json`, které vzniká po dokončení novějšího materiálu;
- `metadata(2).json`, tj. příklad webového manifestu vytvářeného ZIP skriptem;
- ukázkové publikační CSV analyzované v předchozím kroku.

## 3. Terminologie

| Pojem | Význam |
|---|---|
| Firma | Obchodní objednatel projektu. Může mít jednu či více publikovaných značek. |
| Publikovaná značka | Značka, pod kterou je asset publikován v REAWOTE. Patří právě jedné firmě. |
| Projekt | Zakázka patřící jedné firmě; sdružuje PBR assety. |
| PBR asset / materiál | Jeden publikovatelný materiál a jedna fyzická kořenová složka. |
| Hlavní úložná kategorie | Kategorie tvořící technickou identitu a název složky, například `G03`. |
| Online kategorie | Jedna nebo více kategorií exportovaných v poli `categories` publikačního CSV. |
| Produkční metadata | Dříve dodaný `metadata.json` s identitou, barvou, fyzickým rozměrem textury, mapami a zdroji. |
| Webový manifest | `metadata.json` generovaný ZIP skriptem s `WEB_APP_PART` a `DESKTOP_APP_PART`. |
| Master | Nejvyšší dostupné rozlišení map, například `16K`. |
| Odvozená data | Nižší rozlišení, dočasné adresáře a ZIPy vytvořené pro publikační dávku. |

## 4. Potvrzené doménové vztahy

1. Jedna firma může mít více projektů.
2. Jedna firma může mít více publikovaných značek.
3. Každá publikovaná značka patří právě jedné firmě.
4. Jeden projekt patří právě jedné firmě.
5. Jeden PBR asset patří právě jednomu projektu.
6. Jeden PBR asset je publikován právě pod jednou značkou.
7. Objednatel projektu a firma vlastnící publikovanou značku se mohou lišit; systém jejich shodu nesmí vynucovat.
8. Jedna fyzická kořenová složka odpovídá právě jednomu PBR assetu.
9. Prefix složky i čtyřmístná číselná řada se řídí publikovanou značkou, nikoli objednatelem projektu.

## 5. Datový model

Databázové názvy níže jsou doporučené. Lze je přizpůsobit existujícímu technologickému stacku, ale musí zůstat zachované vztahy, unikátní omezení a auditní historie.

### 5.1 `companies`

| Pole | Typ / pravidlo | Zdroj |
|---|---|---|
| `id` | UUID, neměnné, PK | nový systém |
| `notion_page_id` | nullable, unique | Notion URL/ID |
| `name` | text, povinné | Notion `Name` |
| `official_name` | text, nullable | `Official name` |
| `status` | enum `LIBRARY`, `TEST`, `ACTIVE` | `Status` |
| `description` | text, nullable | `Company describtion` |
| `website_url` | URL, nullable | `URL` |
| `vat_id` | text, nullable | `VAT ID` |
| `address` | text, nullable | `Address` |
| `shipping_address` | text, nullable | `Shipping address` |
| `notes` | text, nullable | `Notes` |
| `rwt_category` | nullable | Notion `RWT Categories` |
| `created_at`, `updated_at` | timestamp | systém |

Poznámka: překlep `Company describtion` z Notionu se do interního schématu nepřenáší; cílové pole se jmenuje `description`.

### 5.2 `company_contacts`

Notion dnes drží jen jednu sadu kontaktních polí na řádku firmy. Nový systém ji normalizuje, aby firma mohla mít více kontaktů.

| Pole | Typ / pravidlo | Zdroj |
|---|---|---|
| `id` | UUID, PK | systém |
| `company_id` | FK `companies`, povinné | vztah |
| `name` | text, nullable | `Contact Person` |
| `position` | text, nullable | `Contact position` |
| `email` | email, nullable | `Email` |
| `phone` | text, nullable | `Phone` |
| `is_primary` | boolean | při migraci `true` |

Při migraci vznikne kontakt jen tehdy, pokud je vyplněno alespoň jedno kontaktní pole.

### 5.3 `company_product_types`

Vícenásobné hodnoty z Notion `Product type`: `FABRICS`, `WOOD`, `CONFIGURATOR`, `ARCHITECT`, `FURNITURE`, `RETAIL STORE`, `FACADE SYSTEMS`, `CARPETS`, `STONE`, `TILES`, `METAL`, `PLASTERS`, `LAMINATE`.

Uložit normalizovaně nebo jako podporovaný array typ. Nevytvářet jeden text spojený dvojtečkou; dvojtečka je až exportní formát CSV.

### 5.4 `published_brands`

| Pole | Typ / pravidlo |
|---|---|
| `id` | UUID, PK |
| `company_id` | FK `companies`, povinné |
| `name` | text, povinné |
| `folder_prefix` | text, povinné, stabilní; například `SWISSPEARL` |
| `brand_identifier` | text, povinné, unique; hodnota pro publikační CSV |
| `next_asset_number` | integer nebo samostatný atomický čítač |
| `active` | boolean |
| `online_brand_id` | nullable; doplnit, pokud jej online API poskytuje |
| `created_at`, `updated_at` | timestamp |

Migrační pravidlo: neprázdné Notion pole `Brand Identifier` se neponechá jako jediný atribut firmy. Vytvoří se počáteční publikovaná značka propojená s firmou. Její jméno bude bez dalšího podkladu shodné s `companies.name`. Více značek lze firmě doplnit později.

### 5.5 `projects`

| Pole | Typ / pravidlo | Zdroj |
|---|---|---|
| `id` | UUID, neměnné, PK | systém |
| `notion_page_id` | nullable, unique | Notion |
| `company_id` | FK `companies`, povinné pro produkční projekt | nové pole |
| `name` | text, povinné | Notion `Name` |
| `status` | enum `NOT_STARTED`, `IN_PROGRESS`, `DONE` | Notion `Status` |
| `area_notion_ref` | nullable legacy reference | Notion `Area` |
| `quick_access_visible` | boolean | `Quick access visible` |
| `archived` | boolean | `Archive` |
| `overview` | text, nullable | obsah `Project Overview` v šabloně |
| `created_at`, `updated_at` | timestamp | systém |

Notion navíc eviduje vztahy `Tasks` a `Notes`; projektová šablona obsahuje `Project Overview`, vložené databáze `Tasks`, `Notes` a `Duties`. Pokud již nový systém má vlastní modul úkolů a poznámek, zachovat vazby přes spojovací tabulky nebo pouze Notion reference.

Důležité: současná Notion databáze `Projects` nemá vztah na firmu. `company_id` se proto při migraci nesmí odhadovat podle názvu. Musí být doplněn mapovacím importem nebo ručním potvrzením.

### 5.6 `assets`

| Pole | Typ / pravidlo |
|---|---|
| `id` | UUID, neměnné, PK |
| `project_id` | FK `projects`, povinné |
| `published_brand_id` | FK `published_brands`, povinné při založení složky |
| `asset_number` | integer zobrazený jako čtyři číslice |
| `product_name` | text, povinné; veřejný i technický zdroj názvu dle stávající logiky |
| `storage_category_code` | text, povinné; například `G03` |
| `folder_name` | text, povinné, unikátní |
| `folder_path` | text, povinné, unikátní |
| `assigned_processor_id` | FK user, povinné při založení |
| `created_by_id` | FK user, povinné |
| `production_status` | viz kapitola 8 |
| `validation_status` | viz kapitola 8 |
| `master_resolution` | odvozeno automaticky z nejvyšší složky `nK` |
| `width_cm`, `height_cm` | decimal, nullable |
| `hex_color` | text `#[0-9A-Fa-f]{6}`, nullable |
| `credits` | integer, nullable do předpublikační kontroly |
| `description` | text, nullable |
| `content_status` | `EMPTY`, `AI_DRAFT`, `MANUAL_DRAFT`, `APPROVED` |
| `source_metadata_present` | boolean |
| `source_metadata_hash` | text, nullable |
| `source_metadata_snapshot` | JSON/JSONB, nullable |
| `source_revision_hash` | text, hash master dat a relevantních metadat |
| `packaging_policy` | enum dle kapitoly 11 |
| `created_at`, `updated_at` | timestamp |

Unikátní omezení:

- `(published_brand_id, asset_number)` je unikátní;
- `folder_name` je unikátní v rámci příslušného úložného rootu;
- jednou přidělené číslo se nikdy nevrací do zásoby, ani po smazání či přesunu assetu.

### 5.7 Další tabulky

| Tabulka | Účel |
|---|---|
| `asset_number_ledger` | Atomické rezervace čísel a prevence opětovného použití. |
| `collections` + `asset_collections` | Značkové kolekce; asset může být ve více kolekcích. |
| `online_categories` + `asset_online_categories` | Vícenásobné publikační kategorie. |
| `tags` + `asset_tags` | Tagy jako jednotlivé hodnoty; dvojtečka se použije až při CSV exportu. |
| `asset_identity_history` | Předchozí značky, čísla, názvy, cesty, `identity_name`, důvod změny, osoba a čas. |
| `asset_approvals` | Technické a publikační schválení konkrétní `source_revision_hash`. |
| `publication_jobs` | Jedna dávka generování, ZIPování, CSV a uploadu. |
| `publication_job_assets` | Přesný seznam assetů a revizí v dávce, výsledek každého assetu. |
| `file_operations` | Audit přejmenování, přesunů a rollbacků. |
| `ai_content_drafts` | Návrhy popisů/tagů, zdroje, model, čas, schválení. |

## 6. Technická identita a názvy

Podle dodaného produkčního `metadata.json` se používá tato logika:

```text
FOLDER       = <PREFIX>_<NNNN>_<PRODUCT-NAME>_<CATEGORY>
BASE_NAME    = <PREFIX>_<NNNN>_<PRODUCT-NAME>
map file     = <BASE_NAME>_<MAP_SHORTCUT>_<RESOLUTION>.<ext>
identity_name pro CSV = musí převzít přesnou současnou logiku importeru
```

Příklad:

```text
SWISSPEARL_0001_4111-REFLEX-CRYSTAL_G03
SWISSPEARL_0001_4111-REFLEX-CRYSTAL_COL_4K.jpg
```

Interní UUID assetu je jediná neměnná identita. `folder_name`, číslo, značka, kategorie i `identity_name` jsou verzované obchodní/technické identity.

Před implementací aktualizace již publikovaného assetu je nutné ověřit zdrojový kód online CSV importeru. Současné CSV neobsahuje `online_asset_id`; importer proto může párovat podle `identity_name`. Bez tohoto ověření se nesmí automaticky měnit identita publikovaného online assetu.

## 7. Založení assetu

Povinné údaje při založení a vytvoření fyzické složky:

- projekt;
- publikovaná značka;
- název materiálu;
- hlavní úložná kategorie;
- zpracovatel.

Postup:

1. V databázové transakci atomicky rezervovat další dosud nepoužité číslo značky.
2. Sestavit technický název a ukázat jej uživateli.
3. Zkontrolovat kolize databáze i souborového systému.
4. Vytvořit záznam assetu.
5. Volitelně vytvořit kořenovou složku a očekávané podsložky.
6. Při selhání tvorby složky zaznamenat chybu; rezervované číslo nevracet do oběhu.

## 8. Stavový model

### 8.1 Produkční stav

Doporučené hodnoty:

- `NOT_STARTED`
- `IN_PROGRESS`
- `DONE`

`DONE` znamená pouze „zpracovatel dokončil práci“. Není zárukou technické správnosti ani připravenosti k publikaci.

### 8.2 Validační stav

- `VALID`
- `WITH_WARNINGS`
- `METADATA_MISSING`
- `ERROR_REQUIRING_FIX`

Chybějící nebo nesouhlasící produkční `metadata.json` nikdy neblokuje přechod do `DONE`. Systém zobrazí konkrétní varování a pokračování výslovně dovolí.

### 8.3 Schválení

Oddělit:

- technické schválení;
- schválení k publikaci.

Každé schválení odkazuje na konkrétní `source_revision_hash`, osobu, čas a volitelnou poznámku. Změna relevantních souborů nebo metadat obě schválení aktuální revize zneplatní.

### 8.4 Publikace

Povinná pole:

- `is_published` – systémově spravovaný boolean říkající, zda online verze existuje;
- `publication_status`:
  - `NOT_PUBLISHED`
  - `PREPARING`
  - `UPLOADED_WAITING_FOR_CSV_IMPORT`
  - `WAITING_FOR_VERIFICATION`
  - `PUBLISHED_CURRENT`
  - `PUBLISHED_UPDATE_REQUIRED`
  - `PUBLICATION_ERROR`
- `online_asset_id`, pokud je dostupné;
- `published_at`;
- `last_verified_at`;
- `published_revision_hash`;
- `current_identity_name` a historie předchozích hodnot.

Pokud se po publikaci změní asset, `is_published` zůstává `true`, protože stará verze je stále online. Stav přejde na `PUBLISHED_UPDATE_REQUIRED`.

## 9. Načtení produkčního `metadata.json` při `Done`

Produkční metadata mají fyzický název `metadata.json` a leží v kořeni zdrojové složky materiálu. Obsahují mimo jiné:

- `FOLDER`
- `MANUFACTURER`
- `PRODUCT_NUMBER`
- `PRODUCT_NAME`
- `CATEGORY`
- `BASE_NAME`
- `TEXTURE_SIZE.cm.width` a `.height`
- `TEXTURE_SIZE_SOURCE`
- `COLOR.hex`, `COLOR.measured_from` a pomocná měření
- `MAPS_SHORTCUTS`
- `RESOLUTIONS`
- `SOURCE`
- `WARNINGS`

Při přechodu do `DONE` systém:

1. soubor vyhledá v kořeni assetu;
2. pokud existuje, ověří syntaxi JSON a vytvoří hash;
3. porovná identitu JSON s databází, názvem složky a souborů;
4. načte `COLOR.hex`, `TEXTURE_SIZE.cm.width`, `TEXTURE_SIZE.cm.height` a nejvyšší klíč z `RESOLUTIONS`;
5. uloží snapshot JSON, hash, čas načtení a seznam rozdílů;
6. zpracuje `UNRECOGNIZED` a `WARNINGS` jako validační nálezy;
7. dovolí `DONE` i při chybějícím, nevalidním nebo nesouhlasícím souboru.

Původ každé přepisovatelné hodnoty má být evidován jako `METADATA`, `MANUAL` nebo `API`. Opětovné načtení nesmí bez upozornění přepsat ručně potvrzenou hodnotu.

## 10. Dva různé soubory se stejným názvem `metadata.json`

Systém pracuje se dvěma odlišnými dokumenty:

### A. Produkční metadata

Existují už ve zdrojové složce novějšího materiálu a nesou identitu, rozměr, barvu a technické podklady. Databáze je pouze čte; mění je jen v řízené operaci přejmenování/přesunu.

### B. Webový manifest balíčku

Generuje jej Bash ZIP proces. Má zachovat současné klíče:

```json
{
  "WEB_APP_PART": {
    "TEXTURE_RESOLUTIONS": {
      "16K": "16384x5890",
      "8K": "8192x2945"
    },
    "IMAGE_RATIO": 0.359497,
    "MAPS_SHORTCUTS": ["AO", "COL", "DISP16", "DISP", "GLOSS", "NRM16", "NRM", "ROUGH"]
  },
  "DESKTOP_APP_PART": {}
}
```

Dodaná ukázka i současná funkce `genMetadata()` obsahují koncovou čárku za `DESKTOP_APP_PART`, a nejsou tedy syntakticky validní JSON. Nová implementace musí zachovat klíče a význam, nikoli tuto syntaktickou chybu. Každý vygenerovaný manifest musí projít standardním JSON parserem.

### 10.1 Pravidlo umístění bez kolize

Protože oba dokumenty mají stejný fyzický název, použije se následující rozložení:

- na kořeni výstupní složky assetu zůstává `metadata.json` = generovaný webový manifest;
- na kořeni každého ZIP balíčku zůstává `metadata.json` = generovaný webový manifest;
- produkční `metadata.json`, pokud existuje, se beze změny kopíruje dovnitř konkrétní složky rozlišení vedle PBR map.

Žádný soubor se nesmí tiše přepsat. Pokud cílová cesta již obsahuje jiný `metadata.json`, job skončí pro daný asset chybou a vypíše obě kontrolní sumy.

## 11. ZIP kompilátor a politika data 4. 3. 2026

### 11.1 Zjištěné chování skriptů

Oba dodané Bash skripty:

- běží v Git Bash/Windows;
- používají ImageMagick `magick.exe` a `zip.exe`;
- hledají zdrojové složky odpovídající `[0-9][0-9]*K`;
- podle rozměru `COL` mapy upraví efektivní master rozlišení;
- vytvoří dostupná rozlišení z množiny `16K`, `8K`, `4K`, `2K`, `1K`, pokud jsou nižší než master;
- kopírují `PREVIEW`;
- generují webový manifest;
- vytvoří samostatný ZIP pro každé rozlišení;
- zapisují `operations.txt`.

Rozdíl důležitý pro obchodní pravidlo:

- větev `converted_before 4.3.2026` před vytvořením ZIPu nastaví rekurzivně čas všech položek balíčku na `2026-01-01 00:00` pomocí `touch -t 202601010000`;
- větev `converted_after 4.3.2026` časy nemění.

### 11.2 Výběr politiky

Systém před prvním ZIPováním:

1. najde nejvyšší zdrojovou složku odpovídající `^[0-9]+K$`;
2. načte její modification time ještě před jakoukoli pracovní kopií;
3. porovná jej s hranicí `2026-03-04 00:00:00` v lokálním čase produkčního úložiště;
4. vybere a do assetu i jobu uloží:
   - `LEGACY_BEFORE_2026_03_04`, pokud je čas přísně před hranicí;
   - `CURRENT_FROM_2026_03_04`, pokud je čas na hranici nebo po ní;
5. další joby používají uloženou politiku, aby se historický asset pozdější editací samovolně nepřeklasifikoval;
6. administrátor smí politiku změnit jen ručně, s náhledem dopadu a povinným důvodem v auditu.

Hranice „od půlnoci 4. 3.“ je pracovní interpretace věty „před a po 4. 3. 2026“ a musí být potvrzena vlastníkem procesu před nasazením.

### 11.3 Zjištěné technické vady současných skriptů

Při refaktoringu opravit a pokrýt testy:

- `genMetadata()` produkuje nevalidní JSON kvůli koncové čárce;
- ve větvi `converted_after` se na více místech zapisuje do nedefinovaného `${logpath}` místo `${logPath}`;
- některé chybové hlášky používají `${newFile}` tam, kde je relevantní `${convertedImage}`;
- jedna obdobná chyba `${logpath}` je i ve společné části obou verzí;
- `sourceResolution=$(basename *K)` není bezpečné, pokud pracovní složka obsahuje více adresářů `*K`;
- několik expanzí cest není uzavřeno v uvozovkách;
- pipeline používají `read` bez `-r`;
- kód předpokládá existenci `1K` při sestavení `MAPS_SHORTCUTS`;
- aktuální generování spoléhá na pozici čtvrtého segmentu názvu odděleného `_`; validátor musí chybný název zachytit dříve, než začne konverze.

Nová implementace může stávající skripty obalit nebo bezpečně refaktorovat, ale musí být porovnána s golden fixtures obou historických větví. Funkční změny nad rámec tohoto zadání nejsou povolené bez schválení.

## 12. Požadovaná výstupní struktura

Současný tvar výstupu se zachová:

```text
<BATCH_OUTPUT>/
├── operations.txt
└── <ASSET_FOLDER>/
    ├── PREVIEW/
    ├── metadata.json                  # generovaný webový manifest
    ├── <ASSET_FOLDER>_<RESOLUTION>.zip
    └── ... další ZIPy
```

Obsah každého ZIPu:

```text
<ASSET_FOLDER>_<RESOLUTION>/
├── PREVIEW/
├── metadata.json                      # generovaný webový manifest, beze změny umístění
└── <RESOLUTION>/
    ├── <BASE>_<MAP>_<RESOLUTION>.<ext>
    ├── ... další mapy
    └── metadata.json                  # NOVĚ: kopie produkčních metadat, pouze pokud existují
```

Jedinou strukturální změnou je poslední volitelný soubor. Pokud produkční metadata chybí, ZIP se normálně vytvoří bez něj a job uloží upozornění.

## 13. Řízené přejmenování a změna značky

### 13.1 Spouštěče

Operace se spustí při:

- změně publikované značky/výrobce;
- změně technického názvu materiálu;
- změně hlavní úložné kategorie;
- ručně vyžádané opravě identity.

Při změně značky se vždy přidělí další volné a nikdy dříve nepoužité číslo nové značky.

### 13.2 Povinný bezpečný postup

1. Získat exkluzivní lock assetu.
2. Zastavit nebo odmítnout souběžný publikační job.
3. Vypočítat novou identitu a případně rezervovat číslo.
4. Sestavit úplný rename plan bez provedení změn.
5. Zkontrolovat kolize všech cílových cest.
6. Ukázat uživateli staré a nové názvy, včetně volby přesunu celé kořenové složky pod adresář nové značky.
7. Po potvrzení použít dočasné názvy a transakční journal, aby byly možné rollbacky i při částečném selhání.
8. Přejmenovat kořenovou složku, master mapy a další soubory, které dodržují technickou konvenci.
9. Atomicky aktualizovat produkční `metadata.json`:
   - `FOLDER`
   - `MANUFACTURER`
   - `PRODUCT_NUMBER`
   - `PRODUCT_NAME`
   - `CATEGORY`
   - `BASE_NAME`
   - `TEXTURE_SIZE_SOURCE`
   - `COLOR.measured_from`
   - `SOURCE.SBS`
10. Aktualizovat databázi až po úspěchu souborových operací; při chybě provést rollback podle journalu.
11. Zapsat starou identitu do `asset_identity_history`.
12. Zneplatnit schválení aktuální revize.
13. Pokud je asset online, nastavit `PUBLISHED_UPDATE_REQUIRED`.

Zdrojová data se při běžném načítání, validaci, exportu nebo ZIPování nikdy nepřejmenovávají ani nemažou.

## 14. Publikační CSV

Přesné pořadí sloupců:

```text
identity_name;name;description;credits;dimension;brand_identifier;categories;color;tags
```

Pravidla:

- oddělovač sloupců `;`;
- UTF-8 s BOM;
- `dimension` jako `<width>x<height> cm`, s desetinnou tečkou;
- `color` jako `#RRGGBB`;
- `credits` celé číslo;
- `categories` obsahuje jednu nebo více hodnot spojených `:`;
- `tags` obsahuje jednu nebo více hodnot spojených `:`;
- jednotlivá kategorie ani tag nesmí obsahovat dvojtečku; validátor to zablokuje;
- hodnoty se v databázi ukládají jako jednotlivé položky, ne jako předem spojený text;
- duplicity kategorií/tagů se při exportu odstraní;
- CSV quoting musí odpovídat standardu RFC 4180 pro středník jako delimiter;
- `description` a `tags` mohou být prázdné, pokud to současný importer přijímá; systém zobrazí varování;
- značka, kategorie, barva, rozměr a credits musí projít předpublikační kontrolou.

Mapování:

| CSV | Interní zdroj |
|---|---|
| `identity_name` | generátor současné technické identity; ověřit vůči importeru |
| `name` | `assets.product_name` ve stávajícím publikačním formátu |
| `description` | schválený ruční/AI obsah |
| `credits` | `assets.credits` |
| `dimension` | `width_cm` + `height_cm` |
| `brand_identifier` | `published_brands.brand_identifier` |
| `categories` | `asset_online_categories`, join `:` |
| `color` | `assets.hex_color` |
| `tags` | `asset_tags`, join `:` |

Každý export je neměnný snapshot publikační dávky s hashem CSV a hashem každé zahrnuté revize assetu.

## 15. AI popisy a tagy

Preferovaný tok:

1. Interní API vrátí pouze nutná publikační data: UUID assetu, značku, název, kolekce, kategorie a schválené zdrojové URL.
2. AI vytvoří návrh `description` a seznam tagů.
3. Návrh se uloží jako `AI_DRAFT`, včetně použitých URL a auditních údajů.
4. Zaměstnanec návrh schválí nebo upraví.
5. Do ostrého CSV vstupuje schválená verze.

API nesmí poskytovat přímé databázové ani souborové přístupy. Použít scope omezený na čtení publikačního kontextu a zápis návrhů obsahu.

Alternativní tok:

- exportovat pomocné staging CSV s interním `asset_uuid` navíc;
- AI doplní popisy a tagy;
- importér návrhů ukáže diff a páruje výhradně podle UUID;
- finální publikační CSV pomocný sloupec odstraní a zachová přesných devět polí.

## 16. Publikační job a GCS

1. Uživatel vybere assety nebo projekt.
2. Systém vytvoří neměnný snapshot vstupních revizí.
3. Proběhne předpublikační validace a náhled varování/chyb.
4. V izolovaném dočasném workspace se vytvoří nižší rozlišení, oba typy metadat na určených cestách a ZIPy.
5. Každý ZIP se ověří seznamem souborů, velikostí, čitelností a hashem.
6. Vygeneruje se CSV.
7. Celá dávka se nahraje na GCS ve struktuře očekávané současnou online administrací.
8. Po potvrzení GCS uploadu přejde job do `UPLOADED_WAITING_FOR_CSV_IMPORT`.
9. Zaměstnanec spustí existující import tlačítkem v online administraci.
10. Po ověření online stavu se uloží `online_asset_id`, datum a publikovaný hash, pokud je lze získat.
11. Dočasný lokální workspace se odstraní po úspěchu i po chybě. Další pokus vždy generuje odvozené soubory znovu.

Nikdy nemažte `SOURCE`, `PREVIEW`, nejvyšší master rozlišení ani kořenový produkční `metadata.json`.

Pokud upload nebo ověření selže, odvozené lokální soubory se odstraní a další pokus je znovu vytvoří. Vzdálená částečně nahraná dávka musí být označena jako neplatná nebo izolována pod unikátním `job_id`; nesmí se zaměnit za dokončenou dávku.

## 17. Minimální interní API

Konkrétní URL přizpůsobit stacku, zachovat však význam:

- `POST /assets` – založení assetu a rezervace čísla;
- `POST /assets/{id}/mark-done` – načtení metadat, validace a neblokující `DONE`;
- `POST /assets/{id}/validate` – explicitní technická kontrola;
- `POST /assets/{id}/rename-plan` – pouze dry-run;
- `POST /assets/{id}/rename-confirm` – potvrzená změna s idempotency key;
- `GET /assets/{id}/publishing-context` – bezpečný podklad pro AI;
- `POST /assets/{id}/content-drafts` – návrh popisu/tagů;
- `POST /publication-jobs` – vytvoření dávky;
- `GET /publication-jobs/{id}` – průběh, log a výsledky;
- `POST /publication-jobs/{id}/mark-imported` – záznam ručního kroku v adminu;
- `POST /publication-jobs/{id}/verify` – kontrola online stavu.

Mutující endpointy musí používat oprávnění, audit a idempotency key.

## 18. Bezpečnost a provozní požadavky

- Exkluzivní lock na asset během rename/publish operace.
- Žádné shellové skládání cest z neupraveného uživatelského vstupu.
- Povolené znaky prefixu, názvu, kategorie, tagů a map musí mít explicitní validaci.
- Bash/worker spouštět bez `eval` nad uživatelskými daty.
- Všechny cesty kanonikalizovat a ověřit, že zůstávají pod nakonfigurovanými rooty.
- Žádné přepisování existujících souborů bez předchozího hash comparison a potvrzení.
- Logy nesmí obsahovat přístupové klíče GCS.
- Každá dávka, změna identity, ruční override a schválení musí být auditovatelné.
- Oprávnění minimálně: viewer, processor, reviewer, publisher, administrator.
- Produkční metadata a webové manifesty validovat proti verzovanému JSON Schema; současné soubory bez verze lze načítat jako implicitní schema v1.
- Do budoucích JSONů doporučeno přidat `SCHEMA_VERSION`, `GENERATED_AT` a `GENERATOR_VERSION`, ale bez schválení neměnit formát očekávaný současnými integracemi.

## 19. Akceptační kritéria

### Databáze a Notion

- Všechna pole firem z Notionu jsou mapována nebo výslovně označena jako legacy reference.
- Kontakty jsou normalizované a žádná existující kontaktní hodnota se při importu neztratí.
- Projekt zachová `Name`, `Status`, `Area`, `Notes`, `Tasks`, `Quick access visible` a `Archive`.
- Systém neodhadne vztah projektu k firmě bez explicitního mapování.
- Jedna firma může mít více publikovaných značek.

### Asset a identita

- Dva souběžné požadavky na nové číslo stejné značky nikdy nedostanou stejné číslo.
- Smazané nebo opuštěné číslo se znovu nepoužije.
- Změna značky přidělí nové číslo a vytvoří kompletní historii identity.
- Dry-run přejmenování přesně ukáže všechny změny a nic nezmění.
- Vynucená chyba uprostřed rename operace skončí úplným rollbackem nebo jasně obnovitelným journalem; databáze a filesystem nezůstanou tiše v rozdílném stavu.

### `Done` a metadata

- Validní produkční metadata načtou hex barvu, šířku, výšku a nejvyšší rozlišení.
- Chybějící, nevalidní nebo nesouhlasící metadata vytvoří viditelné varování, ale uživatel může asset označit `DONE`.
- Ruční hodnotu systém bez potvrzení nepřepíše.
- Běžné načtení nebo validace nikdy nemění zdrojové soubory.

### ZIP a historická politika

- Asset před hranicí použije legacy větev s normalizací timestampů na `2026-01-01 00:00`.
- Asset na/po hranici použije současnou větev bez této normalizace.
- Jednou zvolená politika je uložena a opakované spuštění ji samovolně nezmění.
- Output obou větví odpovídá golden fixture s výjimkou výslovně schváleného nového produkčního `metadata.json` ve složce rozlišení.
- Každý generovaný webový manifest projde standardním JSON parserem.
- Produkční metadata se v ZIPu objeví ve všech vytvořených rozlišeních, pokud existují; pokud neexistují, job skončí pouze s upozorněním.
- Webový manifest a produkční metadata se nikdy navzájem nepřepíší.

### CSV

- Výstup má přesně devět sloupců ve stanoveném pořadí, oddělovač `;` a UTF-8 BOM.
- Více kategorií je spojeno `:`.
- Více tagů je spojeno `:`.
- Tag/kategorie obsahující `:` je odmítnut s konkrétní chybou.
- Desetinné rozměry používají tečku.
- Hodnoty se středníkem, uvozovkou nebo novým řádkem jsou korektně escapované.

### Publikace

- Každý job eviduje assety, jejich revize, CSV hash, ZIP hashe, zvolenou historickou politiku a GCS výsledek.
- Po lokální změně již publikovaného assetu zůstává `is_published=true`, ale stav je `PUBLISHED_UPDATE_REQUIRED`.
- Dočasná odvozená data se po každém ukončeném pokusu odstraní; permanentní zdroje zůstanou nedotčené.

## 20. Doporučené fáze implementace

1. **Audit a fixtures** – zmrazit příklady starého/nového assetu, současné ZIPy, CSV a strukturu GCS.
2. **Datový základ** – firmy, kontakty, značky, projekty, assety, čítač čísel a migrace z Notionu.
3. **Filesystem registry** – sken složek, validace názvů, produkční metadata, `Done`.
4. **Bezpečné změny identity** – dry-run, rename, rebrand, journal a rollback.
5. **Packaging worker** – obě historické politiky, validní webový manifest, nové vložení produkčních metadat.
6. **CSV a obsah** – kategorie/tagy s `:`, ruční/AI popisy a schvalování.
7. **GCS a publikační workflow** – dávky, upload, ruční admin import a ověření.
8. **Hardening** – concurrency, recovery testy, oprávnění, audit, monitoring a dokumentace obsluhy.

## 21. Otevřené body před produkčním nasazením

1. Potvrdit, zda 4. 3. 2026 patří do nové větve (`>= 2026-03-04 00:00`), jak předpokládá toto zadání.
2. Ověřit zdrojový kód online CSV importeru: párování podle `identity_name`, jiného ID nebo online asset ID.
3. Potvrdit, zda stávající online proces vyžaduje nevalidní webový manifest s koncovou čárkou. Cílový stav musí být validní JSON; změnu je potřeba otestovat na stagingu.
4. Potvrdit doporučené umístění produkčních metadat uvnitř `<ZIP_ROOT>/<RESOLUTION>/metadata.json`. Je to jediné rozložení, které zachová současný webový manifest na kořeni ZIPu a zabrání kolizi stejného názvu.
5. Dodat reprezentativní golden fixtures: minimálně jeden asset před hranicí, jeden po hranici, jeden bez produkčních metadat, jeden s 16K masterem a jeden s nestandardním poměrem stran.
6. Určit mechanismus, kterým nový systém získá výsledek ručního importu z online administrace.
7. Rozhodnout, které z obecných Notion projektů se mají importovat do produkční databáze a jak se explicitně napárují na firmy.

## 22. Co není součástí této verze

- automatická změna současného online CSV importeru bez jeho auditu;
- přímé odstranění online assetů;
- automatické zakládání značek v online knihovně;
- přepis zdrojového produkčního `metadata.json` mimo potvrzenou změnu identity;
- nahrazování současného admin tlačítka, dokud nebude jasné jeho API a párování assetů.
