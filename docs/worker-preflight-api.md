# Interní worker material-preflight API

Worker poskytuje read-only HTTP adaptér nad existujícím parserem
`metadata.txt` a výběrem master rozlišení/politiky. API nic nezapisuje,
nekopíruje, nepřejmenovává, nemaže ani nevytváří. Není to souhlas s publikací
nebo změnou workflow stavu.

## Spuštění a síť

ASGI aplikace je exportovaná jako `app.main:app` a v kontejneru naslouchá na portu `8080`.
Compose port pouze deklaruje pomocí `expose`; nepřekládá jej na hostitele.
Ostatní služby ve stejné Compose síti jej mohou volat na
`http://worker:8080`. `GET /health` vrací stav samotného HTTP procesu:

```json
{"status":"ok","service":"worker","version":"0.1.0"}
```

`MATERIALS_ROOT` musí být absolutní cesta viditelná uvnitř worker procesu.
Výchozí Compose hodnota je `/materials`. Produkční orchestrátor musí tento
adresář připojit jako read-only volume; samotný repozitář záměrně neurčuje
hostitelskou NAS cestu ani ji během testů nepřipojuje. Proměnná
`ZIP_POLICY_TIMEZONE` nadále volitelně mění výchozí `Europe/Prague`.

## Endpoint

`POST /internal/material-preflight`

```json
{
  "folder_path": "relativni/cesta/TECHNICAL_IDENTITY"
}
```

`folder_path` používá relativní komponenty pod `MATERIALS_ROOT`. Lomítka jsou
ve výstupu normalizovaná na `/`. Prázdné komponenty, `.`, `..`, null byte,
Windows drive/UNC/device cesty, Unix absolutní cesty a komponenty s `:` jsou
odmítnuty ještě před přístupem k filesystemu. Kanonická kontrola, `lstat`
a kontrola reparse pointů zůstávají jako defense in depth, nejsou však
bezpečnostní hranicí pro následné čtení.

### Descriptor-based traversal a TOCTOU ochrana

Produkční podporovaná cesta je Linux worker v Dockeru. Worker otevře
`MATERIALS_ROOT` jednou jako directory file descriptor s `O_RDONLY`,
`O_DIRECTORY`, `O_NOFOLLOW` a `O_CLOEXEC`. Každou komponentu `folder_path`
potom otevírá pomocí `os.open(component, ..., dir_fd=parent_fd)` se stejnými
no-follow directory příznaky. Již ověřená textová cesta se pro přístup
k materiálu znovu nepoužije.

Výpis přímých položek materiálu probíhá přes otevřený material descriptor.
Každý kandidát xK je znovu otevřen relativně k tomuto descriptoru pomocí
`O_DIRECTORY|O_NOFOLLOW`; jeho typ a timestamp pochází z `os.fstat` stejného
otevřeného descriptoru. `metadata.txt` se otevírá relativně k material FD
s `O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK`. `O_NONBLOCK` zabraňuje
zablokování na podvrženém FIFO. Teprve `fstat` otevřeného metadata FD ověří,
že jde o regular file a že deklarovaná velikost nepřekračuje limit. Bajty se
čtou výhradně pomocí `os.read` ze stejného FD.

Přejmenování nebo nahrazení adresáře či `metadata.txt` po prvotní kontrole
proto nemůže přesměrovat otevřený descriptor na jiný inode. Všechny root,
komponentní, resolution a metadata descriptory se zavírají přes garantovaný
cleanup i při chybě.

Pokud platforma neposkytuje `dir_fd`, descriptorové `listdir`, `O_NOFOLLOW`,
`O_DIRECTORY` nebo další potřebné příznaky, API nepoužije `Path.open` ani jiný
fallback. Vrátí 422 s kódem
`SECURE_FILESYSTEM_ACCESS_UNAVAILABLE` a `can_continue=false`. Nativní Windows
je proto záměrně fail-closed; bezpečné produkční zpracování je podporované
v Linux kontejneru.

Úspěšně dokončený preflight má `schema_version=1` a stabilní tvar:

```json
{
  "schema_version": 1,
  "folder_path": "relativni/cesta/TECHNICAL_IDENTITY",
  "folder_name": "TECHNICAL_IDENTITY",
  "master_resolution": "16K",
  "master_last_modified_at": "2026-03-04T00:00:00.000000000+00:00",
  "policy": "CURRENT_ON_OR_AFTER_2026_03_04",
  "metadata": {
    "status": "VALID",
    "source_file_name": "metadata.txt",
    "sha256": "sha256-puvodnich-bajtu",
    "raw_content": "dekodovany obsah",
    "hex_color": "#A1B2C3",
    "width_cm": "12.5",
    "height_cm": "34",
    "warnings": [],
    "errors": []
  },
  "warnings": [],
  "errors": [],
  "can_continue": true
}
```

`policy` je `LEGACY_BEFORE_2026_03_04` před lokální půlnocí 4. 3. 2026,
jinak `CURRENT_ON_OR_AFTER_2026_03_04`. Čas masteru je UTC ISO 8601 s
nanosekundami. Není-li master bezpečně dostupný, související hodnoty jsou
`null`.

Finding v libovolném poli `warnings`/`errors` má vždy klíče `code`, `path`
a `message`. `path` je pouze normalizovaná relativní klientská cesta; absolutní
serverový kořen se nevrací. Top-level chyby rozhodují o `can_continue`.
Metadata warnings i metadata errors jsou neblokující, takže problém pouze
v `metadata.txt` nikdy nenastaví `can_continue=false`.

## Metadata a limit

Čte se pouze kořenový `<material>/metadata.txt`, vždy binárně a read-only
z již otevřeného descriptoru materiálu. Limit je 4 MiB. Nejprve se kontroluje
velikost pomocí `fstat` otevřeného metadata descriptoru; kvůli souběžnému
růstu souboru je vlastní čtení omezeno na 4 MiB + 1 bajt. Při překročení se
vrátí `metadata.status=INVALID`, chyba `SOURCE_METADATA_TOO_LARGE` a hodnoty
`sha256` i `raw_content` jsou `null`. Nevrací se částečný hash ani obsah.

Po úspěšném UTF-8 dekódování obsahuje `raw_content` přesný dekódovaný vstup,
i když je jeho syntaxe neplatná. Raw obsah se nikdy neloguje. Formáty a
význam stavů parseru jsou popsány v `docs/source-metadata-parser.md`.

## HTTP stavy

| Stav | Význam |
|---:|---|
| 200 | Preflight byl dokončen; zahrnuje chybějící/neplatná metadata a jiné neblokující warnings. |
| 404 | Relativní složka materiálu neexistuje. |
| 422 | Vstupní cesta je neplatná/nebezpečná nebo složku nelze bezpečně zkontrolovat. |
| 503 | `MATERIALS_ROOT` chybí, není absolutní, není adresář nebo není dostupný. |

Kód `SECURE_FILESYSTEM_ACCESS_UNAVAILABLE` je kontrolovaná varianta 422 pro
platformu bez požadovaných descriptorových primitiv.

Chybové odpovědi vytvořené endpointem zachovávají stejný top-level JSON tvar;
neobsahují absolutní cestu ani raw metadata a mají `can_continue=false`.

## Testování

API testy vytvářejí výhradně dočasné lokální adresáře. Nečtou skutečný NAS
ani produkční soubory. Pokrývají relativní a chybějící cestu, Windows i Unix
absolutní cestu, traversal a null byte, symlink únik (pokud jej OS dovolí),
nedostupný kořen, všechny požadované metadata případy, 16K politiku, limit,
read-only snapshot, absenci raw obsahu v logu a přesnou množinu klíčů kontraktu.
Linuxová sada navíc deterministicky mění adresář i metadata mezi prvotní
kontrolou a čtením, ověřuje připoutání k otevřenému inode, uzavření descriptorů,
symlinky, adresář/FIFO místo metadat a omezené čtení. Na nativním Windows jsou
tyto POSIX testy přeskočeny a samostatný test vyžaduje fail-closed odpověď.
