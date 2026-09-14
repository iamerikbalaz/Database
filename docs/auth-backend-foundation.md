# Backendový základ autentizace

Tato větev přidává credentials a serverové sessions, ale záměrně ještě
nezamyká existující Company, Brand, Project, InternalUser, Material ani Material
Operations endpointy. Následná větev po sloučení login UI zapne ochranu všech
`/api/**` rout a doménová oprávnění.
`must_change_password` je v této foundation větvi pouze informace vrácená pro
budoucí UI. Její hodnota ještě nevynucuje změnu hesla ani neomezuje přístup
k existujícím veřejným resource endpointům.

## Datový a kryptografický návrh

`internal_users` zůstává profilem a zdrojem role. `user_credentials` je 1:1
tabulka s Argon2id PHC hashem a stavem povinné změny hesla. Existující uživatel
bez řádku credentials se nemůže přihlásit. `auth_sessions` obsahuje UUID,
uživatele, pouze SHA-256 otisk náhodného 256bitového session tokenu, synchronizer
CSRF token, časová omezení a revokaci. CSRF token je serverový stav a nikdy se
neloguje; raw session token databáze nikdy neobsahuje.

Auth používá pro každý e-mail jednu funkci `normalize_email`: odstraní počáteční
a koncové mezery a použije Unicode `lower()`, stejně jako dosavadní uložení
`InternalUser.email`. Bootstrap, provisioning i login používají stejnou hodnotu
pro vyhledání, uložení a rate limit. E-maily se nenormalizují NFKC, NFC ani
`casefold()`: například `groß@example.invalid` zůstává odlišný od
`gross@example.invalid` a složený a rozložený zápis diakritiky se neslučují.
Tím se nemění význam ani jedinečnost dosavadních e-mailů. Varianty lišící se
pouze velikostí písmen nebo okrajovými mezerami vyhledají stejný profil.

Heslo se v CLI, loginu i změně hesla před validací i hashováním normalizuje
stejnou funkcí Unicode NFKC. Minimum je 15 a maximum 256 normalizovaných Unicode
znaků; délka původního kombinujícího zápisu nezpůsobuje odlišné rozhodnutí mezi
CLI a HTTP. Mezery a Unicode jsou povolené a nejsou vyžadované
žádné třídy znaků. Offline blocklist v `backend/app/auth/common_passwords.txt`
obsahuje často kompromitované rodiny hesel a je verzovaný, auditovatelný a lze
jej samostatně aktualizovat z důvěryhodného offline zdroje. Vedle přesné shody se
odmítá opakování jediného znaku a heslo obsahující `reawote`, lokální část e-mailu
nebo významnou část display name. Síťový lookup kompromitovaných hesel se při
loginu neprovádí. Výchozí Argon2id parametry jsou 19 MiB, dva průchody a jedna
paralelní lane; všechny jsou konfigurovatelné pouze nad bezpečnými minimy.

Před normalizací se odmítne vstup nad 4608 původních znaků, aby extrémní vstup
nezatěžoval Unicode normalizaci. Tento limit zachovává všechny reprezentace,
které se vejdou do 256 znaků po NFKC. Login ověřuje existující hash a znovu na
něj neuplatňuje blocklist ani pravidla pro nově vytvářené heslo; aktualizace
blocklistu tedy sama neznepřístupní platné credentials.

Session token vzniká přes CSPRNG s 256 bity entropie. Idle timeout je 30 minut,
absolute timeout osm hodin a zápis `last_seen_at` je standardně omezen na jednou
za 60 sekund. Konfigurace vyžaduje, aby interval aktualizace nepřekročil polovinu
idle timeoutu; například pro idle timeout jednu minutu je maximum 30 sekund.
Změna hesla revokuje všechny sessions. Deaktivovaný uživatel je při
dalším použití session odmítnut a session je revokována.

Login a změna hesla serializují přístup pomocí PostgreSQL `SELECT ... FOR UPDATE`
nad týmž řádkem `user_credentials`, ještě před načtením a ověřením aktuálního
hashe. Credentials zámek se drží do konce transakce: login v ní vytvoří session;
změna hesla v ní změní hash, `must_change_password`, `password_changed_at` a
revokuje všechny sessions. Pokud operace zamyká credentials i sessions, pořadí
je vždy credentials a poté sessions. Čekající operace znovu načte aktuální hash.
Login dokončený před změnou má session revokovanou; login čekající na změnu již
ověřuje nové heslo. Dvě změny se stejným původním heslem proto nemohou obě uspět.
Časy se načítají v UTC přes databázové `clock_timestamp()` po získání potřebných
zámků, nikoli přes transakční `now()` před čekáním. Revokace nikdy nepředchází
`created_at`, což navíc vynucuje databázový constraint.
Význam zámků a rozdíl mezi transakčním a skutečným časem popisuje dokumentace
[PostgreSQL row locks](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-ROWS)
a [časových funkcí](https://www.postgresql.org/docs/current/functions-datetime.html#FUNCTIONS-DATETIME-CURRENT).

## API kontrakt

- `POST /api/auth/login` přijímá `email` a `password`. Vyžaduje důvěryhodný
  `Origin`/`Referer`, případně `Sec-Fetch-Site: same-origin`. Úspěch vrací veřejný
  profil, roli, `must_change_password` a CSRF token a nastaví session cookie.
  Neexistující účet, špatné heslo, chybějící credentials i deaktivace vracejí
  stejné `401`; neexistující credentials používají dummy Argon2 ověření.
- `GET /api/auth/session` vrací stejný session profil a CSRF token, jinak `401`.
- `POST /api/auth/logout` vyžaduje platnou session, důvěryhodný původ a
  `X-CSRF-Token`, serverově session revokuje a odstraní cookie. Opakování se
  stejnou již revokovanou cookie vrací `401`.
- `POST /api/auth/change-password` navíc ověřuje současné heslo. Po úspěchu
  nastaví `must_change_password=false`, revokuje všechny sessions, odstraní
  cookie a vyžaduje nový login.

Login je výjimka ze synchronizer CSRF: před vytvořením session ještě žádný
session synchronizer token neexistuje. Login CSRF místo něj brání přesné
porovnání `Origin` nebo původu `Referer` s povolenými browser origins. Pouze
pokud oba chybí, připouští se Fetch Metadata `same-origin`; `same-site`, cizí
původ i požadavek bez důkazu původu jsou odmítnuty. Nepoužívá se statický nebo
falešný CSRF token. Autentizovaný logout a změna hesla nadále vyžadují skutečný
session synchronizer CSRF token v hlavičce `X-CSRF-Token` i kontrolu původu.

Všechny auth odpovědi, včetně chyb, mají `Cache-Control: no-store`. Login rate
limit používá atomický databázový čítač podle normalizovaného účtu a adresy
bezprostředního klienta, takže je sdílený mezi backend procesy a po skončení
pevného okna sám přestane blokovat. Aplikace záměrně nedůvěřuje klientem dodanému
`X-Forwarded-For`; produkční reverse proxy musí zachovat spolehlivý bezprostřední
client identity mechanismus nebo navazující hardening doplní explicitní seznam
důvěryhodných proxy.

Výchozí cookie v každém prostředí je session-only `__Host-reawote_session`,
`HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/` a bez `Domain`.
`AUTH_COOKIE_SECURE=true` a `AUTH_ALLOW_INSECURE_COOKIE=false` jsou bezpečné
výchozí hodnoty; samotné `APP_ENV=development` není bezpečnostní výjimka.
`APP_ENV=production` vždy vyžaduje Secure cookie a HTTPS browser origins.

Pouze pro HTTP na jednom lokálním počítači je možné výslovně nastavit obě
proměnné:

```dotenv
APP_ENV=development
CORS_ORIGINS=http://localhost:5173
AUTH_COOKIE_SECURE=false
AUTH_ALLOW_INSECURE_COOKIE=true
```

Pokud dosavadní lokální `.env` obsahuje pouze `AUTH_COOKIE_SECURE=false`, nový
backend odmítne start. Pro localhost HTTP doplňte uvedený explicitní opt-in;
pro HTTPS nastavte `AUTH_COOKIE_SECURE=true` a ponechte výjimku vypnutou.

Tato výjimka vyžaduje `APP_ENV` z množiny `development`, `test`, `demo`, `e2e`
a každý povolený origin musí mít přesně host `localhost`, `127.0.0.1` nebo
`::1` (například `http://[::1]:5173`). Pak se použije odlišná cookie
`reawote_dev_session`, stále `HttpOnly`, `SameSite=Strict`, `Path=/` a bez
`Domain`. Název cookie nelze konfigurovat ani změnit po validaci; cookie
s prefixem `__Host-` se bez Secure nikdy nenastavuje.

LAN, VPN a vzdálené demo servery vyžadují HTTPS a Secure cookie i při
`APP_ENV=development` nebo `demo`. Insecure režim odmítá LAN IP, vzdálený
hostname, alternativní zápis loopback adresy, wildcard a UNC cestu; jediný
vzdálený origin v seznamu odmítne celou insecure konfiguraci. Chyby konfigurace
se projeví při startu backendu. Běžný Compose předává backendu všechny zdejší
`AUTH_*` proměnné a konfigurovatelné `APP_ENV`. Vite proxy zachovává browserový
same-origin tok `/api` a cookie nevyžaduje úpravu frontendu na této větvi.

## První administrátor

Po aplikaci migrací spusťte uvnitř backendového prostředí:

```powershell
python -m app.auth.cli --email admin@example.invalid --display-name "REAWOTE Admin"
```

Heslo se dvakrát načte přes `getpass` bez echo a není argumentem procesu ani
výstupem. Příkaz vytvoří nový profil nebo převezme existující profil bez
credentials, nastaví roli `ADMIN`, aktivuje jej a nastaví
`must_change_password=true`. Jakmile existují jakékoli credentials, příkaz se
odmítne spustit; nejde tedy použít jako skrytý reset ani opakovaný bootstrap.
PostgreSQL advisory transaction lock navíc serializuje souběžné pokusy o první
provisioning.
Veřejná registrace, HTTP bootstrap, e-mailový reset a MFA nejsou součástí této
větve.

## Audit a navazující omezení

Strukturovaný logger `reawote.auth` zapisuje pouze typ události, interní user ID
a bezpečný důvod: login success/failure, deaktivovaný účet, logout, změnu hesla
a expiraci/revokaci session. Heslo, hash hesla, raw session token ani CSRF token
se nevkládají do log message ani structured fields. Centrální sanitizace
validačních odpovědí zachovává 422 kontrakt pro běžná pole, ale nevrací citlivé
hodnoty ani ve vnořených chybách nebo při více chybách současně. Původní
`RequestValidationError` se neloguje; jeho `input` a text mohou obsahovat heslo.
Plná perzistentní auditní tabulka, důvěryhodná proxy identita,
distribuce/rotace rozšířeného offline
blocklistu a doménové RBAC patří do samostatné hardening větve po login UI.

## Ověření na PostgreSQL

Integrační testy používají skutečné PostgreSQL a náhodně pojmenované izolované
testovací databáze, které po skončení odstraní. Concurrency testy pozastaví
držitele credentials zámku a přes PostgreSQL ověří, že druhý požadavek opravdu
čeká na tento zámek. Kontrolují výsledky obou HTTP požadavků, revokaci sessions,
časové constrainty i další použitelnost transakcí.

Z kořene repozitáře v hostitelském PowerShellu s běžícím Docker Desktop spusťte:

```powershell
docker compose up -d --wait database
docker compose build backend
docker compose run --rm --no-deps -e RUN_POSTGRES_TESTS=1 backend pytest tests/test_auth_postgresql.py tests/test_materials_postgresql.py
```

Obě sady společně ověřují souběhy auth, databázové constraints, fresh Alembic
upgrade, přechod `0005` → `0006` a `alembic current`, `heads`, `check`.
`scripts/test.ps1` spouští PostgreSQL režim pouze pro dosavadní materiálové testy,
proto je výše uvedený explicitní příkaz nutný i po úspěchu celého skriptu.
Bez `RUN_POSTGRES_TESTS=1` se PostgreSQL sady přeskočí; úspěch SQLite testů ani
výsledek `skipped` nepředstavuje ověření PostgreSQL concurrency.
