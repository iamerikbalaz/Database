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
potvrzeno. Parser takovou strukturu nepředpokládá. Textový výpis materiálu
potvrzuje soubor v kořeni a samostatné adresáře masteru, PREVIEW a SOURCE.

`web-manifest.json` je jiný dokument: objekt s `WEB_APP_PART` obsahujícím
`TEXTURE_RESOLUTIONS`, `IMAGE_RATIO`, `MAPS_SHORTCUTS`, a s prázdným
`DESKTOP_APP_PART`. Dodaný manifest má koncovou čárku za posledním kořenovým
členem, takže není striktní JSON. Jeho rozměry jsou pixely, nikoli centimetry.
Barvu neobsahuje. Bash skripty tento manifest generují jako `metadata.json`;
nejsou zdrojem formátu produkční barvy. Archiv byl přečten v paměti, bez
extrakce, spuštění či změny skriptů.

## Kontrakt

```python
from app.source_metadata import parse_source_metadata

result = parse_source_metadata(material_path, allowed_root=local_snapshot_root)
payload = result.to_dict()
```

`material_path` a povinný `allowed_root` jsou absolutní lokální cesty.
Volitelné `boundary` má stejný kontrakt jako preflight. Funkce nejprve volá
`preflight_material(..., inspect_web_manifest=False)`, takže přebírá jeho
kontroly cest, výběr nejvyšší přímé složky `[1-9][0-9]*K`, warnings pro
nestandardní rozlišení a výběr politiky. Nový přepínač preflightu má výchozí
hodnotu `True`; jeho dosavadní volání zůstávají beze změny.

| Pole výsledku | Typ a význam |
|---|---|
| `status` | `NOT_CHECKED`, `MISSING`, `UNREADABLE`, `UNSAFE_FILE`, `TOO_LARGE`, `EMPTY`, `INVALID_FORMAT`, `PARTIAL` |
| `source_filename` | Vždy `metadata.txt` |
| `sha256` | Hex SHA-256 všech původních bajtů, před dekódováním, ořezem či parsováním; jinak `None` |
| `hex_color` | `None`, protože dostupný formát nemá doložené pole barvy |
| `width_cm`, `height_cm` | Kladný `Decimal`, jinak `None`; první číslo je interpretováno jako šířka, druhé jako výška |
| `master_resolution` | Nejvyšší skutečná xK složka, jinak `None` |
| `master_modified_at` | UTC ISO timestamp převzatý z preflightu |
| `selected_zip_policy` | Beze změny převzatá politika preflightu, jinak `None` |
| `warnings`, `errors` | Seznam `Finding(code, path, message)` |
| `can_continue` | `not errors`; problémy metadat přidávají pouze warnings |

`to_dict()` převádí rozměry na přesné desetinné řetězce pro JSON; žádný krok
nepřevádí rozměry na binární float. Ostatní nullable hodnoty jsou JSON null.
Hash existuje i pro prázdný soubor, nevalidní UTF-8 nebo neplatnou syntaxi.
Při nečitelnosti, nebezpečném typu souboru či překročení limitu hash chybí;
neúplný hash se nevydává za hash souboru.

Parser hledá pouze `<material>/metadata.txt`. Nečte `metadata.json`,
`web-manifest.json`, mapy ani vnořené metadata. Manifest přejmenovaný na
`metadata.txt` je `INVALID_FORMAT`, nikoli alternativní zdroj hodnot.

## Přijatá syntaxe a upozornění

UTF-8 text obsahuje právě jeden rozpoznaný řádek `texture size: WxH cm`.
Okrajové bílé znaky a prázdné řádky se tolerují. Desetinná tečka je explicitní
rozšíření podle požadavku na desetinné rozměry, nikoli vlastnost doloženého
celočíselného vzorku. Čárka, exponent, NaN a Infinity se nepřijímají.
Duplicitní rozměrové řádky jsou nejednoznačné a odmítnou se.

- Chybějící, prázdný, nečitelný, příliš velký či nesprávně strukturovaný vstup
  vrátí `SOURCE_METADATA_<status>`.
- Chybějící nebo duplicitní rozměrový řádek navíc vrací
  `SOURCE_METADATA_DIMENSIONS_MISSING` / `SOURCE_METADATA_DIMENSIONS_AMBIGUOUS`.
- Nulová či záporná hodnota vrací `SOURCE_METADATA_INVALID_DIMENSION`;
  druhý platný rozměr se zachová.
- Doložený text nemá barvu: vrací `SOURCE_METADATA_HEX_MISSING` a stav
  `PARTIAL`, i když je jeho rozměrová syntaxe platná.
- Další řádky vrací `SOURCE_METADATA_UNRECOGNIZED_CONTENT`; platný rozměrový
  řádek lze využít, obsah dalších řádků se neodhaduje.

Samostatná funkce `normalize_hex` ověřuje šest ASCII hex číslic s volitelným
`#` a vrací `#RRGGBB`; neplatná hodnota vyvolá `ValueError`. Je otestována,
ale parser ji zatím nepoužívá, protože umístění/syntaxe barvy není doloženo.
Test normalizace není důkazem podporovaného načítání barvy ze souboru.

Chyby materiálové cesty nebo nemožnost určit master zůstávají blokující jako
v preflightu. Problémy samotného `metadata.txt` nikdy nepřidávají `errors`
a neblokují budoucí Done. Funkce sama žádný workflow stav nemění.

## Časová politika a neměnnost

Hranice zůstává `2026-03-04 00:00:00 Europe/Prague`:
`LEGACY_BEFORE_2026_03_04` před ní,
`CURRENT_ON_OR_AFTER_2026_03_04` přesně na ní a později.
Konfigurace `ZIP_POLICY_TIMEZONE` mění explicitní pásmo bez změny algoritmu.
Používá se LastWriteTime master složky, nikoli čas souboru metadat.

Parser má limit 4 MiB a otevírá zdroj pouze `rb`. Nevytváří soubory, ZIPy,
nepřejmenovává, nemaže, nezapisuje ani nenastavuje timestamps. Testy porovnávají
obsah, seznam cest, mtime a ctime před/po dvou čteních. OS může při čtení měnit
atime; záruka úplné neměnnosti všech časů vyžaduje read-only/noatime snapshot.
Atime se neobnovuje zápisem. Stejně jako preflight je parser určen pro stabilní,
důvěryhodný lokální snapshot, nikoli ochranu proti souběžné výměně cest.

## Otevřené podklady

1. Dodat skutečný vzorek obsahující barvu; bez něj nelze dokončit extrakci hexu
   ani integrační test neplatného hex pole, aniž by se vymyslel vstupní formát.
2. Potvrdit pořadí šířka × výška; samotný dodaný řádek osy nepojmenovává.
3. Pokud existuje další produkční varianta s JSON obsahem, dodat ji před
   implementací jejího parseru. Webový manifest není takovým vzorkem.

Nový fixture obsahuje jen anonymní testovací rozměry. Skutečné podklady nebyly
kopírovány do repozitáře ani měněny. Testy nemají žádnou závislost na jejich
umístění, zákaznících či síťových cestách.

## Ověření implementace

Lokální běh `python -m pytest -p no:cacheprovider`: 78 testů prošlo
(45 stávajících, 33 nových). Použit dostupný Python 3.11.4 a izolované
existující testovací závislosti mimo repozitář. Cílový Python 3.13 tím není
ověřen. Docker není dostupný v PATH ani na standardní instalační cestě,
proto nebyl spuštěn podmíněný `scripts/test.ps1`.
`git diff --check` a kontrola whitespace nových souborů prošly.
