import discord
from discord.ext import commands
import os
import re
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from scraper import fetch_threads_metadata

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Regex to match threads.com or threads.net URLs
THREADS_URL_PATTERN = re.compile(r'https?://(?:www\.)?threads\.(?:com|net)/(?:t/|@[a-zA-Z0-9._-]+/post/)[a-zA-Z0-9_-]+')

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    print('------')

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

    # Find Threads URLs in the non-spoiler content
    urls = THREADS_URL_PATTERN.findall(content_without_spoilers)

    for url in urls:

        # Clean URL (remove query parameters like ?xmt=...)
        clean_url = url.split('?')[0]
        server_name = message.guild.name if message.guild else "Direct Message"
        channel_name = message.channel.name if hasattr(message.channel, 'name') else "DM Channel"
        tz_tpe = timezone(timedelta(hours=8))
        timestamp = datetime.now(tz_tpe).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {clean_url} from {server_name} / {channel_name}", flush=True)
        metadata = await fetch_threads_metadata(clean_url)
        
        if metadata:
            # Create Embed
            description = metadata.get("description") or "No description available."
            
            # Limit description to 150 characters
            if len(description) > 150:
                description = description[:150] + "..."

            embed = discord.Embed(
                title=metadata.get("title") or "Threads Post",
                description=description,

                url=metadata.get("url"),
                color=0x000000  # Threads black color
            )
            
            if metadata.get("image"):
                # Use large image for posts with media, small thumbnail for text-only/profile posts
                if metadata.get("card") == "summary_large_image" or metadata.get("video"):
                    embed.set_image(url=metadata["image"])
                else:
                    embed.set_thumbnail(url=metadata["image"])
            
            embed.set_footer(
                text="Threads", 
                icon_url="https://cdn-icons-png.flaticon.com/128/12105/12105338.png"
            )
            
            # Send the embed and store the ID for sync deletion
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
