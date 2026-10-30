import asyncio
import base64
import json
import logging
import os
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import quote

import aiohttp
import discord
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

BRIDGES = CONFIG.get("bridges", [])
MAX_UPLOAD = int(CONFIG.get("max_upload_mb", 20)) * 1024 * 1024
ANNOUNCE_VOICE = bool(CONFIG.get("announce_discord_voice", False))

def db():
    con = sqlite3.connect(BASE / "state.sqlite3")
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
    con.commit()
    return con

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

def bridge_for_discord(channel_id):
    return next((b for b in BRIDGES if str(b.get("discord_channel_id")) == str(channel_id)), None)

def bridge_for_golive(group_id, channel_id):
    return next((b for b in BRIDGES
                 if str(b.get("golive_group_id")) == str(group_id)
                 and str(b.get("golive_channel_id")) == str(channel_id)), None)

def clean_text(text, limit=5900):
    text = (text or "").strip()
    return text[:limit]

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
                raise RuntimeError(f"GoLive HTTP {response.status}: {data}")
            return data

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
        log.error("GoLive recusou o token: %s", event.get("message"))
        return

    if kind == "banned":
        golive_ready.clear()
        log.error("Bot GoLive banido: %s", event)
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

    if kind == "group-voice" and CONFIG.get("announce_golive_voice", False):
        group_id = event.get("groupId")
        for bridge in BRIDGES:
            if str(bridge.get("golive_group_id")) == str(group_id):
                await send_discord(
                    bridge["discord_channel_id"],
                    "[GoLive] A atividade de voz da sala foi atualizada."
                )

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
                    if event.get("type") in ("register-error", "banned"):
                        log.error("Autenticação GoLive recusada; verifique o token no .env.")
                        return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Gateway GoLive desconectou; tentando novamente em 8 segundos.")
        finally:
            golive_ready.clear()
        await asyncio.sleep(8)

@bot.event
async def on_ready():
    log.info("Discord conectado como %s", bot.user)
    if not getattr(bot, "_golive_started", False):
        bot._golive_started = True
        asyncio.create_task(golive_gateway_loop())

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
    if not ANNOUNCE_VOICE or member.bot:
        return
    if before.channel == after.channel:
        return
    for bridge in BRIDGES:
        channel_id = bridge["discord_channel_id"]
        discord_channel = bot.get_channel(int(channel_id))
        if discord_channel is None or not hasattr(discord_channel, "guild"):
            continue
        if discord_channel.guild.id != member.guild.id:
            continue
        if before.channel is None and after.channel:
            notice = f"[Discord] {member.display_name} entrou na chamada **{after.channel.name}**."
        elif after.channel is None and before.channel:
            notice = f"[Discord] {member.display_name} saiu da chamada **{before.channel.name}**."
        else:
            notice = f"[Discord] {member.display_name} mudou de chamada para **{after.channel.name}**."
        try:
            await golive_api.send_message(
                bridge["golive_group_id"], bridge["golive_channel_id"], notice
            )
        except Exception:
            log.exception("Falha ao publicar aviso de voz")

@bot.command(name="bridge")
@commands.has_permissions(manage_guild=True)
async def bridge_status(ctx):
    state = "conectado" if golive_ready.is_set() else "reconectando/desconectado"
    await ctx.reply(f"VoidBridge ativo. GoLive: **{state}**. Pontes configuradas: **{len(BRIDGES)}**.")

@bridge_status.error
async def bridge_status_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("Você precisa da permissão Gerenciar Servidor para usar esse comando.")

async def main():
    global http_session, golive_api
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        http_session = session
        golive_api = GoLiveAPI(session)
        await golive_api.verify()
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nVoidBridge encerrado.")
