from flask import Flask
from threading import Thread
from discord.ext import commands
import discord
import os
from keep_alive import keep_alive

keep_alive()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

killer_pool_raw = '''
Artist
Blight
Clown
Deathslinger
Demogorgon
Doctor
Dracula
Dredge
Ghost Face
Ghoul
GoodGuy
Hillbilly
Houndmaster
Knight
Lich
Mastermind
NewKiller
Nightmare
Nurse
Oni
Plague
Singularity
SkullMerchant
Spirit
Springtrap
Unknown
Wraith
'''

ALLOWED_ROLES = ["Head of Production", "Admin", "Head of Staff"]

def has_any_role(allowed_roles):
    async def predicate(ctx):
        user_roles = [role.name for role in ctx.author.roles]
        return any(role in user_roles for role in allowed_roles)
    return commands.check(predicate)

killer_pool = sorted(killer_pool_raw.strip().splitlines())

bans = {}    
picks = {}   
turns = {}  
formats = {} 
tb_mode = {}  
actions_done = {} 
format_type = {} 
last_action_team = {} 
ban_streak = {}
team_names = {} 
coinflip_winner = {}
coinflip_used = {}
tiebreaker_picked = {}

EMBED_COLOR = 0x790000

def init_channel(channel_id):
    if channel_id not in bans:
        bans[channel_id] = []
    if channel_id not in picks:
        picks[channel_id] = []
    if channel_id not in turns:
        turns[channel_id] = "A"
    if channel_id not in formats:
        formats[channel_id] = []
    if channel_id not in tb_mode:
        tb_mode[channel_id] = "none"
    if channel_id not in actions_done:
        actions_done[channel_id] = 0
    if channel_id not in format_type:
        format_type[channel_id] = "bo3"
    if channel_id not in last_action_team:
        last_action_team[channel_id] = "A"
    if channel_id not in ban_streak:
        ban_streak[channel_id] = 0
    if channel_id not in team_names:
        team_names[channel_id] = {"A": "Team A", "B": "Team B"}
    if channel_id not in coinflip_winner:
        coinflip_winner[channel_id] = None
    if channel_id not in coinflip_used:
        coinflip_used[channel_id] = False
    if channel_id not in tiebreaker_picked:
        tiebreaker_picked[channel_id] = False

def switch_turn(channel_id):
    if tb_mode[channel_id] == "noTB":
        turns[channel_id] = "B" if turns[channel_id] == "A" else "A"
        return

    fmt = format_type[channel_id]
    index = actions_done[channel_id] - 1
    format_actions = formats[channel_id]

    if index >= len(format_actions):
        return

    current_action = format_actions[index]
    current_team = turns[channel_id]

    if fmt == "bo5" and current_action == "ban":
        if ban_streak[channel_id] == 0:
            ban_streak[channel_id] = 1
        else:
            ban_streak[channel_id] = 0
            turns[channel_id] = "B" if current_team == "A" else "A"
    else:
        turns[channel_id] = "B" if current_team == "A" else "A"
        ban_streak[channel_id] = 0

def show_remaining_killers(channel_id):
    remaining = [k for k in killer_pool if all(k != b[0] for b in bans[channel_id]) and all(k != p[0] for p in picks[channel_id])]
    if remaining:
        return discord.Embed(title="Remaining Killers", description="\n".join(remaining), color=EMBED_COLOR)
    else:
        return None

def announce_next_action(channel_id):
    if tb_mode.get(channel_id) == "noTB":
        remaining = [k for k in killer_pool if all(k != b[0] for b in bans[channel_id]) and all(k != p[0] for p in picks[channel_id])]
        if len(remaining) > 1:
            return f"Next action: **BAN** by {team_names[channel_id][turns[channel_id]]}"
        else:
            return "Format completed. GLHF with your Matches."

    current_format = formats[channel_id]
    index = actions_done[channel_id]

    if index < len(current_format):
        action = current_format[index]
        team = turns[channel_id]
        team_name = team_names[channel_id][team]
        return f"Next action: **{action.upper()}** by {team_name}."
    else:
        return "Format completed. Use !tb <Killer> or !notb."

async def send_final_summary(ctx, channel_id):
    bans_text = "\n".join([f"{k} ({team_names[channel_id].get(team, team)})" for k, team in bans[channel_id]])
    picks_text = "\n".join([f"{k} ({team_names[channel_id].get(team, team)})" for k, team in picks[channel_id]])

    embed = discord.Embed(title="Final Picks & Bans", color=EMBED_COLOR)
    embed.add_field(name="Bans", value=bans_text or "None", inline=False)
    embed.add_field(name="Picks", value=picks_text or "None", inline=False)

    await ctx.send(embed=embed)

@bot.event
async def on_ready():
    print(f"Bot is online: {bot.user}")
    await bot.change_presence(activity=discord.Game(name="made by Fluffy"))

@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

@bot.command()
async def killerpool(ctx):
    embed = discord.Embed(title="Available Killers", description="\n".join(killer_pool), color=EMBED_COLOR)
    await ctx.send(embed=embed)

@bot.command()
async def ban(ctx, *, killer):
    killer = killer.title()
    channel_id = ctx.channel.id
    init_channel(channel_id)

    if not formats[channel_id] and tb_mode[channel_id] != "noTB":
        await ctx.send("Please select a format first.")
        return
    if not coinflip_used[channel_id]:
        await ctx.send("Please use !coinflip first.")
        return
    if turns[channel_id] not in ["A", "B"]:
        await ctx.send("Please use !first or !second to choose the starting team.")
        return

    if tb_mode[channel_id] != "noTB":
        if actions_done[channel_id] >= len(formats[channel_id]) or formats[channel_id][actions_done[channel_id]] != "ban":
            await ctx.send("It's not time to ban. Please follow the pick/ban order.")
            return

    if killer not in killer_pool:
        await ctx.send(f"{killer} is not a valid Killer.")
        return
    if any(k == killer for k, _ in bans[channel_id]):
        await ctx.send(f"{killer} is already banned.")
        return
    if any(k == killer for k, _ in picks[channel_id]):
        await ctx.send(f"{killer} is already picked.")
        return

    team = turns[channel_id]
    team_name = team_names[channel_id][team]
    bans[channel_id].append((killer, team))
    actions_done[channel_id] += 1
    await ctx.send(f"{killer} was banned by {team_name}.")

    if tb_mode[channel_id] == "noTB":
        remaining = [k for k in killer_pool if all(k != b[0] for b in bans[channel_id]) and all(k != p[0] for p in picks[channel_id])]
        if len(remaining) == 1:
            last_killer = remaining[0]
            picks[channel_id].append((last_killer, "Tiebreaker"))
            tb_mode[channel_id] = "resolved"
            await ctx.send(f"Final killer automatically picked: **{last_killer}**")
            await send_final_summary(ctx, channel_id)
            return
        elif len(remaining) > 1:
            embed = show_remaining_killers(channel_id)
            if embed:
                await ctx.send(embed=embed)

    else:
        embed = show_remaining_killers(channel_id)
        if embed:
            await ctx.send(embed=embed)

    switch_turn(channel_id)
    await ctx.send(announce_next_action(channel_id))

@bot.command()
async def pick(ctx, *, killer):
    killer = killer.title()
    channel_id = ctx.channel.id
    init_channel(channel_id)

    if not formats[channel_id]:
        await ctx.send("Please select a format first.")
        return
    if not coinflip_used[channel_id]:
        await ctx.send("Please use !coinflip first.")
        return
    if turns[channel_id] not in ["A", "B"]:
        await ctx.send("Please use !first or !second to choose the starting team.")
        return

    if actions_done[channel_id] >= len(formats[channel_id]) or formats[channel_id][actions_done[channel_id]] != "pick":
        await ctx.send("It's not time to pick. Please follow the pick/ban order.")
        return

    if killer not in killer_pool:
        await ctx.send(f"{killer} is not a valid Killer.")
        return
    if any(k == killer for k, _ in picks[channel_id]):
        await ctx.send(f"{killer} is already picked.")
        return
    if any(k == killer for k, _ in bans[channel_id]):
        await ctx.send(f"{killer} is banned.")
        return

    team = turns[channel_id]
    team_name = team_names[channel_id][team]
    picks[channel_id].append((killer, team))
    actions_done[channel_id] += 1
    await ctx.send(f"{killer} was picked by {team_name}.")

    embed = show_remaining_killers(channel_id)
    if embed:
        await ctx.send(embed=embed)
    else:
        await ctx.send("No killers left to pick or ban.")

    switch_turn(channel_id)
    await ctx.send(announce_next_action(channel_id))

@bot.command()
@has_any_role(ALLOWED_ROLES)
async def reset(ctx):
    channel_id = ctx.channel.id
    init_channel(channel_id)
    bans[channel_id].clear()
    picks[channel_id].clear()
    turns[channel_id] = "A"
    formats[channel_id] = []
    tb_mode[channel_id] = "none"
    actions_done[channel_id] = 0
    format_type[channel_id] = "bo3"
    last_action_team[channel_id] = "A"
    ban_streak[channel_id] = 0
    team_names[channel_id] = {"A": "Team A", "B": "Team B"}
    coinflip_winner[channel_id] = None
    coinflip_used[channel_id] = False
    await ctx.send("Reset complete.")

@bot.command()
@has_any_role(ALLOWED_ROLES)
async def bo3(ctx):
    channel_id = ctx.channel.id
    init_channel(channel_id)
    formats[channel_id] = ["ban", "ban", "ban", "ban", "pick", "pick", "ban", "ban", "ban", "ban", "ban", "ban"]
    format_type[channel_id] = "bo3"
    actions_done[channel_id] = 0
    await ctx.send("Pick & Ban phase set to **Best of 3** format. Use **!coinflip <Team A> <Team B>** to start.")

    format_message = (
        "```diff\n"
        "- Team A bans\n"
        "- Team B bans\n"
        "- Team A bans\n"
        "- Team B bans\n"
        "\n"
        "+ Team A picks\n"
        "+ Team B picks\n"
        "\n"
        "- Team A bans\n"
        "- Team B bans\n"
        "- Team A bans\n"
        "- Team B bans\n"
        "- Team A bans\n"
        "- Team B bans\n"
        "\n"
        "+ Agreeing on TB / 1 ban each until last killer left\n"
        "```"
    )
    await ctx.send(format_message)

@bot.command()
@has_any_role(ALLOWED_ROLES)
async def bo5(ctx):
    channel_id = ctx.channel.id
    init_channel(channel_id)
    formats[channel_id] = ["ban", "ban", "ban", "ban", "ban", "ban", "ban", "ban", "pick", "pick", "ban", "ban", "ban", "ban", "pick", "pick"]
    format_type[channel_id] = "bo5"
    actions_done[channel_id] = 0
    await ctx.send("Pick & Ban phase set to **Best of 5** format. Use **!coinflip <Team A> <Team B>** to start.")

    format_message = (
        "```diff\n"
        "- Team A bans 2\n"
        "- Team B bans 2\n"
        "- Team A bans 2\n"
        "- Team B bans 2\n"
        "\n"
        "+ Team A picks\n"
        "+ Team B picks\n"
        "\n"
        "- Team A bans 2\n"
        "- Team B bans 2\n"
        "\n"
        "+ Team A picks\n"
        "+ Team B picks\n"
        "\n"
        "+ Agreeing on TB / 1 ban each until last killer left\n"
        "```"
    )
    await ctx.send(format_message)

@bot.command()
@has_any_role(ALLOWED_ROLES)
async def coinflip(ctx, *, text: str):
    channel_id = ctx.channel.id
    init_channel(channel_id)

    if not formats[channel_id]:
        await ctx.send("Please select a format first.")
        return

    if coinflip_used[channel_id]:
        await ctx.send("Coinflip has already been used.")
        return

    parts = text.split()
    if len(parts) < 2:
        await ctx.send("Wrong format. Use **!coinflip <Team A> <Team B>**.")
        return

    half = len(parts) // 2
    name1 = " ".join(parts[:half])
    name2 = " ".join(parts[half:])

    import secrets
    if secrets.choice([True, False]):
        team_names[channel_id]["A"] = name1
        team_names[channel_id]["B"] = name2
    else:
        team_names[channel_id]["A"] = name2
        team_names[channel_id]["B"] = name1

    coinflip_winner[channel_id] = secrets.choice(["A", "B"])
    coinflip_used[channel_id] = True

    await ctx.send(f"Coinflip result: **{team_names[channel_id][coinflip_winner[channel_id]]}** won the toss. Use !first or !second to choose turn order.")

@bot.command()
async def first(ctx):
    channel_id = ctx.channel.id
    init_channel(channel_id)

    if coinflip_winner[channel_id] is None:
        await ctx.send("Please run !coinflip before choosing first or second.")
        return
    if actions_done[channel_id] > 0:
        await ctx.send("You can only choose turn order before picks/bans have started.")
        return

    turns[channel_id] = coinflip_winner[channel_id]
    await ctx.send(f"{team_names[channel_id][turns[channel_id]]} chose to go first. Start with !ban <Killer>")

    embed = discord.Embed(title="Available Killers", description="\n".join(killer_pool), color=EMBED_COLOR)
    await ctx.send(embed=embed)

@bot.command()
async def second(ctx):
    channel_id = ctx.channel.id
    init_channel(channel_id)

    if coinflip_winner[channel_id] is None:
        await ctx.send("Please run !coinflip before choosing first or second.")
        return
    if actions_done[channel_id] > 0:
        await ctx.send("You can only choose turn order before picks/bans have started.")
        return

    turns[channel_id] = "A" if coinflip_winner[channel_id] == "B" else "B"
    await ctx.send(f"{team_names[channel_id][turns[channel_id]]} will go first. Use !ban <Killer> to start.")

    embed = discord.Embed(title="Available Killers", description="\n".join(killer_pool), color=EMBED_COLOR)
    await ctx.send(embed=embed)

@bot.command()
async def tb(ctx, *, killer):
    channel_id = ctx.channel.id
    init_channel(channel_id)

    if not formats[channel_id]:
        await ctx.send("Please select a format first with !bo3 or !bo5.")
        return
    if actions_done[channel_id] < len(formats[channel_id]):
        await ctx.send("The pick & ban phase is not finished yet.")
        return
    if tb_mode[channel_id] != "none":
        await ctx.send("Tiebreaker already resolved.")
        return

    killer = killer.title()
    if killer not in killer_pool:
        await ctx.send(f"{killer} is not a valid Killer.")
        return
    if any(k == killer for k, _ in bans[channel_id]) or any(k == killer for k, _ in picks[channel_id]):
        await ctx.send(f"{killer} has already been banned or picked.")
        return

    picks[channel_id].append((killer, "Tiebreaker"))
    tb_mode[channel_id] = "TB"
    await ctx.send(f"Tiebreaker picked: **{killer}**")

    await send_final_summary(ctx, channel_id)

    await ctx.send("Format completed. GLHF with your Matches.")

@bot.command()
async def notb(ctx):
    channel_id = ctx.channel.id
    init_channel(channel_id)

    if not formats[channel_id]:
        await ctx.send("Please select a format first with !bo3 or !bo5.")
        return
    if actions_done[channel_id] < len(formats[channel_id]):
        await ctx.send("The pick & ban phase is not finished yet.")
        return
    if tb_mode[channel_id] != "none":
        await ctx.send("Tiebreaker already resolved.")
        return

    tb_mode[channel_id] = "noTB"

    embed = show_remaining_killers(channel_id)
    if embed:
        await ctx.send("noTB mode activated. Continue banning until only one killer remains.")
        await ctx.send(embed=embed)
    else:
        await ctx.send("noTB mode activated. No killers left.")

    await ctx.send(f"Next action: **BAN** by {team_names[channel_id][turns[channel_id]]}.")

token = os.getenv("TOKEN")
if token is None:
    raise ValueError("TOKEN environment variable not set!")
bot.run(token)
