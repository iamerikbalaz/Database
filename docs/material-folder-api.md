# Propojeni slozky materialu a Mark as Done

Backend vola read-only worker operaci `POST /internal/material-preflight` na
`WORKER_BASE_URL`. Timeout je kratky (vychozi dve sekundy, konfigurovatelny pres
`WORKER_TIMEOUT_SECONDS`). Odpoved se cte streamovane v blocich. Backend nejprve
zkontroluje `Content-Length`, pokud je pritomen, a soucasne pri cteni hlida
dekodovanou velikost. Po prekroceni 5 MiB cteni ihned ukonci a response uzavre;
stejne je omezeno telo bez `Content-Length` i chunked response. Nedostupnost,
timeout, non-2xx odpoved, prilis velke telo nebo odpoved neodpovidajici presne
schematu v1 vraci z verejneho API `503`.

Worker request:

```json
{"folder_path": "library/BRAND_0001_G03"}
```

Worker response v1 ma presne pole `schema_version`, `folder_name`,
`master_resolution`, `policy`, `metadata_status`, `source_filename`, `sha256`,
`raw_content`, `hex_color`, `width_cm`, `height_cm`, `warnings`, `errors` a
`can_continue`. Neznama nebo chybejici pole jsou neplatna. `can_continue` musi byt
presne ekvivalentem prazdneho seznamu `errors`. Backend raw obsah nikdy neloguje.

## Verejne endpointy

Vsechny pozadavky s cestou prijimaji pouze relativni POSIX cestu bez prazdnych,
`.` nebo `..` segmentu:

```json
{"folder_path": "library/BRAND_0001_G03"}
```

`POST /api/materials/{id}/folder-preflight` je read-only. Pri platne worker
odpovedi vraci `200` a stejna pole jako worker krome `raw_content`, navic pole
`identity_matches`. Backend porovna presnou rovnosti `worker.folder_name` s
`material.technical_identity` nactenou z databaze. Pri shode je
`identity_matches=true` a `can_continue` zachovava vysledek workeru. Pri neshode
je `identity_matches=false`, `can_continue=false` a `errors` obsahuje
`TECHNICAL_IDENTITY_MISMATCH` s `expected_technical_identity` a
`actual_folder_name`; tento diagnosticky vysledek stale vraci `200`. Blokujici
filesystem nalez je soucasti odpovedi (`can_continue=false`, `errors`) a sam o
sobe nemeni HTTP status ani databazi.

`POST /api/materials/{id}/folder-link` vzdy provede novy worker preflight. Uspech
vraci `200`:

```json
{
  "material": {"id": "...", "folder_path": "library/BRAND_0001_G03"},
  "preflight": {"schema_version": 1, "folder_name": "BRAND_0001_G03"}
}
```

Objekty ve skutecne odpovedi obsahuji vsechna pole prislusnych verejnych schemat.
`preflight` nikdy nema `raw_content`. `folder_name` se musi presne rovnat
`technical_identity`; jinak endpoint vraci `409`. Metadata warnings linkovani
neblokuji. `can_continue=false` vraci `422` a cesta se neulozi.

`POST /api/materials/{id}/mark-done` nema request body. Vyžaduje propojenou slozku
a provede novy worker preflight. Uspech vraci `200` s objekty `material`,
`metadata`, `snapshot` a sanitizovanym `preflight`. Chybejici nebo neplatny
`metadata.txt` a metadata warnings se ulozi a neblokuji `DONE`. Blokujici chyba
slozky vraci `422`; chybejici link, zmena revize behem preflightu, nesoulad identity
nebo jiz dokonceny material vraci `409`.

## Transakce a data

Sitove volani probiha pred otevrenim finalni zapisove transakce. Ta zamkne radek
`pbr_materials` pomoci `FOR UPDATE` a znovu porovna `folder_path`,
`technical_identity`, `workflow_status` a `updated_at` s revizi pred preflightem.
Pri rozdilu se nic nezapise.

Mark as Done pod stejnym lockem alokuje dalsi `sequence_number`, vlozi immutable
snapshot, nastavi jej jako current metadata a prepne pouze `workflow_status` na
`DONE`. Raw text se uklada do `source_content`, ale zadne verejne metadata schema
jej nevraci. `validation_status` ani `publication_status` se nemeni. Snapshot,
current metadata a workflow se potvrdi jedinym commitem; pri chybe se cela
transakce vrati zpet. Backend neprovadi zadny zapis do NAS.

Neexistujici material vraci `404`, neplatny request `422` a nedostupny nebo
protokolove neplatny worker `503`. Pro tyto operace neexistuje `DELETE`.
