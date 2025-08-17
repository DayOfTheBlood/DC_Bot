from discord.ext import commands
import discord
import os
import asyncio
import re
from datetime import datetime, timedelta, timezone


keep_alive()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

killer_map_lookup = {
    "Blight": "Blood Lodge",
    "Hillbilly": "Blood Lodge",
    "Nurse": "Groaning Storehouse 2",
    "Spirit": "Father Campbell's Chapel",
    "Dark Lord": "Coal Tower 2",
    "Ghoul": "Wreckers Yard",
    "Houndmaster": "Coal Tower 2",
    "Singularity": "Groaning Storehouse 2",
    "Artist": "Azarov's Resting Place",
    "Clown": "Father Campbell's Chapel",
    "Deathslinger": "Azarov's Resting Place",
    "Demogorgon": "Ormond Lake Mine",
    "Good Guy": "Hawkins Lab",
    "Knight": "Grim Pantry",
    "Mastermind": "Ormond Lake Mine",
    "Nightmare": "Wretched Shop",
    "Oni": "Wretched Shop",
    "Plague": "Family Residence 2",
    "Springtrap": "Lery's Memorial Institute",
    "Unknown": "Family Residence 2",
    "Lich": "Grim Pantry",
    "Dredge": "Midwich Elementary School",
    "Doctor": "Wreckers Yard",
    "Ghost Face": "Lery's Memorial Institute",
    "Wraith": "Hawkins Lab"
}

killer_pool_raw = '''
Artist
Blight
Clown
Dark Lord
Deathslinger
Demogorgon
Doctor
Dredge
Ghostface
Ghoul
Good Guy
Hillbilly
Houndmaster
Knight
Lich
Mastermind
Nightmare
Nurse
Oni
Plague
Singularity
Spirit
Springtrap
Unknown
Wraith
'''

ALLOWED_ROLES = ["Head of Production", "Admin", "Head of Staff"]
STAFF_ROLES = ["Staff", "Head of Production", "Admin", "Head of Staff"]

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

running_timers = {}


def init_channel(channel_id):
    if channel_id not in bans:
        bans[channel_id] = []
    if channel_id not in picks:
        picks[channel_id] = []
    if channel_id not in turns:
        turns[channel_id] = None
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
    remaining = [
        k for k in killer_pool
        if all(k != b[0]
               for b in bans[channel_id]) and all(k != p[0]
                                                  for p in picks[channel_id])
    ]
    if remaining:
        return discord.Embed(title="Remaining Killers",
                             description="\n".join(remaining),
                             color=EMBED_COLOR)
    else:
        return None

def announce_next_action(channel_id):
    if tb_mode.get(channel_id) == "noTB":
        remaining = [
            k for k in killer_pool
            if all(k != b[0] for b in bans[channel_id]) and all(
                k != p[0] for p in picks[channel_id])
        ]
        if len(remaining) > 1:
            return f"Next action: **BAN** by {team_names[channel_id][turns[channel_id]]}"
        else:
            return "Format completed. **GLHF with your Matches**."

    current_format = formats[channel_id]
    index = actions_done[channel_id]

    if index < len(current_format):
        if turns[channel_id] not in ["A", "B"]:
            return "Turn order not set. Use **!first** or **!second**."
        action = current_format[index]
        team = turns[channel_id]
        team_name = team_names[channel_id][team]
        return f"Next action: **{action.upper()}** by {team_name}."
    else:
        return "Format completed. Use **!tb <Killer>** to select a Tiebreaker now or **!notb** to continiue banning."

async def send_final_summary(ctx, channel_id):
    bans_text = "\n".join([
        f"{k} ({team_names[channel_id].get(team, team)})"
        for k, team in bans[channel_id]
    ])
    picks_text = "\n".join([
        f"{k} ({team_names[channel_id].get(team, team)})"
        for k, team in picks[channel_id]
    ])

    embed = discord.Embed(title="Final Picks & Bans", color=EMBED_COLOR)
    embed.add_field(name="Bans", value=bans_text or "None", inline=False)
    embed.add_field(name="Picks", value=picks_text or "None", inline=False)

    await ctx.send(embed=embed)

@bot.event
async def on_ready():
    print(f"Bot is online: {bot.user}")
    await bot.change_presence(activity=discord.Game(name="made by Fluffy"))

@bot.command()
@has_any_role(ALLOWED_ROLES)
async def ping(ctx):
    await ctx.send("pong!")

@bot.command()
async def killerpool(ctx):
    embed = discord.Embed(title="Available Killers",
                          description="\n".join(killer_pool),
                          color=EMBED_COLOR)
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
        await ctx.send("Please use **!coinflip <Team A> <Team B>** first.")
        return
    if turns[channel_id] not in ["A", "B"]:
        await ctx.send(
            "Please use **!first** or **!second** to choose the starting team."
        )
        return

    if tb_mode[channel_id] != "noTB":
        if actions_done[channel_id] >= len(
                formats[channel_id]) or formats[channel_id][
                    actions_done[channel_id]] != "ban":
            await ctx.send(
                "It's not time to ban. Please follow the pick/ban order.")
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
        remaining = [
            k for k in killer_pool
            if all(k != b[0] for b in bans[channel_id]) and all(
                k != p[0] for p in picks[channel_id])
        ]
        if len(remaining) == 1:
            last_killer = remaining[0]
            picks[channel_id].append((last_killer, "Tiebreaker"))
            tb_mode[channel_id] = "resolved"
            await ctx.send(
                f"Final killer automatically picked: **{last_killer}**")
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
        await ctx.send("Please use **!coinflip <Team A> <Team B>** first.")
        return
    if turns[channel_id] not in ["A", "B"]:
        await ctx.send(
            "Please use **!first** or **!second** to choose the starting team."
        )
        return

    picked_map = killer_map_lookup.get(killer)
    if picked_map:
        used_maps = {
            killer_map_lookup.get(k, None)
            for k, team in picks[channel_id]
            if team != "Tiebreaker" and killer_map_lookup.get(k) is not None
        }

        picked_killers = {
            k for k, team in picks[channel_id]
            if team != "Tiebreaker"
        }

        for other_killer, other_map in killer_map_lookup.items():
            if other_killer != killer and other_map == picked_map and other_killer in picked_killers:
                await ctx.send(
                    f"{killer} cannot be picked. The map **{picked_map}** is already in use by **{other_killer}**."
                )
                return

        if picked_map in used_maps:
            conflicting_killers = [
                k for k, m in killer_map_lookup.items()
                if m == picked_map and any(k == pk
                                           for pk, _ in picks[channel_id])
            ]
            await ctx.send(
                f"{killer} cannot be picked. The map **{picked_map}** is already used by another picked killer: {', '.join(conflicting_killers)}"
            )
            return

    if actions_done[channel_id] >= len(
            formats[channel_id]) or formats[channel_id][
                actions_done[channel_id]] != "pick":
        await ctx.send(
            "It's not time to pick. Please follow the pick/ban order.")
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
@has_any_role(STAFF_ROLES)
async def reset(ctx):
    channel_id = ctx.channel.id
    init_channel(channel_id)
    bans[channel_id].clear()
    picks[channel_id].clear()
    turns[channel_id] = None
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
@has_any_role(STAFF_ROLES)
async def bo3(ctx):
    channel_id = ctx.channel.id
    init_channel(channel_id)
    formats[channel_id] = [
        "ban", "ban", "ban", "ban", "pick", "pick", "ban", "ban", "ban", "ban",
        "ban", "ban"
    ]
    format_type[channel_id] = "bo3"
    actions_done[channel_id] = 0
    await ctx.send(
        "Pick & Ban phase set to **Best of 3** format. Use **!coinflip <Team A> <Team B>** to start."
    )

    format_message = ("```diff\n"
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
                      "```")
    await ctx.send(format_message)

@bot.command()
@has_any_role(STAFF_ROLES)
async def bo5(ctx):
    channel_id = ctx.channel.id
    init_channel(channel_id)
    formats[channel_id] = [
        "ban", "ban", "ban", "ban", "ban", "ban", "ban", "ban", "pick", "pick",
        "ban", "ban", "ban", "ban", "pick", "pick"
    ]
    format_type[channel_id] = "bo5"
    actions_done[channel_id] = 0
    await ctx.send(
        "Pick & Ban phase set to **Best of 5** format. Use **!coinflip <Team A> <Team B>** to start."
    )

    format_message = ("```diff\n"
                      "- Team A bans 2x\n"
                      "- Team B bans 2x\n"
                      "- Team A bans 2x\n"
                      "- Team B bans 2x\n"
                      "\n"
                      "+ Team A picks\n"
                      "+ Team B picks\n"
                      "\n"
                      "- Team A bans 2x\n"
                      "- Team B bans 2x\n"
                      "\n"
                      "+ Team A picks\n"
                      "+ Team B picks\n"
                      "\n"
                      "+ Agreeing on TB / 1 ban each until last killer left\n"
                      "```")
    await ctx.send(format_message)

@bot.command()
@has_any_role(STAFF_ROLES)
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

    await ctx.send(
        f"Coinflip result: **{team_names[channel_id][coinflip_winner[channel_id]]}** won the toss. Use **!first** or **!second** to choose turn order."
    )

@bot.command()
async def first(ctx):
    channel_id = ctx.channel.id
    init_channel(channel_id)

    if coinflip_winner[channel_id] is None:
        await ctx.send(
            "Please run **!coinflip <Team A> <Team B>** before choosing first or second."
        )
        return
    if actions_done[channel_id] > 0:
        await ctx.send(
            "You can only choose turn order before picks/bans have started.")
        return

    turns[channel_id] = coinflip_winner[channel_id]
    await ctx.send(
        f"{team_names[channel_id][turns[channel_id]]} chose to go first. Start with **!ban <Killer>**."
    )

    embed = discord.Embed(title="Available Killers",
                          description="\n".join(killer_pool),
                          color=EMBED_COLOR)
    await ctx.send(embed=embed)

@bot.command()
async def second(ctx):
    channel_id = ctx.channel.id
    init_channel(channel_id)

    if coinflip_winner[channel_id] is None:
        await ctx.send(
            "Please run **!coinflip <Team A> <Team B>** before choosing first or second."
        )
        return
    if actions_done[channel_id] > 0:
        await ctx.send(
            "You can only choose turn order before picks/bans have started.")
        return

    turns[channel_id] = "A" if coinflip_winner[channel_id] == "B" else "B"
    await ctx.send(
        f"{team_names[channel_id][turns[channel_id]]} will go first. Use **!ban <Killer>** to start."
    )

    embed = discord.Embed(title="Available Killers",
                          description="\n".join(killer_pool),
                          color=EMBED_COLOR)
    await ctx.send(embed=embed)

@bot.command()
async def tb(ctx, *, killer):
    channel_id = ctx.channel.id
    init_channel(channel_id)

    if not formats[channel_id]:
        await ctx.send(
            "Please select a format first with **!bo3** or **!bo5**.")
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
    if any(k == killer
           for k, _ in bans[channel_id]) or any(k == killer
                                                for k, _ in picks[channel_id]):
        await ctx.send(f"{killer} has already been banned or picked.")
        return

    picks[channel_id].append((killer, "Tiebreaker"))
    tb_mode[channel_id] = "TB"
    await ctx.send(f"Tiebreaker picked: **{killer}**")

    await send_final_summary(ctx, channel_id)

    await ctx.send("Format completed. **GLHF with your Matches**.")

@bot.command()
async def notb(ctx):
    channel_id = ctx.channel.id
    init_channel(channel_id)

    if not formats[channel_id]:
        await ctx.send(
            "Please select a format first with **!bo3** or **!bo5**.")
        return
    if actions_done[channel_id] < len(formats[channel_id]):
        await ctx.send("The pick & ban phase is not finished yet.")
        return
    if tb_mode[channel_id] != "none":
        await ctx.send("Tiebreaker already resolved.")
        return
    if turns[channel_id] not in ["A", "B"]:
        await ctx.send("Please use **!first** or **!second** to choose the starting team before continuing.")
        return

    tb_mode[channel_id] = "noTB"

    embed = show_remaining_killers(channel_id)
    if embed:
        await ctx.send(
            "noTB mode activated. Continue banning until only one killer remains."
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send("noTB mode activated. No killers left.")

    await ctx.send(
        f"Next action: **BAN** by {team_names[channel_id][turns[channel_id]]}."
    )

@bot.command()
@has_any_role(STAFF_ROLES)
async def fluffy(ctx):

    steam_id = "76561198159133215"
    dbd_name = "FluffyTailedHog#6a05"
    steam_profile_url = f"https://steamcommunity.com/profiles/{steam_id}"

    embed = discord.Embed(title="Fluffy's Streamer ID",
                          color=EMBED_COLOR,
                          description="Streamer for your matches, please add:")
    embed.add_field(name="Steam ID",
                    value=f"[{steam_id}]({steam_profile_url})",
                    inline=False)

    embed.add_field(name="DBD ID", value=dbd_name, inline=False)

    await ctx.send(embed=embed)

@bot.command()
@has_any_role(STAFF_ROLES)
async def voum(ctx):

    steam_id = "76561198441488741"
    dbd_name = "Voum#9559"
    steam_profile_url = f"https://steamcommunity.com/profiles/{steam_id}"

    embed = discord.Embed(title="Voum's Streamer ID",
                          color=EMBED_COLOR,
                          description="Streamer for your matches, please add:")
    embed.add_field(name="Steam ID",
                    value=f"[{steam_id}]({steam_profile_url})",
                    inline=False)

    embed.add_field(name="DBD ID", value=dbd_name, inline=False)

    await ctx.send(embed=embed)

@bot.command()
@has_any_role(STAFF_ROLES)
async def brian(ctx):

    steam_id = "76561199053313449"
    dbd_name = "vCryxby#65d0"
    steam_profile_url = f"https://steamcommunity.com/profiles/{steam_id}"

    embed = discord.Embed(title="Brian's Streamer ID",
                          color=EMBED_COLOR,
                          description="Streamer for your matches, please add:")
    embed.add_field(name="Steam ID",
                    value=f"[{steam_id}]({steam_profile_url})",
                    inline=False)

    embed.add_field(name="DBD ID", value=dbd_name, inline=False)

    await ctx.send(embed=embed)

@bot.command()
@has_any_role(STAFF_ROLES)
async def random(ctx):
    import random

    random_killer = random.choice(killer_pool)

    embed = discord.Embed(
        title="Random Killer",
        description=f"The randomly selected killer is: **{random_killer}**",
        color=EMBED_COLOR)
    await ctx.send(embed=embed)

@bot.command()
async def killerinfo(ctx):
    killer_tiers = {
        "Tier 1": [
            ("Blight", "Blood Lodge"),
            ("Hillbilly", "Blood Lodge"),
            ("Nurse", "Groaning Storehouse 2"),
        ],
        "Tier 2": [
            ("Spirit", "Father Campbell's Chapel"),
            ("Dark Lord", "Coal Tower 2"),
            ("Ghoul", "Wreckers Yard"),
            ("Houndmaster", "Coal Tower 2"),
            ("Singularity", "Groaning Storehouse 2"),
        ],
        "Tier 3": [
            ("Artist", "Azarov's Resting Place"),
            ("Clown", "Father Campbell's Chapel"),
            ("Deathslinger", "Azarov's Resting Place"),
            ("Demogorgon", "Ormond Lake Mine"),
            ("Good Guy", "Hawkins Lab"),
            ("Knight", "Grim Pantry"),
            ("Mastermind", "Ormond Lake Mine"),
            ("Nightmare", "Wretched Shop"),
            ("Oni", "Wretched Shop"),
            ("Plague", "Family Residence 2"),
            ("Springtrap", "Lery's Memorial Institute"),
            ("Unknown", "Family Residence 2"),
            ("Lich", "Grim Pantry"),
        ],
        "Tier 4": [
            ("Dredge", "Midwich Elementary School"),
            ("Doctor", "Wreckers Yard"),
            ("Ghost Face", "Lery's Memorial Institute"),
            ("Wraith", "Hawkins Lab"),
        ]
    }

    output = "Killer Tier List\n"
    output += "================\n\n"

    for tier, killers in killer_tiers.items():
        output += f"{tier}:\n"
        for killer, map_name in sorted(killers, key=lambda x: x[0]):
            output += f" - {killer} (Map: {map_name})\n"
        output += "\n"

    await ctx.send(f"```{output}```")

@bot.command()
@has_any_role(ALLOWED_ROLES)
async def clear(ctx, limit: int = 100):
    """Löscht alle Bot-Nachrichten in diesem Channel (max. <limit>)."""

    def is_bot_msg(msg):
        return msg.author == bot.user

    deleted = await ctx.channel.purge(limit=limit, check=is_bot_msg)
    
    info = await ctx.send(f"{len(deleted)} Messages were deleted.")

@bot.command()
async def allcommands(ctx):
    embed = discord.Embed(
        title="Available Commands",
        color=EMBED_COLOR
    )
    embed.add_field(name="", value="", inline=False)
    embed.add_field(name="!first / !second", value="Coinflip winner decides to pick first or second", inline=False)
    embed.add_field(name="!ban <Killer>", value="Bans the specified killer.", inline=False)
    embed.add_field(name="!pick <Killer>", value="Picks the specified killer (with map check).", inline=False)
    embed.add_field(name="!killerpool", value="Displays the full list of available killers. (Updated during Pick/Bans)", inline=False)
    embed.add_field(name="!killerinfo", value="Displays all killers with their assigned map and tier.", inline=False)
    embed.add_field(name="!pov", value="Displays the official streaming rules for the match.", inline=False)
    embed.add_field(name="!ping", value="Replies with 'Pong!' to test bot responsiveness.", inline=False)
    embed.add_field(name="!tb", value="Picks the Tiebreaker and ends the Phase", inline=False)
    embed.add_field(name="!notb", value="Sets the Phase to ban until last Killer", inline=False)
    embed.add_field(name="!timer <duration>", value="Starts a timer. Accepts minutes (number), seconds (…s), or combos like 1m30s.", inline=False)

    await ctx.send(embed=embed)

@bot.command()
@has_any_role(STAFF_ROLES)
async def staffcommands(ctx):
    embed = discord.Embed(
        title="Available Commands",
        description="Here is a list of all available commands and their functions:",
        color=EMBED_COLOR
    )
    embed.add_field(name="", value="", inline=False)
    embed.add_field(name="~!first / !second", value="Choose which team starts the pick & ban phase.", inline=False)
    embed.add_field(name="~!ban <Killer>", value="Bans the specified killer.", inline=False)
    embed.add_field(name="~!pick <Killer>", value="Picks the specified killer (with map check).", inline=False)
    embed.add_field(name="~!killerpool", value="Shows the full list of available killers.", inline=False)
    embed.add_field(name="~!killerinfo", value="Displays all killers with their assigned map and tier.", inline=False)
    embed.add_field(name="~!pov", value="Displays the official streaming rules for the match.", inline=False)
    embed.add_field(name="~!ping", value="Replies with 'Pong!' to test bot responsiveness.", inline=False)
    embed.add_field(name="~!tb", value="Picks the Tiebreaker and ends the Phase", inline=False)
    embed.add_field(name="~!notb", value="Sets the Phase to ban until last Killer", inline=False)
    embed.add_field(name="~!timer <duration>", value="Starts a timer. Accepts minutes (number), seconds (…s), or combos like 1m30s.", inline=False)
    embed.add_field(name="!bo3", value="Sets the pick & ban phase to Best of 3 format.", inline=False)
    embed.add_field(name="!bo5", value="Sets the pick & ban phase to Best of 5 format.", inline=False)
    embed.add_field(name="!coinflip <Team A> <Team B>", value="Randomly assigns teams A/B and determines coinflip winner.", inline=False)
    embed.add_field(name="!tb <Killer>", value="Picks a tiebreaker killer.", inline=False)
    embed.add_field(name="!notb", value="Activates no-TB mode (ban until one killer remains).", inline=False)
    embed.add_field(name="!random", value="Randomly selects a killer from the pool.", inline=False)
    embed.add_field(name="!reset", value="Resets the draft state for this channel.", inline=False)
    embed.add_field(name="!fluffy / !voum / !brian", value="Shows the streamer's Steam and DBD ID.", inline=False)
    embed.add_field(name="", value="", inline=False)
    embed.add_field(name="", value="Note that all commands that start with '~' can be used by the teams too.", inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
@has_any_role(ALLOWED_ROLES)
async def purge(ctx):
    """Löscht alle Nachrichten im aktuellen Channel."""
    await ctx.send("Deleting all massages...", delete_after=3)

    def always_true(msg):
        return True

    deleted = await ctx.channel.purge(check=always_true)
    info = await ctx.send(f"{len(deleted)} Messages were deleted.",
                          delete_after=5)

@bot.command()
async def pov(ctx):
    embed = discord.Embed(
        title="Streaming Rules",
        description=
        ("While streaming a match, a minimum stream delay of **15 minutes** is required "
         "and your stream title must include `@DayOfTheBlood`.\n\n"
         "*Please note: Day of the Blood is not responsible for any cases of stream sniping.*"
         ),
        color=EMBED_COLOR)
    await ctx.send(embed=embed)

@bot.command()
async def killer(ctx):
    embed = discord.Embed(
        title="Killer Setup",
        description=
        ("Please invite the streamer to your lobby and make sure to provide [steam login history](https://help.steampowered.com/en/accountdata/SteamLoginHistory), "
         "a full screenshot of your build, map and disable the 'idle crows' setting.\n\n"
         "You have 5 minutes to do so."
         ),
        color=EMBED_COLOR)
    await ctx.send(embed=embed)

@bot.command()
async def survivor(ctx):
    embed = discord.Embed(
        title="Survivor Setup",
        description=
        ("Please join streamer's lobby and provide full screenshots of your builds and crossplay settings.\n\n You have 5 minutes to do so."
         ),
        color=EMBED_COLOR)
    await ctx.send(embed=embed)

def parse_duration_to_seconds(spec: str) -> int | None:
    """
    Accepts:
      - '34s', '5m'
      - combos: '1m30s', '90s'
      - bare number => minutes (compat with '!timer 5')
    Returns seconds (int) or None on parse error.
    """
    s = spec.strip().lower()

    # bare number => minutes
    if re.fullmatch(r"\d+", s):
        return int(s) * 60

    # single unit m/s
    m = re.fullmatch(r"(\d+)\s*([ms])", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        return n * (60 if unit == "m" else 1)

    # combo 'XmYs' (both optional but at least one present)
    m = re.fullmatch(r"^\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?\s*$", s)
    if m and (m.group(1) or m.group(2)):
        mins = int(m.group(1) or 0)
        secs = int(m.group(2) or 0)
        return mins * 60 + secs

    return None

def human_label_from_seconds_en(total: int) -> str:
    """Return label like '34 second', '5 minute', '1 minute 30 second' (singular/plural handled)."""
    mins = total // 60
    secs = total % 60

    parts = []
    if mins:
        parts.append(f"{mins} minute" + ("" if mins == 1 else "s"))
    if secs:
        parts.append(f"{secs} second" + ("" if secs == 1 else "s"))

    return " ".join(parts) if parts else "0-seconds"

async def _run_timer_seconds(ctx, total_seconds: int, label: str):
        target_dt = datetime.now(timezone.utc) + timedelta(seconds=total_seconds)
        unix_ts = int(target_dt.timestamp())

        # start message
        msg = await ctx.send(
            f"Timer ends in <t:{unix_ts}:R>"
        )

        try:
            await asyncio.sleep(total_seconds)
        except asyncio.CancelledError:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass
            return

        # delete old, post final
        try:
            await msg.delete()
        except discord.HTTPException:
            pass

        await ctx.send(f"The {label} timer has ended.")

@bot.command(aliases=["t"])
async def timer(ctx, *, amount: str | None = None):
    """
    Start a timer.
    Examples:
      !timer 5       -> 5 minutes
      !timer 34s     -> 34 seconds
      !timer 1m30s   -> 1 minute 30 seconds
    """
    if not amount:
        await ctx.send("Usage: !timer <number|…m|…s|combo>  e.g., !timer 34s or !timer 1m30s")
        return

    seconds = parse_duration_to_seconds(amount)
    if seconds is None:
        await ctx.send("Could not parse duration. Try: !timer 34s, !timer 5m or !timer 1m30s")
        return

    if seconds < 1:
        await ctx.send("Minimum duration is 1 second.")
        return
    if seconds > 24 * 3600:
        await ctx.send("Maximum duration is 24 hours.")
        return

    label = human_label_from_seconds_en(seconds)
    asyncio.create_task(_run_timer_seconds(ctx, seconds, label))

token = os.getenv("TOKEN")
if token is None:
    raise ValueError("TOKEN environment variable not set!")
bot.run(token)
