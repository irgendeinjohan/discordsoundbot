# Discord Soundbot

Ein kleiner Discord-Bot, der automatisch Musik in einem Sprachkanal abspielt:

- Sobald ein **echter Nutzer** (kein Bot) den festgelegten Sprachkanal betritt, verbindet sich der Bot.
- Er spielt alle Dateien aus dem Ordner `music` in **zufälliger Reihenfolge** und ohne Pause nacheinander ab.
  Jeder Titel läuft einmal, danach wird neu gemischt. Bei jedem neuen Betreten wird ebenfalls neu gemischt.
- Der Ordner wird vor jedem Mischen neu eingelesen. Neue Dateien kannst du also einfach hineinlegen, ohne den Container neu zu starten.
- Als Status zeigt der Bot **„Hört …“** mit dem aktuellen Titel. Er nimmt „Interpret - Titel“ aus den Tags, sonst den Dateinamen.
- Verlässt der letzte Nutzer den Kanal, trennt sich der Bot sofort.

### Befehle

| Befehl  | Wirkung |
|---------|---------|
| `/skip` | Überspringt den aktuellen Titel. Funktioniert nur, wenn du selbst im Musik-Kanal bist. |

Die Befehle erscheinen nach dem Start des Bots sofort auf deinem Server, wenn du im Chat `/` tippst.

Unterstützte Formate: `mp3`, `flac`, `wav`, `ogg`, `oga`, `opus`, `m4a`, `aac`, `wma`.
Es werden nur Dateien direkt im Ordner gelesen, keine Unterordner. Die Lautstärke bleibt original (100 %).

---

## 1. Discord-Bot anlegen

1. Öffne <https://discord.com/developers/applications> und klicke **New Application**. Vergib einen Namen, z. B. „Techno Bot“.
2. Links auf **Bot** gehen und **Reset Token** klicken. Den angezeigten Token kopieren, er kommt später in die `docker-compose.yml`.
   > Der Token ist wie ein Passwort. Gib ihn niemandem weiter und lade ihn nicht auf GitHub hoch.
3. Die Schalter unter *Privileged Gateway Intents* brauchst du **nicht**.
4. Links auf **OAuth2** gehen und unten den **URL Generator** öffnen:
   - Bei *Scopes* **`bot`** und **`applications.commands`** anhaken (für `/skip`).
   - Bei *Bot Permissions* **View Channels**, **Connect** und **Speak** anhaken.
   - Die erzeugte URL im Browser öffnen und den Bot auf deinen Server einladen.

## 2. Kanal-ID herausfinden

1. In Discord **Einstellungen → Erweitert → Entwicklermodus** einschalten.
2. Rechtsklick auf den gewünschten Sprachkanal und **Kanal-ID kopieren** wählen.

## 3. Docker-Image (wird automatisch gebaut)

Bei jedem Push baut GitHub Actions das Image und legt es in der GitHub Container Registry ab:

```
ghcr.io/irgendeinjohan/discordsoundbot:latest
```

`latest` entsteht nur aus dem Standard-Branch des Repositories. Andere Branches bekommen ein Tag mit ihrem Branch-Namen.

**Einmalig:** Neue Pakete sind auf GitHub zunächst privat. Damit die NAS das Image ohne Login herunterladen kann:
GitHub → dein Profil → **Packages** → `discordsoundbot` → **Package settings** → *Change visibility* → **Public**.

## 4. Einrichtung auf der Synology (Container Manager)

1. In der **File Station** diese Ordner anlegen:
   ```
   /volume1/docker/discordsoundbot
   /volume1/docker/discordsoundbot/music
   ```
2. Deine Musikdateien in den Ordner `music` legen.
3. **Container Manager** öffnen, dann **Projekt → Erstellen**:
   - Projektname: `discordsoundbot`
   - Pfad: `/volume1/docker/discordsoundbot`
   - Quelle: *docker-compose.yml erstellen* und den Inhalt aus [`docker-compose.yml`](docker-compose.yml) einfügen.
4. In der `docker-compose.yml` diese beiden Werte anpassen:
   ```yaml
   DISCORD_TOKEN: "dein-bot-token"
   VOICE_CHANNEL_ID: "123456789012345678"
   ```
5. **Weiter → Fertig.** Der Container startet und ist mit `restart: unless-stopped` auch nach einem Neustart der NAS wieder da.

Die Logs findest du unter **Container Manager → Container → discordsoundbot → Protokoll**.

### Update auf eine neue Version

Container Manager → **Projekt** → `discordsoundbot` → **Aktion → Stoppen**, dann unter **Image** das Image
`ghcr.io/irgendeinjohan/discordsoundbot` aktualisieren (oder löschen) und das Projekt wieder **erstellen/starten**.

## Konfiguration

| Variable           | Pflicht | Beschreibung                                           |
|--------------------|---------|--------------------------------------------------------|
| `DISCORD_TOKEN`    | ja      | Bot-Token aus dem Developer Portal                     |
| `VOICE_CHANNEL_ID` | ja      | ID des Sprachkanals                                    |
| `MUSIC_DIR`        | nein    | Musikordner im Container (Standard: `/music`)          |
| `TZ`               | nein    | Zeitzone für die Log-Zeitstempel, z. B. `Europe/Berlin` |

## Fehlersuche

| Meldung im Protokoll                                  | Lösung |
|-------------------------------------------------------|--------|
| `DISCORD_TOKEN ist nicht gesetzt` / `Improper token`  | Token in der `docker-compose.yml` prüfen. Er muss ohne Leerzeichen in den Anführungszeichen stehen. |
| `Sprachkanal mit ID … nicht gefunden`                 | Kanal-ID prüfen und sicherstellen, dass der Bot auf dem Server ist und den Kanal sehen darf. |
| `Verbinden fehlgeschlagen`                            | Dem Bot fehlen im Kanal die Rechte **Verbinden** oder **Sprechen**. |
| `Keine Musikdateien in /music gefunden`               | Liegen die Dateien direkt in `/volume1/docker/discordsoundbot/music`? Stimmt der Pfad im `volumes:`-Eintrag? |
| `Musikordner … kann nicht gelesen werden`             | Ordnerrechte in der File Station prüfen. |
| `Slash-Befehle konnten nicht registriert werden`      | Bot über den URL Generator mit den Scopes `bot` **und** `applications.commands` erneut einladen (er muss vorher nicht entfernt werden). |

## Lokal ohne Docker starten

```bash
pip install -r requirements.txt   # zusätzlich muss ffmpeg installiert sein
DISCORD_TOKEN=... VOICE_CHANNEL_ID=... MUSIC_DIR=./music python soundbot.py
```
