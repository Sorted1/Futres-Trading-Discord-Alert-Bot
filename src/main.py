import discord
from discord.ext import commands
from utils.config import PREFIX, ACTIVITY
import asyncio
from dotenv import load_dotenv
import os

load_dotenv()

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}!")

    activity_type_map = {
        "playing": discord.ActivityType.playing,
        "watching": discord.ActivityType.watching,
        "listening": discord.ActivityType.listening,
        "streaming": discord.ActivityType.streaming
    }

    act_type = activity_type_map.get(ACTIVITY.get("activity_type", "playing"), discord.ActivityType.playing)
    activity_text = ACTIVITY.get("activity_text", "")
    activity_url = ACTIVITY.get("activity_url", None) if act_type == discord.ActivityType.streaming else None

    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(type=act_type, name=activity_text, url=activity_url)
    )

async def main():
    async with bot:
        await bot.load_extension("cogs.monitor")
        #await bot.load_extension("cogs.commands")
        await bot.start(os.getenv("TOKEN"))

asyncio.run(main())