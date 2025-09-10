from discord.ext import commands
import discord
import os
import asyncio
import re
from datetime import datetime, timedelta, timezone
import json
import asyncio
from pathlib import Path
try:
    from zoneinfo import ZoneInfo  # stdlib
except Exception:
    ZoneInfo = None
from typing import Optional, Literal
import time, aiohttp

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

killer_map_lookup = {
    "Animatronic": "Lery's Memorial Institute",
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
    "Unknown": "Family Residence 2",
    "Lich": "Grim Pantry",
    "Dredge": "Midwich Elementary School",
    "Doctor": "Wreckers Yard",
    "Ghostface": "Lery's Memorial Institute",
    "Wraith": "Hawkins Lab"
}

KILLER_ALIASES = {
    "ghost face": "Ghostface",
    "goodguy": "Good Guy",
    "chucky": "Good Guy",
    "wesker": "Mastermind",
    "dracula": "Dark Lord",
    "springtrap": "Animatronic"
}

killer_pool_raw = '''
Animatronic
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
Unknown
Wraith
'''

ALLOWED_ROLES = ["Head of Production", "Admin", "Head of Staff"]
STAFF_ROLES = ["Staff", "Head of Production", "Admin", "Head of Staff"]

def normalize_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())

def normalize_killer_name(raw: str) -> str:
    s = re.sub(r"[^a-z0-9]", "", raw.lower())
    if s in KILLER_ALIASES:
        return KILLER_ALIASES[s]
    return raw.strip().title()

killer_pool = sorted(killer_pool_raw.strip().splitlines())

action_log: dict[int, list[str]] = {}
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
_match_index: dict[int, dict[int, datetime]] = {}

ATTENDANCE_CHANNEL_IDS: set[int] = {
    1194231270851477544,
}
# Custom-Emoji-IDs (IDs sind stabiler als Namen). Falls du die IDs noch nicht hast,
# setze hier 0 und der Bot versucht per Name-Fallback ("r_letter_c", "r_letter_r", "r_cross03").
ATTENDANCE_EMOJI_IDS = {
    "C": 0,  # :r_letter_c:
    "R": 0,  # :r_letter_r:
    "X": 0,  # :r_cross03:
}
ATTENDANCE_ROLE_IDS = {"Caster": None, "Referee": None}
ATTENDANCE_ROLE_NAMES = {"Caster", "Referee"}
GSHEETS_START_ROW = 10          # ab hier nach oben einfügen
SHEET_NUM_COLS = 4              # A:D  (Name, ID, Status, Note)
GSHEETS_LIVE_START_ROW = 10

if ZoneInfo is not None:
    ATTENDANCE_TZ = ZoneInfo("Europe/Berlin")
else:
    try:
        import pytz  # optional
        ATTENDANCE_TZ = pytz.timezone("Europe/Berlin")
    except Exception:
        from datetime import timezone, timedelta
        # letzter Ausweg (ohne DST): CET+1 — nicht perfekt, aber der Bot bleibt lauffähig
        ATTENDANCE_TZ = timezone(timedelta(hours=1))

ATTENDANCE_STORE_FILE = Path(__file__).with_name("attendance_store.json")

GSHEETS_KEYFILE = Path(__file__).with_name("google_service_account.json")
GSHEETS_SPREADSHEET_NAME = "AT"
GSHEETS_SHEET_TITLE = "AT"
GSHEETS_LOG_TITLE = "LOGS"


EMBED_COLOR = 0x790000
DEFAULT_FORUM_CHANNEL_ID = 1401038120916357140
KILLER_FORUM_OVERRIDES: dict[str, int] = {
    #für override
}
running_timers = {}

attendance_store = {
    "sessions": {},       # { "<guild_id>:<message_id>": {meta...} }
    "finalized": {},      # { "<guild_id>:<message_id>": { snapshot, rows } }
    "blacklist": {},      # { "<guild_id>": [user_id, ...] }
    "sheet_blocks": {},   # { "<guild>:<message>": {"start": int, "height": int, "finalized": bool} }
}

def _att_key(guild_id: int, message_id: int) -> str:
    return f"{guild_id}:{message_id}"

def _att_backfill_sheets() -> int:
    """Exportiert alle finalisierten Sessions, die noch kein 'exported'=True haben."""
    count = 0
    finals = attendance_store.get("finalized", {})
    for key, entry in list(finals.items()):
        if entry.get("exported"):
            continue
        snapshot = entry.get("snapshot") or {}
        rows = entry.get("rows") or []
        if not rows:
            continue
        try:
            ok = _att_sheets_append_rows(snapshot, rows)
            if ok:
                entry["exported"] = True
                count += 1
        except Exception:
            # still skip; optional: log ins Sheet via _att_sheets_log
            pass
    if count:
        _attendance_save_store()
    return count

def _attendance_load_store():
    global attendance_store
    try:
        if ATTENDANCE_STORE_FILE.exists():
            attendance_store = json.loads(
                ATTENDANCE_STORE_FILE.read_text(encoding="utf-8")
            )
        else:
            attendance_store = {
                "sessions": {},
                "finalized": {},
                "blacklist": {},
                "sheet_blocks": {},
            }
    except Exception:
        # falls Laden schief geht, wenigsten eine gültige Struktur haben
        attendance_store = {
            "sessions": {},
            "finalized": {},
            "blacklist": {},
            "sheet_blocks": {},
        }
    # sicherstellen, dass der neue Key vorhanden ist (auch bei alten Dateien)
    attendance_store.setdefault("sheet_blocks", {})

def _attendance_save_store():
    ATTENDANCE_STORE_FILE.write_text(json.dumps(attendance_store, ensure_ascii=False, indent=2), encoding="utf-8")

_date_re_full = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")
_ts_re = re.compile(r"<t:(\d+)(?::[a-zA-Z])?>")

def _is_date_anchor_message(msg: discord.Message) -> Optional[str]:
    """
    Returns 'YYYY-MM-DD' if content is exactly a date dd.mm.yyyy (no replies), else None.
    """
    if msg.reference:  # Anchors sind keine Replies
        return None
    m = _date_re_full.match((msg.content or "").strip())
    if not m:
        return None
    d, mo, y = map(int, m.groups())
    try:
        return f"{y:04d}-{mo:02d}-{d:02d}"
    except Exception:
        return None

def _extract_first_timestamp_dt(msg: discord.Message) -> Optional[datetime]:
    """
    Returns aware datetime (Europe/Berlin) for the first <t:UNIX> timestamp in message, else None.
    """
    m = _ts_re.search(msg.content or "")
    if not m:
        return None
    try:
        ts = int(m.group(1))
        return datetime.fromtimestamp(ts, tz=ATTENDANCE_TZ)
    except Exception:
        return None

def _resolve_role_id(guild: discord.Guild, role_name: str, role_id: Optional[int]) -> Optional[int]:
    if role_id:
        return role_id
    r = discord.utils.find(lambda rr: rr.name == role_name, guild.roles)
    return r.id if r else None

def _resolve_roles(guild: discord.Guild) -> dict[str, Optional[int]]:
    return {
        name: _resolve_role_id(guild, name, ATTENDANCE_ROLE_IDS.get(name))
        for name in ATTENDANCE_ROLE_NAMES
    }

def _emoji_id_from_reaction_emoji(e) -> Optional[int]:
    # Works for Emoji / PartialEmoji; unicode returns None
    try:
        return int(getattr(e, "id", None) or 0) or None
    except Exception:
        return None

def _resolve_emoji_ids(guild: discord.Guild) -> dict[str, Optional[int]]:
    ids = dict(ATTENDANCE_EMOJI_IDS)  # copy
    # Fallback per Name, falls IDs = 0
    needed = {k for k, v in ids.items() if not v}
    if needed:
        by_name = {
            "C": ("r_letter_c",),
            "R": ("r_letter_r",),
            "X": ("r_cross03",),
        }
        for k in needed:
            for name in by_name.get(k, ()):
                em = discord.utils.get(guild.emojis, name=name)
                if em:
                    ids[k] = em.id
                    break
    return ids

def _guild_blacklist(guild_id: int) -> set[int]:
    return set(attendance_store.get("blacklist", {}).get(str(guild_id), []))

#------- ATTANDANCE TRACKER SHEET ----------

def _try_import_gs():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        return gspread, Credentials
    except Exception:
        return None, None

def _gs_open_or_none():
    if not GSHEETS_KEYFILE.exists():
        return None
    gspread, Credentials = _try_import_gs()
    if not gspread:
        return None
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(str(GSHEETS_KEYFILE), scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open(GSHEETS_SPREADSHEET_NAME)
    # Attendance sheet
    try:
        ws_data = sh.worksheet(GSHEETS_SHEET_TITLE)
    except Exception:
        ws_data = sh.add_worksheet(title=GSHEETS_SHEET_TITLE, rows=1000, cols=8)
        ws_data.append_row(["Date","SlotTime","User ID","Discord Name","Status","Snapshot Time","Note"])
    # Log sheet
    try:
        ws_log = sh.worksheet(GSHEETS_LOG_TITLE)
    except Exception:
        ws_log = sh.add_worksheet(title=GSHEETS_LOG_TITLE, rows=1000, cols=6)
        ws_log.append_row(["When","Guild","Channel","MessageID","Level","Message"])
    return ws_data, ws_log

def _ws_data_or_none():
    out = _gs_open_or_none()
    if not out:
        return None
    ws_data, _ = out
    return ws_data

def _sheet_format_header(ws, row: int, finalized: bool):
    # grün (interim) / rot (final) + fett
    color = {"red": 0.2, "green": 0.9, "blue": 0.2} if not finalized else {"red": 0.9, "green": 0.2, "blue": 0.2}
    ws.format(f"A{row}:D{row}", {
        "backgroundColor": color,
        "textFormat": {"bold": True}
    })

def _sheet_shift_indices(from_row: int, delta: int):
    """Wenn über bestehenden Blöcken Zeilen eingefügt werden, verschieben wir deren Startzeilen im Store."""
    blocks = attendance_store.setdefault("sheet_blocks", {})
    for info in blocks.values():
        try:
            if int(info.get("start", 10)) >= from_row:
                info["start"] = int(info["start"]) + delta
        except Exception:
            pass

def _sheet_read_block(ws, start: int, height: int) -> tuple[dict[str, str], list[str]]:
    """
    liest A..D des Blocks und liefert:
      - id->note Mapping (User-ID -> Note)
      - bestehende User-ID-Reihenfolge (Liste von Strings)
    """
    id_to_note = {}
    order = []
    if height <= 1:
        return id_to_note, order
    try:
        values = ws.get(f"A{start}:D{start + height - 1}")
    except Exception:
        return id_to_note, order
    # Zeile 0 = Header
    for row in values[1:]:
        uid = (row[1] if len(row) > 1 else "").strip()
        note = (row[3] if len(row) > 3 else "").strip()
        if uid:
            id_to_note[uid] = note
            order.append(uid)
    return id_to_note, order

def _att_sheets_upsert_block(
    session_key: str,
    date_label: str,             # 'YYYY-MM-DD'
    slot_time_label: str,        # 'HH:MM' (CET) oder ''
    user_rows: list[tuple[str, int, str]],  # [(display_name, user_id, status)]
    finalized: bool
) -> None:
    """
    Pro Game genau EIN Header:
      A: Datum (YYYY-MM-DD)
      B: Uhrzeit (HH:MM CET)
    Darunter User-Zeilen:
      A: Name, B: User-ID, C: Status, D: Note (frei)

    Inkrementelles Upsert:
      - existierende User im Block werden aktualisiert
      - neue User werden am Blockende hinzugefügt
      - keine Duplikate
      - User-Zeilen farbig nach Status
    """
    import re

    out = _gs_open_or_none()
    if not out:
        return
    ws_data, _ = out

    START_ROW = 10
    DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    TIME_RX = re.compile(r"^\d{2}:\d{2}$")

    # ---------- Format-Helper ----------
    def _format_header(row: int, is_final: bool):
        color = {"red": 0.9, "green": 0.2, "blue": 0.2} if is_final else {"red": 0.0, "green": 0.8, "blue": 0.0}
        try:
            ws_data.format(f"A{row}:E{row}", {
                "backgroundColor": color,
                "textFormat": {"bold": True}
            })
        except Exception:
            pass

    def _format_users_plain(r1: int, r2: int):
        if r2 >= r1:
            try:
                ws_data.format(f"A{r1}:E{r2}", {
                    "backgroundColor": {"red": 1, "green": 1, "blue": 1},
                    "textFormat": {"bold": False}
                })
            except Exception:
                pass

    def _format_row_by_status(row: int, status: str | None):
        s = (status or "").upper()
        if s in ("C", "R", "C+R"):
            col = {"red": 1.0, "green": 1.0, "blue": 0.05}   # gelb
        elif s == "X":
            col = {"red": 1.0, "green": 0.30, "blue": 0.05}   # orange
        elif s == "NR":
            col = {"red": 0.7, "green": 0.01, "blue": 0.01}  # dunkles rot
        else:
            col = {"red": 1, "green": 1, "blue": 1}           # fallback weiß
        try:
            ws_data.format(f"C{row}:C{row}", {
                "backgroundColor": col,
                "textFormat": {"bold": False}
            })
        except Exception:
            pass

    # ---------- 0) Eingabe deduplizieren ----------
    dedup: dict[int, tuple[str, int, str]] = {}
    for dn, uid, st in user_rows:
        dedup[int(uid)] = (dn, int(uid), st)
    new_users = list(dedup.values())
    new_users.sort(key=lambda x: x[0].casefold())

    # ---------- 1) Header finden/erstellen ----------
    colA = ws_data.col_values(1)  # Datum
    colB = ws_data.col_values(2)  # Uhrzeit
    nrows = max(len(colA), len(colB))

    header_row = None
    for i in range(START_ROW, nrows + 1):
        a = (colA[i - 1] if i - 1 < len(colA) else "").strip()
        b = (colB[i - 1] if i - 1 < len(colB) else "").strip()
        if a == date_label and b == (slot_time_label or ""):
            header_row = i
            break

    if header_row is None:
        ws_data.insert_row(["", "", "", ""], index=START_ROW)
        header_row = START_ROW
        ws_data.update(f"A{header_row}:B{header_row}", [[date_label, slot_time_label or ""]])
    else:
        if (ws_data.cell(header_row, 2).value or "") != (slot_time_label or ""):
            ws_data.update_cell(header_row, 2, (slot_time_label or ""))

    # ---------- 2) Block-Grenzen bestimmen ----------
    colA = ws_data.col_values(1)
    colB = ws_data.col_values(2)
    nrows = max(len(colA), len(colB))

    block_start = header_row + 1
    block_end = block_start - 1
    for i in range(block_start, nrows + 1):
        a = (colA[i - 1] if i - 1 < len(colA) else "").strip()
        b = (colB[i - 1] if i - 1 < len(colB) else "").strip()
        if DATE_RX.match(a) and TIME_RX.match(b):
            break  # nächster Header
        # belegte Zeile?
        if a or b or any((ws_data.cell(i, c).value or "").strip() for c in (3, 4, 5)):
            block_end = i

    # ---------- 3) Vorhandene User im Block (ID->Zeile) ----------
    existing_by_id: dict[int, int] = {}
    if block_end >= block_start:
        existing_ids = ws_data.get(f"B{block_start}:B{block_end}")  # Liste von [value]
        for idx, cell in enumerate(existing_ids or [], start=block_start):
            try:
                raw = (cell[0] if isinstance(cell, list) and cell else "").strip()
                if raw:
                    uid = int(raw)
                    existing_by_id[uid] = idx
            except Exception:
                continue

    # ---------- 4) Updates & Adds vorbereiten ----------
    updates: list[tuple[int, list[str]]] = []  # (row_idx, [name, id, status])
    adds: list[list[str]] = []                # [[name, id, status, ""]]

    for dn, uid, st in new_users:
        row_idx = existing_by_id.get(uid)
        if row_idx:
            updates.append((row_idx, [dn, str(uid), st]))
        else:
            adds.append([dn, str(uid), st, ""])

    # ---------- 5) Neue User am Blockende einfügen ----------
    if adds:
        insert_at = (block_end + 1) if block_end >= block_start else block_start
        ws_data.insert_rows([["", "", "", ""] for _ in range(len(adds))], row=insert_at)
        ws_data.update(f"A{insert_at}:D{insert_at + len(adds) - 1}", adds, value_input_option="RAW")
        # zuerst neutral entformatieren …
        _format_users_plain(insert_at, insert_at + len(adds) - 1)
        # … dann je Zeile nach Status einfärben
        for offset, row_vals in enumerate(adds):
            st = row_vals[2]
            _format_row_by_status(insert_at + offset, st)
        block_end = insert_at + len(adds) - 1

    # ---------- 6) Bestehende User aktualisieren + einfärben ----------
    for row_idx, vals in updates:
        try:
            ws_data.update(
                range_name=f"A{row_idx}:C{row_idx}",
                values=[vals],
                value_input_option="RAW",
            )
        except Exception:
            pass
        _format_row_by_status(row_idx, vals[2])

    # ---------- 7) Header zuletzt formatieren ----------
    _format_header(header_row, bool(finalized))

def _att_sheets_mark_finalized(session_key: str):
    ws = _ws_data_or_none()
    if not ws:
        return
    blk = attendance_store.setdefault("sheet_blocks", {}).get(session_key)
    if not blk:
        return
    _sheet_format_header(ws, int(blk["start"]), finalized=True)
    blk["finalized"] = True
    _attendance_save_store()

def _att_sheets_append_rows(snapshot: dict, rows: list[dict], note: str = "") -> bool:
    out = _gs_open_or_none()
    if not out:
        return False
    ws_data, _ = out
    to_append = []
    for r in rows:
        to_append.append([
            snapshot.get("date") or "",
            snapshot.get("slot_time") or "",
            str(r["user_id"]),
            r["display_name"],
            r["status"],
            snapshot.get("snapshot_time") or "",
            note,  # <- hier landet INTERIM / FINAL
        ])
    if to_append:
        ws_data.append_rows(to_append, value_input_option="RAW")
    return True

def _att_sheets_append_raw(rows: list[list[str]]) -> bool:
    """
    Hängt rohe Zeilen (bereits im AT-Format) an die AT-Tabelle an.
    Erwartete Reihenfolge je Zeile:
    [Date, SlotTime, User ID, Discord Name, Status, Snapshot Time, Note]
    """
    out = _gs_open_or_none()
    if not out or not rows:
        return False
    ws_data, _ = out
    ws_data.append_rows(rows, value_input_option="RAW")
    return True

def _att_sheets_log(guild: discord.Guild, channel: discord.abc.GuildChannel, message_id: int, level: str, text: str):
    out = _gs_open_or_none()
    if not out:
        return
    _, ws_log = out
    ws_log.append_row([
        datetime.now(timezone.utc).isoformat(),
        f"{guild.name} ({guild.id})",
        f"{getattr(channel, 'name', '?')} ({getattr(channel, 'id', '?')})",
        str(message_id),
        level,
        text,
    ])

def _roster_now(guild: discord.Guild, roles: dict[str, Optional[int]], blacklist: set[int]) -> set[int]:
    caster_id = roles.get("Caster"); ref_id = roles.get("Referee")
    ids = set()
    for m in guild.members:
        if m.bot:
            continue
        has_c = bool(caster_id and discord.utils.get(m.roles, id=caster_id))
        has_r = bool(ref_id and discord.utils.get(m.roles, id=ref_id))
        if has_c or has_r:
            if m.id not in blacklist:
                ids.add(m.id)
    return ids

def _finalize_at(anchor_date_ymd: str, slot_dt: Optional[datetime]) -> datetime:
    """Return finalize time as aware UTC datetime."""
    if slot_dt:
        return slot_dt.astimezone(timezone.utc)
    y, mo, d = map(int, anchor_date_ymd.split("-"))
    dt_berlin = datetime(y, mo, d, 23, 59, 59, tzinfo=ATTENDANCE_TZ)
    return dt_berlin.astimezone(timezone.utc)

def _att_slot_label_for_date(date_key: str) -> str:
    """
    Baut das Uhrzeiten-Label (z.B. '08:00, 20:00') für die Datumszeile.
    Nimmt alle Sessions mit anchor_date == date_key, liest slot_time (ISO) und
    formatiert in Europe/Berlin als HH:MM. Sortiert aufsteigend.
    """
    try:
        sess = attendance_store.get("sessions", {})
        times = []
        for s in sess.values():
            if s.get("anchor_date") != date_key:
                continue
            iso = s.get("slot_time")
            if not iso:
                continue
            try:
                dt = datetime.fromisoformat(iso)
                # dt ist bereits tz-aware (Europe/Berlin) – zur Sicherheit in ATTENDANCE_TZ
                dt_local = dt.astimezone(ATTENDANCE_TZ)
                times.append(dt_local.strftime("%H:%M"))
            except Exception:
                continue
        times = sorted(set(times))
        return ", ".join(times)
    except Exception:
        return ""


STATE_FILE = Path(__file__).with_name("state.json")
TEAM_ROLES_FILE = Path(__file__).with_name("team_roles.json")
TEAM_MGMT_CHANNEL_ID = 1409588957200515180
SWAP_CONFIRM_TTL = 24 * 60 * 60
CAPTAIN_ROLE_NAME = "Captain"
MANAGER_ROLE_NAME = "Manager"
ROSTER_EXCLUDE_NAMES = {"Coach", "Manager"}
MAX_ACTIVE_PLAYERS = 10
RESTRICT_ROLE_NAME = "X"
EVENT_SCAN_INTERVAL_SEC = 60
TEAM_SCAN_INTERVAL_SEC = 30
REGIONS = {"EU", "NAE", "NAW", "RU", "SA", "OCE"}
PLATFORMS = {"PC", "PS", "XBOX", "SWITCH"}
_ADD_SPLIT_RE = re.compile(r"\s*-\s*")                
_DBD_ID_RE   = re.compile(r"^[^\s#]{1,32}#[A-Za-z0-9]{4}$")
ALLOWED_KILLER_KEYS = {
    normalize_key(k): k for k in set(killer_pool) | set(killer_map_lookup.keys())
}

def _save_player_profile(guild: discord.Guild, member: discord.Member, team_role: discord.Role,
                         *, platform: str, region: str, dbdid: str):
    store = _load_team_roles_store()
    gkey = str(guild.id)
    gobj = store.setdefault("guilds", {}).setdefault(gkey, {})
    profiles = gobj.setdefault("profiles", {})
    profiles[str(member.id)] = {
        "team_role_id": int(team_role.id),
        "platform": platform,
        "region": region,
        "dbd_id": dbdid,
        "discord_name": member.display_name,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    _save_team_roles_store(store)

def _classify_add_token(tok: str) -> tuple[str, str] | None:
    """Gibt ('platform'|'region'|'dbdid', wert) oder None zurück."""
    t = tok.strip().strip("<>").strip()   # <...> erlauben
    up = t.upper()
    if up in PLATFORMS:
        return ("platform", up)
    if up in REGIONS:
        return ("region", up)
    if _DBD_ID_RE.match(t):
        return ("dbdid", t)
    return None

def parse_add_tail(tail: str) -> tuple[str|None,str|None,str|None,str|None]:
    """
    tail: alles hinter der @Mention, getrennt durch ' - '
    return: (platform, region, dbdid, error)
    """
    platform = region = dbdid = None
    if not tail:
        return None, None, None, "Missing data. Format: `!add @User - <Platform> - <Region> - <DBD_ID>`"

    parts = [p for p in _ADD_SPLIT_RE.split(tail) if p.strip()]
    for p in parts:
        kind = _classify_add_token(p)
        if not kind:
            return None, None, None, f"Unrecognized part: `{p}`"
        k, v = kind
        if k == "platform": platform = v
        elif k == "region": region = v
        elif k == "dbdid": dbdid = v

    missing = [lbl for lbl, val in [("Platform", platform), ("Region", region), ("DBD ID", dbdid)] if not val]
    if missing:
        return None, None, None, f"Missing: {', '.join(missing)}. Use e.g. `PC - EU - Name#1a2b`"
    return platform, region, dbdid, None

def _member_team_roles(member: discord.Member) -> list[discord.Role]:
    team_ids = _team_role_ids_from_store(member.guild.id)
    return [r for r in member.roles if r.id in team_ids]

async def _temp_reply(ctx, content: str, *, delay: int = 10):
    m = await ctx.send(content)
    asyncio.create_task(_delete_messages_later(ctx.message, m, delay=delay))
    return m

def _is_exempt_from_roster(m: discord.Member) -> bool:
    """True, wenn der Member NICHT als aktiver Spieler zählt (Coach/Manager)."""
    return any(r.name in ROSTER_EXCLUDE_NAMES for r in m.roles)

def _active_players_in_team(guild: discord.Guild, team_role: discord.Role) -> list[discord.Member]:
    """Alle aktiven Spieler (ohne Coach/Manager) mit genau dieser Teamrolle."""
    return [m for m in guild.members if team_role in m.roles and not _is_exempt_from_roster(m)]

async def _get_text_channel_in_guild(guild: discord.Guild, cid: int) -> discord.TextChannel | None:
    ch = bot.get_channel(cid) or guild.get_channel(cid)
    if ch is None:
        try:
            ch = await bot.fetch_channel(cid)
        except (discord.InvalidData, discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    if not isinstance(ch, discord.TextChannel):
        return None
    return ch if ch.guild.id == guild.id else None

async def _delete_messages_later(*msgs: discord.Message, delay: int = 10):
    await asyncio.sleep(delay)
    for m in msgs:
        try:
            await m.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

async def _remove_role_later(guild_id: int, member_id: int, role_id: int, delay_seconds: int):
    await asyncio.sleep(max(1, delay_seconds))
    try:
        guild = bot.get_guild(guild_id) or await bot.fetch_guild(guild_id)
        if not guild:
            return
        member = guild.get_member(member_id) or await guild.fetch_member(member_id)
        role = guild.get_role(role_id)
        if member and role and role in member.roles:
            await member.remove_roles(role, reason="Auto-unrestrict after match window")
    except Exception:
        pass

async def _maybe_apply_killer_restriction(member: discord.Member, to_role: discord.Role) -> str | None:
    """Gibt Hinweis-Text zurück, falls die X-Rolle vergeben wurde."""
    # Nächsten Match-Start dieses Teams aus dem Index holen
    next_start = _match_index.get(member.guild.id, {}).get(to_role.id)
    if not next_start:
        return None

    now = datetime.now(timezone.utc)
    delta = next_start - now
    if timedelta(0) <= delta <= timedelta(hours=2):
        restrict = discord.utils.get(member.guild.roles, name=RESTRICT_ROLE_NAME)
        if not restrict:
            return f"Restrict role '{RESTRICT_ROLE_NAME}' not found."
        if restrict not in member.roles:
            try:
                await member.add_roles(restrict, reason="Joined team <2h before next match")
            except discord.Forbidden:
                return "Missing permission to add restrict role."
        # Auto-Removal: z.B. 4h nach Start
        remove_after = int(delta.total_seconds()) + 4 * 3600
        asyncio.create_task(_remove_role_later(member.guild.id, member.id, restrict.id, remove_after))
        return f"Applied {restrict.mention} (next match starts <t:{int(next_start.timestamp())}:R>)."
    return None


def _load_team_roles_store() -> dict:
    if TEAM_ROLES_FILE.exists():
        try:
            return json.loads(TEAM_ROLES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _team_role_ids_from_store(guild_id: int) -> set[int]:
    store = _load_team_roles_store()
    teams = store.get("guilds", {}).get(str(guild_id), {}).get("teams", [])
    out = set()
    for t in teams:
        try:
            out.add(int(t["id"]))
        except Exception:
            continue
    return out

def _save_team_roles_store(store: dict):
    TEAM_ROLES_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")

def _find_team_anchors(guild: discord.Guild) -> tuple[discord.Role | None, discord.Role | None]:
    start = discord.utils.get(guild.roles, name="---Team Names Start---")
    end   = discord.utils.get(guild.roles, name="---Team Names End---")
    return start, end

def _scan_team_roles_between(guild: discord.Guild, start: discord.Role, end: discord.Role) -> list[discord.Role]:
    lo, hi = sorted((start.position, end.position))
    return [r for r in guild.roles if lo < r.position < hi]

async def _team_roles_autoscan_loop():
    placeholder = re.compile(r"team\s*\d+$", re.IGNORECASE)
    while True:
        try:
            store = _load_team_roles_store()
            store.setdefault("guilds", {})
            now_iso = datetime.utcnow().isoformat() + "Z"

            for guild in bot.guilds:
                start, end = _find_team_anchors(guild)
                if not start or not end:
                    continue

                roles_between = _scan_team_roles_between(guild, start, end)
                visible = [r for r in roles_between if not placeholder.fullmatch(r.name)]
                visible_sorted = sorted(visible, key=lambda r: r.name.casefold())
                teams_payload = []
                for r in visible_sorted:
                    # Mitglieder mit dieser Team-Rolle
                    members = [m for m in guild.members if r in m.roles]
                
                    # Id-Sets für schnelle Checks
                    captain_ids = {m.id for m in members if any(x.name == CAPTAIN_ROLE_NAME for x in m.roles)}
                    manager_ids = {m.id for m in members if any(x.name == MANAGER_ROLE_NAME for x in m.roles)}
                
                    # Spieler = Mitglieder ohne ausgeschlossene Rollen
                    def _is_excluded(mem: discord.Member) -> bool:
                        return any(x.name in ROSTER_EXCLUDE_NAMES for x in mem.roles)
                
                    players = [m for m in members if not _is_excluded(m)]
                
                    # sauber sortieren
                    _by_name = lambda m: m.display_name.casefold()
                    capt_sorted = sorted((m for m in members if m.id in captain_ids), key=_by_name)
                    mgrs_sorted = sorted((m for m in members if m.id in manager_ids), key=_by_name)
                    players_sorted = sorted(players, key=_by_name)
                
                    teams_payload.append({
                        "id": r.id,
                        "name": r.name,
                        "position": r.position,
                        "counts": {
                            "members": len(members),
                            "players": len(players),
                        },
                        # Für den Command reichen IDs; Namen holen wir zur Anzeige live
                        "captain_ids": list(captain_ids),
                        "manager_ids": list(manager_ids),
                        "member_ids": [m.id for m in members],
                        "player_ids": [m.id for m in players_sorted],
                    })
                gid = str(guild.id)
                existing = store["guilds"].get(gid, {})
                profiles = existing.get("profiles", {})
                
                store["guilds"][gid] = {
                    "updated_at": now_iso,
                    "anchors": {"start": start.id, "end": end.id},
                    "teams": teams_payload,
                    "profiles": profiles,
                }

            _save_team_roles_store(store)
        except Exception as e:
            print(f"[teams autoscan] error: {e}")
        await asyncio.sleep(TEAM_SCAN_INTERVAL_SEC)

_vs_re = re.compile(r"^\s*(.+?)\s+vs\.?\s+(.+?)\s*$", re.IGNORECASE)

def _parse_vs_title(name: str) -> tuple[str, str] | None:
    m = _vs_re.match(name or "")
    if not m:
        return None
    a = m.group(1).strip()
    b = m.group(2).strip()
    return (a, b)

def _find_team_role_by_name_from_store(guild: discord.Guild, team_name: str) -> discord.Role | None:
    """Exakte Namenssuche über den JSON-Snapshot; fallback: reguläre Rollen-Suche."""
    store = _load_team_roles_store()
    data = store.get("guilds", {}).get(str(guild.id), {})
    wanted = (team_name or "").casefold()
    for t in data.get("teams", []):
        if str(t.get("name","")).casefold() == wanted:
            r = guild.get_role(int(t["id"]))
            if r:
                return r
    # Fallback (nicht so verlässlich, aber praktisch)
    return discord.utils.find(lambda r: r.name.casefold() == wanted, guild.roles)

async def _events_autoscan_loop():
    """Baut alle ~2min einen Index: Teamrolle -> nächster Match-Start (UTC)."""
    global _match_index
    while True:
        try:
            now = datetime.now(timezone.utc)
            for guild in bot.guilds:
                try:
                    events = await guild.fetch_scheduled_events()
                except Exception:
                    continue

                per_team: dict[int, datetime] = {}
                for ev in events:
                    # Wir berücksichtigen nur geplante, zukünftige Events mit 'TeamA vs TeamB' im Titel
                    if getattr(ev, "status", None) != discord.GuildScheduledEventStatus.scheduled:
                        continue
                    if not ev.start_time:
                        continue
                    start_utc = ev.start_time.astimezone(timezone.utc)
                    if start_utc <= now:
                        continue

                    vs = _parse_vs_title(ev.name or "")
                    if not vs:
                        continue

                    a_name, b_name = vs
                    ra = _find_team_role_by_name_from_store(guild, a_name)
                    rb = _find_team_role_by_name_from_store(guild, b_name)

                    for role in (ra, rb):
                        if not role:
                            continue
                        old = per_team.get(role.id)
                        if old is None or start_utc < old:
                            per_team[role.id] = start_utc

                _match_index[guild.id] = per_team
        except Exception as e:
            print(f"[events autoscan] error: {e}")
        await asyncio.sleep(EVENT_SCAN_INTERVAL_SEC)

def has_any_role(allowed_roles):

    async def predicate(ctx):
        user_roles = [role.name for role in ctx.author.roles]
        return any(role in user_roles for role in allowed_roles)

    return commands.check(predicate)

def init_channel(channel_id):
    if channel_id not in action_log:
        action_log[channel_id] = []
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
    mode = tb_mode.get(channel_id)

    # Wenn TB bereits gesetzt oder automatisch entschieden ist:
    if mode in ("TB", "resolved"):
        return None  # kein "Next"-Text mehr anzeigen

    if mode == "noTB":
        remaining = [
            k for k in killer_pool
            if all(k != b[0] for b in bans[channel_id])
            and all(k != p[0] for p in picks[channel_id])
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
        # Hinweis: Buttons im Board benutzen, nicht mehr !tb/!notb
        return "Format completed. Choose a Tiebreaker (Set TB) or choose No TB to continue banning."

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

def _collect_channel_ids():
    # Union aller bekannten Channel-IDs
    sets = [bans, picks, turns, formats, tb_mode, actions_done, format_type,
            last_action_team, ban_streak, team_names, coinflip_winner, coinflip_used, tiebreaker_picked]
    ids = set()
    for d in sets:
        ids.update(d.keys())
    return ids

def get_full_state():
    """Python-State -> JSON-serialisierbares Dict."""
    state = {}
    for cid in _collect_channel_ids():
        # Sicherstellen, dass Struktur existiert
        init_channel(cid)
        state[str(cid)] = {
            "action_log": action_log[cid],
            "bans": bans[cid],                        
            "picks": picks[cid],                      
            "turns": turns[cid],                       
            "formats": formats[cid],                   
            "tb_mode": tb_mode[cid],                   
            "actions_done": actions_done[cid],         
            "format_type": format_type[cid],           
            "last_action_team": last_action_team[cid], 
            "ban_streak": ban_streak[cid],             
            "team_names": team_names[cid],             
            "coinflip_winner": coinflip_winner[cid],   
            "coinflip_used": coinflip_used[cid],       
            "tiebreaker_picked": tiebreaker_picked[cid]
        }
        state["boards"] = {str(cid): mid for cid, mid in board_message_id.items()}
    return state

def apply_full_state(data: dict):
    """JSON-Dict -> Python-State (überschreibt in-memory)."""
    # Erst alles leeren
    bans.clear(); picks.clear(); turns.clear(); formats.clear(); tb_mode.clear()
    actions_done.clear(); format_type.clear(); last_action_team.clear(); ban_streak.clear()
    team_names.clear(); coinflip_winner.clear(); coinflip_used.clear(); tiebreaker_picked.clear()
    action_log.clear()
    board_message_id.clear()
    for cid_str, mid in data.get("boards", {}).items():
        try:
            cid = int(cid_str)
            board_message_id[cid] = int(mid)
        except (ValueError, TypeError):
            continue

    for cid_str, s in data.items():
        try:
            cid = int(cid_str)
        except ValueError:
            continue
        init_channel(cid)
        action_log[cid] = list(s.get("action_log", []))
        bans[cid] = [tuple(x) for x in s.get("bans", [])]
        picks[cid] = [tuple(x) for x in s.get("picks", [])]
        turns[cid] = s.get("turns", "A")
        formats[cid] = list(s.get("formats", []))
        tb_mode[cid] = s.get("tb_mode", "none")
        actions_done[cid] = int(s.get("actions_done", 0))
        format_type[cid] = s.get("format_type", "bo3")
        last_action_team[cid] = s.get("last_action_team", "A")
        ban_streak[cid] = int(s.get("ban_streak", 0))
        team_names[cid] = dict(s.get("team_names", {"A": "Team A", "B": "Team B"}))
        coinflip_winner[cid] = s.get("coinflip_winner", None)
        coinflip_used[cid] = bool(s.get("coinflip_used", False))
        tiebreaker_picked[cid] = bool(s.get("tiebreaker_picked", False))
        

def save_state():
    """Atomisches Speichern auf Disk."""
    tmp = STATE_FILE.with_suffix(".json.tmp")
    data = get_full_state()
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)

def load_state_if_exists():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            apply_full_state(data)
            print(f"[state] Loaded state from {STATE_FILE}")
        except Exception as e:
            print(f"[state] Failed to load state: {e}")

async def autosave_loop():
    # alle 30s persistieren (unkritisch, leichtgewichtig)
    while True:
        try:
            save_state()
        except Exception as e:
            print(f"[state] autosave failed: {e}")
        await asyncio.sleep(30)

def _truncate_for_embed(text: str, limit: int = 4096):
    import io
    if len(text) <= limit:
        return text, None
    head = text[:limit - 10].rstrip()
    leftover = text[len(head):]
    bio = io.BytesIO(leftover.encode("utf-8"))
    bio.name = "message_overflow.txt"
    return head + "\n… (gekürzt, kompletter Text als Anhang)", bio

def _first_image_attachment(msg: discord.Message):
    for a in msg.attachments:
        if (a.content_type and a.content_type.startswith("image/")) or a.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            return a
    return None

def _attachment_list(msg: discord.Message):
    return [f"[{a.filename}]({a.url})" for a in msg.attachments]

async def _get_forum_channel(guild: discord.Guild, killer: str) -> discord.ForumChannel | None:
    chan_id = KILLER_FORUM_OVERRIDES.get(killer, DEFAULT_FORUM_CHANNEL_ID)
    ch = guild.get_channel(chan_id) or await guild.fetch_channel(chan_id)
    return ch if isinstance(ch, discord.ForumChannel) else None

async def _find_thread_by_name(forum: discord.ForumChannel, title: str) -> discord.Thread | None:
    needle = normalize_key(title)

    def _match(th: discord.Thread) -> bool:
        nk = normalize_key(getattr(th, "name", "") or "")
        return nk == needle or nk.startswith(needle) or needle.startswith(nk)

    # ---------- 1) AKTIVE THREADS verlässlich laden ----------
    try:
        act = await forum.guild.fetch_active_threads()
        active_threads = getattr(act, "threads", act)
        active_threads = [th for th in active_threads if th.parent_id == forum.id]
    except Exception:
        active_threads = list(getattr(forum, "threads", []))  # Fallback (kann leer sein)

    for th in active_threads:
        if _match(th):
            return th

    # ---------- 2) ARCHIVE: Hilfs-Iteratoren, die mehrere APIs abdecken ----------
    async def _iter_public_archived():
        # Variante A: neue discord.py mit fetch_archived_threads
        if hasattr(forum, "fetch_archived_threads"):
            before = None
            while True:
                res = await forum.fetch_archived_threads(private=False, before=before, limit=100)
                threads = getattr(res, "threads", [])
                for th in threads:
                    yield th
                if not threads or not getattr(res, "has_more", False):
                    break
                before = threads[-1].last_message_id or threads[-1].id
            return

        # Variante B: Pycord/Disnake: archived_threads(limit=..., before=...)
        if hasattr(forum, "archived_threads"):
            before = None
            while True:
                try:
                    # bevorzugt mit Paging-Parametern
                    itr = forum.archived_threads(limit=100, before=before)
                except TypeError:
                    # ältere Signatur, keine kwargs
                    itr = forum.archived_threads()
                got_any = False
                async for th in itr:
                    got_any = True
                    yield th
                    before = th.last_message_id or th.id
                if not got_any:
                    break

    async def _iter_private_archived():
        # Variante A: neue discord.py
        if hasattr(forum, "fetch_archived_threads"):
            before = None
            while True:
                res = await forum.fetch_archived_threads(private=True, before=before, limit=100)
                threads = getattr(res, "threads", [])
                for th in threads:
                    yield th
                if not threads or not getattr(res, "has_more", False):
                    break
                before = threads[-1].last_message_id or threads[-1].id
            return

        # Variante B: Pycord/Disnake: private_archived_threads(...)
        if hasattr(forum, "private_archived_threads"):
            before = None
            while True:
                try:
                    itr = forum.private_archived_threads(limit=100, before=before)
                except TypeError:
                    itr = forum.private_archived_threads()
                got_any = False
                async for th in itr:
                    got_any = True
                    yield th
                    before = th.last_message_id or th.id
                if not got_any:
                    break

    # ---------- 2a) öffentliche Archive ----------
    async for th in _iter_public_archived():
        if _match(th):
            return th

    # ---------- 2b) private Archive (falls API/Permissions vorhanden) ----------
    try:
        async for th in _iter_private_archived():
            if _match(th):
                return th
    except (discord.Forbidden, AttributeError, TypeError):
        # kein Zugriff oder API existiert nicht → ignorieren
        pass

    return None

async def _get_second_message(thread: discord.Thread) -> discord.Message | None:
    msgs = []
    async for m in thread.history(limit=2, oldest_first=True):
        msgs.append(m)
    return msgs[1] if len(msgs) >= 2 else None

@bot.event
async def on_ready():
    load_state_if_exists()
    if not getattr(bot, "_team_autoscan_started", False):
        asyncio.create_task(_team_roles_autoscan_loop())
        bot._team_autoscan_started = True

    if not getattr(bot, "_events_autoscan_started", False):
        asyncio.create_task(_events_autoscan_loop())
        bot._events_autoscan_started = True

    for cid, _ in list(board_message_id.items()):
        ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
        if not isinstance(ch, discord.TextChannel):
            board_message_id.pop(cid, None)
            continue
        try:
            await _update_or_create_board(ch, force_existing=True)
        except discord.Forbidden:
            pass
        except discord.NotFound:
            await _update_or_create_board(ch, force_existing=True)    

    print(f"Bot is online: {bot.user}")
    await bot.change_presence(activity=discord.Game(name="made by Fluffy"))
    # Attendance-Store laden + Autoscan starten
    _attendance_load_store()
    if not getattr(bot, "_attendance_autoscan_started", False):
        asyncio.create_task(_attendance_autoscan_loop())
        bot._attendance_autoscan_started = True
    await _attendance_startup_catchup_once()

@bot.event
async def on_disconnect():
    try:
        save_state()
        print("[state] saved on disconnect")
    except Exception as e:
        print(f"[state] save on disconnect failed: {e}")

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
    save_state()

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
    save_state()

@bot.command()
@has_any_role(STAFF_ROLES)
async def reset(ctx):
    channel_id = ctx.channel.id
    init_channel(channel_id)
    action_log[channel_id] = []
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
    save_state()

    mid = board_message_id.get(channel_id)
    if mid:
        try:
            msg = await ctx.channel.fetch_message(mid)
            await msg.delete()
        except discord.NotFound:
            pass  # schon weg
        except discord.Forbidden:
            # Optional: kurze Info, falls der Bot seine eigene Nachricht nicht löschen darf (sollte selten sein)
            await ctx.send("I couldn't delete the existing board message (missing permissions).")
        finally:
            board_message_id.pop(channel_id, None)

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
    save_state()

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
    save_state()

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

    await ctx.send(f"Coinflip result: **{team_names[channel_id][coinflip_winner[channel_id]]}** won the toss. Use **!first** or **!second** to choose turn order.")
    save_state()

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
    save_state()

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
    save_state()

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
    save_state()

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
        f"Next action: **BAN** by {team_names[channel_id][turns[channel_id]]}.")
    save_state()

@bot.command()
@has_any_role(STAFF_ROLES)
async def fluffy(ctx):

    steam_id = "76561198159133215"
    dbd_name = "FluffyTailedHog#6a05"
    steam_profile_url = f"https://steamcommunity.com/profiles/{steam_id}"

    embed = discord.Embed(title="Fluffy's Streamer ID",
                          color=EMBED_COLOR,
                          description="Streamer for your matches, please add:")
    embed.add_field(name="Epic ID",
                    value=f"FluffyTailedHog",
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
    embed.add_field(name="!<killername>", value="Shows the balancing of the killer if they are in the pool", inline=False)

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
    embed.add_field(name="~!<killername>", value="Shows the balancing of the killer if they are in the pool", inline=False)
    embed.add_field(name="!bo3", value="Sets the pick & ban phase to Best of 3 format.", inline=False)
    embed.add_field(name="!bo5", value="Sets the pick & ban phase to Best of 5 format.", inline=False)
    embed.add_field(name="!coinflip <Team A> <Team B>", value="Randomly assigns teams A/B and determines coinflip winner.", inline=False)
    embed.add_field(name="!tb <Killer>", value="Picks a tiebreaker killer.", inline=False)
    embed.add_field(name="!notb", value="Activates no-TB mode (ban until one killer remains).", inline=False)
    embed.add_field(name="!random", value="Randomly selects a killer from the pool.", inline=False)
    embed.add_field(name="!reset", value="Resets the draft state for this channel.", inline=False)
    embed.add_field(name="!fluffy / !voum / !brian", value="Links the streamer's Platform and DBD ID.", inline=False)
    embed.add_field(name="", value="", inline=False)
    embed.add_field(name="", value="Note that all commands that start with '~' can be used by the teams too.", inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name="ppurge")
@has_any_role(STAFF_ROLES)
async def ppurge(ctx: commands.Context):
    """Purge ALL messages in this channel after a y/n confirmation."""
    prompt = await ctx.send("**Clear ALL messages in this channel?** Reply `y` to confirm or `n` to cancel. (auto-cancels in 20s)")

    def _chk(m: discord.Message) -> bool:
        return (
            m.author.id == ctx.author.id
            and m.channel.id == ctx.channel.id
            and m.content.lower() in {"y", "yes", "n", "no"}
        )

    try:
        reply: discord.Message = await bot.wait_for("message", check=_chk, timeout=20)
    except asyncio.TimeoutError:
        try:
            await prompt.edit(content="Cancelled (no response).")
            await prompt.delete(delay=5)
        except Exception:
            pass
        return

    if reply.content.lower() in {"n", "no"}:
        try:
            await prompt.edit(content="Cancelled.")
            await reply.delete()
            await prompt.delete(delay=5)
        except Exception:
            pass
        return

    # confirmed
    deleted_total = 0
    try:
        while True:
            batch = await ctx.channel.purge(limit=1000)
            deleted_total += len(batch)
            if len(batch) < 2:  # nothing (or only 1) left in range
                break
            await asyncio.sleep(0.3)
    except discord.Forbidden:
        await ctx.send("I don't have permission to manage messages here.", delete_after=8)
        return
    except discord.HTTPException:
        pass  # fall through and report what we got

    # try to remove the prompt/answer too (if still present)
    for m in (prompt, reply):
        try:
            await m.delete()
        except Exception:
            pass

    info = await ctx.send(
        f"Deleted **{deleted_total}** messages. "
        "(Discord only bulk-deletes messages younger than 14 days.)"
    )
    try:
        await info.delete(delay=5)
    except Exception:
        pass


@bot.command()
async def pov(ctx):
    embed = discord.Embed(
        title="Streaming Rules",
        description=
        ("While streaming a match, a minimum stream delay of **10 minutes** is required "
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
    seconds = 5 * 60
    label = human_label_from_seconds_en(seconds)
    asyncio.create_task(_run_timer_seconds(ctx, seconds, label))

@bot.command()
async def survivor(ctx):
    embed = discord.Embed(
        title="Survivor Setup",
        description=
        ("Please join streamer's lobby and provide full screenshots of your builds and crossplay settings.\n\n You have 5 minutes to do so."
         ),
        color=EMBED_COLOR)
    await ctx.send(embed=embed)
    seconds = 5 * 60
    label = human_label_from_seconds_en(seconds)
    asyncio.create_task(_run_timer_seconds(ctx, seconds, label))

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

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    content = message.content.strip()
    if content.startswith("!"):
        raw = content[1:].strip()
        killer_canonical = normalize_killer_name(raw)
        killer_key = normalize_key(killer_canonical)

        # Ist es ein gültiger Killer (nach Key)?
        if killer_key in ALLOWED_KILLER_KEYS:
            if message.guild is None:
                await message.channel.send("Dieser Befehl funktioniert nur auf einem Server.")
                return

            # 0) Sicherstellen, dass Forum-ID gesetzt ist
            if DEFAULT_FORUM_CHANNEL_ID == 123456789012345678:
                await message.channel.send("DEFAULT_FORUM_CHANNEL_ID ist nicht konfiguriert.")
                return

            forum = await _get_forum_channel(message.guild, killer_canonical)
            if not forum:
                await message.channel.send("Forum-Channel nicht gefunden oder keine Rechte.")
                return

            thread = await _find_thread_by_name(forum, killer_canonical)
            if not thread:
                await message.channel.send(f"There is no thread that includes **{killer_canonical}**.")
                return

            second = await _get_second_message(thread)
            if not second:
                await message.channel.send("No Messages in that Channel.")
                return

            text = second.clean_content or "*Kein Textinhalt*"
            desc, overflow = _truncate_for_embed(text)

            embed = discord.Embed(
                description=desc,
                color=EMBED_COLOR,
            )
            embed.set_author(name=second.author.display_name, icon_url=second.author.display_avatar.url)
            embed.add_field(name="", value=f"[Original]({second.jump_url})", inline=False)

            img = _first_image_attachment(second)
            if img:
                embed.set_image(url=img.url)

            atts = _attachment_list(second)
            if atts:
                embed.add_field(name="Anhänge", value="\n".join(atts), inline=False)

            files = []
            if overflow:
                files.append(discord.File(overflow, filename=overflow.name))

            await message.channel.send(embed=embed, files=files)
            return

    await bot.process_commands(message)

# =====================[ STATUS BOARD: GLOBALS ]=====================
from typing import Optional
import asyncio
import re
import discord

# per channel: the board message ID
board_message_id: dict[int, int] = {}
# per channel: a lock to avoid race conditions
_board_locks: dict[int, asyncio.Lock] = {}

# per guild: cache resolved emoji string
_emoji_cache: dict[tuple[int, str], str] = {}

def _lock_for_channel(cid: int) -> asyncio.Lock:
    if cid not in _board_locks:
        _board_locks[cid] = asyncio.Lock()
    return _board_locks[cid]

def _remaining_killers(channel_id: int) -> list[str]:
    return [
        k for k in killer_pool
        if all(k != b[0] for b in bans[channel_id])
        and all(k != p[0] for p in picks[channel_id])
    ]

def _next_action(channel_id: int) -> Optional[str]:
    """ 'ban' | 'pick' | None  (None = format finished or TB/noTB special-case) """
    if tb_mode.get(channel_id) == "noTB":
        # in noTB it is always BAN while >1 remain
        return "ban" if len(_remaining_killers(channel_id)) > 1 else None

    fmt = formats[channel_id]
    idx = actions_done[channel_id]
    if idx < len(fmt):
        return fmt[idx]
    return None  # format finished (TB or end)

def _emoji_str(guild: discord.Guild, name: str, *, fallback: str = "") -> str:
    """
    Resolve a custom emoji by name in this guild and return its mention string (<:name:id>).
    Falls back to the given fallback string if not found.
    """
    key = (guild.id, name)
    if key in _emoji_cache:
        return _emoji_cache[key]
    e = discord.utils.get(guild.emojis, name=name)
    s = str(e) if e else fallback
    _emoji_cache[key] = s
    return s

def _format_progress_text(channel_id: int, guild: discord.Guild) -> str:
    """
    Vertical list, one action per line.
    Completed actions are suffixed with the custom emoji r_check02.
    """
    fmt = formats[channel_id]
    done = actions_done[channel_id]
    if not fmt:
        return "(no format set)"
    check = _emoji_str(guild, "r_check02", fallback=":r_check02:")
    lines = []
    for i, a in enumerate(fmt):
        label = "BAN" if a == "ban" else "PICK"
        suffix = f" {check}" if i < done else ""
        lines.append(f"{label}{suffix}")
    return "\n".join(lines)

def _map_conflict_for_pick(channel_id: int, killer: str) -> Optional[str]:
    """None = ok, otherwise a human-readable conflict message."""
    picked_map = killer_map_lookup.get(killer)
    if not picked_map:
        return None
    used_maps = {
        killer_map_lookup.get(k, None)
        for k, team in picks[channel_id]
        if team != "Tiebreaker" and killer_map_lookup.get(k) is not None
    }
    picked_killers = {k for k, team in picks[channel_id] if team != "Tiebreaker"}

    for other_killer, other_map in killer_map_lookup.items():
        if other_killer != killer and other_map == picked_map and other_killer in picked_killers:
            return f"Map {picked_map} is already occupied by **{other_killer}**."

    if picked_map in used_maps:
        # redundant safety
        return f"Map {picked_map} is already used by another pick."
    return None

def _simulate_turn_after_n_actions(channel_id: int, start_team: str, total_actions: int) -> str:
    """Reconstruct whose turn it is after total_actions (mirrors switch_turn)."""
    t = start_team
    ban_stk = 0
    for i in range(total_actions):
        action = formats[channel_id][i] if i < len(formats[channel_id]) else None
        if tb_mode.get(channel_id) == "noTB":
            t = "B" if t == "A" else "A"
            continue
        if format_type[channel_id] == "bo5" and action == "ban":
            if ban_stk == 0:
                ban_stk = 1
            else:
                ban_stk = 0
                t = "B" if t == "A" else "A"
        else:
            t = "B" if t == "A" else "A"
            ban_stk = 0
    return t

async def _apply_ban(ctx, channel_id: int, killer: str) -> str:
    """Same rules as your !ban command; returns a user-facing message (or error)."""
    if not formats[channel_id] and tb_mode[channel_id] != "noTB":
        return "Please select a format first."
    if not coinflip_used[channel_id]:
        return "Please use **!coinflip <Team A> <Team B>** first."
    if turns[channel_id] not in ["A", "B"]:
        return "Please choose the starting team with **!first** or **!second**."

    if tb_mode[channel_id] != "noTB":
        if actions_done[channel_id] >= len(formats[channel_id]) or formats[channel_id][actions_done[channel_id]] != "ban":
            return "It's not time to BAN right now."
    if killer not in killer_pool:
        return f"{killer} is not a valid killer."
    if any(k == killer for k, _ in bans[channel_id]):
        return f"{killer} is already banned."
    if any(k == killer for k, _ in picks[channel_id]):
        return f"{killer} is already picked."

    team = turns[channel_id]
    bans[channel_id].append((killer, team))
    actions_done[channel_id] += 1
    action_log[channel_id].append(f"BAN — {killer} by {team_names[channel_id][turns[channel_id]]}")

    if tb_mode[channel_id] == "noTB":
        remaining = _remaining_killers(channel_id)
        if len(remaining) == 1:
            last_killer = remaining[0]
            picks[channel_id].append((last_killer, "Tiebreaker"))
            tb_mode[channel_id] = "resolved"
            action_log[channel_id].append(f"TB — {last_killer} auto-selected")
            await send_final_summary(ctx, channel_id)

    # turn switching like your switch_turn
    if tb_mode[channel_id] == "noTB":
        turns[channel_id] = "B" if team == "A" else "A"
    else:
        idx = actions_done[channel_id] - 1
        action = formats[channel_id][idx] if idx < len(formats[channel_id]) else None
        if format_type[channel_id] == "bo5" and action == "ban":
            if ban_streak[channel_id] == 0:
                ban_streak[channel_id] = 1
            else:
                ban_streak[channel_id] = 0
                turns[channel_id] = "B" if team == "A" else "A"
        else:
            turns[channel_id] = "B" if team == "A" else "A"
            ban_streak[channel_id] = 0

    save_state()
    return f"{killer} was banned by {team_names[channel_id][team]}."

async def _apply_pick(ctx, channel_id: int, killer: str) -> str:
    """Same rules as your !pick command; returns a message."""
    if not formats[channel_id]:
        return "Please select a format first."
    if not coinflip_used[channel_id]:
        return "Please use **!coinflip <Team A> <Team B>** first."
    if turns[channel_id] not in ["A", "B"]:
        return "Please choose the starting team with **!first** or **!second**."

    # map conflicts
    conflict = _map_conflict_for_pick(channel_id, killer)
    if conflict:
        return f"{killer} cannot be picked: {conflict}"

    if actions_done[channel_id] >= len(formats[channel_id]) or formats[channel_id][actions_done[channel_id]] != "pick":
        return "It's not time to PICK right now."
    if killer not in killer_pool:
        return f"{killer} is not a valid killer."
    if any(k == killer for k, _ in picks[channel_id]):
        return f"{killer} is already picked."
    if any(k == killer for k, _ in bans[channel_id]):
        return f"{killer} is banned."

    team = turns[channel_id]
    picks[channel_id].append((killer, team))
    actions_done[channel_id] += 1
    action_log[channel_id].append(f"PICK — {killer} by {team_names[channel_id][turns[channel_id]]}")

    # turn switching (same as above)
    if tb_mode[channel_id] == "noTB":
        turns[channel_id] = "B" if team == "A" else "A"
    else:
        idx = actions_done[channel_id] - 1
        action = formats[channel_id][idx] if idx < len(formats[channel_id]) else None
        if format_type[channel_id] == "bo5" and action == "ban":
            if ban_streak[channel_id] == 0:
                ban_streak[channel_id] = 1
            else:
                ban_streak[channel_id] = 0
                turns[channel_id] = "B" if team == "A" else "A"
        else:
            turns[channel_id] = "B" if team == "A" else "A"
            ban_streak[channel_id] = 0

    save_state()
    return f"{killer} was picked by {team_names[channel_id][team]}."

async def _apply_undo(ctx, channel_id: int) -> str:
    """Undo the last format action (BAN/PICK)."""
    if tb_mode.get(channel_id) == "noTB":
        if bans[channel_id]:
            bans[channel_id].pop()
            # toggle turn back
            turns[channel_id] = "B" if turns[channel_id] == "A" else "A"
            save_state()
            return "Last BAN in noTB has been undone."
        return "No action to undo (noTB)."

    if actions_done[channel_id] == 0:
        return "No action to undo."

    last_idx = actions_done[channel_id] - 1
    last_action = formats[channel_id][last_idx]
    if last_action == "ban":
        if not bans[channel_id]:
            return "Internal state inconsistent (no BAN)."
        bans[channel_id].pop()
    else:
        if not picks[channel_id]:
            return "Internal state inconsistent (no PICK)."
        picks[channel_id].pop()

    actions_done[channel_id] -= 1
    # best-effort recompute whose turn it is
    initial_turn = turns[channel_id]
    turns[channel_id] = _simulate_turn_after_n_actions(channel_id, initial_turn, actions_done[channel_id])

    save_state()
    return "Last action has been undone."

async def _apply_tb(ctx, channel_id: int, killer: str) -> str:
    if not formats[channel_id]:
        return "Please select a format first."
    if actions_done[channel_id] < len(formats[channel_id]):
        return "The pick & ban phase is not finished yet."
    if tb_mode[channel_id] != "none":
        return "Tiebreaker has already been decided."
    if killer not in killer_pool:
        return f"{killer} is not a valid killer."
    if any(k == killer for k, _ in bans[channel_id]) or any(k == killer for k, _ in picks[channel_id]):
        return f"{killer} has already been banned or picked."

    picks[channel_id].append((killer, "Tiebreaker"))
    tb_mode[channel_id] = "TB"
    if 'action_log' in globals():
        action_log[channel_id].append(f"TB — {killer}")
    
    save_state()
    return f"Tiebreaker picked: **{killer}**"

async def _apply_notb(ctx, channel_id: int) -> str:
    if not formats[channel_id]:
        return "Please select a format first."
    if actions_done[channel_id] < len(formats[channel_id]):
        return "The pick & ban phase is not finished yet."
    if tb_mode[channel_id] != "none":
        return "Tiebreaker already resolved."

    if turns[channel_id] not in ["A", "B"]:
        return "Please choose the starting team with **!first** or **!second** before continuing."

    tb_mode[channel_id] = "noTB"
    if 'action_log' in globals():
        action_log[channel_id].append("noTB — continue banning until one remains")
    save_state()
    return "noTB mode activated. Continue banning until only one killer remains."

def _build_board_embed(channel_id: int, guild: discord.Guild) -> discord.Embed:
    emb = discord.Embed(
        title="Match Draft Board",
        description=f"Format: **{format_type.get(channel_id, 'bo3')}**\n{_format_progress_text(channel_id, guild)}",
        color=EMBED_COLOR
    )
    bans_text = "\n".join([f"{k} ({team_names[channel_id].get(t, t)})" for k, t in bans[channel_id]]) or "—"
    picks_text = "\n".join([f"{k} ({team_names[channel_id].get(t, t)}) – {killer_map_lookup.get(k, '—')}" for k, t in picks[channel_id]]) or "—"

    emb.add_field(name="Bans", value=bans_text, inline=True)
    emb.add_field(name="Picks", value=picks_text, inline=True)

    tb_killer = next((k for k, t in picks[channel_id] if t == "Tiebreaker"), None)
    if tb_mode.get(channel_id) == "TB":
        emb.add_field(name="Tiebreaker", value=tb_killer or "—", inline=False)
    elif actions_done[channel_id] >= len(formats[channel_id]) and tb_mode.get(channel_id) == "none":
        emb.add_field(name="Tiebreaker", value="Not decided — use the buttons below.", inline=False)

    recent = "\n".join(action_log[channel_id][-3:]) or "—"
    emb.add_field(name="Recent", value=recent, inline=False)

    na = announce_next_action(channel_id)
    if na:
        # remove "Next action:" prefix if present
        na_clean = re.sub(r'^\s*Next action:\s*', '', na, flags=re.IGNORECASE)
        emb.add_field(name="Next", value=na_clean, inline=False)
    else:
        mode = tb_mode.get(channel_id)
        if mode == "noTB":
            if len(_remaining_killers(channel_id)) == 1:
                emb.add_field(name="Status", value="noTB finished – last killer auto-selected as TB.", inline=False)
            else:
                # explizit zeigen, wer als Nächstes dran ist
                emb.add_field(name="Status", value=f"noTB active — Next: BAN by {team_names[channel_id][turns[channel_id]]}.", inline=False)
        elif mode == "TB":
            emb.add_field(name="Status", value="TB chosen — draft complete.", inline=False)
        elif mode == "resolved":
            emb.add_field(name="Status", value="TB auto-selected — draft complete.", inline=False)
        else:
            emb.add_field(name="Status", value="Format finished.", inline=False)

    return emb

def _user_is_staff(member: discord.Member) -> bool:
    names = {r.name for r in member.roles}
    return any(s in names for s in STAFF_ROLES)

# =====================[ STATUS BOARD: VIEW ]=====================
class DraftBoardView(discord.ui.View):
    def __init__(self, channel_id: int, *, timeout: Optional[float] = 300):
        super().__init__(timeout=timeout)
        self.channel_id = channel_id

    async def _ensure_prereqs(self, interaction: discord.Interaction) -> bool:
        cid = self.channel_id
        if not formats[cid] and tb_mode[cid] != "noTB":
            await interaction.response.send_message("Please set **!bo3** or **!bo5** first.", ephemeral=True)
            return False
        if not coinflip_used[cid]:
            await interaction.response.send_message("Please use **!coinflip** first.", ephemeral=True)
            return False
        if turns[cid] not in ["A", "B"]:
            await interaction.response.send_message("Please run **!first** or **!second**.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="BAN", style=discord.ButtonStyle.danger)
    async def btn_ban(self, interaction: discord.Interaction, button: discord.ui.Button):
        cid = self.channel_id
        if not await self._ensure_prereqs(interaction):
            return
        if _next_action(cid) != "ban" and tb_mode.get(cid) != "noTB":
            await interaction.response.send_message("No BAN scheduled right now.", ephemeral=True)
            return

        opts = _remaining_killers(cid)
        if not opts:
            await interaction.response.send_message("No killers remaining.", ephemeral=True)
            return

        # Build select (<=25 options)
        class BanSelect(discord.ui.Select):
            def __init__(self, channel_id: int):
                options = [discord.SelectOption(label=k, value=k) for k in opts[:25]]
                super().__init__(placeholder="Choose a killer to ban…", min_values=1, max_values=1, options=options)
                self.channel_id = channel_id

            async def callback(self, inter: discord.Interaction):
                async with _lock_for_channel(self.channel_id):
                    killer = self.values[0]
                    msg = await _apply_ban(inter, self.channel_id, killer)
                    # update board
                    await _update_or_create_board(inter.channel, force_existing=True)
                    await inter.response.edit_message(content=msg, view=None)

        v = discord.ui.View(timeout=60)
        v.add_item(BanSelect(cid))
        await interaction.response.send_message("Select BAN:", view=v, ephemeral=True)

    @discord.ui.button(label="PICK", style=discord.ButtonStyle.success)
    async def btn_pick(self, interaction: discord.Interaction, button: discord.ui.Button):
        cid = self.channel_id
        if not await self._ensure_prereqs(interaction):
            return
        if _next_action(cid) != "pick":
            await interaction.response.send_message("No PICK scheduled right now.", ephemeral=True)
            return

        opts = _remaining_killers(cid)
        if not opts:
            await interaction.response.send_message("No killers remaining.", ephemeral=True)
            return

        class PickSelect(discord.ui.Select):
            def __init__(self, channel_id: int):
                options = [discord.SelectOption(label=k, value=k) for k in opts[:25]]
                super().__init__(placeholder="Choose a killer to pick…", min_values=1, max_values=1, options=options)
                self.channel_id = channel_id

            async def callback(self, inter: discord.Interaction):
                killer = self.values[0]
                conflict = _map_conflict_for_pick(self.channel_id, killer)
                if conflict:
                    await inter.response.send_message(f"❌ {conflict}", ephemeral=True)
                    return
                async with _lock_for_channel(self.channel_id):
                    msg = await _apply_pick(inter, self.channel_id, killer)
                    await _update_or_create_board(inter.channel, force_existing=True)
                    await inter.response.edit_message(content=msg, view=None)

        v = discord.ui.View(timeout=60)
        v.add_item(PickSelect(cid))
        await interaction.response.send_message("Select PICK:", view=v, ephemeral=True)

    @discord.ui.button(label="Set TB", style=discord.ButtonStyle.primary)
    async def btn_tb(self, interaction: discord.Interaction, button: discord.ui.Button):
        cid = self.channel_id
        if not await self._ensure_prereqs(interaction):
            return
        if actions_done[cid] < len(formats[cid]):
            await interaction.response.send_message("The pick & ban phase is not finished yet.", ephemeral=True)
            return
        if tb_mode.get(cid) != "none":
            await interaction.response.send_message("Tiebreaker already resolved.", ephemeral=True)
            return
    
        opts = _remaining_killers(cid)
        if not opts:
            await interaction.response.send_message("No killers remaining.", ephemeral=True)
            return

        class TBSelect(discord.ui.Select):
            def __init__(self, channel_id: int):
                options = [discord.SelectOption(label=k, value=k) for k in opts[:25]]
                super().__init__(placeholder="Choose a Tiebreaker…", min_values=1, max_values=1, options=options)
                self.channel_id = channel_id
    
            async def callback(self, inter: discord.Interaction):
                killer = self.values[0]
                async with _lock_for_channel(self.channel_id):
                    msg = await _apply_tb(inter, self.channel_id, killer)
                    await _update_or_create_board(inter.channel, force_existing=True)
                    await inter.response.edit_message(content=f"{msg} ✅", view=None)
    
        v = discord.ui.View(timeout=60)
        v.add_item(TBSelect(cid))
        await interaction.response.send_message("Select Tiebreaker:", view=v, ephemeral=True)

    @discord.ui.button(label="No TB", style=discord.ButtonStyle.secondary)
    async def btn_notb(self, interaction: discord.Interaction, button: discord.ui.Button):
        cid = self.channel_id
        if not await self._ensure_prereqs(interaction):
            return
        if actions_done[cid] < len(formats[cid]):
            await interaction.response.send_message("The pick & ban phase is not finished yet.", ephemeral=True)
            return
        if tb_mode.get(cid) != "none":
            await interaction.response.send_message("Tiebreaker already resolved.", ephemeral=True)
            return
    
        async with _lock_for_channel(cid):
            msg = await _apply_notb(interaction, cid)
            await _update_or_create_board(interaction.channel, force_existing=True)
            await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="Undo", style=discord.ButtonStyle.secondary)
    async def btn_undo(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _user_is_staff(interaction.user):
            await interaction.response.send_message("Only staff can undo.", ephemeral=True)
            return
        async with _lock_for_channel(self.channel_id):
            msg = await _apply_undo(interaction, self.channel_id)
            await _update_or_create_board(interaction.channel, force_existing=True)
            await interaction.response.send_message(msg, ephemeral=True)

# =====================[ STATUS BOARD: CREATE / UPDATE ]=====================
async def _update_or_create_board(channel: discord.TextChannel, *, force_existing: bool = False):
    """Create or update the status board in this channel."""
    cid = channel.id
    init_channel(cid)  # ensure state exists
    emb = _build_board_embed(cid, channel.guild)
    view = DraftBoardView(cid)

    # Dynamically enable/disable buttons
    next_act = _next_action(cid)
    fmt_done = bool(formats[cid]) and actions_done[cid] >= len(formats[cid])
    tb_open = tb_mode.get(cid) == "none"
    has_turn = turns[cid] in ("A", "B")

    for item in view.children:
        if isinstance(item, discord.ui.Button):
            if item.label == "BAN":
                item.disabled = not (next_act == "ban" or tb_mode.get(cid) == "noTB")
            elif item.label == "PICK":
                item.disabled = not (next_act == "pick")
            elif item.label == "Undo":
                item.disabled = False
            elif item.label == "Set TB":
                item.disabled = not (fmt_done and tb_open)
            elif item.label == "No TB":
                item.disabled = not (fmt_done and tb_open and turns[cid] in ("A", "B"))

    mid = board_message_id.get(cid)
    if mid:
        try:
            msg = await channel.fetch_message(mid)
            await msg.edit(embed=emb, view=view)
            return
        except discord.NotFound:
            board_message_id.pop(cid, None)
            # fall through to create new

    if force_existing:
        # requested an update, but no message exists – create it
        pass

    msg = await channel.send(embed=emb, view=view)
    board_message_id[cid] = msg.id
    save_state()

# =====================[ STATUS BOARD: COMMAND ]=====================
@bot.command(name="board")
@has_any_role(STAFF_ROLES)
async def board_cmd(ctx):
    """Create/update the status board in this channel."""
    await _update_or_create_board(ctx.channel)
    await ctx.message.add_reaction("✅")








# =====================[ TEST ZONE ]=====================

@bot.command(name="teams", aliases=["teamscan"])
@has_any_role(STAFF_ROLES)
async def teams_cmd(ctx: commands.Context):
    if ctx.guild is None:
        await ctx.send("This command must be used in a server.")
        return

    store = _load_team_roles_store()
    data = store.get("guilds", {}).get(str(ctx.guild.id))
    if not data:
        await ctx.send("No team role snapshot found yet. The autoscan runs every 5 minutes.")
        return

    start_id = data.get("anchors", {}).get("start")
    end_id = data.get("anchors", {}).get("end")
    teams = data.get("teams", [])
    updated = data.get("updated_at", "—")

    embed = discord.Embed(
        title="Team Roles (from JSON)",
        description=(
            f"Anchors: Start {('<@&'+str(start_id)+'>' if start_id else '—')} • "
            f"End {('<@&'+str(end_id)+'>' if end_id else '—')}\n"
            f"Last update: `{updated}`\n"
            f"Found **{len(teams)}** team role(s)."
        ),
        color=EMBED_COLOR,
    )

    teams_sorted = sorted(teams, key=lambda t: t.get("name","").casefold())
    lines = [f"- <@&{t['id']}> (`{t['name']}`)" for t in teams_sorted]

    chunk, chunk_len, parts = [], 0, []
    for ln in lines:
        if chunk_len + len(ln) + 1 > 900:
            parts.append("\n".join(chunk)); chunk, chunk_len = [ln], len(ln)
        else:
            chunk.append(ln); chunk_len += len(ln) + 1
    if chunk: parts.append("\n".join(chunk))

    if parts:
        for i, block in enumerate(parts, 1):
            embed.add_field(name=f"Teams ({i}/{len(parts)})", value=block, inline=False)
    else:
        embed.add_field(name="Teams", value="—", inline=False)

    await ctx.send(embed=embed)

class TeamSwapConfirmView(discord.ui.View):
    def __init__(self, target: discord.Member, from_role: discord.Role, to_role: discord.Role,
                 requester: discord.Member, origin_msg: discord.Message, *,
                 platform: str, region: str, dbdid: str, timeout: float = SWAP_CONFIRM_TTL):
        super().__init__(timeout=timeout)
        self.target = target
        self.from_role = from_role
        self.to_role = to_role
        self.requester = requester
        self.origin_msg = origin_msg
        self.platform = platform
        self.region = region
        self.dbdid = dbdid
        self.message: discord.Message | None = None

    async def on_timeout(self):
        # nach 24h: Request & Ursprungsbefehl entfernen
        try:
            if self.message:
                await self.message.delete()
        except Exception:
            pass
        try:
            if self.origin_msg:
                await self.origin_msg.delete()
        except Exception:
            pass

    def _is_target(self, user: discord.abc.User) -> bool:
        return user.id == self.target.id

    async def _finalize(self, interaction: discord.Interaction, text: str):
        # Disable buttons + Info
        for c in self.children:
            if isinstance(c, discord.ui.Button):
                c.disabled = True
        try:
            if self.message:
                await self.message.edit(content=text, view=self)
        except Exception:
            pass
        await interaction.response.send_message("Received. ✅", ephemeral=True)
        if self.message or self.origin_msg:
            asyncio.create_task(_delete_messages_later(*(x for x in (self.message, self.origin_msg) if x), delay=10))


@discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
async def btn_accept(self, interaction: discord.Interaction, button: discord.ui.Button):
    if not self._is_target(interaction.user):
        await interaction.response.send_message("This request is not for you.", ephemeral=True)
        return

    # Roster-Limit prüfen (nur für aktive Spieler, nicht Manager/Coach)
    if not _is_exempt_from_roster(self.target):
        current_players = _active_players_in_team(interaction.guild, self.to_role)
        if len(current_players) >= MAX_ACTIVE_PLAYERS:
            await interaction.response.send_message(
                f"Roster limit reached for {self.to_role.mention} "
                f"(max {MAX_ACTIVE_PLAYERS} active players). Try again later.",
                ephemeral=True
            )
            return

    # Hat der User noch die alte Teamrolle?
    current_team_roles = _member_team_roles(self.target)
    if not any(r.id == self.from_role.id for r in current_team_roles):
        await interaction.response.send_message(
            "Your team role changed meanwhile. Please ask your captain to resend.", ephemeral=True
        )
        return

    # Wechsel durchführen
    try:
        await self.target.remove_roles(self.from_role, reason=f"Team swap (by {self.requester})")
        if not any(r.id == self.to_role.id for r in self.target.roles):
            await self.target.add_roles(self.to_role, reason=f"Team swap (by {self.requester})")
    except discord.Forbidden:
        await interaction.response.send_message(
            "I can't change roles (missing permissions / role hierarchy).", ephemeral=True
        )
        return

    # <2h vor Match? -> Restrict-Rolle setzen + Info
    note = await _maybe_apply_killer_restriction(self.target, self.to_role)
    extra = f"\n{note}" if note else ""

    await self._finalize(
        interaction,
        f"{self.target.mention} moved from {self.from_role.mention} to "
        f"{self.to_role.mention} (by {self.requester.mention}).{extra}"
    )

    _save_player_profile(
        interaction.guild, self.target, self.to_role,
        platform=self.platform, region=self.region, dbdid=self.dbdid
    )



    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def btn_decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_target(interaction.user):
            await interaction.response.send_message("This request is not for you.", ephemeral=True)
            return
        await self._finalize(interaction, f"{self.target.mention} declined the team change to {self.to_role.mention}.")

@bot.command(name="add")
async def add_member_to_team(ctx: commands.Context, member: discord.Member | None = None, *, tail: str = ""):
    if ctx.guild is None:
        await ctx.send("This command must be used in a server.")
        return

    if TEAM_MGMT_CHANNEL_ID and ctx.channel.id != TEAM_MGMT_CHANNEL_ID:
        return await _temp_reply(ctx, "Please use this command in the designated team management channel.")

    if member is None:
        return await _temp_reply(ctx, "Usage: `!add @User - <Platform> - <Region> - <DBD_ID>`")

    # Zusatzdaten parsen (Reihenfolge egal)
    platform, region, dbdid, err = parse_add_tail(tail)
    if err:
        return await _temp_reply(ctx, f"{err}\nAllowed Platforms: {', '.join(sorted(PLATFORMS))}\n"
                                      f"Allowed Regions: {', '.join(sorted(REGIONS))}")

    team_role_ids = _team_role_ids_from_store(ctx.guild.id)
    if not team_role_ids:
        await ctx.send("No team roles snapshot found yet. Wait for the autoscan (every 5 min) or set up anchors.")
        return

    author_team_roles = [r for r in ctx.author.roles if r.id in team_role_ids]
    if len(author_team_roles) == 0:
        return await _temp_reply(ctx, "You don't have a team role, so I can't infer which team to assign.")
    if len(author_team_roles) > 1:
        names = ", ".join(f"`{r.name}`" for r in author_team_roles)
        return await _temp_reply(ctx, f"You have multiple team roles ({names}). Remove the extra one(s) first.")

    team_role = author_team_roles[0]

    # Roster-Limit prüfen (nur aktive Spieler zählen, Manager/Coach exempt)
    active_players = _active_players_in_team(ctx.guild, team_role)
    target_is_exempt = _is_exempt_from_roster(member)
    if not target_is_exempt and len(active_players) >= MAX_ACTIVE_PLAYERS:
        info = await ctx.send(
            f"Roster limit reached for {team_role.mention}: maximum **{MAX_ACTIVE_PLAYERS}** active players. "
            f"(Managers/Coaches are exempt.)"
        )
        asyncio.create_task(_delete_messages_later(info, ctx.message, delay=10))
        return

    current = _member_team_roles(member)

    # schon im Zielteam?
    if any(r.id == team_role.id for r in current):
        await ctx.send(f"No change: {member.mention} is already in {team_role.mention}.")
        return

    # mehrere Teamrollen -> erst bereinigen
    if len(current) > 1:
        names = ", ".join(r.mention for r in current)
        await ctx.send(f"{member.mention} has multiple team roles ({names}). Please clean this up first.")
        return

    # Wechsel (eine andere Teamrolle) -> Confirm View, Infos mitgeben
    if len(current) == 1:
        from_role = current[0]
        view = TeamSwapConfirmView(
            target=member, from_role=from_role, to_role=team_role,
            requester=ctx.author, origin_msg=ctx.message,
            platform=platform, region=region, dbdid=dbdid,
            timeout=SWAP_CONFIRM_TTL
        )
        msg = await ctx.send(
            content=(f"{member.mention} please confirm the team change:\n"
                     f"From {from_role.mention} ➜ To {team_role.mention}\n"
                     f"(Requested by {ctx.author.mention})\n"
                     f"**Platform:** `{platform}` • **Region:** `{region}` • **DBD:** `{dbdid}`"),
            view=view
        )
        view.message = msg
        return

    # keine Teamrolle -> direkt zuweisen + Profil speichern
    try:
        await member.add_roles(team_role, reason=f"Team assignment by {ctx.author} ({ctx.author.id})")
        _save_player_profile(ctx.guild, member, team_role,
                             platform=platform, region=region, dbdid=dbdid)
        note = await _maybe_apply_killer_restriction(member, team_role)
        msg = f"Added {team_role.mention} to {member.mention}.\n" \
              f"**Platform:** `{platform}` • **Region:** `{region}` • **DBD:** `{dbdid}`"
        if note:
            msg += f"\n{note}"
        info = await ctx.send(msg)
        asyncio.create_task(_delete_messages_later(info, ctx.message, delay=10))
    except discord.Forbidden:
        await ctx.send("I lack permission or my role is below the team role. Adjust role hierarchy/permissions.")
        return

@bot.command(name="remove")
async def remove_member_from_team(ctx: commands.Context, member: discord.Member | None = None):
    """Entfernt dem genannten User die Teamrolle des Aufrufers (auf Basis der Anker-Teams)."""
    if ctx.guild is None:
        await ctx.send("This command must be used in a server.")
        return

    # nur im Management-Channel
    if TEAM_MGMT_CHANNEL_ID and ctx.channel.id != TEAM_MGMT_CHANNEL_ID:
        await ctx.send("Please use this command in the designated team management channel.")
        return

    if member is None:
        await ctx.send("Usage: `!remove @User`")
        return

    team_role_ids = _team_role_ids_from_store(ctx.guild.id)
    if not team_role_ids:
        await ctx.send("No team roles snapshot found yet. Wait for the autoscan (every 5 min) or set up anchors.")
        return

    # Aufrufer muss genau eine Teamrolle haben
    author_team_roles = [r for r in ctx.author.roles if r.id in team_role_ids]
    if len(author_team_roles) == 0:
        await ctx.send("You don't have a team role, so I can't infer which team to remove from.")
        return
    if len(author_team_roles) > 1:
        names = ", ".join(f"`{r.name}`" for r in author_team_roles)
        await ctx.send(f"You have multiple team roles ({names}). Remove the extra one(s) first.")
        return

    team_role = author_team_roles[0]

    # Ziel-User hat die Teamrolle nicht -> nichts zu tun
    if not any(r.id == team_role.id for r in member.roles):
        info = await ctx.send(f"{member.mention} is not a member of {team_role.mention}. Nothing to do.")
        asyncio.create_task(_delete_messages_later(info, ctx.message, delay=10))
        return

    # Rolle entfernen
    try:
        await member.remove_roles(team_role, reason=f"Team removal by {ctx.author} ({ctx.author.id})")
    except discord.Forbidden:
        await ctx.send("I lack permission or my role is below the team role. Adjust role hierarchy/permissions.")
        return
    except discord.HTTPException:
        await ctx.send("Role update failed due to an API error. Try again.")
        return

    info = await ctx.send(f"Removed {member.mention} from {team_role.mention}.")
    asyncio.create_task(_delete_messages_later(info, ctx.message, delay=10))

@bot.command(name="status")
@has_any_role(STAFF_ROLES)
async def status_cmd(ctx: commands.Context, *, team_name: str | None = None):
    if ctx.guild is None:
        await ctx.send("This command must be used in a server.")
        return
    if not team_name:
        await ctx.send("Usage: `!status <team name>`")
        return

    store = _load_team_roles_store()
    gdata = store.get("guilds", {}).get(str(ctx.guild.id))
    if not gdata:
        await ctx.send("No team role snapshot found yet. The autoscan runs every 5 minutes.")
        return

    teams = gdata.get("teams", [])
    profiles = gdata.get("profiles", {})  # <- wichtig: Profil-Tabelle holen

    # passendes Team suchen (exakt, sonst eindeutiger Prefix)
    tn = team_name.strip().casefold()
    exact = [t for t in teams if t.get("name", "").casefold() == tn]
    cand = exact or [t for t in teams if t.get("name", "").casefold().startswith(tn)]
    if not cand:
        await ctx.send(f"No team found for `{team_name}`.")
        return
    if len(cand) > 1 and not exact:
        names = ", ".join(f"`{t['name']}`" for t in cand[:5])
        await ctx.send(f"Multiple teams match: {names} … be more specific.")
        return
    team = cand[0]

    role_id = team["id"]
    role = ctx.guild.get_role(role_id)

    # Helfer
    def _profile(uid: int):
        p = profiles.get(str(uid), {})
        dbd = p.get("dbd_id") or "-"
        plat = p.get("platform") or "-"
        reg = p.get("region") or "-"
        return dbd, plat, reg

    def _fmt(uid: int) -> str:
        m = ctx.guild.get_member(uid)
        mention = (m.mention if m else f"<@{uid}>")
        dbd, plat, reg = _profile(uid)
        return f"{mention}\n`{dbd} • {plat} • {reg}`"

    def _resolve_list(uids):
        # (member, mention, name, formatted)
        out = []
        for uid in uids:
            m = ctx.guild.get_member(uid)
            name = (m.display_name if m else f"User {uid}")
            out.append((m, (m.mention if m else f"<@{uid}>"), name.casefold(), _fmt(uid)))
        # nach Namen sortieren
        out.sort(key=lambda x: x[2])
        return [x[3] for x in out]  # nur die formatierte Zeile zurück

    captain_ids = team.get("captain_ids", [])
    manager_ids = team.get("manager_ids", [])
    player_ids  = team.get("player_ids", [])
    member_ids  = team.get("member_ids", [])

    # Coach-IDs aus member_ids ableiten (haben die Rolle "Coach")
    coach_ids = []
    for uid in member_ids:
        m = ctx.guild.get_member(uid)
        if m and any(r.name == "Coach" for r in m.roles):
            coach_ids.append(uid)

    # Für die Anzeige: Captain NICHT doppelt unter Players
    players_display_ids = [uid for uid in player_ids if uid not in set(captain_ids)]

    cap_lines = _resolve_list(captain_ids)
    mgr_lines = _resolve_list(manager_ids)
    coach_lines = _resolve_list(coach_ids)
    ply_lines = _resolve_list(players_display_ids)

    roster_size = team.get("counts", {}).get("players", len(player_ids))  # zählt Captain mit, Coach/Manager nicht

    role_display = role.mention if role else f"`{team.get('name', '')}`"
    emb = discord.Embed(
        title=f"Team Status — {team.get('name', 'Unknown')}",
        description=f"Role: {role_display}",
        color=EMBED_COLOR,
    )
    emb.add_field(name="Roster size (players)", value=str(roster_size), inline=True)
    emb.add_field(name="Captain", value="\n".join(cap_lines) or "—", inline=False)
    emb.add_field(name="Manager", value="\n".join(mgr_lines) or "—", inline=False)
    emb.add_field(name="Coach", value="\n".join(coach_lines) or "—", inline=False)
    emb.add_field(name="Players", value=("\n".join(ply_lines) if ply_lines else "—"), inline=False)

    updated = gdata.get("updated_at", "—")
    emb.set_footer(text=f"Last autoscan: {updated}")

    await ctx.send(embed=emb)







async def _att_scan_channel(
    guild: discord.Guild,
    channel: discord.TextChannel,
    *,
    silent: bool = True,
    collect_live_rows: bool = False
):
    warnings: list[str] = []
    finalized_count = 0
    live_rows: list[list[str]] = []

    # 1) Anker finden
    anchors: dict[int, str] = {}  # anchor_msg_id -> 'YYYY-MM-DD'
    async for msg in channel.history(limit=500, oldest_first=True):
        ymd = _is_date_anchor_message(msg)
        if ymd:
            anchors[msg.id] = ymd

    if not anchors:
        return (warnings, finalized_count)  # nichts zu tun

    # 2) Games finden (Replies auf Anker)
    # Wir lesen erneut (oder du buffert obige), hier reicht eine zweite Schleife:
    games: list[discord.Message] = []
    async for msg in channel.history(limit=500, oldest_first=True):
        if not msg.reference or not msg.reference.message_id:
            continue
        parent_id = msg.reference.message_id
        if parent_id in anchors:
            games.append(msg)

    roles_map = _resolve_roles(guild)
    emoji_ids = _resolve_emoji_ids(guild)
    blacklist = _guild_blacklist(guild.id)

    now_utc = datetime.now(timezone.utc)

    for gm in games:
        key = _att_key(guild.id, gm.id)
        anchor_id = gm.reference.message_id
        anchor_ymd = anchors.get(anchor_id)
        if not anchor_ymd:
            if not silent:
                warnings.append(f"Game {gm.id}: Reply ohne gültigen Anker.")
            continue

        slot_dt = _extract_first_timestamp_dt(gm)  # Europe/Berlin oder None
        fin_utc = _finalize_at(anchor_ymd, slot_dt)

        ses = attendance_store["sessions"].setdefault(key, {
            "guild_id": guild.id,
            "channel_id": gm.channel.id,
            "message_id": gm.id,
            "anchor_message_id": anchor_id,
            "anchor_date": anchor_ymd,              # YYYY-MM-DD
            "slot_time": slot_dt.isoformat() if slot_dt else None,
            "finalize_at": fin_utc.isoformat(),
            "frozen": False,
            "interim": {"C": [], "R": [], "X": [], "NR": []},
            "last_scanned_at": None,
        })

        # Falls Meta sich geändert hat (z.B. Emoji-IDs später gesetzt), finalize_at bleibt aber stabil.
        ses["anchor_date"] = anchor_ymd
        ses["slot_time"] = slot_dt.isoformat() if slot_dt else None
        ses["finalize_at"] = fin_utc.isoformat()

        # 3) Reaktionen für C/R/X einlesen
        present = {"C": set(), "R": set(), "X": set()}
        # map der drei IDs für schnellen Vergleich
        eid_C, eid_R, eid_X = emoji_ids.get("C"), emoji_ids.get("R"), emoji_ids.get("X")
        if not (eid_C and eid_R and eid_X) and not silent:
            warnings.append("Emoji-IDs fehlen (C/R/X). Fallback per Name versucht. Bitte konfigurieren.")

        for react in gm.reactions:
            rid = _emoji_id_from_reaction_emoji(react.emoji)
            kind: Optional[Literal["C","R","X"]] = None
            if rid == eid_C: kind = "C"
            elif rid == eid_R: kind = "R"
            elif rid == eid_X: kind = "X"
            # Fallback per Name (nur wenn IDs fehlen)
            if kind is None and not rid:
                s = str(react.emoji)  # unicode fallback (hier eher nutzlos)
            if kind is None and hasattr(react.emoji, "name"):
                nm = getattr(react.emoji, "name", "") or ""
                if nm == "r_letter_c": kind = "C"
                elif nm == "r_letter_r": kind = "R"
                elif nm == "r_cross03": kind = "X"

            if not kind:
                continue

            # alle User für diese Reaction holen
            try:
                async for user in react.users():
                    if user.bot:
                        continue
                    mem = guild.get_member(user.id)
                    if not mem:
                        continue
                    # Staff-Filter + Blacklist
                    has_caster = roles_map.get("Caster") and discord.utils.get(mem.roles, id=roles_map["Caster"])
                    has_ref   = roles_map.get("Referee") and discord.utils.get(mem.roles, id=roles_map["Referee"])
                    if (not has_caster and not has_ref) or (user.id in blacklist):
                        continue
                    present[kind].add(user.id)
            except Exception:
                if not silent:
                    warnings.append(f"Reactions lesen fehlgeschlagen für Game {gm.id} (Emoji {kind}).")

        # 4) Interim-NR errechnen (erlaubt; dein Wunsch)
        roster = _roster_now(guild, roles_map, blacklist)
        union_CRX = present["C"] | present["R"] | present["X"]
        nr_now = {uid for uid in roster if uid not in union_CRX}

                # ---- Live-Zeilen bauen (optional, nur wenn gewünscht & nicht gefroren) ----
        if collect_live_rows and not ses.get("frozen"):
            for uid in sorted(roster):
                is_x = uid in present["X"]
                c    = uid in present["C"]
                r    = uid in present["R"]

                if is_x:
                    status = "X"
                else:
                    if c and r:
                        status = "C+R"
                    elif c:
                        status = "C"
                    elif r:
                        status = "R"
                    else:
                        status = "NR"

                mem = guild.get_member(uid)
                dname = mem.display_name if mem else f"User {uid}"

                live_rows.append([
                    anchor_ymd,                 # Date
                    ses["slot_time"] or "",     # SlotTime (ISO oder leer)
                    str(uid),                   # User ID
                    dname,                      # Discord Name
                    status,                     # Status (C/R/C+R/X/NR)
                    now_utc.isoformat(),        # Snapshot Time (UTC)
                    "INTERIM",                  # Note
                ])

        ses["interim"] = {
            "C": sorted(present["C"]),
            "R": sorted(present["R"]),
            "X": sorted(present["X"]),
            "NR": sorted(nr_now),
        }
        ses["last_scanned_at"] = datetime.now(timezone.utc).isoformat()

        # ❗ Finalisierte Sessions NICHT mehr live ins Sheet schreiben,
        # sonst würden wir rote Header wieder grün färben.
        if not ses.get("frozen"):
            try:
                # Zeit-Label (CET) für Spalte B
                slot_label = ""
                if slot_dt:
                    slot_label = slot_dt.astimezone(ATTENDANCE_TZ).strftime("%H:%M")
        
                # Live-Zeilen bauen (Interim): Name, ID, Status (X > C+R > C > R > NR)
                live_rows = []
                for uid in sorted(_roster_now(guild, roles_map, blacklist)):
                    is_x = uid in present["X"]
                    c = uid in present["C"]
                    r = uid in present["R"]
                    if is_x:
                        st = "X"
                    else:
                        if c and r: st = "C+R"
                        elif c:     st = "C"
                        elif r:     st = "R"
                        else:       st = "NR"
                    mem = guild.get_member(uid)
                    dname = mem.display_name if mem else f"User {uid}"
                    live_rows.append((dname, uid, st))
        
                # Live upsert (grün) – aber nur solange NICHT final
                _att_sheets_upsert_block(
                    session_key=key,
                    date_label=anchor_ymd,
                    slot_time_label=slot_label,
                    user_rows=live_rows,
                    finalized=False
                )
            except Exception as e:
                if not silent:
                    warnings.append(f"Sheet (interim) fehlgeschlagen für Game {gm.id}: {e}")
        
                # --- Live-Block für dieses Game im Sheet (Header GRÜN) ---
                try:
                    live_rows = []
                    for uid in sorted(roster):
                        mem = guild.get_member(uid)
                        name = mem.display_name if mem else f"User {uid}"
                        is_x = uid in present["X"]
                        c = uid in present["C"]
                        r = uid in present["R"]
                        if is_x:
                            status = "X"
                        else:
                            status = "C+R" if (c and r) else ("C" if c else ("R" if r else "NR"))
                        live_rows.append((name, uid, status))
                    _att_sheets_upsert_block(key, anchor_ymd, live_rows, finalized=False)
                except Exception as e:
                    if not silent:
                        warnings.append(f"Sheet (interim) fehlgeschlagen für Game {gm.id}: {e}")


        # 5) Finalisieren wenn fällig und noch nicht frozen
        already_final = attendance_store["finalized"].get(key)
        if not ses.get("frozen") and now_utc >= fin_utc:
            # Final-Roster & Statusberechnung
            final_roster = _roster_now(guild, roles_map, blacklist)
            rows = []
            for uid in sorted(final_roster):
                # Status mit X-Dominanz
                is_x = uid in present["X"]
                c = uid in present["C"]
                r = uid in present["R"]
                if is_x:
                    status = "X"
                else:
                    if c and r: status = "C+R"
                    elif c: status = "C"
                    elif r: status = "R"
                    else: status = "NR"
                mem = guild.get_member(uid)
                dname = mem.display_name if mem else f"User {uid}"
                rows.append({
                    "user_id": uid,
                    "display_name": dname,
                    "react_c": c,
                    "react_r": r,
                    "react_x": is_x,
                    "status": status,
                })

            snapshot = {
                "date": anchor_ymd,
                "slot_time": ses["slot_time"],  # ISO (Berlin) oder None
                "snapshot_time": datetime.now(timezone.utc).isoformat(),
            }
            attendance_store["finalized"][key] = {
                "meta": {
                    "guild_id": guild.id,
                    "message_id": gm.id,
                    "channel_id": gm.channel.id,
                    "anchor_message_id": anchor_id,
                },
                "snapshot": snapshot,
                "rows": rows,
            }
            # Optional: Sheets-Append + Export-Flag
            exported = False
            try:
                exported = _att_sheets_append_rows(snapshot, rows)
            except Exception:
                exported = False
                if not silent:
                    warnings.append(f"Sheets-Export fehlgeschlagen für Game {gm.id}.")
            
            attendance_store["finalized"][key]["exported"] = bool(exported)
            ses["frozen"] = True
            finalized_count += 1

            # Header/Block im Sheet jetzt als FINAL neu färben/setzen (ROT):
            try:
                user_rows_simple = [(r["display_name"], r["user_id"], r["status"]) for r in rows]
                _att_sheets_upsert_block(
                    session_key=key,
                    date_label=anchor_ymd,
                    slot_time_label=(slot_dt.astimezone(ATTENDANCE_TZ).strftime("%H:%M") if slot_dt else ""),
                    user_rows=user_rows_simple,
                    finalized=True  # -> ROT
                )
            except Exception as e:
                if not silent:
                    warnings.append(f"Sheet (final) recolor failed for Game {gm.id}: {e}")

            try:
                # simple rows aus 'rows' bauen: (Name, ID, Status)
                user_rows_simple = [(r["display_name"], r["user_id"], r["status"]) for r in rows]
                _att_sheets_upsert_block(
                    session_key=key,                      # "<guild_id>:<message_id>"
                    date_label=anchor_ymd,               # "YYYY-MM-DD"
                    user_rows=user_rows_simple,          # [(name, id, status), ...]
                    finalized=True                       # -> Kopfzeile ROT
                )
            except Exception as e:
                if not silent:
                    warnings.append(f"Sheet (final) recolor failed for Game {gm.id}: {e}")

            # Optional: Sheets-Append
            try:
                _att_sheets_append_rows(snapshot, rows)
            except Exception:
                # still silently continue; Warnung nur bei !ATupdate
                if not silent:
                    warnings.append(f"Sheets-Export fehlgeschlagen für Game {gm.id}.")

            # --- FINALER Sheet-Block (Header ROT) ---
            try:
                final_rows = []
                for r in rows:
                    final_rows.append((r["display_name"], r["user_id"], r["status"]))
                _att_sheets_upsert_block(key, anchor_ymd, final_rows, finalized=True)
                _att_sheets_mark_finalized(key)
            except Exception as e:
                if not silent:
                    warnings.append(f"Sheet (final) fehlgeschlagen für Game {gm.id}: {e}")


    _attendance_save_store()
    return (warnings, finalized_count, live_rows)

@bot.command(name="ATblacklist")
@has_any_role(STAFF_ROLES)
async def at_blacklist(ctx: commands.Context, action: str | None = None, member: discord.Member | None = None):
    """
    !ATblacklist add @User
    !ATblacklist remove @User
    !ATblacklist show
    (gilt guild-weit für Attendance)
    """
    if ctx.guild is None:
        return
    gkey = str(ctx.guild.id)
    attendance_store.setdefault("blacklist", {}).setdefault(gkey, [])
    bl = set(attendance_store["blacklist"][gkey])

    if action in {"add","remove"} and not member:
        await ctx.send("Bitte @User angeben.")
        return

    if action == "add":
        bl.add(member.id)
        attendance_store["blacklist"][gkey] = sorted(bl)
        _attendance_save_store()
        await ctx.send(f"Blacklist: hinzugefügt {member.mention}.")
    elif action == "remove":
        bl.discard(member.id)
        attendance_store["blacklist"][gkey] = sorted(bl)
        _attendance_save_store()
        await ctx.send(f"Blacklist: entfernt {member.mention}.")
    else:
        # show
        if not bl:
            await ctx.send("Blacklist ist leer.")
        else:
            names = []
            for uid in sorted(bl):
                m = ctx.guild.get_member(uid)
                names.append(m.mention if m else f"<@{uid}>")
            await ctx.send("Blacklist: " + ", ".join(names))

@bot.command(name="ATupdate")
@has_any_role(STAFF_ROLES)
async def at_update(ctx: commands.Context):
    """
    Scannt alle Attendance-Channels:
      - Anchors & Games erkennen (Reply-Struktur)
      - Interim C/R/X/NR aktualisieren
      - fällige Games finalisieren (Append-only + optional Sheets)
    Gibt nur hier eine knappe Zusammenfassung & Warnungen aus.
    """
    if ctx.guild is None:
        await ctx.send("Server-only.")
        return
    channels = []
    for cid in ATTENDANCE_CHANNEL_IDS:
        ch = bot.get_channel(cid) or ctx.guild.get_channel(cid)
        if ch is None:
            try:
                ch = await bot.fetch_channel(cid)
            except Exception:
                continue  # nicht vorhanden/kein Zugriff -> überspringen
        if not isinstance(ch, discord.TextChannel):
            continue
        if ch.guild.id != ctx.guild.id:
            # Fremd-Guild -> bewusst ignorieren (optional: Warnung loggen)
            continue
        channels.append(ch)


    total_games = 0
    total_final = 0
    all_warnings = []
    all_live_rows: list[list[str]] = []

    for ch in channels:
        warnings, finalized, live_rows = await _att_scan_channel(
            ctx.guild, ch, silent=False, collect_live_rows=True
        )
        all_live_rows.extend(live_rows)
        total_final += finalized
        all_warnings.extend([f"#{ch.name}: {w}" for w in warnings])

        # Für das Reporting: wie viele Games existieren (Sessions in diesem Channel)
        # Schätzen über aktuelle Anchors/Replies:
        anchors_count = 0
        games_count = 0
        async for m in ch.history(limit=500, oldest_first=True):
            if _is_date_anchor_message(m):
                anchors_count += 1
            elif m.reference and m.reference.message_id:
                if _is_date_anchor_message(await ch.fetch_message(m.reference.message_id)):
                    games_count += 1
        total_games += games_count

        # Optionale Sheet-Logs der Warnungen
        for w in warnings[:20]:  # nicht spammen
            _att_sheets_log(ctx.guild, ch, 0, "WARN", w)
    backfilled = _att_backfill_sheets()
    summary = f"ATupdate: {total_games} Games gescannt, {total_final} finalisiert"
    if backfilled:
        summary += f", {backfilled} exportiert (Backfill)"
    summary += "."

    if all_warnings:
        # zeige nur die ersten ~8 Warnungen, Rest andeuten
        head = "\n".join(f"• {w}" for w in all_warnings[:8])
        more = f"\n(+{len(all_warnings)-8} weitere…)" if len(all_warnings) > 8 else ""
        await ctx.send(f"{summary}\nWarnungen:\n{head}{more}")
    else:
        await ctx.send(summary)

async def _attendance_autoscan_loop():
    """
    Läuft stündlich:
      1) scannt alle konfigurierten Attendance-Channels (silent=True) -> aktualisiert sessions/interim/finalized
      2) schreibt/aktualisiert für JEDE Session einen Block im Sheet (ab GSHEETS_START_ROW),
         inkl. Datums-Header (Spalte A) + Slot-Zeiten (Spalte B) + Zeilen: Name | UserID | Status | Note
      3) finalisierte Sessions bleiben eingefroren; nur aktive Blöcke wachsen (wenn neue Staff hinzukommt)
    """
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            for guild in bot.guilds:
                # --- 1) Channels bestimmen & scannen ---
                channels: list[discord.TextChannel] = []
                for cid in ATTENDANCE_CHANNEL_IDS:
                    ch = guild.get_channel(cid) or await guild.fetch_channel(cid)
                    if isinstance(ch, discord.TextChannel):
                        channels.append(ch)

                for ch in channels:
                    # aktualisiert attendance_store["sessions"] & ggf. ["finalized"]
                    await _att_scan_channel(guild, ch, silent=True)

                # --- 2) Alle Sessions dieses Guilds (nur die o.g. Channels) -> Sheet upserten ---
                channel_ids = {c.id for c in channels}
                sessions = attendance_store.get("sessions", {})
                finals   = attendance_store.get("finalized", {})

                # Hilfsfunktion: ISO-Zeit -> datetime für Sortierung (höhere Zeiten später einfügen => oben stehen)
                def _slot_dt(ses: dict) -> datetime:
                    s = ses.get("slot_time") or ""
                    try:
                        dt = datetime.fromisoformat(s)
                        if dt.tzinfo is None:
                            # naive -> als Berlin interpretieren
                            return datetime.fromisoformat(s).replace(tzinfo=ATTENDANCE_TZ)
                        return dt
                    except Exception:
                        # kein Slot -> weit in die Zukunft, damit Sessions MIT Zeit zuerst einsortiert werden
                        return datetime.max.replace(tzinfo=timezone.utc)

                # Sessions dieses Guilds + Channels einsammeln
                sess_list: list[tuple[str, dict]] = []
                for skey, ses in sessions.items():
                    if ses.get("guild_id") == guild.id and ses.get("channel_id") in channel_ids:
                        sess_list.append((skey, ses))

                # Sortieren nach Datum, dann Slotzeit (frühe zuerst) — weil wir "von alt nach neu" einfügen,
                # damit die späteren Einsätze im Sheet weiter nach oben rücken.
                def _sort_key(item: tuple[str, dict]):
                    _, ses = item
                    return (ses.get("anchor_date") or "", _slot_dt(ses))
                sess_list.sort(key=_sort_key)

                for skey, ses in sess_list:
                    try:
                        # --- User-Zeilen aus "interim" bauen ---
                        interim = ses.get("interim", {}) or {}
                        # Status-Priorität: X dominiert; dann C+R; dann C / R; sonst NR
                        # Wir bauen eine Map uid -> Status
                        c_set = set(interim.get("C", []))
                        r_set = set(interim.get("R", []))
                        x_set = set(interim.get("X", []))
                        nr_set = set(interim.get("NR", []))

                        all_uids = sorted(set().union(c_set, r_set, x_set, nr_set))
                        user_rows: list[tuple[str, int, str]] = []
                        for uid in all_uids:
                            if uid in x_set:
                                status = "X"
                            else:
                                c = uid in c_set
                                r = uid in r_set
                                if c and r: status = "C+R"
                                elif c:      status = "C"
                                elif r:      status = "R"
                                else:        status = "NR"
                            mem = guild.get_member(uid)
                            dname = mem.display_name if mem else f"User {uid}"
                            user_rows.append((dname, uid, status))

                        # --- Final-Flag bestimmen ---
                        is_frozen  = bool(ses.get("frozen"))
                        is_final   = is_frozen or (skey in finals)

                        # --- Datumslabel (YYYY-MM-DD) holen ---
                        date_label = ses.get("anchor_date") or ""
                        if not date_label:
                            # sollte nicht vorkommen (wird im Scan gesetzt), Sicherheitsnetz
                            continue

                        # Upsert in die Tabelle (inkl. Datumskopf + Slotliste in Spalte B)
                        ok = _att_sheets_upsert_block(
                            session_key=skey,
                            date_label=date_label,
                            user_rows=user_rows,
                            finalized=is_final
                        )
                        if not ok:
                            # optional loggen
                            _att_sheets_log(guild, guild.text_channels[0] if guild.text_channels else None,
                                            0, "WARN", f"Sheet (interim) fehlgeschlagen für Game {skey}: upsert False")

                    except Exception as e:
                        # je Channel protokollieren
                        try:
                            _att_sheets_log(guild, guild.text_channels[0] if guild.text_channels else None,
                                            0, "WARN", f"Sheet (interim) fehlgeschlagen für Game {skey}: {e}")
                        except Exception:
                            pass

        except Exception as outer:
            # Grobfehler einmal protokollieren
            try:
                _att_sheets_log(guild, guild.text_channels[0] if guild.text_channels else None,
                                0, "ERROR", f"autoscan loop: {outer}")
            except Exception:
                pass

        # --- 3) schlafen bis zum nächsten Durchlauf ---
        await asyncio.sleep(3600)

async def _attendance_startup_catchup_once():
    """
    Einmaliger Catch-up beim Bot-Start:
      - scannt alle konfigurierten Attendance-Channels (silent=True)
      - finalisiert Sessions, deren finalize_at in der Vergangenheit liegt
      - upsertet/aktualisiert die Blöcke im Google Sheet (Datum+Zeit(en) Kopf, Zeilen: Name | ID | Status | Note)
      - backfillt finalisierte Sessions, die noch nicht exportiert wurden
    """
    await bot.wait_until_ready()

    for guild in bot.guilds:
        # 1) Channels sammeln
        channels: list[discord.TextChannel] = []
        for cid in ATTENDANCE_CHANNEL_IDS:
            try:
                ch = guild.get_channel(cid) or await guild.fetch_channel(cid)
                if isinstance(ch, discord.TextChannel):
                    channels.append(ch)
            except Exception:
                pass

        # 2) Channel-Scan (liest Reaktionen, setzt interim/finalized)
        for ch in channels:
            try:
                await _att_scan_channel(guild, ch, silent=True)
            except Exception as e:
                try:
                    _att_sheets_log(guild, ch, 0, "ERROR", f"startup scan: {e}")
                except Exception:
                    pass

        # 3) Fallback: Aus Store überfällige Sessions finalisieren (falls Messages z.B. gelöscht/älter sind)
        try:
            now_utc = datetime.now(timezone.utc)
            channel_ids = {c.id for c in channels}
            sessions = attendance_store.get("sessions", {})
            finals   = attendance_store.get("finalized", {})

            for skey, ses in list(sessions.items()):
                if ses.get("guild_id") != guild.id or ses.get("channel_id") not in channel_ids:
                    continue
                if ses.get("frozen") or skey in finals:
                    continue

                fin_iso = ses.get("finalize_at")
                if not fin_iso:
                    continue
                try:
                    fin_dt = datetime.fromisoformat(fin_iso)
                except Exception:
                    continue

                if now_utc >= fin_dt:
                    # aus 'interim' + aktuellem Namens-Snapshot finalisieren
                    interim = ses.get("interim", {}) or {}
                    c_set = set(interim.get("C", []))
                    r_set = set(interim.get("R", []))
                    x_set = set(interim.get("X", []))
                    # NR aus interim übernehmen (wir rechnen nicht neu)
                    nr_set = set(interim.get("NR", []))
                    all_uids = sorted(set().union(c_set, r_set, x_set, nr_set))

                    rows = []
                    for uid in all_uids:
                        if uid in x_set:
                            status = "X"
                        else:
                            c = uid in c_set
                            r = uid in r_set
                            if c and r: status = "C+R"
                            elif c:      status = "C"
                            elif r:      status = "R"
                            else:        status = "NR"
                        mem = guild.get_member(uid)
                        dname = mem.display_name if mem else f"User {uid}"
                        rows.append({
                            "user_id": uid,
                            "display_name": dname,
                            "react_c": (uid in c_set),
                            "react_r": (uid in r_set),
                            "react_x": (uid in x_set),
                            "status": status,
                        })

                    snapshot = {
                        "date": ses.get("anchor_date"),
                        "slot_time": ses.get("slot_time"),
                        "snapshot_time": datetime.now(timezone.utc).isoformat(),
                    }
                    attendance_store.setdefault("finalized", {})[skey] = {
                        "meta": {
                            "guild_id": ses.get("guild_id"),
                            "message_id": ses.get("message_id"),
                            "channel_id": ses.get("channel_id"),
                            "anchor_message_id": ses.get("anchor_message_id"),
                        },
                        "snapshot": snapshot,
                        "rows": rows,
                        "exported": False,
                    }
                    ses["frozen"] = True
            _attendance_save_store()
        except Exception:
            pass

        # 4) Blöcke im Sheet upserten (wie im Autoscan)
        try:
            channel_ids = {c.id for c in channels}
            sessions = attendance_store.get("sessions", {})
            finals   = attendance_store.get("finalized", {})

            def _slot_dt(ses: dict) -> datetime:
                s = ses.get("slot_time") or ""
                try:
                    dt = datetime.fromisoformat(s)
                    if dt.tzinfo is None:
                        return datetime.fromisoformat(s).replace(tzinfo=ATTENDANCE_TZ)
                    return dt
                except Exception:
                    return datetime.max.replace(tzinfo=timezone.utc)

            def _sort_key(item: tuple[str, dict]):
                _, ses = item
                return (ses.get("anchor_date") or "", _slot_dt(ses))

            sess_list = [(k, s) for k, s in sessions.items()
                         if s.get("guild_id") == guild.id and s.get("channel_id") in channel_ids]
            sess_list.sort(key=_sort_key)

            for skey, ses in sess_list:
                try:
                    interim = ses.get("interim", {}) or {}
                    c_set = set(interim.get("C", []))
                    r_set = set(interim.get("R", []))
                    x_set = set(interim.get("X", []))
                    nr_set = set(interim.get("NR", []))
                    all_uids = sorted(set().union(c_set, r_set, x_set, nr_set))

                    user_rows: list[tuple[str, int, str]] = []
                    for uid in all_uids:
                        if uid in x_set:
                            status = "X"
                        else:
                            c = uid in c_set
                            r = uid in r_set
                            if c and r: status = "C+R"
                            elif c:      status = "C"
                            elif r:      status = "R"
                            else:        status = "NR"
                        mem = guild.get_member(uid)
                        dname = mem.display_name if mem else f"User {uid}"
                        user_rows.append((dname, uid, status))

                    is_final = bool(ses.get("frozen")) or skey in finals
                    date_label = ses.get("anchor_date") or ""
                    if date_label:
                        _att_sheets_upsert_block(
                            session_key=skey,
                            date_label=date_label,
                            user_rows=user_rows,
                            finalized=is_final
                        )
                except Exception as e:
                    try:
                        _att_sheets_log(guild, guild.text_channels[0] if guild.text_channels else None,
                                        0, "WARN", f"startup upsert failed for {skey}: {e}")
                    except Exception:
                        pass

        except Exception as e:
            try:
                _att_sheets_log(guild, guild.text_channels[0] if guild.text_channels else None,
                                0, "ERROR", f"startup upsert loop: {e}")
            except Exception:
                pass

    # 5) bereits finalisierte Sessions ohne Export ins Sheet nachtragen
    try:
        _att_backfill_sheets()
    except Exception:
        pass

@bot.command(name="ATclear")
@has_any_role(STAFF_ROLES)
async def at_clear(ctx: commands.Context):
    """
    Leert die attendance_store.json nach y/yes-Bestätigung.
    """
    prompt = await ctx.send(
        "Do you want to clear the AT Json?"
        "Y or N ?"
    )

    def _chk(m: discord.Message) -> bool:
        return (
            m.author.id == ctx.author.id
            and m.channel.id == ctx.channel.id
            and m.content.lower() in {"y", "yes", "n", "no"}
        )

    try:
        reply: discord.Message = await bot.wait_for("message", check=_chk, timeout=20)
    except asyncio.TimeoutError:
        try:
            await prompt.edit(content="Aborted.")
            await prompt.delete(delay=5)
        except Exception:
            pass
        return

    if reply.content.lower() in {"n", "no"}:
        try:
            await prompt.edit(content="Aborted.")
            await reply.delete()
            await prompt.delete(delay=5)
        except Exception:
            pass
        return

    # --- bestätigter Wipe ---
    # Falls du die Blacklist behalten willst: auskommentieren/aktivieren
    keep_blacklist = False
    saved_bl = attendance_store.get("blacklist", {}) if keep_blacklist else {}

    attendance_store.clear()
    attendance_store.update({
        "sessions": {},
        "finalized": {},
        "blacklist": saved_bl,   # { } wenn keep_blacklist = False
        "sheet_blocks": {},
    })
    _attendance_save_store()

    try:
        await reply.delete()
        await prompt.delete()
    except Exception:
        pass

    await ctx.send("Attendance-Tracker JSON cleared.")










if __name__ == "__main__":
    import os, time
    import aiohttp

    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        raise SystemExit("TOKEN env var not set. Setze Environment=TOKEN=... in systemd oder export TOKEN=...")

    while True:
        try:
            bot.run(TOKEN)  # discord.py macht Reconnects; diese Schleife fängt DNS/Netzfehler ab
            break
        except aiohttp.ClientConnectorError as e:
            print(f"[net] connect failed: {e}; retry in 30s")
            time.sleep(30)
        except Exception as e:
            print(f"[bot] crashed: {e}; retry in 30s")
            time.sleep(30)
