import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlencode

import aiohttp
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import websockets
BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))

API = "https://apigolive.nemtudo.me"
GATEWAY = "wss://apigolive.nemtudo.me/ws"
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GOLIVE_TOKEN = os.getenv("GOLIVE_TOKEN", "").strip()
GOLIVE_OAUTH_CLIENT_ID = os.getenv("GOLIVE_OAUTH_CLIENT_ID", "").strip()
GOLIVE_OAUTH_CLIENT_SECRET = os.getenv("GOLIVE_OAUTH_CLIENT_SECRET", "").strip()
OAUTH_REDIRECT_URI = os.getenv(
    "GOLIVE_OAUTH_REDIRECT_URI",
    "https://voidbridge.onrender.com/oauth/golive/callback"
).strip()
if not DISCORD_TOKEN or not GOLIVE_TOKEN:
    raise SystemExit("Preencha DISCORD_TOKEN e GOLIVE_TOKEN no arquivo .env")

# O portal copia "Bot <token>"; algumas integrações entregam só o token.
# Aceitamos os dois formatos sem duplicar o prefixo.
if GOLIVE_TOKEN.startswith("Bearer "):
    raise SystemExit("GOLIVE_TOKEN deve ser o token de BOT do GoLive, não um token Bearer de usuário.")
GOLIVE_CREDENTIAL = GOLIVE_TOKEN if GOLIVE_TOKEN.startswith("Bot ") else f"Bot {GOLIVE_TOKEN}"

LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "voidbridge.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("VoidBridge")

GOLIVE_GROUP_ID = str(CONFIG.get("golive_group_id", "")).strip()
DISCORD_GROUP_ID = str(CONFIG.get("discord_group_id", "")).strip()
if not GOLIVE_GROUP_ID or not DISCORD_GROUP_ID:
    raise SystemExit("Preencha golive_group_id e discord_group_id no config.json")
MAX_UPLOAD = int(CONFIG.get("max_upload_mb", 20)) * 1024 * 1024
ANNOUNCE_VOICE = bool(CONFIG.get("announce_discord_voice", False))

@contextmanager
def db():
    # O context manager padrão do sqlite faz commit/rollback, mas não fecha
    # a conexão. Fechar explicitamente evita acumular conexões abertas.
    con = sqlite3.connect(BASE / "state.sqlite3", timeout=15)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS message_map (
            source_platform TEXT NOT NULL,
            source_id TEXT NOT NULL,
            destination_platform TEXT NOT NULL,
            destination_id TEXT NOT NULL,
            discord_channel_id TEXT,
            golive_group_id TEXT,
            golive_channel_id TEXT,
            PRIMARY KEY(source_platform, source_id)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS structure_map (
            entity_type TEXT NOT NULL, discord_id TEXT NOT NULL, golive_id TEXT NOT NULL,
            last_name TEXT NOT NULL, PRIMARY KEY(entity_type, discord_id),
            UNIQUE(entity_type, golive_id)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS oauth_states (
            state TEXT PRIMARY KEY, discord_id TEXT NOT NULL,
            code_verifier TEXT NOT NULL, expires_at INTEGER NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS account_links (
            discord_id TEXT PRIMARY KEY, golive_user_id TEXT NOT NULL UNIQUE,
            golive_username TEXT, linked_at INTEGER NOT NULL
        )""")
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def save_map(source_platform, source_id, destination_platform, destination_id,
             discord_channel_id=None, group_id=None, channel_id=None):
    with db() as con:
        con.execute(
            """INSERT OR REPLACE INTO message_map
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (source_platform, str(source_id), destination_platform, str(destination_id),
             str(discord_channel_id) if discord_channel_id else None,
             group_id, channel_id),
        )

def get_map(source_platform, source_id):
    with db() as con:
        row = con.execute(
            """SELECT destination_platform, destination_id, discord_channel_id,
                      golive_group_id, golive_channel_id
               FROM message_map WHERE source_platform=? AND source_id=?""",
            (source_platform, str(source_id)),
        ).fetchone()
    return row

def remove_map(source_platform, source_id):
    with db() as con:
        con.execute(
            "DELETE FROM message_map WHERE source_platform=? AND source_id=?",
            (source_platform, str(source_id)),
        )

def get_structure_maps(kind):
    with db() as con:
        return con.execute("SELECT discord_id, golive_id, last_name FROM structure_map WHERE entity_type=?", (kind,)).fetchall()

def get_structure_map_by_discord(kind, ident):
    with db() as con:
        return con.execute("SELECT discord_id, golive_id, last_name FROM structure_map WHERE entity_type=? AND discord_id=?", (kind, str(ident))).fetchone()

def get_structure_map_by_golive(kind, ident):
    with db() as con:
        return con.execute("SELECT discord_id, golive_id, last_name FROM structure_map WHERE entity_type=? AND golive_id=?", (kind, str(ident))).fetchone()

def save_structure_map(kind, discord_id, golive_id, name):
    with db() as con:
        con.execute("""INSERT INTO structure_map VALUES (?, ?, ?, ?)
            ON CONFLICT(entity_type, discord_id) DO UPDATE SET
            golive_id=excluded.golive_id, last_name=excluded.last_name""",
            (kind, str(discord_id), str(golive_id), str(name)))

def bridge_for_discord(channel_id):
    row = get_structure_map_by_discord("channel", channel_id)
    return {"discord_channel_id": str(channel_id), "golive_group_id": GOLIVE_GROUP_ID,
            "golive_channel_id": row[1]} if row else None

def bridge_for_golive(group_id, channel_id):
    if str(group_id) != GOLIVE_GROUP_ID:
        return None
    row = get_structure_map_by_golive("channel", channel_id)
    return {"discord_channel_id": row[0], "golive_group_id": GOLIVE_GROUP_ID,
            "golive_channel_id": str(channel_id)} if row else None

def clean_text(text, limit=5900):
    text = (text or "").strip()
    return text[:limit]

class GoLiveAPIError(RuntimeError):
    def __init__(self, status, data):
        self.status = status
        self.data = data
        super().__init__(f"GoLive HTTP {status}: {data}")


class GoLiveAPI:
    def __init__(self, session):
        self.session = session
        self.headers = {"Authorization": GOLIVE_CREDENTIAL}

    async def request(self, method, path, **kwargs):
        async with self.session.request(
            method, API + path, headers=self.headers, **kwargs
        ) as response:
            body = await response.text()
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {"raw": body[:500]}
            if response.status >= 400:
                raise GoLiveAPIError(response.status, data)
            return data

    async def get_group(self, group_id):
        return await self.request("GET", f"/groups/{quote(str(group_id), safe='')}")

    async def create_category(self, group_id, name):
        return await self.request("POST", f"/groups/{quote(str(group_id), safe='')}/categories", json={"name": name[:32]})

    async def rename_category(self, group_id, category_id, name):
        return await self.request("PATCH", f"/groups/{quote(str(group_id), safe='')}/categories/{quote(str(category_id), safe='')}", json={"name": name[:32]})

    async def create_channel(self, group_id, name, kind, category_id=None):
        return await self.request("POST", f"/groups/{quote(str(group_id), safe='')}/channels",
                                  json={"name": name[:32], "kind": kind, "categoryId": category_id})

    async def rename_channel(self, group_id, channel_id, name):
        return await self.request("PATCH", f"/groups/{quote(str(group_id), safe='')}/channels/{quote(str(channel_id), safe='')}", json={"name": name[:32]})

    async def verify(self):
        data = await self.request("GET", "/auth/me")
        account = data.get("account", {})
        if not account.get("bot", False):
            raise RuntimeError("O token autenticou, mas a conta não está marcada como bot no GoLive.")
        log.info("GoLive autenticado como %s (@%s)",
                 account.get("displayName"), account.get("username"))
        return account

    async def send_message(self, group_id, channel_id, text, attachments=None, images=None):
        body = {"text": clean_text(text, 2000), "nonce": uuid.uuid4().hex}
        if attachments:
            body["attachments"] = attachments[:5]
        if images:
            body["images"] = images[:3]
        return await self.request(
            "POST",
            f"/groups/{quote(str(group_id), safe='')}/channels/{quote(str(channel_id), safe='')}/messages",
            json=body,
        )

    async def edit_message(self, group_id, channel_id, message_id, text):
        return await self.request(
            "PATCH",
            f"/groups/{quote(str(group_id), safe='')}/channels/{quote(str(channel_id), safe='')}/messages/{quote(str(message_id), safe='')}",
            json={"text": clean_text(text, 2000)},
        )

    async def upload(self, filename, content_type, raw_bytes):
        params = {
            "name": filename[:180],
            "type": content_type or "application/octet-stream",
            "for": "groups",
        }
        async with self.session.post(
            API + "/uploads",
            params=params,
            headers={**self.headers, "Content-Type": "application/octet-stream"},
            data=raw_bytes,
        ) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(f"GoLive upload HTTP {response.status}: {data}")
            return data["token"]

    async def delete_message(self, group_id, channel_id, message_id):
        return await self.request(
            "DELETE",
            f"/groups/{quote(str(group_id), safe='')}/channels/{quote(str(channel_id), safe='')}/messages/{quote(str(message_id), safe='')}",
        )

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)

http_session = None
golive_api = None
golive_ready = asyncio.Event()
golive_task = None

async def send_discord(channel_id, text, files=None):
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except Exception:
            log.exception("Não consegui encontrar o canal Discord %s", channel_id)
            return None
    return await channel.send(
        clean_text(text, 1900) or "(mensagem sem texto)",
        files=files or [],
        allowed_mentions=discord.AllowedMentions.none(),
    )

async def download_golive_attachments(message):
    files = []
    urls = []
    for item in message.get("attachments", []) or []:
        if isinstance(item, dict) and item.get("url"):
            urls.append((item.get("name") or "arquivo", item["url"]))
    for url in message.get("images", []) or []:
        if isinstance(url, str):
            urls.append(("imagem", url))
    for name, url in urls[:5]:
        try:
            async with http_session.get(url) as response:
                if response.status != 200:
                    continue
                data = await response.read()
                if len(data) > MAX_UPLOAD:
                    continue
                files.append(discord.File(fp=__import__("io").BytesIO(data), filename=name))
        except Exception:
            log.exception("Falha ao baixar anexo GoLive: %s", url)
    return files

async def handle_golive_event(event):
    kind = event.get("type")

    if kind == "registered":
        log.info("Gateway GoLive registrado como %s", event.get("name", "bot"))
        golive_ready.set()
        return

    if kind == "register-error":
        golive_ready.clear()
        if event.get("retryable", False):
            log.warning("GoLive informou erro temporário no registro: %s", event.get("message"))
        else:
            log.error("GoLive recusou o registro: %s", event.get("message"))
        return

    if kind == "banned":
        golive_ready.clear()
        log.error("Bot GoLive banido: %s", event)
        return

    if kind == "group-updated" and str(event.get("groupId")) == GOLIVE_GROUP_ID:
        await sync_structure()
        return

    if kind == "group-message":
        message = event.get("message", {})
        author = event.get("author", {})
        if author.get("bot"):
            return
        bridge = bridge_for_golive(message.get("groupId"), message.get("channelId"))
        if not bridge:
            return
        source_id = str(message.get("id", ""))
        if not source_id or get_map("golive", source_id):
            return
        author_name = author.get("name") or message.get("fromName") or "GoLive"
        text = clean_text(message.get("text", ""))
        content = f"{CONFIG.get('golive_prefix', '[GoLive]')} **{author_name}:** {text}"
        files = await download_golive_attachments(message)
        sent = await send_discord(bridge["discord_channel_id"], content, files)
        if sent:
            save_map("golive", source_id, "discord", sent.id,
                     discord_channel_id=bridge["discord_channel_id"],
                     group_id=str(message.get("groupId")),
                     channel_id=str(message.get("channelId")))
        return

    if kind == "group-message-updated":
        message = event.get("message", {})
        mapped = get_map("golive", message.get("id"))
        if not mapped:
            return
        _, discord_id, channel_id, _, _ = mapped
        channel = bot.get_channel(int(channel_id)) if channel_id else None
        if channel:
            try:
                target = await channel.fetch_message(int(discord_id))
                author = message.get("fromName") or "GoLive"
                await target.edit(content=clean_text(
                    f"{CONFIG.get('golive_prefix', '[GoLive]')} **{author}:** {message.get('text','')}", 1900
                ))
            except Exception:
                log.exception("Não consegui atualizar mensagem editada do GoLive")
        return

    if kind == "group-message-deleted":
        mapped = get_map("golive", event.get("messageId"))
        if mapped:
            _, discord_id, channel_id, _, _ = mapped
            channel = bot.get_channel(int(channel_id)) if channel_id else None
            if channel:
                try:
                    target = await channel.fetch_message(int(discord_id))
                    await target.delete()
                except discord.NotFound:
                    pass
                except Exception:
                    log.exception("Falha ao apagar mensagem espelhada no Discord")
            remove_map("golive", event.get("messageId"))
        return

    if kind == "group-removed":
        log.warning("Bot removido do grupo GoLive %s: %s",
                    event.get("groupId"), event.get("reason"))
        return


async def golive_gateway_loop():
    global golive_ready
    while True:
        try:
            log.info("Conectando ao Gateway GoLive...")
            async with websockets.connect(
                GATEWAY, ping_interval=25, ping_timeout=25, close_timeout=5
            ) as ws:
                await ws.send(json.dumps({"type": "register", "token": GOLIVE_CREDENTIAL}))
                async for raw in ws:
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    await handle_golive_event(event)
                    if event.get("type") == "register-error":
                        if event.get("retryable", False):
                            log.warning("Erro temporário no Gateway; a conexão será refeita.")
                            break
                        log.error("Registro recusado pelo GoLive. Confira o token e as permissões.")
                        return
                    if event.get("type") == "banned":
                        log.error("O bot foi banido do GoLive; interrompendo a conexão.")
                        return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Gateway GoLive desconectou; tentando novamente em 8 segundos.")
        finally:
            golive_ready.clear()
        await asyncio.sleep(8)


# Sincroniza estrutura sem excluir nada. O SQLite mantém os pares de IDs.
structure_lock = asyncio.Lock()

def normalized_name(value):
    return " ".join(str(value or "").strip().lower().split())

def discord_channel_kind(channel):
    if isinstance(channel, discord.CategoryChannel): return "category"
    if isinstance(channel, discord.VoiceChannel): return "voice"
    if isinstance(channel, discord.TextChannel): return "text"
    return None

async def sync_structure():
    if structure_lock.locked() or golive_api is None: return
    async with structure_lock:
        try:
            guild = bot.get_guild(int(DISCORD_GROUP_ID))
            if guild is None: guild = await bot.fetch_guild(int(DISCORD_GROUP_ID))
            remote = await golive_api.get_group(GOLIVE_GROUP_ID)
            gl_cats = list(remote.get("categories", []) or [])
            gl_chans = list(remote.get("channels", []) or [])
            dc_chans = list(await guild.fetch_channels())

            # Categorias existentes no Discord: vincula por ID salvo ou nome.
            known = {r[0]: r for r in get_structure_maps("category")}
            used = {r[1] for r in known.values()}
            for dc in [x for x in dc_chans if isinstance(x, discord.CategoryChannel)]:
                row = known.get(str(dc.id))
                gl = next((x for x in gl_cats if row and str(x.get("id")) == row[1]), None)
                if gl is None:
                    gl = next((x for x in gl_cats if str(x.get("id")) not in used and normalized_name(x.get("name")) == normalized_name(dc.name)), None)
                if gl is None:
                    result = await golive_api.create_category(GOLIVE_GROUP_ID, dc.name)
                    gl = result.get("category", {})
                    if gl.get("id"): gl_cats.append(gl)
                if not gl or not gl.get("id"): continue
                previous = row[2] if row else dc.name
                gn, dn = str(gl.get("name") or ""), dc.name
                if row and dn != previous and gn == previous:
                    await golive_api.rename_category(GOLIVE_GROUP_ID, gl["id"], dn); gn = dn
                elif row and gn != previous and dn == previous:
                    await dc.edit(name=gn, reason="VoidBridge: sincronizar categoria"); dn = gn
                elif row and gn != dn and dn != previous and gn != previous:
                    await golive_api.rename_category(GOLIVE_GROUP_ID, gl["id"], dn); gn = dn
                save_structure_map("category", dc.id, gl["id"], dn)
                used.add(str(gl["id"]))

            # Categorias criadas no GoLive.
            mapped = {r[1] for r in get_structure_maps("category")}
            for gl in gl_cats:
                if str(gl.get("id")) in mapped: continue
                dc = next((x for x in dc_chans if isinstance(x, discord.CategoryChannel) and normalized_name(x.name) == normalized_name(gl.get("name"))), None)
                if dc is None:
                    dc = await guild.create_category(str(gl.get("name") or "categoria")[:100], reason="VoidBridge: categoria GoLive")
                    dc_chans.append(dc)
                save_structure_map("category", dc.id, gl["id"], dc.name)

            cat_rows = get_structure_maps("category")
            gl_to_dc_cat = {r[1]: next((c for c in dc_chans if str(c.id) == r[0]), None) for r in cat_rows}
            dc_to_gl_cat = {int(r[0]): r[1] for r in cat_rows}

            # Canais Discord -> GoLive.
            known = {r[0]: r for r in get_structure_maps("channel")}
            used = {r[1] for r in known.values()}
            supported = [x for x in dc_chans if discord_channel_kind(x) in ("text", "voice")]
            for dc in supported:
                kind = discord_channel_kind(dc)
                row = known.get(str(dc.id))
                gl = next((x for x in gl_chans if row and str(x.get("id")) == row[1]), None)
                if gl is None:
                    gl = next((x for x in gl_chans if str(x.get("id")) not in used and x.get("kind", "text") == kind and normalized_name(x.get("name")) == normalized_name(dc.name)), None)
                if gl is None:
                    parent = getattr(dc, "category_id", None)
                    result = await golive_api.create_channel(GOLIVE_GROUP_ID, dc.name, kind, dc_to_gl_cat.get(parent) if parent else None)
                    gl = result.get("channel", {})
                    if gl.get("id"): gl_chans.append(gl)
                if not gl or not gl.get("id"): continue
                previous = row[2] if row else dc.name
                gn, dn = str(gl.get("name") or ""), dc.name
                if row and dn != previous and gn == previous:
                    await golive_api.rename_channel(GOLIVE_GROUP_ID, gl["id"], dn); gn = dn
                elif row and gn != previous and dn == previous:
                    await dc.edit(name=gn, reason="VoidBridge: sincronizar sala"); dn = gn
                elif row and gn != dn and dn != previous and gn != previous:
                    await golive_api.rename_channel(GOLIVE_GROUP_ID, gl["id"], dn); gn = dn
                save_structure_map("channel", dc.id, gl["id"], dn)
                used.add(str(gl["id"]))

            # Canais GoLive -> Discord.
            mapped = {r[1] for r in get_structure_maps("channel")}
            for gl in gl_chans:
                if str(gl.get("id")) in mapped: continue
                kind = gl.get("kind", "text")
                if kind not in ("text", "voice"): continue
                dc = next((x for x in supported if discord_channel_kind(x) == kind and normalized_name(x.name) == normalized_name(gl.get("name"))), None)
                if dc is None:
                    category = gl_to_dc_cat.get(str(gl.get("categoryId"))) if gl.get("categoryId") else None
                    name = str(gl.get("name") or "sala")[:100]
                    if kind == "voice":
                        dc = await guild.create_voice_channel(name, category=category, reason="VoidBridge: sala GoLive")
                    else:
                        dc = await guild.create_text_channel(name, category=category, reason="VoidBridge: sala GoLive")
                    supported.append(dc)
                save_structure_map("channel", dc.id, gl["id"], dc.name)
            log.info("Estrutura Discord ↔ GoLive sincronizada.")
        except discord.Forbidden:
            log.exception("Sem permissão Discord para criar ou renomear canais.")
        except GoLiveAPIError as exc:
            log.error("Erro GoLive HTTP %s: %s", exc.status, exc.data)
        except Exception:
            log.exception("Falha ao sincronizar estrutura.")

@bot.event
async def on_ready():
    log.info("Discord conectado como %s", bot.user)
    if not getattr(bot, "_commands_synced", False):
        try:
            await bot.tree.sync()
            bot._commands_synced = True
            log.info("Comandos /vincular e /vinculo sincronizados.")
        except Exception:
            log.exception("Não foi possível sincronizar os comandos slash.")
    if not getattr(bot, "_golive_started", False):
        bot._golive_started = True
        asyncio.create_task(golive_gateway_loop())
        asyncio.create_task(sync_structure())

@bot.event
async def on_guild_channel_create(channel):
    if str(channel.guild.id) == DISCORD_GROUP_ID: await sync_structure()

@bot.event
async def on_guild_channel_update(before, after):
    if str(after.guild.id) == DISCORD_GROUP_ID: await sync_structure()

@bot.event
async def on_message(message):
    await bot.process_commands(message)
    if message.author.bot or message.webhook_id:
        return
    bridge = bridge_for_discord(message.channel.id)
    if not bridge:
        return
    text = clean_text(message.clean_content)
    attachments = []
    images = []
    image_total = 0
    supported_images = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}

    for attachment in message.attachments:
        content_type = (attachment.content_type or "").lower().split(";")[0].strip()

        if content_type.startswith("image/"):
            if content_type not in supported_images:
                text += f" [Imagem ignorada por formato não suportado: {attachment.filename}]"
                continue
            if len(images) >= 3 or attachment.size > 5 * 1024 * 1024 or image_total + attachment.size > 8 * 1024 * 1024:
                text += f" [Imagem ignorada por limite do GoLive: {attachment.filename}]"
                continue
            try:
                raw = await attachment.read()
                if len(raw) > 5 * 1024 * 1024 or image_total + len(raw) > 8 * 1024 * 1024:
                    text += f" [Imagem ignorada por limite do GoLive: {attachment.filename}]"
                    continue
                images.append(f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}")
                image_total += len(raw)
            except Exception:
                log.exception("Falha ao preparar imagem do Discord para GoLive")
            continue

        if len(attachments) >= 5:
            text += f" [Arquivo ignorado: limite de 5 arquivos por mensagem ({attachment.filename})]"
            continue
        if attachment.size > MAX_UPLOAD:
            text += f" [Arquivo ignorado por exceder {MAX_UPLOAD // (1024*1024)} MB: {attachment.filename}]"
            continue
        try:
            raw = await attachment.read()
            token = await golive_api.upload(
                attachment.filename, content_type or "application/octet-stream", raw
            )
            attachments.append(token)
        except Exception:
            log.exception("Falha ao enviar anexo do Discord para GoLive")

    author = message.author.display_name
    payload_text = f"{CONFIG.get('discord_prefix', '[Discord]')} {author}: {text}"
    try:
        result = await golive_api.send_message(
            bridge["golive_group_id"], bridge["golive_channel_id"],
            payload_text, attachments, images
        )
        sent = result.get("message", {})
        if sent.get("id"):
            save_map("discord", message.id, "golive", sent["id"],
                     discord_channel_id=message.channel.id,
                     group_id=bridge["golive_group_id"],
                     channel_id=bridge["golive_channel_id"])
    except Exception:
        log.exception("Não consegui encaminhar mensagem Discord → GoLive")

@bot.event
async def on_raw_message_delete(payload):
    mapped = get_map("discord", payload.message_id)
    if not mapped:
        return
    _, golive_id, _, group_id, channel_id = mapped
    try:
        await golive_api.delete_message(group_id, channel_id, golive_id)
    except Exception:
        log.exception("Não consegui apagar a cópia no GoLive")
    remove_map("discord", payload.message_id)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.webhook_id:
        return
    bridge = bridge_for_discord(after.channel.id)
    if not bridge:
        return
    mapped = get_map("discord", before.id)
    if not mapped:
        return
    # A API REST atual oferece PATCH para editar mensagens do próprio bot.
    _, golive_message_id, _, group_id, channel_id = mapped
    edited_text = (
        f"{CONFIG.get('discord_prefix', '[Discord]')} "
        f"{after.author.display_name}: {clean_text(after.clean_content)}"
    )
    try:
        await golive_api.edit_message(
            group_id, channel_id, golive_message_id, edited_text
        )
    except RuntimeError as exc:
        # Algumas páginas da documentação do GoLive divergem sobre PATCH.
        # Se a instância ainda não oferecer essa rota, usa o fallback documentado.
        if "GoLive HTTP 404" not in str(exc) and "GoLive HTTP 405" not in str(exc):
            log.exception("Falha ao sincronizar edição Discord → GoLive")
            return
        try:
            await golive_api.delete_message(group_id, channel_id, golive_message_id)
            result = await golive_api.send_message(
                group_id, channel_id, edited_text
            )
            new_message = result.get("message", {})
            if new_message.get("id"):
                save_map(
                    "discord", after.id, "golive", new_message["id"],
                    discord_channel_id=after.channel.id,
                    group_id=group_id, channel_id=channel_id
                )
        except Exception:
            log.exception("Falha no fallback de edição Discord → GoLive")
    except Exception:
        log.exception("Falha ao sincronizar edição Discord → GoLive")

@bot.event
async def on_voice_state_update(member, before, after):
    # Só sincronizamos a existência/nome dos canais, nunca o áudio.
    return

@bot.command(name="bridge")
@commands.has_permissions(manage_guild=True)
async def bridge_status(ctx):
    state = "conectado" if golive_ready.is_set() else "reconectando/desconectado"
    await ctx.reply(f"VoidBridge ativo. GoLive: **{state}**. Discord: **{DISCORD_GROUP_ID}** | GoLive: **{GOLIVE_GROUP_ID}**.")

@bridge_status.error
async def bridge_status_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("Você precisa da permissão Gerenciar Servidor para usar esse comando.")


def create_oauth_link(discord_id):
    if not GOLIVE_OAUTH_CLIENT_ID or not GOLIVE_OAUTH_CLIENT_SECRET:
        raise RuntimeError("OAuth do GoLive ainda não foi configurado no Render.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    with db() as con:
        con.execute("DELETE FROM oauth_states WHERE expires_at < ?", (int(time.time()),))
        con.execute(
            "INSERT INTO oauth_states VALUES (?, ?, ?, ?)",
            (state, str(discord_id), verifier, int(time.time()) + 600),
        )
    params = {
        "client_id": GOLIVE_OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": OAUTH_REDIRECT_URI,
        "scope": "identify",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return "https://golive.nemtudo.me/oauth2/authorize?" + urlencode(params)


def take_oauth_state(state):
    with db() as con:
        row = con.execute(
            "SELECT discord_id, code_verifier, expires_at FROM oauth_states WHERE state=?",
            (state,),
        ).fetchone()
        if row:
            con.execute("DELETE FROM oauth_states WHERE state=?", (state,))
    if not row or row[2] < int(time.time()):
        return None
    return row


async def golive_oauth_callback(request):
    if not GOLIVE_OAUTH_CLIENT_ID or not GOLIVE_OAUTH_CLIENT_SECRET:
        return web.Response(text="OAuth ainda não configurado no servidor.", status=503)
    error = request.query.get("error")
    if error:
        return web.Response(
            text="A autorização foi cancelada ou recusada. Volte ao Discord e use /vincular novamente.",
            content_type="text/plain",
        )
    code = request.query.get("code", "")
    state = request.query.get("state", "")
    if not code or not state:
        return web.Response(text="Resposta OAuth incompleta.", status=400)
    pending = take_oauth_state(state)
    if not pending:
        return web.Response(text="Link expirado ou inválido. Volte ao Discord e use /vincular novamente.", status=400)
    discord_id, verifier, _expires = pending
    try:
        async with http_session.post(
            API + "/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": GOLIVE_OAUTH_CLIENT_ID,
                "client_secret": GOLIVE_OAUTH_CLIENT_SECRET,
                "code": code,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as response:
            token_data = await response.json(content_type=None)
            if response.status >= 400:
                log.error("GoLive OAuth token exchange falhou: HTTP %s", response.status)
                return web.Response(text="Não foi possível concluir o login no GoLive. Tente /vincular novamente.", status=502)
        access_token = token_data.get("access_token")
        if not access_token:
            log.error("GoLive OAuth não retornou access_token.")
            return web.Response(text="O GoLive não retornou uma autorização válida.", status=502)
        async with http_session.get(
            API + "/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        ) as response:
            user_data = await response.json(content_type=None)
            if response.status >= 400:
                log.error("GoLive /users/@me falhou: HTTP %s", response.status)
                return web.Response(text="Não foi possível identificar sua conta GoLive.", status=502)
        user = user_data.get("user", user_data.get("account", user_data))
        golive_id = user.get("id")
        username = user.get("username") or user.get("displayName") or "GoLive"
        if not golive_id:
            log.error("Resposta /users/@me sem ID de usuário: %s", user_data)
            return web.Response(text="O GoLive não informou o ID da conta.", status=502)
        with db() as con:
            existing = con.execute(
                "SELECT discord_id FROM account_links WHERE golive_user_id=?",
                (str(golive_id),),
            ).fetchone()
            if existing and existing[0] != discord_id:
                return web.Response(
                    text="Esta conta GoLive já está vinculada a outra conta Discord. Contate a administração.",
                    status=409,
                )
            con.execute(
                """INSERT INTO account_links(discord_id, golive_user_id, golive_username, linked_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(discord_id) DO UPDATE SET
                   golive_user_id=excluded.golive_user_id,
                   golive_username=excluded.golive_username,
                   linked_at=excluded.linked_at""",
                (discord_id, str(golive_id), str(username), int(time.time())),
            )
        # Não armazenamos access_token nem refresh_token: só precisamos da identidade estável.
        return web.Response(
            text=f"Conta GoLive @{username} vinculada com sucesso! Você já pode voltar ao Discord.",
            content_type="text/plain",
        )
    except Exception:
        log.exception("Erro durante callback OAuth do GoLive.")
        return web.Response(text="Ocorreu um erro ao concluir o vínculo. Tente novamente.", status=500)


@bot.tree.command(name="vincular", description="Vincula sua conta GoLive à sua conta Discord.")
async def link_golive_account(interaction: discord.Interaction):
    try:
        url = create_oauth_link(interaction.user.id)
    except RuntimeError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    await interaction.response.send_message(
        "Abra este link para autorizar sua conta GoLive. O link expira em 10 minutos e só pode ser usado por esta conta Discord:\\n"
        + url,
        ephemeral=True,
    )


@bot.tree.command(name="vinculo", description="Mostra se sua conta GoLive está vinculada.")
async def show_golive_link(interaction: discord.Interaction):
    with db() as con:
        row = con.execute(
            "SELECT golive_username FROM account_links WHERE discord_id=?",
            (str(interaction.user.id),),
        ).fetchone()
    if row:
        await interaction.response.send_message(
            f"Sua conta está vinculada ao GoLive @{row[0]}.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            "Sua conta GoLive ainda não está vinculada. Use /vincular.",
            ephemeral=True,
        )


async def main():
    global http_session, golive_api
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        http_session = session
        golive_api = GoLiveAPI(session)
        app = web.Application()
        app.router.add_get("/oauth/golive/callback", golive_oauth_callback)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "10000")))
        await site.start()
        log.info("Callback OAuth disponível em %s", OAUTH_REDIRECT_URI)
        # A API pode responder 503 retryable durante uma inicialização/manutenção.
        # Nessa situação aguardamos e tentamos novamente, sem derrubar o bot.
        while True:
            try:
                await golive_api.verify()
                break
            except GoLiveAPIError as exc:
                if exc.status == 503 and isinstance(exc.data, dict) and exc.data.get("retryable"):
                    log.warning("API GoLive temporariamente indisponível; nova tentativa em 10 segundos.")
                    await asyncio.sleep(10)
                    continue
                raise
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nVoidBridge encerrado.")
