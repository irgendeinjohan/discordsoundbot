"""Discord-Soundbot: spielt Musik aus einem Ordner, solange echte Nutzer im Sprachkanal sind.

Konfiguration über Umgebungsvariablen:
    DISCORD_TOKEN     Bot-Token aus dem Discord Developer Portal (Pflicht)
    VOICE_CHANNEL_ID  ID des Sprachkanals, den der Bot bespielt (Pflicht)
    MUSIC_DIR         Ordner mit den Musikdateien (Standard: /music)
"""

import asyncio
import logging
import os
import random
import sys
from pathlib import Path

import discord
from discord import app_commands
from mutagen import File as MutagenFile

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wma"}

# Wie oft (Sekunden) der Zustand zur Sicherheit überprüft wird, falls ein Event verloren geht
WATCHDOG_INTERVAL = 30
# Wartezeit, wenn der Musikordner leer ist oder keine Datei abspielbar war
EMPTY_RETRY_DELAY = 30
# Titel, die kürzer liefen, zählen als fehlgeschlagen (defekte Datei)
MIN_TRACK_SECONDS = 2
# Nach so vielen fehlgeschlagenen Titeln in Folge wird pausiert
MAX_FAILED_IN_ROW = 10

log = logging.getLogger("soundbot")


def read_config() -> tuple[str, int, Path]:
    # Versehentlich mitkopierte Leerzeichen, Anführungszeichen oder ein "Bot "-Präfix entfernen
    token = os.environ.get("DISCORD_TOKEN", "").strip().strip("\"'").strip()
    token = token.removeprefix("Bot ").strip()
    channel_id = os.environ.get("VOICE_CHANNEL_ID", "").strip().strip("\"'").strip()
    music_dir = Path(os.environ.get("MUSIC_DIR", "/music"))

    if not token or token == "HIER_DEINEN_BOT_TOKEN_EINTRAGEN":
        sys.exit("FEHLER: DISCORD_TOKEN ist nicht gesetzt. Bitte in der docker-compose.yml eintragen.")
    if token.count(".") != 2:
        log.warning(
            "DISCORD_TOKEN sieht nicht wie ein Bot-Token aus (erwartet: drei Teile mit Punkten, ~70 Zeichen; "
            "aktuell %d Zeichen). Application ID, Public Key oder Client Secret sind NICHT der Token.",
            len(token),
        )
    if not channel_id.isdigit():
        sys.exit(f"FEHLER: VOICE_CHANNEL_ID muss eine Zahl sein (aktuell: {channel_id!r}).")
    return token, int(channel_id), music_dir


def scan_music(music_dir: Path) -> list[Path]:
    """Liest alle Audiodateien direkt im Ordner ein (ohne Unterordner)."""
    try:
        return [
            p for p in music_dir.iterdir()
            if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS and not p.name.startswith(".")
        ]
    except OSError as e:
        log.error("Musikordner %s kann nicht gelesen werden: %s", music_dir, e)
        return []


def track_title(path: Path) -> str:
    """'Interpret - Titel' aus den Tags, sonst der Dateiname ohne Endung."""
    try:
        tags = MutagenFile(path, easy=True)
        if tags is not None:
            title = (tags.get("title") or [""])[0].strip()
            artist = (tags.get("artist") or [""])[0].strip()
            if title:
                return f"{artist} - {title}" if artist else title
    except Exception:
        pass
    return path.stem


class SoundBot(discord.Client):
    def __init__(self, channel_id: int, music_dir: Path):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents, status=discord.Status.online)

        self.channel_id = channel_id
        self.music_dir = music_dir
        self.playlist: list[Path] = []
        self.lock = asyncio.Lock()
        # Wird bei jedem Verbinden erhöht, damit Callbacks einer alten Sitzung nichts mehr auslösen
        self.session = 0
        self.failed_in_row = 0
        self.waiting_for_music = False
        self.current_title: str | None = None
        self.skip_requested = False
        self.commands_synced = False

        self.tree = app_commands.CommandTree(self)
        self.tree.command(name="skip", description="Überspringt den aktuellen Titel")(self.cmd_skip)

    async def setup_hook(self) -> None:
        self.loop.create_task(self.watchdog())

    # ---------- Events ----------

    async def on_ready(self) -> None:
        log.info("Eingeloggt als %s (ID %s)", self.user, self.user.id)
        channel = self.get_channel(self.channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            log.error(
                "Sprachkanal mit ID %s nicht gefunden. Ist die ID richtig und ist der Bot auf dem Server?",
                self.channel_id,
            )
        else:
            log.info("Überwache Sprachkanal '%s' auf Server '%s'", channel.name, channel.guild.name)
            await self.sync_commands(channel.guild)
        await self.sync_state()

    async def sync_commands(self, guild: discord.Guild) -> None:
        """Registriert die Slash-Befehle direkt auf dem Server (wirkt sofort, global dauert es bis zu 1 h)."""
        if self.commands_synced:
            return
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            self.commands_synced = True
            log.info("Slash-Befehle registriert: /skip")
        except discord.HTTPException as e:
            log.error(
                "Slash-Befehle konnten nicht registriert werden (Bot mit Scope 'applications.commands' "
                "neu einladen): %s", e,
            )

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
        ids = {getattr(before.channel, "id", None), getattr(after.channel, "id", None)}
        if self.channel_id in ids or member.id == self.user.id:
            await self.sync_state()

    async def watchdog(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(WATCHDOG_INTERVAL)
            try:
                await self.sync_state()
            except Exception:
                log.exception("Fehler bei der Zustandsprüfung")

    # ---------- Befehle ----------

    async def cmd_skip(self, interaction: discord.Interaction) -> None:
        channel = self.get_channel(self.channel_id)
        if not isinstance(channel, discord.VoiceChannel) or interaction.guild_id != channel.guild.id:
            await interaction.response.send_message("Dieser Befehl funktioniert hier nicht.", ephemeral=True)
            return
        if interaction.user.id not in {m.id for m in channel.members}:
            await interaction.response.send_message(
                f"Du musst im Kanal {channel.mention} sein, um Titel zu überspringen.", ephemeral=True
            )
            return
        vc = channel.guild.voice_client
        if vc is None or not vc.is_playing():
            await interaction.response.send_message("Gerade läuft nichts.", ephemeral=True)
            return

        log.info("%s überspringt: %s", interaction.user, self.current_title)
        await interaction.response.send_message(f"⏭️ **{self.current_title}** übersprungen")
        self.skip_requested = True
        vc.stop()  # löst den after-Callback aus → nächster Titel startet

    # ---------- Kanal betreten / verlassen ----------

    def human_count(self, channel: discord.VoiceChannel) -> int:
        return sum(1 for m in channel.members if not m.bot)

    async def sync_state(self) -> None:
        """Bringt den Bot in den passenden Zustand: Nutzer da → verbunden und spielend, sonst getrennt."""
        async with self.lock:
            channel = self.get_channel(self.channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                return
            vc: discord.VoiceClient | None = channel.guild.voice_client
            humans = self.human_count(channel)

            if humans == 0:
                if vc is not None:
                    log.info("Keine Nutzer mehr im Kanal – trenne Verbindung")
                    await self.leave(vc)
                return

            if vc is None:
                await self.join(channel)
                return
            if not vc.is_connected():
                return  # discord.py verbindet gerade selbst neu

            if vc.channel.id != channel.id:
                log.info("Bot wurde verschoben – kehre zurück nach '%s'", channel.name)
                await vc.move_to(channel)

            if not vc.is_playing() and not vc.is_paused() and not self.waiting_for_music:
                self.play_next(vc, self.session)

    async def join(self, channel: discord.VoiceChannel) -> None:
        log.info("%d Nutzer im Kanal '%s' – verbinde", self.human_count(channel), channel.name)
        try:
            vc = await channel.connect(timeout=30, reconnect=True, self_deaf=True)
        except Exception as e:
            log.error("Verbinden fehlgeschlagen (fehlen dem Bot die Rechte 'Verbinden'/'Sprechen'?): %s", e)
            return
        self.session += 1
        self.playlist = []  # bei jedem Betreten neu mischen
        self.failed_in_row = 0
        self.skip_requested = False
        self.waiting_for_music = False
        self.play_next(vc, self.session)

    async def leave(self, vc: discord.VoiceClient) -> None:
        self.session += 1
        self.playlist = []
        self.waiting_for_music = False
        self.current_title = None
        vc.stop()
        try:
            await vc.disconnect(force=True)
        except Exception as e:
            log.warning("Fehler beim Trennen: %s", e)
        await self.change_presence(activity=None)

    # ---------- Wiedergabe ----------

    def next_track(self) -> Path | None:
        if not self.playlist:
            self.playlist = scan_music(self.music_dir)
            random.shuffle(self.playlist)
            if self.playlist:
                log.info("Playlist neu gemischt: %d Titel", len(self.playlist))
        return self.playlist.pop() if self.playlist else None

    def play_next(self, vc: discord.VoiceClient, session: int) -> None:
        if session != self.session or not vc.is_connected() or vc.is_playing():
            return

        track = self.next_track()
        if track is None or self.failed_in_row >= MAX_FAILED_IN_ROW:
            if track is None:
                log.warning("Keine Musikdateien in %s gefunden – neuer Versuch in %ds", self.music_dir, EMPTY_RETRY_DELAY)
            else:
                log.warning("Zu viele Titel hintereinander fehlgeschlagen – Pause für %ds", EMPTY_RETRY_DELAY)
            self.failed_in_row = 0
            self.waiting_for_music = True
            self.loop.create_task(self.retry_later(vc, session))
            return

        try:
            source = discord.FFmpegOpusAudio(str(track), before_options="-nostdin -hide_banner -loglevel error", options="-vn")
        except Exception as e:
            log.error("Kann %s nicht öffnen: %s", track.name, e)
            self.failed_in_row += 1
            self.play_next(vc, session)
            return

        started = self.loop.time()

        def after(error: Exception | None) -> None:
            # Läuft in einem eigenen Thread → zurück in die Event-Loop springen
            if error:
                log.error("Fehler beim Abspielen von %s: %s", track.name, error)
            failed = error is not None or self.loop.time() - started < MIN_TRACK_SECONDS
            self.loop.call_soon_threadsafe(self.track_finished, vc, session, failed)

        title = track_title(track)
        self.current_title = title
        log.info("Spiele: %s", title)
        vc.play(source, after=after)
        self.loop.create_task(self.set_now_playing(title))

    def track_finished(self, vc: discord.VoiceClient, session: int, failed: bool) -> None:
        if session != self.session:
            return
        if self.skip_requested:  # übersprungene Titel zählen nicht als defekt
            self.skip_requested = False
            failed = False
        self.failed_in_row = self.failed_in_row + 1 if failed else 0
        self.play_next(vc, session)

    async def retry_later(self, vc: discord.VoiceClient, session: int) -> None:
        await self.change_presence(activity=None)
        await asyncio.sleep(EMPTY_RETRY_DELAY)
        if session == self.session:
            self.waiting_for_music = False
            self.play_next(vc, session)

    async def set_now_playing(self, title: str) -> None:
        try:
            await self.change_presence(
                activity=discord.Activity(type=discord.ActivityType.listening, name=title[:128])
            )
        except Exception as e:
            log.debug("Status konnte nicht gesetzt werden: %s", e)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    token, channel_id, music_dir = read_config()
    if not music_dir.is_dir():
        log.warning("Musikordner %s existiert nicht – ist das Volume richtig eingebunden?", music_dir)

    bot = SoundBot(channel_id, music_dir)
    try:
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        log.error(
            "Discord hat den Token abgelehnt. Im Developer Portal unter 'Bot' auf 'Reset Token' klicken, "
            "den neuen Token in DISCORD_TOKEN eintragen und das Projekt neu erstellen."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
