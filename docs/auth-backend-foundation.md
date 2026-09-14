# Backendový základ autentizace

Tato větev přidává credentials a serverové sessions, ale záměrně ještě
nezamyká existující Company, Brand, Project, InternalUser, Material ani Material
Operations endpointy. Následná větev po sloučení login UI zapne ochranu všech
`/api/**` rout a doménová oprávnění.

## Datový a kryptografický návrh

`internal_users` zůstává profilem a zdrojem role. `user_credentials` je 1:1
tabulka s Argon2id PHC hashem a stavem povinné změny hesla. Existující uživatel
bez řádku credentials se nemůže přihlásit. `auth_sessions` obsahuje UUID,
uživatele, pouze SHA-256 otisk náhodného 256bitového session tokenu, synchronizer
CSRF token, časová omezení a revokaci. CSRF token je serverový stav a nikdy se
neloguje; raw session token databáze nikdy neobsahuje.

Heslo se před validací i hashováním normalizuje Unicode NFKC. Minimum je 15 a
maximum 256 Unicode znaků; mezery a Unicode jsou povolené a nejsou vyžadované
žádné třídy znaků. Offline blocklist v `backend/app/auth/common_passwords.txt`
obsahuje často kompromitované rodiny hesel a je verzovaný, auditovatelný a lze
jej samostatně aktualizovat z důvěryhodného offline zdroje. Vedle přesné shody se
odmítá opakování jediného znaku a heslo obsahující `reawote`, lokální část e-mailu
nebo významnou část display name. Síťový lookup kompromitovaných hesel se při
loginu neprovádí. Výchozí Argon2id parametry jsou 19 MiB, dva průchody a jedna
paralelní lane; všechny jsou konfigurovatelné pouze nad bezpečnými minimy.

Session token vzniká přes CSPRNG s 256 bity entropie. Idle timeout je 30 minut,
absolute timeout osm hodin a zápis `last_seen_at` je standardně omezen na jednou
za 60 sekund. Změna hesla revokuje všechny sessions. Deaktivovaný uživatel je při
dalším použití session odmítnut a session je revokována.

## API kontrakt

- `POST /api/auth/login` přijímá `email` a `password`. Vyžaduje důvěryhodný
  `Origin`/`Referer`, případně same-site Fetch Metadata. Úspěch vrací veřejný
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

Všechny auth odpovědi, včetně chyb, mají `Cache-Control: no-store`. Login rate
limit používá atomický databázový čítač podle normalizovaného účtu a adresy
bezprostředního klienta, takže je sdílený mezi backend procesy a po skončení
pevného okna sám přestane blokovat. Aplikace záměrně nedůvěřuje klientem dodanému
`X-Forwarded-For`; produkční reverse proxy musí zachovat spolehlivý bezprostřední
client identity mechanismus nebo navazující hardening doplní explicitní seznam
důvěryhodných proxy.

Produkční cookie je session-only `__Host-reawote_session`, `HttpOnly`, `Secure`,
`SameSite=Strict`, `Path=/` a bez `Domain`. `APP_ENV=production` odmítne start s
`AUTH_COOKIE_SECURE=false`, wildcard CORS nebo ne-HTTPS originem. Explicitní
výjimka `AUTH_COOKIE_SECURE=false` je určena pouze pro lokální HTTP development,
demo a test. Vite proxy zachovává browserový same-origin tok `/api` a cookie
nepotřebuje úpravu frontendu na této větvi.

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
se nevkládají do log message ani structured fields. Plná perzistentní auditní
tabulka, důvěryhodná proxy identita, distribuce/rotace rozšířeného offline
blocklistu a doménové RBAC patří do samostatné hardening větve po login UI.
