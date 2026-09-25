# Vizuální styl REAWOTE

Rozhraní používá dodaný brand manuál REAWOTE (strany 01–05), originální
`reawote-logo-long.png` a fonty z dodaného `Poppins.zip`. Referencí pro světlé
plochy, zaoblené ovládání a galerii je [reawote.com](https://reawote.com/textures),
vizuálně zkontrolovaný 25. 9. 2026.

## Společná pravidla

| Prvek | Použití |
|---|---|
| `#1F2444` | Základní text a nadpisy |
| `#394173` | Primární akce, vybraný režim a indikace zaměření klávesnicí |
| `#9093B9` | Dekorativní levandulová a zvýraznění okraje |
| `#DDE0ED` | Vybraná navigace, jemné okraje |
| `#F2F3F9`, bílá | Pozadí aplikace, bílé panely a navigace |
| `#4CBC81`, `#EA8181`, `#99B3F8` | Úspěch, chyba a informační stav |

Malé popisky používají tmavší odvozenou barvu `#626789`, aby zůstaly čitelné.
Stavová hlášení mají světlé pozadí a tmavý text; barva doplňuje slovní popis.
Varování si zachovávají samostatnou jantarovou barvu.

Poppins Bold a tracking `-0.04em` sjednocují nadpisy. Drobné názvy sekcí mají
verzálky a tracking `0.2em`. Formulářová pole používají také dodaný Poppins Light;
popisky, tabulky a navigace využívají čitelnější řezy 400–600. Technické hashe a
identifikátory na místech určených pro monospace zachovávají technické písmo.

Styl je společný pro přihlášení, dashboard, firmy, projekty, materiály, katalog,
importy, publikaci, nastavení, dialogy a chybové/prázdné stavy. Galerie i seznam
zachovávají filtry, velikosti náhledů a ovládání obrázků. Mobilní navigace používá
stejné logo a barvy; dlouhé jméno účtu se v hlavičce zkracuje s úplným tooltipem.

## Soubory a načítání

- Pravidla a barevné proměnné: `frontend/src/styles.css`.
- Originální horizontální logo: `frontend/src/assets/brand/reawote-logo-long.png`.
  Aplikace zachovává poměr stran a nevyžaduje přístup na NAS.
- Pět lokálních TTF řezů Poppins 300/400/500/600/700 v
  `frontend/src/assets/fonts/`, celkem 716 028 bajtů. Prohlížeč načítá používané
  řezy a `font-display: swap` dovoluje vykreslení textu před dokončením načtení.
  Vite vytváří soubory s hashem pod `/assets`; fonty nevyžadují externí službu.
- SIL Open Font License je u zdrojů a také v
  `frontend/public/assets/Poppins-OFL.txt`, odkud se kopíruje do sestavení.
- Dodané logo je firemní asset REAWOTE; licence fontu se na logo nevztahuje.

Změna se týká frontendové prezentace. Nemění databázové schéma, pracovní postupy,
oprávnění ani obsah a strukturu složek materiálů. Vizuální kontrola používá
lokální testovací instanci se 100 materiály; její náhledy nadále pocházejí z
datované lokální kopie popsané v [testu R:](historical-r100-acceptance.md).

## Ověření

Proběhla kontrola skutečného katalogu, dashboardu, formuláře materiálu, seznamu,
galerie, přepnutí obrázku a mobilní navigace. Při viewportu 390 × 844 nebylo
vodorovné přetékání galerie ani katalogu. Zkrácení dlouhého účtu bylo ověřeno
proti výšce hlavičky. Dočasná změna viewportu byla vrácena.

Konkrétní výsledky automatických testů a případné opakované běhy uvádí
[aktuální checkpoint](autonomous-pbr-progress.md).
