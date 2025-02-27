import asyncio
import datetime
import logging

import aiosqlite
import colorama
import discord
from discord.ext import commands, tasks

from config import BASE_XP, TIME_FORMAT, DB_NAME, FILE_TYPES, CHANNELS_TO_LISTEN, ADMIN_ROLES
from discord_token import DISCORD_TOKEN

colorama.init()

log = logging.getLogger("discord.my_log")
handler = logging.FileHandler(f'{"discord"}.log')
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
log .setLevel(20)
log .addHandler(handler)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="-", intents=intents)

DAILY_RESET_TIME = datetime.time(hour=1, minute=0, second=0)


def message_contains_image(msg: discord.Message) -> bool:
    return any(any(f_type in a.content_type for f_type in FILE_TYPES) for a in msg.attachments)


async def handle_new_user(msg: discord.Message, cursor):
    _channel = msg.channel
    _author = msg.author
    _guild = msg.guild

    await _channel.send(f"We spy a new practitioner of the mystic arts!")
    await cursor.execute(f"INSERT INTO {DB_NAME} ("
                         "user,"
                         "streak,"
                         "max_streak,"
                         "last_submission,"
                         "has_posted_today,"
                         "timezone,"
                         "xp,"
                         "guild)"
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (_author.id, 1, 1, datetime.datetime.now().strftime(TIME_FORMAT), 0, 1, BASE_XP, _guild.id))


async def handle_existing_user(msg: discord.Message, cursor, curr_xp):
    """Handle an existing user by updating their XP and sending a confirmation message."""
    _channel = msg.channel
    _author = msg.author
    _guild = msg.guild

    new_xp = curr_xp + BASE_XP
    log.info(f"Adding {BASE_XP} to {_author.id} in guild {_guild.id}")

    await cursor.execute(f"UPDATE {DB_NAME} SET xp = ?, last_submission = ? WHERE user = ? AND guild = ?",
                         (new_xp, datetime.datetime.now().strftime(TIME_FORMAT), _author.id, _guild.id))
    await _channel.send(f"Added {BASE_XP} to {_author} in server {_guild}. (Current exp: {new_xp})")


async def has_posted_today(ctx, cursor):
    """Check if a user has posted today."""
    _author = ctx.author
    _guild = ctx.guild

    async with bot.db.cursor() as cursor:
        await cursor.execute(f"SELECT last_submission from {DB_NAME} where user = ? AND guild = ?",
                             (_author.id, _guild.id))
        last_submission = await cursor.fetchone()

    if last_submission:
        return datetime.datetime.now().day == datetime.datetime.strptime(last_submission[0], TIME_FORMAT).day
    else:
        return False


@bot.event
async def on_ready() -> None:
    log.info(f"{bot.user} is connected to the following guild: {bot.guilds}")

    log.info("Connecting to data base...")
    bot.db = await aiosqlite.connect("daily_challenge_data.db")
    await asyncio.sleep(8.0)

    async with bot.db.cursor() as cursor:
        await cursor.execute(f"CREATE TABLE IF NOT EXISTS {DB_NAME} ("
                             "user INTEGER,"
                             "streak INTEGER,"
                             "max_streak INTEGER,"
                             "last_submission TEXT,"
                             "has_posted_today INTEGER,"
                             "timezone INTEGER,"
                             "xp INTEGER,"
                             "guild INTEGER)")

    log.info(f"Adding cog-task to bot... (Run time: {DAILY_RESET_TIME})")
    await bot.add_cog(MyCog(bot))

    channel = next((ch for ch in bot.get_all_channels() if ch.name in CHANNELS_TO_LISTEN), None)
    if channel:
        await channel.send("Ready to break some wrists")


@bot.event
async def on_message(message: discord.Message) -> None:
    _author = message.author
    _channel = message.channel
    _guild = message.guild

    if _channel.name not in CHANNELS_TO_LISTEN:
        return

    log.info(f"New message:\n"
             f"Channel: {_channel}\n"
             f"Author: {_author}\n"
             f"Contents: {message.content}\n"
             f"Attachments: {message.attachments}")

    if _author.bot:
        return

    if message_contains_image(message):
        log.info("Image was detected.")

        # find data by user and guild id
        async with bot.db.cursor() as cursor:
            await cursor.execute(f"SELECT * FROM {DB_NAME} WHERE user = ? AND guild = ?", (_author.id, _guild.id))
            user_data = await cursor.fetchone()
            # user, streak, max_streak, last_submission, has_posted, timezone, xp, guild

            # if the user has already posted do nothing
            if user_data and user_data[4]:
                log.info(f"User {_author} already posted today")
            else:
                # if a user does not exist
                if not user_data:
                    await handle_new_user(message, cursor)
                # if the user is in our database already
                else:
                    await handle_existing_user(message, cursor, user_data[6])  # xp

                    # if streak is larger than amx streak save as max
                    if user_data and (user_data[1] + 1) > user_data[2]:  # streak > max_streak
                        await cursor.execute(f"UPDATE {DB_NAME} SET max_streak = ? WHERE user = ? AND guild = ?",
                                             (user_data[1] + 1, _author.id, _guild.id))
                    # increase streak by 1
                    await cursor.execute(
                        f"UPDATE {DB_NAME} SET streak = ? WHERE user = ? AND guild = ?",
                        (user_data[1] + 1 if user_data else 2, _author.id, _guild.id))

                # in both cases mark user has posted today
                await cursor.execute(
                    f"UPDATE {DB_NAME} SET has_posted_today = ? WHERE user = ? AND guild = ?",
                    (1, _author.id, _guild.id))

                await bot.db.commit()
    await bot.process_commands(message)


@bot.command()
async def score(ctx, _author: discord.Member = None):
    _channel = ctx.channel
    if _author is None:
        _author = ctx.author
    _guild = ctx.guild

    async with bot.db.cursor() as cursor:
        await cursor.execute(f"SELECT streak, xp, has_posted_today FROM {DB_NAME} WHERE user = ? AND guild = ?",
                             (_author.id, _guild.id))
        user_data = await cursor.fetchone()

    if user_data:
        streak, xp, has_posted = user_data[0], user_data[1], bool(user_data[2])
    else:
        streak, xp, has_posted = 0, 0, False

    log.info(f"{_author} has {streak} day streak, xp: {xp}, has posted: {has_posted}, role: {_author.roles}")
    em = discord.Embed(title=f"{_author.name}'s score", description=f"score: {xp}\nstreak: {streak}\nhas posted: {has_posted}")
    await ctx.send(embed=em)


@bot.command(pass_context=True)
async def scoreboard(ctx):
    _channel = ctx.channel
    _author = ctx.author
    _guild = ctx.guild

    async with bot.db.cursor() as cursor:
        await cursor.execute(f"SELECT user, max_streak, streak, xp from {DB_NAME} where guild = ? ORDER BY max_streak DESC LIMIT 10", (_guild.id,))
        data = await cursor.fetchall()

    em = discord.Embed(title="Top 10 scoreboard")
    for i, user in enumerate(data):
        name = await ctx.bot.fetch_user(user[0])
        log.info(f"{user, name}")
        em.add_field(name=f"{i+1}. {name}", value=f"max streak: {user[1]}, streak: {user[2]}, total xp: {user[3]}", inline=False)
    await ctx.send(embed=em)


@bot.command()
@commands.has_role("test")
async def daily_reset(ctx, _author: discord.Member = None):
    _channel = ctx.channel
    if _author is None:
        _author = ctx.author
    _guild = ctx.guild

    log.info(f"Forced daily reset by: {_author}")
    await daily_task_standalone()


async def daily_task_standalone():
    log.info("Resetting post status!")

    channel_id = None
    for ch in bot.get_all_channels():
        if ch.name in CHANNELS_TO_LISTEN:
            channel_id = ch.id
    channel = bot.get_channel(channel_id)
    await channel.send("Pruning the weaklings..")

    await bot.wait_until_ready()
    async with bot.db.cursor() as cursor:
        await cursor.execute(f"SELECT rowid, user, guild, has_posted_today, streak FROM {DB_NAME}")
        rows = await cursor.fetchall()
        for row in rows:
            row_id, user_id, guild_id, has_posted_today_str, streak = row
            log.info(f"{row_id, user_id, guild_id, has_posted_today_str, streak}")

            if int(has_posted_today_str) == 0:
                await cursor.execute(f"UPDATE {DB_NAME} SET streak = ? WHERE user = ? AND guild = ?",
                                     (0, user_id, guild_id))

            await cursor.execute(f"UPDATE {DB_NAME} SET has_posted_today = ?WHERE user = ? AND guild = ?", (0, user_id, guild_id))
        await bot.db.commit()  # Commit the changes

    await channel.send("A new sun rises on the battlefield..")


class MyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.daily_task.start()
        log.info("Initialised cog.")

    def cog_unload(self):
        self.daily_task.cancel()

    # Define your async function to run at intervals here.
    @tasks.loop(time=DAILY_RESET_TIME, reconnect=False)  # Run once every 24 hours.
    async def daily_task(self):
        await daily_task_standalone()

    @daily_task.before_loop
    async def before_printer(self):
        print('waiting...')
        await self.bot.wait_until_ready()


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
