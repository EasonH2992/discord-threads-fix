import discord
from discord.ext import commands
from discord import app_commands
import os
import re
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import httpx
from scraper import fetch_metadata

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

def log_metadata(url: str, metadata: dict):
    # Extract post ID from URL (last path segment, strip query string)
    post_id = url.rstrip('/').split('/')[-1].split('?')[0] or "unknown"
    platform = "instagram" if "instagram.com" in url else "threads"
    path = os.path.join(LOG_DIR, platform, f"{post_id}.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tz_tpe = timezone(timedelta(hours=8))
    ts = datetime.now(tz_tpe).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"URL: {url}",
        f"Fetched: {ts}",
        f"Title: {metadata.get('title')}",
        f"Description: {metadata.get('description')}",
        f"Image: {metadata.get('image')}",
        f"Video: {metadata.get('video')}",
        f"Type: {metadata.get('type')}",
        f"Card: {metadata.get('card')}",
        f"Taken At: {metadata.get('taken_at')}",
    ]
    images = metadata.get("images", [])
    if images:
        lines.append(f"Carousel Images ({len(images)}):")
        for i, img in enumerate(images, 1):
            lines.append(f"  [{i}] {img}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
DIFY_API_KEY = os.getenv('DIFY_API_KEY')
DIFY_API_URL = os.getenv('DIFY_API_URL')

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Regex to match threads.com/.net or instagram.com URLs (excluding stories)
META_URL_PATTERN = re.compile(r'https?://(?:www\.)?(?:threads\.(?:com|net)/(?:t/|@[a-zA-Z0-9._-]+/post/)|instagram\.com/(?:p|reel|tv)/)[a-zA-Z0-9_-]+')

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    print('------')



def clean_messages(messages: list[discord.Message], bot_id: int) -> str:
    """清洗訊息：過濾 bot、指令、系統訊息，處理 mention 和 URL。"""
    lines = []
    url_re = re.compile(r'https?://\S+')

    for msg in messages:
        # 過濾所有 bot 訊息（包含自己和其他 bot）
        if msg.author.bot:
            continue
        # 過濾系統訊息（加入伺服器、釘選等）
        if msg.type != discord.MessageType.default and msg.type != discord.MessageType.reply:
            continue

        content = msg.content.strip()
        if not content:
            continue

        # 過濾指令訊息
        if content.startswith(('/', '!', '$')):
            continue

        # 處理 Mention：<@123> → @顯示名稱
        def replace_mention(m):
            uid = int(m.group(1))
            member = msg.guild.get_member(uid) if msg.guild else None
            name = member.display_name if member else str(uid)
            return f'@{name}'

        content = re.sub(r'<@!?(\d+)>', replace_mention, content)
        # 移除頻道/身分組 mention 符號
        content = re.sub(r'<#\d+>', '[頻道]', content)
        content = re.sub(r'<@&\d+>', '[身分組]', content)

        # URL 簡化：以 [URL] 取代
        content = url_re.sub('[URL]', content)

        member = msg.guild.get_member(msg.author.id) if msg.guild else None
        author = member.nick or member.global_name or member.name if member else (msg.author.global_name or msg.author.name)
        ts = msg.created_at.astimezone(timezone(timedelta(hours=8))).strftime('%H:%M')
        lines.append(f'[{ts}] **{author}**: {content}')

    return '\n'.join(lines)


@bot.tree.command(name='懶人包', description='整理此頻道的訊息')
@app_commands.describe(hours='要整理幾小時內的訊息（1~12）')
async def lazypack(interaction: discord.Interaction, hours: int = 1):
    hours = max(1, min(hours, 12))
    await interaction.response.defer(thinking=True, ephemeral=True)

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    messages = []
    async for msg in interaction.channel.history(after=since, limit=500, oldest_first=True):
        messages.append(msg)

    if not messages:
        await interaction.followup.send('過去1小時內沒有任何訊息。', ephemeral=True)
        return

    cleaned = clean_messages(messages, bot.user.id)
    if not cleaned:
        await interaction.followup.send('過濾後沒有可整理的訊息。', ephemeral=True)
        return

    # 傳送至 Dify
    payload = {
        'inputs': {'messages': cleaned},
        'response_mode': 'blocking',
        'user': str(interaction.user.id),
    }
    headers = {
        'Authorization': f'Bearer {DIFY_API_KEY}',
        'Content-Type': 'application/json',
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(DIFY_API_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # 取出 Dify 回傳的輸出文字（依工作流輸出欄位調整）
        output = (
            data.get('data', {}).get('outputs', {}).get('text')
            or data.get('answer')
            or str(data)
        )
    except httpx.HTTPStatusError as e:
        await interaction.followup.send(f'Dify 回傳錯誤：{e.response.status_code}\n```{e.response.text[:500]}```', ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f'傳送至 Dify 時發生錯誤：{e}', ephemeral=True)
        return

    # Discord 訊息上限 2000 字，超過則截斷
    if len(output) > 1900:
        output = output[:1900] + '\n…（內容過長已截斷）'

    await interaction.followup.send(f'{output}', ephemeral=True)

# Track original messages and bot responses
# Key: Original Message ID, Value: Bot Response Message ID
response_map = {}

@bot.event
async def on_message(message):
    # Ignore bot's own messages
    if message.author == bot.user:
        return

    # General log for received message (optional, but requested for visibility)
    # print(f"Received message from {message.author}: {message.content[:50]}...")


    # Remove spoiler content before finding URLs
    # Discord spoilers are enclosed in ||...||
    content_without_spoilers = re.sub(r'\|\|.*?\|\|', '', message.content, flags=re.DOTALL)

    # Find URLs in the non-spoiler content
    urls = META_URL_PATTERN.findall(content_without_spoilers)

    for url in urls:

        # Clean URL (remove query parameters like ?xmt=...)
        clean_url = url.split('?')[0]
        server_name = message.guild.name if message.guild else "Direct Message"
        channel_name = message.channel.name if hasattr(message.channel, 'name') else "DM Channel"
        tz_tpe = timezone(timedelta(hours=8))
        timestamp = datetime.now(tz_tpe).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {clean_url} from {server_name} / {channel_name}", flush=True)
        metadata = await fetch_metadata(clean_url)
        
        if metadata:
            log_metadata(clean_url, metadata)
            # Create Embed
            description = metadata.get("description") or ""
            raw_title = metadata.get("title") or "Meta Post"

            # Determine Platform for Footer and Color
            platform_name = "Instagram" if "instagram.com" in clean_url else "Threads"
            platform_icon = "https://cdn-icons-png.flaticon.com/128/2111/2111463.png" if platform_name == "Instagram" else "https://cdn-icons-png.flaticon.com/128/12105/12105338.png"
            embed_color = 0xC13584 if platform_name == "Instagram" else 0x000000

            if platform_name == "Instagram":
                # IG: Extract Name from "Name on Instagram:..."
                name = raw_title.split(" on Instagram")[0] if " on Instagram" in raw_title else raw_title
                
                # IG: Extract username and clean description from "likes - username on Date: \"...\"."
                username = ""
                clean_desc = description
                if " on " in description and ": \"" in description:
                    prefix = description.split(": \"", 1)[0]
                    if " - " in prefix:
                        username = prefix.split(" - ")[-1].split(" on ")[0]
                    else:
                        username = prefix.split(" on ")[0]
                        
                    clean_desc = description.split(": \"", 1)[1]
                    
                    # Strip whitespace first to handle cases like '". '
                    clean_desc = clean_desc.strip()
                    if clean_desc.endswith("\"."):
                        clean_desc = clean_desc[:-2]
                    elif clean_desc.endswith("\""):
                        clean_desc = clean_desc[:-1]
                
                embed_title = f"{name} ({username})" if username else name
                description = clean_desc
                
                # IG 150 char limit
                if len(description) > 150:
                    description = description[:150] + "..."
            else:
                # Threads: no length limit
                embed_title = raw_title

            embed = discord.Embed(
                title=embed_title,
                description=description,
                url=metadata.get("url"),
                color=embed_color
            )
            
            if metadata.get("image"):
                # Use large image for posts with media, small thumbnail for text-only/profile posts
                if metadata.get("card") == "summary_large_image" or metadata.get("video"):
                    embed.set_image(url=metadata["image"])
                else:
                    embed.set_thumbnail(url=metadata["image"])
            
            if metadata.get("taken_at"):
                # Discord embed timestamp expects a UTC datetime object
                embed.timestamp = datetime.fromtimestamp(metadata["taken_at"], tz=timezone.utc)

            embed.set_footer(
                text=platform_name, 
                icon_url=platform_icon
            )

            # Build gallery embeds for multi-image posts
            images = metadata.get("images", [])
            if images:
                embed.set_image(url=images[0])
            if len(images) > 1:
                # Gallery mode: multiple embeds sharing the same url renders as a grid
                post_url = metadata.get("url")
                extra_embeds = [
                    discord.Embed(url=post_url).set_image(url=img)
                    for img in images[1:]
                ]
                response = await message.reply(embeds=[embed] + extra_embeds, mention_author=False, silent=True)
            else:
                response = await message.reply(embed=embed, mention_author=False, silent=True)

            response_map[message.id] = response.id
            
            # Keep map size in check (limit to last 1000 messages)
            if len(response_map) > 1000:
                oldest_key = next(iter(response_map))
                del response_map[oldest_key]
    
    # Process other commands if any
    await bot.process_commands(message)

@bot.event
async def on_raw_message_delete(payload):
    # Check if the deleted message was one we responded to
    if payload.message_id in response_map:
        bot_msg_id = response_map.pop(payload.message_id)
        channel = bot.get_channel(payload.channel_id)
        if channel:
            try:
                bot_msg = await channel.fetch_message(bot_msg_id)
                await bot_msg.delete()
                server_name = channel.guild.name if hasattr(channel, 'guild') and channel.guild else "Direct Message"
                channel_name = channel.name if hasattr(channel, 'name') else "DM Channel"
                tz_tpe = timezone(timedelta(hours=8))
                timestamp = datetime.now(tz_tpe).strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{timestamp}] Deleted bot response for original message {payload.message_id} from {server_name} / {channel_name}", flush=True)
            except Exception as e:
                print(f"Failed to delete bot message: {e}")

if __name__ == "__main__":
    if not TOKEN or TOKEN == "your_bot_token_here":
        print("Error: DISCORD_TOKEN is not set in .env file.")
    else:
        bot.run(TOKEN)
