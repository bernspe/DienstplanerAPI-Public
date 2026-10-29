# DienZt – Dienstplaner API (Backend)

REST-Backend für **DienZt**, eine Webanwendung zur Dienstplanung im Gesundheitswesen:
Mitarbeitende hinterlegen Dienstwünsche, Planer erstellen und veröffentlichen Dienstpläne,
Dienste können getauscht und abgerechnet werden.

Das zugehörige Frontend (Vue 3) liegt in einem eigenen Repository:
[bernspe/Dienstplaner](https://github.com/bernspe/Dienstplaner).

## Zweck dieses Repositorys

Dieses Repository dient als **Referenz für Interessierte, die das System nachbauen möchten**.
Es ist kein fertig verpacktes Produkt: Der Code wird ohne Gewähr und ohne Support veröffentlicht,
Zugangsdaten, Domains und Server-Konfiguration sind bewusst nicht enthalten und müssen
selbst gesetzt werden.

## Funktionen

- **Dienstwünsche** (`/wish/`), **Dienstpläne** (`/dienstplan/`), **Diensttausch** (`/trade/`)
- **Einrichtungen und Dienstmodelle** (`/institution/`, `/dienstmodell/`)
- **Monatsabrechnung** (`/abrechnung/`) mit Stunden- und Vergütungsdaten (verschlüsselt gespeichert) und Freigabe durch die Verwaltung
- **Dienstplanservice** (`/dienstplanservice/`) für die automatisierte Planerstellung
- **Benutzer- und Rechteverwaltung** mit Gruppen (`manage.py create_groups`)
- **Authentifizierung** per OAuth2 (django-oauth-toolkit, Password-Grant), E-Mail-Token,
  Passwort ändern/zurücksetzen und optional Zwei-Faktor über [privacyIDEA](https://www.privacyidea.org/)
- **Verschlüsselte JSON-Felder** für sensible Daten (django-encrypted-json-fields)
- **Hintergrundaufgaben** mit Celery + Celery Beat (Redis), z. B. Token-Bereinigung und die automatische Dienstplanerstellung im Hintergrund
- **Datenbank-Backups** per FTP (django-dbbackup)

## Technik

| Bereich | Eingesetzt |
|---|---|
| Sprache / Framework | Python 3.11, Django 5.0, Django REST Framework 3.15 |
| Auth | django-oauth-toolkit 2.3, privacyIDEA (optional) |
| Daten | SQLite (Entwicklung), PostgreSQL 14 (Produktion) |
| Queue | Celery 5.4, django-celery-beat, Redis |
| Auslieferung | gunicorn, nginx, Docker Compose |

## Schnellstart (lokale Entwicklung)

Voraussetzungen: Python 3.11, `git` (eine Abhängigkeit wird direkt von GitHub installiert)
und ein lokaler Redis-Server auf `localhost:6379`, falls Celery-Tasks laufen sollen.

```bash
git clone https://github.com/bernspe/DienstplanerAPI.git
cd DienstplanerAPI

python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # Werte anpassen, siehe „Umgebungsvariablen“
python manage.py generate_encryption_key   # zweimal ausführen → ENC_KEY1 / ENC_KEY2

python manage.py migrate
python manage.py initadmin    # legt das Admin-Konto aus DJANGO_SUPERUSER_* an
python manage.py create_groups
python manage.py createapplication \
    --client-id "<CLIENT_ID>" --client-secret "<CLIENT_SECRET>" \
    --skip-authorization --name DienstplanAuth confidential password

python manage.py runserver     # http://127.0.0.1:8000/
```

Celery-Worker mit Scheduler (optional, benötigt Redis):

```bash
celery -A DienstplanerAPI worker -B -l INFO \
    --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

Mit `APP_ENV=development` (Standard in `.env.example`) läuft das Projekt mit SQLite und
`DEBUG=True`. Das Django-Admin-Interface ist unter `/vw/` erreichbar.

## Betrieb mit Docker Compose

`docker-compose.yml` startet: `db` (PostgreSQL 14), `db-admin` (pgAdmin), `api` (gunicorn),
`worker` (Celery + Beat), `redis`, `nginx`, `privacyidea` und `db-pi`. Der `api`-Container
führt beim Start `collectstatic`, `migrate`, `initadmin`, `create_groups` und
`createapplication` aus und setzt `APP_ENV=production`.

```bash
cp .env.example .env     # alle Werte setzen, insbesondere Passwörter und Schlüssel
docker-compose up -d
```

Vor dem ersten Start anpassen:

- **nginx** (`nginx/default.conf`): `example.org` im Zertifikatspfad
  (`/etc/letsencrypt/live/<domain>/…`) durch die eigene Domain ersetzen. Zertifikate werden aus dem
  Host-Verzeichnis `/etc/letsencrypt` eingebunden und gehören **nicht** ins Repository.
- **Hosts und Origins** über die Variablen `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` und
  `CORS_ALLOWED_ORIGINS` setzen (siehe unten). Im Produktivmodus sind ohne `CORS_ALLOWED_ORIGINS`
  keine fremden Origins erlaubt.
- **pgAdmin** (Port 8080) und **privacyIDEA** (Port 8081) sind nur an `127.0.0.1` gebunden und
  z. B. per SSH-Tunnel erreichbar. Nicht öffentlich freigeben.

## Umgebungsvariablen

Konfiguration über `.env` (Vorlage: [`.env.example`](.env.example)), eingelesen mit
`django-environ`. Variablen ohne Standardwert müssen gesetzt sein, sonst bricht Django beim
Start ab – auch dann, wenn die Funktion (z. B. der zweite Mail-Relay) nicht genutzt wird.

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `APP_ENV` | ja | `production` → PostgreSQL, Redis-Container, `*_DEPLOY`-URLs, `DEBUG=False`; sonst Entwicklungsmodus mit SQLite |
| `APP_VERSION` | Docker | Image-Tag (`dienstplan_api:${APP_VERSION}`) |
| `BASE_URL_LOCAL`, `FRONTEND_URL_LOCAL` | Entwicklung | URLs von Backend und Frontend, u. a. für Links in E-Mails |
| `BASE_URL_DEPLOY`, `FRONTEND_URL_DEPLOY` | Produktion | wie oben, für `APP_ENV=production` |
| `ALLOWED_HOSTS` | Produktion | Komma-getrennte Hostnamen, Standard `localhost,127.0.0.1` |
| `CSRF_TRUSTED_ORIGINS` | Produktion | Komma-getrennte Origins inkl. Schema (`https://…`) |
| `CORS_ALLOWED_ORIGINS` | Produktion | Komma-getrennte Origins des Frontends; im Entwicklungsmodus sind alle erlaubt |
| `SECRET_KEY` | ja | Django-Secret-Key, zufällig erzeugen |
| `DATEFORMAT_INPUT` | ja | Datumsformat der Wunschdaten, z. B. `%d/%m/%Y` |
| `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_PASSWORD` | `initadmin` | Erstes Admin-Konto |
| `CLIENT_ID`, `CLIENT_SECRET` | Docker | OAuth2-Client des Frontends (`createapplication`); muss zum Frontend passen |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Produktion | PostgreSQL-Zugang |
| `PGADMIN_DEFAULT_EMAIL`, `PGADMIN_DEFAULT_PASSWORD` | Docker | pgAdmin-Login |
| `PI_ADMIN_USER`, `PI_ADMIN_PASSWORD` | Docker | privacyIDEA-Administrator |
| `PI_SECRET_KEY`, `PI_PEPPER` | Docker | Interne Schlüssel von privacyIDEA, zufällig erzeugen |
| `PI_OTP_PIN` | ja | Statischer PIN-Anteil, der bei der OTP-Prüfung vorangestellt wird |
| `ENC_KEY1`, `ENC_KEY2` | ja | Schlüssel für verschlüsselte Felder (`generate_encryption_key`) – gehen sie verloren, sind die Daten nicht mehr lesbar |
| `EMAIL_RELAY` | ja | `gmail` (yagmail) oder `renecol` (eigener SMTP-Server mit DKIM) |
| `GMAIL_USERNAME`, `GMAIL_PASSWORD` | ja | Zugang für `EMAIL_RELAY=gmail` (App-Passwort) |
| `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_FROM` | ja | Eigener SMTP-Server |
| `DKIM_PRIVATE_KEY_FILE`, `DKIM_SELECTOR` | ja | Pfad zum privaten DKIM-Schlüssel (nicht im Repo) und Selector |
| `FTP_BACKUP_CONNECTION` | ja | Ziel für `dbbackup`, Format `ftp://user:passwort@host:21/pfad` |

> **Sicherheit:** `.env`, Zertifikate, DKIM-Schlüssel und Datenbankdateien dürfen nie committet
> werden (siehe `.gitignore`). Die DRF-Standardberechtigung ist `AllowAny` – vor einem
> Produktivbetrieb die Berechtigungen der einzelnen Views prüfen.

## API-Überblick

| Pfad | Zweck |
|---|---|
| `/users/`, `/institution/`, `/dienstmodell/`, `/abrechnung/` | Benutzer, Einrichtungen, Dienstmodelle, Abrechnung |
| `/wish/`, `/dienstplan/`, `/trade/` | Wünsche, Dienstpläne, Diensttausch |
| `/dienstplanservice/` | Automatisierte Dienstplanerstellung |
| `/register/`, `/userinfo/`, `/changepassword/`, `/forgotpassword/`, `/setnewpassword/` | Konto-Funktionen |
| `/o/` (inkl. `/o/token/`), `/o/emailtoken/` | OAuth2 und E-Mail-Token |
| `/vw/` | Django-Admin |

## Projektstruktur

```
DienstplanerAPI/   Django-Projekt (settings, urls, celery)
users/             Benutzer, Institutionen, Dienstmodelle, Abrechnung, Management-Befehle
dienstplan/        Wünsche, Dienstpläne, Diensttausch, Celery-Tasks
nginx/             nginx-Konfiguration für Docker Compose
templates/         E-Mail-Vorlagen (Dienstplan, Diensttausch, Benutzer)
```

## Lizenz

Veröffentlicht unter der [PolyForm Noncommercial License 1.0.0](LICENSE). Nicht-kommerzielle
Nutzung, Änderung und Weitergabe sind erlaubt, etwa zum Lernen, für Forschung und Nachbauten
ohne kommerzielle Absicht sowie für die im Lizenztext genannten Einrichtungen. Für jede
kommerzielle Nutzung ist eine gesonderte Lizenz beim Autor (Peter Bernstein) erforderlich.
Maßgeblich ist allein der Lizenztext. Die Lizenz ist keine Open-Source-Lizenz im Sinne der OSI;
eingesetzte Abhängigkeiten behalten ihre eigenen Lizenzen.
