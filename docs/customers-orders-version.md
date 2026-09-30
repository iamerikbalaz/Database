# Customers / Orders — testovací verze

## Doplnění GUI a identit Customers (30. 9. 2026)

- Customers mají rozsahy Created a Updated (od/do, včetně koncového dne v UTC).
  Jméno se v seznamu pouze otevírá. Změna je na kartě přes **Edit name** a potvrzovací dialog.
- Nový název nastaví prefix nových materiálů. Starší materiály si bez výslovné volby
  ponechají technický název, cestu a původní výrobce v `metadata.json`, i při další editaci
  vlastního jména materiálu. Historické prefixy zůstávají rezervované pro původního Customer.
- Volba přejmenovat starší materiály používá souborový žurnál pro každou položku.
  Dokončené položky jsou unpublished; změna není atomická napříč všemi složkami.
  Nejistý výsledek má obnovu stejné operace. Po částečném neúspěchu nový potvrzený plán
  zahrne jen zbývající rozdíly. Původní výsledky se nemažou. Kořenové složky výrobců
  ani složky existujících Orders se tím automaticky nepřejmenovávají.
- Materials, archiv, Customers, Orders a Catalog používají horní vodorovný posuvník
  s hlavičkou. Svisle se posouvá celá stránka bez pevné výšky tabulky. Hromadné změny
  jsou vidět po výběru položek a používají společné ovládací prvky a šířky.
- Settings jsou rozcestník pro Automatic ZIP packaging, Accounts a Imports.
  Mobilní navigace se zavře tlačítkem, Escape nebo klikem mimo menu.
- Ve skutečné lokální testovací databázi bylo přiřazeno 124 ověřených log z veřejné
  galerie [REAWOTE Brands](https://reawote.com/brands), vždy podle jednoznačné shody názvu.
  Dvě nejednoznačné shody a 177 zákazníků bez přesné shody zůstaly beze změny.
- Processors jsou sjednoceni na sedm aktivních osob podle zadání. Starší přiřazení
  a historie zůstávají zachované; bývalé pracovnice a nahrazené duplicitní účty jsou
  neaktivní. Při sloučení zkratek stejné osoby se zachovaly výsledky kontrol souborů.

Tato verze přidává schéma 0036. Záloha `before-schema0036-apply.private.dump` obsahuje stav
po přiřazení log a osob, před migrací. Downgrade odmítne zahodit historii přejmenování
nebo odlišné historické jméno výrobce. V případě návratu obnovte zálohu do dalšího
vlastního testovacího kontejneru a použijte aplikaci schématu 0035; souborové změny
provedené uživatelem po záloze vyžadují samostatné smíření přes jejich žurnály.

## Co se mění

- Projects se jmenují Orders; Companies a Brands nahrazuje jedna úroveň Customers.
- Zachované jsou identifikátory materiálů, jejich číselné řady, odkazy na složky a historie.
- Orders mají vlastnosti podle Notionu: číslo, Customer, Project type, Starting date, Due date, Note, Responsible, Status a Priority.
- Generated Name odpovídá `upper(join([Number, Cutomer rollup, Project type, formatDate(Starting date, "MMYYYY")], "_"))`.
- Customers mají profilová pole, Brand Identifier, nahrání loga a kategorie odvozené z Main category přiřazených materiálů, včetně archivovaných.
- Tabulky Customers, Orders a Catalog mají společné ovládání výběru, Ctrl/Shift zvýraznění, Properties, přímé editace a hromadné změny. Catalog nevyžaduje důvod změny; audit zůstává.

## Úvodní převod z Notionu

Bylo načteno 294 řádků Customers a 162 řádků Orders. Tři zákazníci bez jména a dva řádky bez platného čísla zakázky nejsou skutečné importovatelné záznamy.

Po ověření na izolované kopii byl převod 30. 9. 2026 proveden i v místní testovací aplikaci na `http://127.0.0.1:53033`: **303 Customers, 282 Orders a nezměněných 50 materiálů**. Sedm nepoužívaných duplicitních brandů je skryto; původní záznamy a jejich historické vazby se nemažou. U zakázek se zachovává všech 252 původních cest. Osm záznamů People je propojeno s odpovědnými osobami; nově přidaným osobám se nevytváří přihlašovací heslo.

K ručnímu dořešení zůstává:

- Jeden zákazník má rozdílný skutečný Brand Identifier v místních materiálech a v Notionu. Záznamy zůstávají oddělené do vyjasnění.
- Dvě čísla zakázek se v Notionu opakují, celkem na čtyřech stránkách.

Čtyři stránky s duplicitními čísly se zatím nepropojují. Nejednoznačné párování se nikdy neřeší přepsáním posledním nalezeným řádkem.

## Jednosměrný zápis a složky

Po úvodním převodu je aplikace zdrojem pravdy. Změny společných properties ukládá do trvalé fronty pro Notion; obsah stránek a klientské příspěvky zůstávají zachované. Dřívější převzetí změn Notion → aplikace je vypnuté.

Automatický zápis vyžaduje vlastní Notion integration token. Připojení Codexu se nepoužívá jako přihlašovací údaj aplikace. Bez tokenu aplikace ukládá změny lokálně a zobrazuje vypnutou synchronizaci.

V desktopovém testu je připraven soubor `tmp/local-materials-v5-20260928/notion-connection.private.json`. Token patří do `access_token`, následně nastavte `enabled` na `true` a restartujte testovací server. Token neposílejte do chatu a neukládejte do Gitu. Integraci zpřístupněte Customers, Orders a People s oprávněním číst, vytvářet a aktualizovat záznamy. Podrobnosti jsou v [nastavení synchronizace](customers-orders-outbound.md).

Nové Orders mohou vytvářet složky pod `R:\0. PROJECTS`. Existující složky se při importu nepřejmenovávají. Změna vlastností může změnit Generated Name; fyzické přejmenování se provede samostatným potvrzením na kartě Order. Kolize názvu ani nejistý výsledek operace se nepřepisují automaticky.

Přejmenování Customer nyní řeší potvrzená operace popsaná výše. Přímý PATCH jména je odmítnut i přes starší kompatibilní API. Ostatní profilová pole lze upravovat. Změna Responsible nahradí dřívější vícečetný seznam osob jedním vybraným processorem; při úpravě jiných polí zůstává celý původní seznam zachovaný.

Stejnou ochranu respektuje úvodní import: u 16 propojených zákazníků zachovává původní místní zápis jména, protože je už uložený v `metadata.json`. Rozdílný zápis v Notionu není důvodem pro tiché přejmenování. Ostatní profilová data a vazby se naimportují; přesné rozdíly obsahuje privátní report.

Notion templates, OneDrive odkazy a ZIP workflow jsou ponechány na další krok.

## Ověření a návrat

Frontend prošel 1 108 testy, lintem a sestavením; následné drobné úpravy výběru zákazníka prošly 20 cílenými testy.

- Backend: 110 cílených testů adresáře, Catalogu, publikačního ověření a zachování původní historie; 41 testů synchronizace, složek, API a ochrany migrace prošlo. Jeden test symlinku byl přeskočen kvůli oprávnění Windows.
- Širší PostgreSQL sada: 343 kontrol prošlo při prvním běhu; 24 selhání zastaralých testů bylo opraveno a všech 24 pak prošlo cíleným opakováním. Mezi nimi je 27 úspěšných kontrol autentizace. Výběr neobsahoval všechny historické testy downgrade/metadat.
- Nové PostgreSQL kontroly: 6 testů číslování, souběžných změn, potvrzení příkazů a migrace; 2 testy souběhu synchronizačních závislostí. Vše prošlo.
- Kopie i skutečná místní testovací instance prošly migrací 0033 → 0035, kontrolou Alembic a kontrolou importovaných vazeb. Po restartu prošlo přihlášení, Customers/Orders/Materials API, historie, CORS pro nahrávání loga a dostupnost sestavených souborů UI.

Nové vizuální E2E nebylo v tomto běhu dostupné. Zápis do skutečného Notionu není bez tokenu ověřen; operace složek se testovaly na dočasných testovacích adresářích. Import ani uvedené ověření nevytvořily žádnou skutečnou složku zakázky a neprovedly žádný zápis do Notionu.

Před migrací je uložen privátní dump testovací databáze `tmp/local-materials-v5-20260928/before-live-directory.private.dump`. Konkrétní nejednoznačné záznamy jsou pouze v privátním importním reportu ve stejné složce; zákaznická data nepatří do Gitu.

Po importu nelze bezpečně zahodit nové sloupce pomocí downgrade; migrace chrání nová data i historii. Při návratu nejprve zastavte vlastní testovací server a zazálohujte jeho aktuální databázi. Obnovte původní dump do nového označeného testovacího kontejneru, přepněte soukromou runtime konfiguraci na tento kontejner a spusťte odpovídající předchozí aplikaci (commit `739feec`, schéma 0033). Nezasahujte do chráněných původních databázových kontejnerů. Novější změny a případné složky vytvořené uživatelem po migraci vyžadují samostatné smíření; návrat je nesmí automaticky mazat.
