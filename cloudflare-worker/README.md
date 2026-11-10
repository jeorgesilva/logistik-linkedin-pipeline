# Asynchrone Warteschlange — Deployment des Cloudflare Workers

Dieser Worker ist die immer erreichbare „Rezeption“: Er nimmt den Klick auf „Posts generieren“
eines Teammitglieds auch dann entgegen, wenn dein Rechner ausgeschaltet ist, speichert die
Anfrage, und die `engine-api` (die auf deinem Rechner läuft) fragt diese Anfrage regelmäßig ab,
sobald sie online ist.

Die folgenden Schritte erfordern dein Cloudflare-Konto — sie lassen sich nicht automatisieren und
sind hier nur dokumentiert.

## 1. Voraussetzungen

```bash
npm install -g wrangler
wrangler login
```

## 2. KV-Namespace anlegen

```bash
cd cloudflare-worker
wrangler kv namespace create QUEUE_KV
```

Der Befehl gibt eine `id` aus. Diese kopieren und in `wrangler.toml` anstelle von
`REPLACE_WITH_YOUR_KV_NAMESPACE_ID` einsetzen.

## 3. Gemeinsames Geheimnis erzeugen und konfigurieren

Dieses Geheimnis authentifiziert die Kommunikation `engine-api` ↔ Worker auf den Routen
`/api/pending` und `/api/complete` (die Route `/api/request`, die das Teammitglied nutzt, ist
absichtlich öffentlich — dieselbe Vertrauensstufe wie `/run` der engine-api heute).

```bash
openssl rand -hex 32
# den erzeugten Wert kopieren
wrangler secret put QUEUE_SHARED_SECRET
# bei der Abfrage denselben Wert einfügen
```

Denselben Wert aufbewahren, um ihn als `QUEUE_SHARED_SECRET` in die `.env` der `engine-api`
einzutragen (außerhalb von Git).

## 4. Deployen

```bash
wrangler deploy
```

Der Befehl gibt die öffentliche URL aus (etwa
`https://logistik-queue.DEINE-SUBDOMAIN.workers.dev`).

## 5. (Optional) Eigene Domain

Im Cloudflare-Dashboard → Workers & Pages → `logistik-queue` → Settings →
Domains & Routes → Add Custom Domain (z. B. `posts.frachtwerk.example`). Das wirkt beim Weitergeben an
das Teammitglied professioneller als die `.workers.dev`-URL.

## 6. `engine-api` konfigurieren

In der `.env` des Projekts (nicht versioniert):

```
QUEUE_WORKER_URL=https://posts.frachtwerk.example        # oder die .workers.dev-URL
QUEUE_SHARED_SECRET=<derselbe Wert wie in Schritt 3>
QUEUE_POLL_INTERVAL_SECONDS=60
```

Danach: `docker compose build engine-api && docker compose up -d engine-api`.

## 7. Testen

1. Bei **ausgeschalteter** `engine-api` die Worker-URL öffnen und auf „🚀 Posts generieren“
   klicken — es sollte „⏳ Anfrage registriert am …“ erscheinen.
2. Die `engine-api` starten (`docker compose up -d`). Innerhalb von
   `QUEUE_POLL_INTERVAL_SECONDS` (standardmäßig 60 s) sollten die Logs
   (`docker compose logs -f engine-api`) die Meldung
   `Queue: offene Anfrage gefunden ... Pipeline wird gestartet` zeigen.
3. Nach Abschluss die Worker-Seite neu laden — sie sollte „✅ Fertig! N Posts erstellt.“
   anzeigen.

## Zur Sicherheit

`/api/request` ist öffentlich (jeder mit dem Link kann klicken). Das ist beabsichtigt und
konsistent mit `/run` der `engine-api`, das derzeit ebenfalls keine Authentifizierung hat. Wenn du
einschränken möchtest, wer den Button auslösen kann, ist der einfachste Weg, **Cloudflare Access**
vor die Route dieses Workers zu schalten (dieselbe Idee wie bereits bei den Hostnamen des
Cloudflare Tunnels) — dann sehen nur Personen, die sich anmelden (E-Mail/OTP), die Seite und
können klicken.
