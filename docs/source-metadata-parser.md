# Read-only parser zdrojových metadat

## Ověřený vstup a rozdíl proti manifestu

Dodaná položka `source-metadata.txt` je ve skutečnosti adresář. Uvnitř leží
22bajtový `metadata.txt`: jediný ASCII řádek, bez BOM a bez koncového newline.
Jeho struktura je `texture size: <celé číslo>x<celé číslo> cm`.
Anonymizovaný minimální příklad (hodnoty jsou testovací):

```text
texture size: 12x34 cm
```

Vzorek **není JSON a neobsahuje hex barvu**. Starší tvrzení v technickém zadání
o produkčním JSON s `COLOR.hex` a `TEXTURE_SIZE.cm` nebylo tímto podkladem
potvrzeno. JSON varianta je nyní podporována jako explicitně požadované
rozšíření kontraktu. Textový výpis materiálu
potvrzuje soubor v kořeni a samostatné adresáře masteru, PREVIEW a SOURCE.

`web-manifest.json` je jiný dokument: objekt s `WEB_APP_PART` obsahujícím
`TEXTURE_RESOLUTIONS`, `IMAGE_RATIO`, `MAPS_SHORTCUTS`, a s prázdným
`DESKTOP_APP_PART`. Dodaný manifest má koncovou čárku za posledním kořenovým
členem, takže není striktní JSON. Jeho rozměry jsou pixely, nikoli centimetry.
Barvu neobsahuje. Bash skripty tento manifest generují jako `metadata.json`;
nejsou zdrojem formátu produkční barvy. Archiv byl přečten v paměti, bez
extrakce, spuštění či změny skriptů.

## Kontrakt parseru

`parse_source_metadata(material_path, *, allowed_root, boundary=None)` cte
pouze `metadata.txt` v koreni materialu. Cesty musi byt absolutni, lokalni,
pod povolenym rootem a bez symlinku/reparse points. Neplatny nebo nedostupny
koren vraci `NOT_SCANNED`, strukturovanou chybu a `can_continue=false`.

Vysledek obsahuje `source_filename`, `status`, `sha256`, `hex_color`,
`width_cm`, `height_cm`, `master_resolution`, `master_modified_at`,
`selected_zip_policy`, `warnings`, `errors`, `master_warnings`, `master_errors`
a vlastnost `can_continue`. `to_dict()` serializuje Decimal jako presne
retezce, nikdy jako float.

### Presne stavy metadat

| Status | Vyznam |
|---|---|
| NOT_SCANNED | Cteni nebylo zahajeno, napr. odmitnuty koren |
| MISSING | Korenovy metadata.txt chybi |
| VALID | Podporovana syntaxe, platne rozmery i barva, bez metadata warnings |
| WARNING | Parsovatelny vstup s chybejici barvou, neplatnou hodnotou nebo jinym neblokujicim nedostatkem |
| INVALID | Necitelny, prazdny, syntakticky neplatny, nepodporovany, prilis velky nebo nebezpecny soubor |

Podrobnosti jsou ve Finding `{code, path, message}`. Problemy metadat vzdy
pridavaji warnings, nikoli blokujici errors. `can_continue = not errors`.
Toto neni souhlas s publikaci ani zmena workflow stavu.

### Podporovane formaty

1. Dolozeny text `texture size: WxH cm`. Prvni hodnota je sirka, druha vyska.
   Toleruji se okrajove bile znaky, prazdne radky a desetinna tecka.
   Duplicitni rozmerove radky nebo vadna syntaxe jsou INVALID.
   Dalsi nezname radky se neinterpretuji a pridaji warning.
2. Explicitne vyzadany JSON kontrakt, rozsireni oproti dodanemu textovemu
   vzorku (nikoli tvrzeni, ze tento vzorek obsahuje JSON):

```json
{
  "COLOR": {"hex": "#aabbcc"},
  "TEXTURE_SIZE": {"cm": {"width": 12.5, "height": 34}}
}
```

JSON parser vyzaduje striktni syntaxi a korenovy objekt. Odmita duplicity,
NaN/Infinity, koncove carky i dokumenty s WEB_APP_PART nebo DESKTOP_APP_PART.
Neprohledava nahodne retezce pro hex a nepouziva literalni klic `COLOR.hex`;
cte vnorene `COLOR` -> `hex`. Pripadne dalsi JSON atributy ignoruje.
Rozmery cte pouze z `TEXTURE_SIZE.cm.width` a `.height`, jako kladna JSON
cisla pres `Decimal` pro celociselne i desetinne tokeny. Boolean ani ciselny
retezec nejsou rozmery. Chybejici/neplatny rozmer je null a warning
`SOURCE_METADATA_INVALID_DIMENSION`; ostatni dostupne hodnoty zustavaji.

Chybejici hex je null a `HEX_COLOR_MISSING`. Pritomny neplatny hex nebo
vadny typ COLOR vraci null a `HEX_COLOR_INVALID`. `normalize_hex()` je
zapojena do JSON parsovani: prijima sest ASCII hex cislic s volitelnym #,
vraci uppercase `#RRGGBB`. Nic se neodhaduje. Dolozeny textovy vzorek nema
pole barvy a vraci WARNING / HEX_COLOR_MISSING.

### Master je oddeleny od metadat

Po kontrole bezpecnosti korene se master a politika zjistuji stejnym
preflight algoritmem jako drive (`inspect_web_manifest=False`). Jeho nalezy
jsou v `master_warnings` a `master_errors`, nemeni metadata status ani
`can_continue`. Chybejici xK, vadny master nebo chyba jeho stat nepreskoci
cteni metadata.txt. Pro packaging musi budouci volajici kontrolovat
`master_errors` zvlast. Stavajici `preflight_material` svuj kontrakt nemeni.

SHA-256 se pocita ze vsech puvodnich bajtu pred dekodovanim. Je dostupny i
pro prazdny/poskozeny soubor a bez masteru. Pri necitelnosti nebo limitu
4 MiB je null, nikdy se nevraci hash nekompletniho souboru.
Parser necte web-manifest.json, metadata.json ani vnorene TXT.

## Politika a read-only zaruky

Hranice zustava 2026-03-04 00:00:00 Europe/Prague; pasmo lze konfigurovat
ZIP_POLICY_TIMEZONE. Pred hranici plati LEGACY_BEFORE_2026_03_04,
na ni a po ni CURRENT_ON_OR_AFTER_2026_03_04. Rozhoduje mtime master slozky.

Parser otevre metadata pouze rb. Nic nezapisuje, nekopiruje, neprejmenovava,
nevytvari ZIPy a nenastavuje timestamps. Testy overuji obsah, seznam cest,
mtime a ctime. Atime muze menit operacni system; parser jej neobnovuje.
Kontroly cest predpokladaji stabilni duveryhodny lokalni snapshot, nejsou
atomickou ochranou proti soubezne vymene souboru.

## Testy a zbyle overeni

Testy pouzivaji jen anonymni fixture a docasne slozky. Pokryvaji oba formaty,
normalizaci a chyby hexu, presne Decimal, vsech pet statusu, chybejici master,
SHA-256 puvodnich bajtu, manifest, bezpecnost korene, nemennost zdroje a
historickou politiku. Skutecne podklady nejsou kopirovany do repozitare.

JSON kontrakt COLOR.hex je nyni explicitne podporovany na zadost zadavatele;
jeho pritomnost v produkci zustava neoverena. Dodany text obsahuje pouze
rozmery. Poradi sirka x vyska je interpretace nepojmenovanych os.
Pro produkcni smoke test je potreba lokalni kompletni koren s 16K, PREVIEW,
SOURCE a metadata.txt; samostatna dosavadni kopie tuto podminku nesplnuje.

Aktuální ověření: 46 cílených testů parseru a všech 91 worker testů prošlo
na dostupném Pythonu 3.11.4. Cílový Python 3.13 zůstává neověřený.
Docker není dostupný; podmíněný scripts/test.ps1 nebyl spuštěn.
git diff --check prošel.
