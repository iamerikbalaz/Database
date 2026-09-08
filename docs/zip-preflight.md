# Read-only ZIP preflight a audit referenčních Bash skriptů

Stav: implementace ve větvi `feature/zip-preflight`, 8. 9. 2026.
Rozsah: pouze knihovní funkce workeru, unit testy a tento audit. Worker zůstává
bez job integrace; `run_once()` nadále vrací `idle`. Nic nevytváří ZIPy,
nespouští ImageMagick ani Bash a neprovádí změnu stavu Done.

## Podklady a jejich dostupnost

Byly přečteny celý README, oba dokumenty v `docs/`, celý worker a jeho testy,
`tests/README.md`, `scripts/test.ps1` a oba kompletní skripty s `readme.txt`
uvnitř uživatelem dodaného `scripts/texture-zip.zip`. ZIP byl pouze čten
v paměti, bez extrakce nebo spuštění jeho obsahu. Zůstává původním
nesledovaným souborem uživatele, není součástí implementace.

Pozor: názvy existujících dokumentů jsou prohozené vůči jejich obsahu:
`docs/technical-specification.md` obsahuje předávací prompt a
`docs/codex-handoff-prompt.md` obsahuje technické zadání verze 0.9.

Archiv neobsahuje produkční JSON ani golden ZIP výstupy. Přesný původní
`metadata(2).json` je nyní dodán jako
`worker/tests/fixtures/metadata_legacy_web_app.json` (495 bajtů, SHA-256
`1c895c2fe94a216e3c8b352b34cebb4568fb8304f8d141a76a7f227df7359c9b`).
Test čte tento skutečný fixture a kopíruje jeho bajty pouze do dočasného
materiálu. Originál neformátuje ani nepřepisuje; koncová čárka zůstává.
Lokální `.gitattributes` zakazuje převod konců řádků tohoto fixture Gitem.

SHA-256 nezměněných referenčních skriptů uvnitř archivu:

| Cesta | Bajty | SHA-256 |
|---|---:|---|
| `converted_before 4.3.2026/rename.sh` | 10840 | `6757536b0eea0078df5f78bf8e9bb70eb7bbf6b86193b48af2e475c1eed8c813` |
| `converted_after 4.3.2026/rename.sh` | 10735 | `b877701ac745a54ac0bb356539a31b3353cc9ab4aa55de926da32b41b4719456` |

## Přesné rozdíly skriptů

Úplný textový diff má právě čtyři změnová místa. Čísla řádků jsou počítána
od shebangu; do řádku 195 se v obou verzích shodují.

| Místo | Before | After | Důsledek |
|---|---|---|---|
| 121, převod většího masteru | Chyba uvádí `convertedImage`, zapisuje do `logPath` | Uvádí `newFile`, zapisuje do `logpath` | Nová větev ztrácí správný název souboru i spolehlivé chybové logování. |
| 137, převod menšího masteru | `convertedImage`, `logPath` | `newFile`, `logpath` | Stejná regrese. |
| 180, nižší rozlišení | `newFile`, `logPath` | `newFile`, `logpath` | Regrese cíle chybového logu; název souboru je zde správný. |
| 196 ve Before | `find "${name}_${newResolution}" -exec touch -t 202601010000 {} +` | Řádek chybí | Pouze stará větev rekurzivně normalizuje časy pracovního balíčku před ZIPem. |

Obě verze už mají chybu `newFile` / `logpath` na řádku 151. Celkem tedy
Before obsahuje jeden výskyt `logpath`, After čtyři. Definováno je pouze
`logPath` (řádek 80); Bash rozlišuje velikost písmen. Pokud `logpath` není
zděděno z prostředí, přesměrování do prázdné cesty selže. Pokud zděděno je,
log může skončit na jiné cestě. Běžné logování a stdout ZIPu používají
v obou verzích správné `logPath`.

## Společné chování a rizika Bash procesu

### Timestampy

Skripty samy datum nevyhodnocují ani nevybírají politiku. Volba konkrétního
skriptu je externí. Before provádí `touch` po kopii PREVIEW a manifestu
a přesunu rozlišení do pracovního obalu. Zahrnuje obal, všechny podsložky
i soubory. Bez `-a`/`-m` nastavuje access i modification time, v lokálním
pásmu procesu, na 1. 1. 2026 00:00. After tento krok nemá. Ani After
nezaručuje zachování zdrojových časů: používá obyčejné `cp` bez zachování
timestampů a vytváří nové obrázky/manifesty. Při správně odděleném cíli se
`touch` netýká zdrojového masteru. Oddělení zdroje a cíle však skripty
nekontrolují; při překryvu mohou mutovat skutečná data.

### Metadata

`genMetadata()` (39–77) je v obou verzích totožná. Přesměrování na řádku 186
vytváří/přepisuje výstupní `metadata.json`. `WEB_APP_PART` obsahuje
`TEXTURE_RESOLUTIONS`, `IMAGE_RATIO` a `MAPS_SHORTCUTS`; `DESKTOP_APP_PART`
je prázdný objekt. Za jeho uzavřením je čárka, takže výstup není striktní JSON.
Žádná validace následně neprobíhá. Rozměry a poměr stran vycházejí z prvního
nalezeného JPG/JPEG; pořadí není stabilizováno a chybějící obrázek/selhání
identify může vytvořit prázdné hodnoty nebo neplatný výraz pro awk.
Shortcuty se odvozují ze čtvrtého segmentu názvu souborů v `1K`; PNG se zde
nezahrnují, přestože se konvertují. `sort | uniq` deduplikuje cesty, ne shortcuty.
Zdrojový produkční `metadata.json` se explicitně nekopíruje ani nenačítá.
`COLOR.hex` ani `TEXTURE_SIZE.cm` tento generátor nevytváří.

### PREVIEW a skutečná struktura ZIPu

Na řádku 160 se zdrojové `PREVIEW` kopíruje do výstupní složky assetu,
na 194 znovu do každého pracovního obalu. Chybějící PREVIEW nebo chyba kopie
nezastaví archivaci. Výstup má následující zamýšlený tvar:

```text
<output>/operations.txt
<output>/<relative material>/PREVIEW/...
<output>/<relative material>/metadata.json
<output>/<relative material>/<name>_<resolution>.zip

# Uvnitř ZIPu je ještě jeden vrchní adresář:
<name>_<resolution>/PREVIEW/...
<name>_<resolution>/metadata.json
<name>_<resolution>/<resolution>/<map files>
```

`name` vzniká z basename relativní cesty nahrazením výskytů master rozlišení
pomocí sed. Obal se zipuje celý pomocí `zip -r`; manifest tedy neleží přímo
na bezejmenném kořeni archivu. Produkční metadata uvnitř rozlišení jsou
požadavek budoucí implementace v dokumentaci, nikoli současné chování skriptů.

### Nástroje a chybové stavy

Obě verze mají stejné pevné cesty (5–6):

- `/c/Program Files/ImageMagick-7.1.1-Q16-HDRI/magick.exe`
- `/c/ProgramData/chocolatey/lib/zip/tools/zip.exe`

Cesty nástrojů jsou správně uzavřené v uvozovkách. Úvodní kontrola ověřuje
existenci souboru (`-f`), nikoli spustitelnost/verzi. Kontroluje také existenci
obou vstupních adresářů a ukončení jejich cesty lomítkem; při neúspěchu vrátí 1.
Neověřuje závislosti `timeout`, `awk`, `find`, `realpath`, `zip` kompatibilitu
ani kanonické oddělení zdroje a cíle.

Konverze masteru má dva pokusy po maximálně třech minutách, odvozené
rozlišení tři pokusy. Po vyčerpání se pouze vypíše chyba a pokračuje se.
Návraty `identify` nejsou kontrolované; první identify používá `eval` a má
navíc prohozené názvy width/height. Skripty nemají `set -e`, `set -u`,
`pipefail`, transakční staging, trap pro cleanup ani ověření kompletnosti.
Pipeline `while read` běží v subshellu, takže přiřazení `sourceResolution`
uvnitř preprocessingu se nepřenáší ven; později se znovu odhaduje z `*K`.

Po ZIP příkazu následuje bezpodmínečné `rm -fr` pracovního obalu
(Before 198 / After 197), i když zipování selhalo. Existující archiv může
`zip` aktualizovat a zachovat v něm staré položky. Nedokončené konverze,
prázdné manifesty, částečné ZIPy a staré výstupy se mohou smíchat. `Done`
na stdout ani výsledný exit code nejsou důkazem úspěšné publikace.
`operations.txt` se při každém spuštění nejprve přepíše a stderr ZIPu
se do něj explicitně nesměruje.

### Cesty, rozlišení a opakované zpracování

Vnější GNU find používá sed BRE `[0-9][0-9]*K`: znamená jednu nebo více
číslic, tedy zahrnuje i `1K`, nikoli pouze dvouciferná rozlišení. Je rekurzivní
a zpracuje každou odpovídající složku, ne jen nejvyšší master jednoho materiálu.
Povoluje i `0K` a počáteční nuly. Více vstupních rozlišení tak může vést
k opakovanému zpracování do stejného cíle.

`sourceResolution=$(basename *K)` je nejednoznačné pro více shod a chybné
bez shody. Metadata a archivace mají širší rekurzivní pattern `*K`.
Z COL se může efektivní rozlišení snížit celočíselným dělením nejdelší
strany 1024 (dokonce na `0K`); generují se nižší členové 16/8/4/2/1.
Preflight záměrně nečte obrázky a tento přepočet neprovádí.

Část kopírovacích příkazů cesty správně quotuje, ale celá pipeline není
bezpečná pro mezery a speciální znaky: `for i in $(find ...)`, nequotované
`find ${sourceResolution}`, `echo $newFile | xargs`, expanze pro `eval`
a `read` bez `-r` mění nebo dělí vstup. `IFS=$'\n'` před jedním `find`
neopraví ostatní smyčky. `cut -d_ -f4/-f5` a `cut -d.` předpokládají pevné
segmenty názvů. Chyby `pushd` nejsou kontrolované, takže další relativní
mutace mohou při jeho selhání proběhnout v nesprávném adresáři.

## Implementovaný kontrakt

```python
from app.preflight import preflight_material

# Pouze příklad lokální testovací cesty. Výchozí pásmo je Europe/Prague.
result = preflight_material(
    "C:/local-fixtures/material with spaces",
    allowed_root="C:/local-fixtures",
)
payload = result.to_dict()  # lze předat json.dumps; funkce sama nic nezapisuje
```

Výchozí hranice je `2026-03-04 00:00:00 Europe/Prague`, vytvořená pomocí
`ZoneInfo`. Proměnná prostředí `ZIP_POLICY_TIMEZONE` umožňuje změnit IANA
pásmo bez úpravy algoritmu. Závislost workeru `tzdata` poskytuje data pásem
i na Windows. Volitelné explicitní `boundary` musí být půlnoc 4. 3. 2026
s pásmem/offsetem. Neplatná hranice nebo neznámé pásmo vyvolají konfigurační
výjimku před přístupem ke zdroji. Lokální pásmo počítače se nepoužívá.
Čas z `st_mtime_ns` se načte jako UTC datetime a před porovnáním převede
do zvoleného pásma. Celočíselné dělení na sekundy zachovává správné rozhodnutí
i jednu nanosekundu před hranicí, protože hranice leží přesně na celé sekundě.
Před hranicí se volí `LEGACY_BEFORE_2026_03_04`, na hranici
a později `CURRENT_ON_OR_AFTER_2026_03_04`. Nový název politiky má přednost
před starším `CURRENT_FROM_2026_03_04` uvedeným v původních dokumentech.
Inkluzivitu 4. 3. už potvrdilo aktuální zadání, není třeba ji potvrzovat znovu.

| Pole výsledku | Význam |
|---|---|
| `material_path` | Absolutní předaná cesta materiálu. |
| `policy_boundary` | ISO 8601 hranice s explicitním offsetem pro audit. |
| `policy_timezone` | Název IANA pásma, výchozí `Europe/Prague`; u explicitního pevného offsetu jeho označení. |
| `found_resolutions` | Numericky vzestupný seznam přímých skutečných složek odpovídajících fullmatch `[1-9][0-9]*K`. Žádné počáteční nuly, malé k nebo Unicode číslice. |
| `master_resolution` | Nejvyšší nalezené rozlišení; podporuje i další kladná celočíselná xK. |
| `master_modified_at` | Čas master složky v UTC, ISO 8601 s devíti desetinnými místy; není to creation time ani maximum časů map. |
| `master_modified_ns` | Původní celočíselný mtime pro přesný audit a porovnání. |
| `selected_zip_policy` | Jedna ze dvou výše uvedených hodnot, jinak null. |
| `metadata_status` | `NOT_CHECKED`, `MISSING`, `UNREADABLE`, `UNSAFE_FILE`, `TOO_LARGE`, `INVALID_JSON`, `INVALID_STRUCTURE`, `LEGACY_NON_STANDARD_JSON`, `VALID`. |
| `metadata_exists`, `metadata_readable` | true/false, null pokud se vlastnost nepodařilo zjistit/nebyla zkoumána. |
| `metadata_strict_json` | true pro přijatý striktní JSON, false pro odmítnutý JSON nebo legacy fallback, null pokud se neparsovalo. Parser navíc konzervativně odmítá duplicitní klíče a NaN/Infinity. |
| `metadata_sha256` | SHA-256 přesných vstupních bajtů, i u nevalidního UTF-8/JSON. Null při nečitelnosti nebo limitu velikosti. Hash se počítá před jakoukoli tolerancí legacy syntaxe. |
| `metadata_fields_present` | Přítomnost `WEB_APP_PART`, `WEB_APP_PART.TEXTURE_RESOLUTIONS` a `WEB_APP_PART.MAPS_SHORTCUTS`; prázdné, pokud data nelze parsovat. |
| `metadata_web_app_part` | Parsovaný objekt WEB_APP_PART, pokud je dostupný, jinak null. Nevytváří domnělá pole COLOR.hex ani TEXTURE_SIZE.cm. |
| `warnings`, `errors` | Seznam objektů `{code, path, message}`. |
| `can_continue` | false pouze při blokující chybě: neexistující/neadresářová/nečitelná či nepovolená cesta, žádná použitelná xK složka nebo nemožnost bezpečně určit master/jeho čas/politiku. Metadata ani nestandardní rozlišení nikdy sama nepřidávají errors. Není to rozhodnutí o povolení Done ani publikace. |

Standardní rozlišení jsou `1K`, `2K`, `4K`, `8K`, `16K`. Každá další
nalezená kladná celočíselná xK složka (např. `12K`) vrací warning
`NON_STANDARD_RESOLUTION` s cestou a popisem. Zůstává kandidátem na nejvyšší
master a sama neblokuje pokračování. Warning rozlišení nemění stav metadat.

Metadata se hledají jen jako `<material>/metadata.json`. Požadované vnoření
odpovídá webovému manifestu. Přítomné objekty/listy se kontrolují na základní
typ; úplná validace položek map a rozměrů není součástí tohoto preflightu.
Produkční dokument s jinou strukturou může dostat warnings za chybějící
webová pole, ale neblokuje pokračování. Žádné pole barvy/rozměru se nedomýšlí.

Legacy fallback toleruje pouze jednu koncovou čárku před uzavřením kořenového
objektu s `WEB_APP_PART` a `DESKTOP_APP_PART`, a jen po neúspěchu striktního
parseru. V paměti odstraní právě tuto čárku, znovu parsuje a vrátí warning
`LEGACY_NON_STANDARD_JSON` i `metadata_strict_json=false`. Vnořené čárky,
komentáře a další poškození nejsou opravovány. Zdroj se nikdy nepřepisuje.
Při chybějících polích přibudou další warnings i u legacy dokumentu.

## Bezpečnostní rozsah a zbývající rizika

- Prohlíží se jen výslovně předaný materiál pod povinným `allowed_root`.
  Relativní cesty, `..`, UNC/device cesty a symlinky/reparse points v řetězci
  adresářů se odmítají. Resolution link je chyba; metadata link jen warning.
  Žádné rekurzivní skenování map, přejmenování, kopírování, mazání, mkdir,
  touch/utime, logování do zdroje nebo tvorba archivů se neprovádí.
- Čtení souboru je omezené na 4 MiB + jeden detekční bajt. Příliš velký
  dokument se neparsuje a nemá neúplný hash vydávaný za hash celého souboru.
- Knihovní read-only operace samy nezapisují timestamps. OS ale může při
  čtení aktualizovat **atime**. Absolutní neměnnost všech filesystem metadat
  vyžaduje read-only/noatime snapshot nebo odpovídající mount nastavení.
  Preflight atime neobnovuje pomocí utime, protože by to byl zápis do zdroje.
- Kontroly cest nejsou atomickým bezpečnostním sandboxem vůči souběžnému
  útočníkovi. Pro konzistentní audit je nutný důvěryhodný stabilní lokální
  snapshot. Mapped network drive nebo POSIX network mount nelze spolehlivě
  poznat z názvu cesty; `allowed_root` musí dodávat důvěryhodná konfigurace
  lokálního workspace. V této práci byly použity jen dočasné lokální složky.
- Mtime složky se nemusí změnit při přepsání obsahu existující mapy. Pozdější
  packaging musí snapshotovat/ukládat volbu politiky; nynější samostatný
  preflight ji při každém volání znovu vyhodnocuje. Persistence není implementována.

## Ověření

Worker: `python -m pytest -p no:cacheprovider`, všech 45 testů prošlo
(42 preflight a 3 původní). Pokryty jsou chybějící/samostatná/vícenásobná
rozlišení, další xK, bezpečný pattern, mezery, před/na/po hranici,
nanosekundová hranice i explicitní Europe/Prague, změna pásma konfigurací
nezávisle na systémovém TZ, nefatální 12K s validními metadaty,
metadata včetně nečitelnosti,
poškození, nevalidního UTF-8, skutečného legacy fixture s koncovou čárkou
a pevným SHA-256, načtení jeho WEB_APP_PART a absence domnělých polí,
chybějících polí, typů a limitu,
chyby cest/stat a odmítnutí linků. Permission/link/resource selhání jsou
deterministicky simulována; zbytek filesystem testů běží v `tmp_path`.
Snapshot test dvakrát spustí preflight a ověří stejné cesty, obsah, mtime
a ctime; navíc zakáže mutující OS funkce a zápisový režim otevření souborů.
Atime z výše uvedeného důvodu nepovažuje za garantované aplikační čtením.

Prostředí: nalezený Python 3.12.14, pytest 9.1.1 instalovaný izolovaně do
dočasného adresáře. Projekt požaduje Python 3.13; ověření na cílové verzi
zůstává nutné. Docker nebyl dostupný v PATH ani na standardní instalační
cestě, proto se podmíněný celý `scripts/test.ps1` nespouštěl.
`git diff --check` prošel; samostatné `git diff --no-index --check` prošly
také pro nové implementační textové soubory včetně `.gitattributes`. Git pouze
upozornil na budoucí převod LF na CRLF podle místního nastavení.

## Co musí potvrdit/dodat zadavatel

Pásmo Europe/Prague, původní fixture a nefatální podpora libovolných kladných
celočíselných xK jsou potvrzené aktuálním zadáním.

1. Před budoucím napojením zdrojů určit read-only/noatime snapshot a ochranu
   před souběžnou změnou, pokud je vyžadována neměnnost včetně atime.
2. Před packaging fází dodat golden ZIPy. Tato implementace rozlišení pouze
   rozeznává a vybírá jejich maximum, nezavádí konverzi ani ZIPování.

Implementační změny jsou v modulu preflight, jeho testech, tomto auditu,
závislosti `tzdata` v `worker/pyproject.toml` a ochraně fixture pomocí
`.gitattributes`. Při případném vrácení změn zachovat uživatelem dodaný fixture.
Nejsou migrace ani změny běžícího procesu.
