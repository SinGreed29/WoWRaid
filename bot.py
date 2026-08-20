
import os
import re
import json
import sqlite3
import asyncio
import time
import html
import ast
from dataclasses import dataclass
from typing import Optional, Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from pathlib import Path
from dotenv import load_dotenv

# Local development: if .env exists next to bot.py, load it.
# On Railway/.other hosts secrets are provided as environment variables, so
# .env is optional and must NOT be committed to GitHub.
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
if ENV_FILE.exists():
    load_dotenv(dotenv_path=ENV_FILE, override=False)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
WCL_CLIENT_ID = os.getenv("WCL_CLIENT_ID", "").strip()
WCL_CLIENT_SECRET = os.getenv("WCL_CLIENT_SECRET", "").strip()
WCL_SITE = os.getenv("WCL_SITE", "https://www.warcraftlogs.com").rstrip("/")
WCL_API = os.getenv("WCL_API", f"{WCL_SITE}/api/v2/client").rstrip("/")
WCL_REGION = os.getenv("WCL_REGION", "eu").strip().lower()
WCL_DEFAULT_REALM = os.getenv("WCL_DEFAULT_REALM", "howling-fjord").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "raids.sqlite3")
REFRESH_MINUTES = max(5, int(os.getenv("REFRESH_MINUTES", "15")))
RAID_LIMIT = max(1, min(40, int(os.getenv("RAID_LIMIT", "30"))))
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()

if not DISCORD_TOKEN:
    raise SystemExit("DISCORD_TOKEN не задан. Добавьте DISCORD_TOKEN в переменные окружения Railway или в локальный .env.")
if not WCL_CLIENT_ID or not WCL_CLIENT_SECRET:
    raise SystemExit("WCL_CLIENT_ID/WCL_CLIENT_SECRET не заданы. Добавьте их в переменные окружения Railway или в локальный .env.")
print(f"[CONFIG] bot.py: {Path(__file__).resolve()}")
print(f"[CONFIG] .env найден: {ENV_FILE.exists()} (на Railway не требуется)")
print(f"[CONFIG] Discord token: {'OK' if DISCORD_TOKEN else 'НЕ НАЙДЕН'}")
print(f"[CONFIG] WCL Client ID: {'OK' if WCL_CLIENT_ID else 'НЕ НАЙДЕН'}")
print(f"[CONFIG] WCL Secret: {'OK' if WCL_CLIENT_SECRET else 'НЕ НАЙДЕН'}")
print(f"[CONFIG] RAID_LIMIT: {RAID_LIMIT}")

# Midnight raid zones currently used by the bot.
RAIDS = {
    "voidspire": {
        "name": "Шпиль Пустоты",
        "name_en": "The Voidspire",
        "zone_id": 46,
        "bosses": 6,
        "url": "https://www.warcraftlogs.com/zone/rankings/46",
        "wowhead_url": "https://www.wowhead.com/guide/midnight/raids/the-voidspire-overview-location-rewards-bosses",
    },
    "dreamrift": {
        "name": "Разлом Снов",
        "name_en": "The Dreamrift",
        "zone_id": 46,
        "bosses": 1,
        "url": "https://www.warcraftlogs.com/zone/rankings/46",
        "wowhead_url": "https://www.wowhead.com/guide/midnight/raids/the-dreamrift-overview-location-rewards-boss",
    },
    "quelthalas": {
        "name": "Поход на Кель'Данас",
        "name_en": "March on Quel'Danas",
        "zone_id": 46,
        "bosses": 2,
        "url": "https://www.warcraftlogs.com/zone/rankings/46",
        "wowhead_url": "https://www.wowhead.com/guide/midnight/raids/march-on-quel-danas-overview-location-rewards-bosses",
    },
    "sporefall": {
        "name": "Спороцвет",
        "name_en": "Sporefall",
        "zone_id": 50,
        "bosses": 1,
        "url": "https://www.warcraftlogs.com/zone/rankings/50",
        "wowhead_url": "https://www.wowhead.com/guide/midnight/raids/sporefall-overview-location-rewards-boss",
    },
    "venomous_abyss": {
        "name": "Ядовитая Бездна",
        "name_en": "The Venomous Abyss",
        "zone_id": 54,
        "bosses": 8,
        "url": "https://www.warcraftlogs.com/zone/rankings/54",
        "wowhead_url": "https://www.wowhead.com/guide/midnight/raids/the-venomous-abyss-overview-location-rewards-bosses",
    },
}

DIFFICULTIES = {
    "Нормальный": 3,
    "Героический": 4,
    "Мифический": 5,
    "Рейд-поиск": 1,
}

# Russian label -> WCL English spec name.
CLASS_SPECS = {
    "Воин": {
        "wcl": "Warrior", "emoji": "⚔️",
        "specs": {
            "Оружие": "Arms",
            "Неистовство": "Fury",
            "Защита": "Protection",
        },
    },
    "Паладин": {
        "wcl": "Paladin", "emoji": "🛡️",
        "specs": {
            "Свет": "Holy",
            "Защита": "Protection",
            "Воздаяние": "Retribution",
        },
    },
    "Охотник": {
        "wcl": "Hunter", "emoji": "🏹",
        "specs": {
            "Повелитель зверей": "BeastMastery",
            "Стрельба": "Marksmanship",
            "Выживание": "Survival",
        },
    },
    "Разбойник": {
        "wcl": "Rogue", "emoji": "🗡️",
        "specs": {
            "Ликвидация": "Assassination",
            "Головорез": "Outlaw",
            "Скрытность": "Subtlety",
        },
    },
    "Жрец": {
        "wcl": "Priest", "emoji": "🙏",
        "specs": {
            "Послушание": "Discipline",
            "Свет": "Holy",
            "Тьма": "Shadow",
        },
    },
    "Рыцарь смерти": {
        "wcl": "DeathKnight", "emoji": "💀",
        "specs": {
            "Кровь": "Blood",
            "Лёд": "Frost",
            "Нечестивость": "Unholy",
        },
    },
    "Шаман": {
        "wcl": "Shaman", "emoji": "🌊",
        "specs": {
            "Стихии": "Elemental",
            "Совершенствование": "Enhancement",
            "Исцеление": "Restoration",
        },
    },
    "Маг": {
        "wcl": "Mage", "emoji": "🔮",
        "specs": {
            "Тайная магия": "Arcane",
            "Огонь": "Fire",
            "Лёд": "Frost",
        },
    },
    "Чернокнижник": {
        "wcl": "Warlock", "emoji": "👿",
        "specs": {
            "Колдовство": "Affliction",
            "Демонология": "Demonology",
            "Разрушение": "Destruction",
        },
    },
    "Монах": {
        "wcl": "Monk", "emoji": "🐼",
        "specs": {
            "Хмелевар": "Brewmaster",
            "Ткач туманов": "Mistweaver",
            "Танцующий с ветром": "Windwalker",
        },
    },
    "Друид": {
        "wcl": "Druid", "emoji": "🌿",
        "specs": {
            "Баланс": "Balance",
            "Сила зверя": "Feral",
            "Страж": "Guardian",
            "Восстановление": "Restoration",
        },
    },
    "Охотник на демонов": {
        "wcl": "DemonHunter", "emoji": "😈",
        "specs": {
            "Истребление": "Havoc",
            "Месть": "Vengeance",
            "Пожиратель": "Devourer",
        },
    },
    "Эвокер": {
        "wcl": "Evoker", "emoji": "🐉",
        "specs": {
            "Опустошение": "Devastation",
            "Сохранение": "Preservation",
            "Усиление": "Augmentation",
        },
    },
}

SPEC_ROLE = {
    "Protection": "Танк", "Blood": "Танк", "Brewmaster": "Танк",
    "Guardian": "Танк", "Vengeance": "Танк",
    "Holy": "Хил", "Discipline": "Хил", "Restoration": "Хил",
    "Mistweaver": "Хил", "Preservation": "Хил",
}

STATUS_LABELS = {
    "confirmed": ("Записан", "👥"),
    "unsure": ("Не уверен", "⚖️"),
    "late": ("Опоздаю", "🕐"),
    "cant": ("Не смогу", "❌"),
}

ROLE_EMOJI = {"Танк": "🛡️", "Хил": "💚", "ДД": "⚔️"}

# Custom Discord emoji convention.
# Upload WoW icons to the server with these names:
#   class_warrior, class_paladin, class_hunter, ...
#   spec_arms, spec_fury, spec_protection, ...
# The bot automatically finds them by name. IDs do NOT need to be hardcoded.
RAID_IMAGES: dict[str, str] = {}

CLASS_EMOJI_NAMES = {
    "Warrior": "class_warrior",
    "Paladin": "class_paladin",
    "Hunter": "class_hunter",
    "Rogue": "class_rogue",
    "Priest": "class_priest",
    "DeathKnight": "class_deathknight",
    "Shaman": "class_shaman",
    "Mage": "class_mage",
    "Warlock": "class_warlock",
    "Monk": "class_monk",
    "Druid": "class_druid",
    "DemonHunter": "class_demonhunter",
    "Evoker": "class_evoker",
}

# Spec emojis are keyed by WCL spec name, so duplicate Russian names such as
# "Свет" (Paladin/Priest) and "Защита" (Warrior/Paladin) resolve identically.

SPEC_EMOJI_NAMES = {
    "Arms": "spec_arms",
    "Fury": "spec_fury",
    "Protection": "spec_protection",
    "Holy": "spec_holy",
    "Retribution": "spec_retribution",
    "BeastMastery": "spec_beastmastery",
    "Marksmanship": "spec_marksmanship",
    "Survival": "spec_survival",
    "Assassination": "spec_assassination",
    "Outlaw": "spec_outlaw",
    "Subtlety": "spec_subtlety",
    "Discipline": "spec_discipline",
    "Shadow": "spec_shadow",
    "Blood": "spec_blood",
    "Frost": "spec_frost",
    "Unholy": "spec_unholy",
    "Elemental": "spec_elemental",
    "Enhancement": "spec_enhancement",
    "Restoration": "spec_restoration",
    "Arcane": "spec_arcane",
    "Fire": "spec_fire",
    "Affliction": "spec_affliction",
    "Demonology": "spec_demonology",
    "Destruction": "spec_destruction",
    "Brewmaster": "spec_brewmaster",
    "Mistweaver": "spec_mistweaver",
    "Windwalker": "spec_windwalker",
    "Balance": "spec_balance",
    "Feral": "spec_feral",
    "Guardian": "spec_guardian",
    "Havoc": "spec_havoc",
    "Vengeance": "spec_vengeance",
    "Devourer": "spec_devourer",
    "Devastation": "spec_devastation",
    "Preservation": "spec_preservation",
    "Augmentation": "spec_augmentation",
}

def get_custom_emoji_obj(guild: Optional[discord.Guild], emoji_name: Optional[str]):
    """Return a guild emoji object for Discord SelectOption, if available."""
    if guild and emoji_name:
        return discord.utils.get(guild.emojis, name=emoji_name)
    return None

def get_custom_emoji(guild: Optional[discord.Guild], emoji_name: Optional[str]) -> str:
    emoji = get_custom_emoji_obj(guild, emoji_name)
    return str(emoji) if emoji else ""

def player_icons(guild: Optional[discord.Guild], player: dict) -> str:
    """Class + specialization icons only. Role icons are intentionally omitted next to names."""
    cd = CLASS_SPECS.get(player.get("class_name"), {})
    class_name = CLASS_EMOJI_NAMES.get(player.get("wcl_class") or cd.get("wcl"))
    if player.get("class_name") == "Маг" and player.get("wcl_spec") == "Frost":
        spec_name = "mage_frost"
    else:
        spec_name = SPEC_EMOJI_NAMES.get(player.get("wcl_spec"))
    class_icon = get_custom_emoji(guild, class_name) if class_name else ""
    spec_icon = get_custom_emoji(guild, spec_name) if spec_name else ""
    if not class_icon:
        class_icon = cd.get("emoji", "👤")
    return f"{class_icon}{spec_icon}"

def select_emoji(guild: Optional[discord.Guild], class_name: str, wcl_spec: Optional[str] = None):
    """Custom emoji for dropdowns, with a safe Unicode fallback."""
    if wcl_spec:
        emoji_name = "mage_frost" if class_name == "Маг" and wcl_spec == "Frost" else SPEC_EMOJI_NAMES.get(wcl_spec)
        emoji = get_custom_emoji_obj(guild, emoji_name)
        if emoji:
            return emoji
    cd = CLASS_SPECS[class_name]
    return get_custom_emoji_obj(guild, CLASS_EMOJI_NAMES.get(cd["wcl"])) or cd["emoji"]

async def fetch_wowhead_image(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    """Get Wowhead's social/preview image URL for a raid page."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Discord Raid Bot; +https://wowhead.com)"}
        async with session.get(url, headers=headers, allow_redirects=True) as r:
            if r.status != 200:
                return None
            body = await r.text(errors="ignore")
        m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            body,
            re.I,
        )
        if not m:
            m = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                body,
                re.I,
            )
        return html.unescape(m.group(1)) if m else None
    except Exception as e:
        print(f"[Wowhead image] {url}: {e}")
        return None

async def load_raid_images():
    if not wcl.session:
        return
    for raid_id, info in RAIDS.items():
        wowhead_url = info.get("wowhead_url")
        if not wowhead_url:
            continue
        image = await fetch_wowhead_image(wcl.session, wowhead_url)
        if image:
            RAID_IMAGES[raid_id] = image
            print(f"[Wowhead image] {raid_id}: OK")
        else:
            print(f"[Wowhead image] {raid_id}: not found")

REALM_ALIASES = {
    "ревущий фьорд": "howling-fjord",
    "ревущий-фьорд": "howling-fjord",
    "ревущий фьорд": "howling-fjord",
    "howling fjord": "howling-fjord",
    "howling-fjord": "howling-fjord",
}

def normalize_realm(value: str) -> str:
    raw = value.strip().lower()
    return REALM_ALIASES.get(raw, raw.replace(" ", "-"))

def class_data(class_name: str) -> dict:
    return CLASS_SPECS[class_name]

def role_for_spec(wcl_spec: str) -> str:
    return SPEC_ROLE.get(wcl_spec, "ДД")

def percentile_number(value: Any) -> Optional[float]:
    try:
        n = float(value)
        if 0 <= n <= 100:
            return n
    except (TypeError, ValueError):
        pass
    return None

def parse_color(p: Optional[float]) -> int:
    if p is None:
        return 0x666666
    if p >= 99:
        return 0xE268A8
    if p >= 95:
        return 0xFF8000
    if p >= 75:
        return 0xA335EE
    if p >= 50:
        return 0x0070FF
    if p >= 25:
        return 0x1EFF00
    return 0x666666

def parse_emoji(p: Optional[float]) -> str:
    if p is None:
        return "▫️"
    if p >= 99:
        return "🟣"
    if p >= 95:
        return "🟠"
    if p >= 75:
        return "🟪"
    if p >= 50:
        return "🟦"
    if p >= 25:
        return "🟩"
    return "⬜"


def get_custom_emoji_by_name(guild: Optional[discord.Guild], name: str, fallback: str = "") -> str:
    """Return a server custom emoji by name, falling back safely when unavailable."""
    if guild:
        emoji = discord.utils.get(guild.emojis, name=name)
        if emoji:
            return str(emoji)
    return fallback

class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS raids (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            date_text TEXT NOT NULL,
            raid_id TEXT NOT NULL,
            difficulty_name TEXT NOT NULL,
            difficulty_id INTEGER NOT NULL,
            leader_id INTEGER NOT NULL,
            leader_name TEXT,
            message_id INTEGER,
            closed INTEGER NOT NULL DEFAULT 0,
            raid_log TEXT,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS players (
            channel_id INTEGER NOT NULL,
            discord_id INTEGER NOT NULL,
            discord_name TEXT NOT NULL,
            character_name TEXT NOT NULL,
            realm TEXT NOT NULL,
            region TEXT NOT NULL,
            class_name TEXT NOT NULL,
            wcl_class TEXT NOT NULL,
            spec_name TEXT NOT NULL,
            wcl_spec TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'confirmed',
            avg_parse REAL,
            best_parse REAL,
            bosses_json TEXT NOT NULL DEFAULT '[]',
            profile_url TEXT,
            updated_at INTEGER,
            group_no INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(channel_id, discord_id),
            FOREIGN KEY(channel_id) REFERENCES raids(channel_id) ON DELETE CASCADE
        );
        """)
        # Safe migration for existing databases created before manual groups.
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(players)").fetchall()}
        if "group_no" not in columns:
            self.conn.execute("ALTER TABLE players ADD COLUMN group_no INTEGER NOT NULL DEFAULT 1")
            for channel_id in [r[0] for r in self.conn.execute("SELECT DISTINCT channel_id FROM players").fetchall()]:
                rows = self.conn.execute(
                    "SELECT discord_id FROM players WHERE channel_id=? AND status='confirmed' "
                    "ORDER BY updated_at ASC, discord_id ASC",
                    (channel_id,),
                ).fetchall()
                for idx, row in enumerate(rows):
                    self.conn.execute(
                        "UPDATE players SET group_no=? WHERE channel_id=? AND discord_id=?",
                        (idx // 6 + 1, channel_id, row[0]),
                    )
        raid_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(raids)").fetchall()}
        if "leader_name" not in raid_columns:
            self.conn.execute("ALTER TABLE raids ADD COLUMN leader_name TEXT")
        self.conn.commit()

    def create_raid(self, data: dict):
        self.conn.execute("""
        INSERT INTO raids
        (channel_id,guild_id,name,date_text,raid_id,difficulty_name,difficulty_id,leader_id,leader_name,message_id,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            data["channel_id"], data["guild_id"], data["name"], data["date_text"],
            data["raid_id"], data["difficulty_name"], data["difficulty_id"],
            data["leader_id"], data.get("leader_name"), None, int(time.time())
        ))
        self.conn.commit()

    def set_leader_name(self, channel_id: int, leader_name: str):
        self.conn.execute("UPDATE raids SET leader_name=? WHERE channel_id=?", (leader_name, channel_id))
        self.conn.commit()

    def set_message(self, channel_id: int, message_id: int):
        self.conn.execute("UPDATE raids SET message_id=? WHERE channel_id=?", (message_id, channel_id))
        self.conn.commit()

    def set_log(self, channel_id: int, log_url: Optional[str]):
        self.conn.execute("UPDATE raids SET raid_log=? WHERE channel_id=?", (log_url, channel_id))
        self.conn.commit()

    def close_raid(self, channel_id: int):
        self.conn.execute("UPDATE raids SET closed=1 WHERE channel_id=?", (channel_id,))
        self.conn.commit()

    def delete_raid(self, channel_id: int):
        self.conn.execute("DELETE FROM players WHERE channel_id=?", (channel_id,))
        self.conn.execute("DELETE FROM raids WHERE channel_id=?", (channel_id,))
        self.conn.commit()

    def get_raid(self, channel_id: int):
        return self.conn.execute("SELECT * FROM raids WHERE channel_id=?", (channel_id,)).fetchone()

    def active_raids(self):
        return self.conn.execute("SELECT * FROM raids WHERE closed=0 AND message_id IS NOT NULL").fetchall()

    def get_players(self, channel_id: int):
        return self.conn.execute(
            "SELECT * FROM players WHERE channel_id=? ORDER BY status='confirmed' DESC, updated_at ASC, discord_id ASC",
            (channel_id,)
        ).fetchall()

    def get_player(self, channel_id: int, discord_id: int):
        return self.conn.execute(
            "SELECT * FROM players WHERE channel_id=? AND discord_id=?", (channel_id, discord_id)
        ).fetchone()

    def upsert_player(self, data: dict):
        self.conn.execute("""
        INSERT INTO players
        (channel_id,discord_id,discord_name,character_name,realm,region,class_name,wcl_class,
         spec_name,wcl_spec,role,status,avg_parse,best_parse,bosses_json,profile_url,updated_at,group_no)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(channel_id,discord_id) DO UPDATE SET
          discord_name=excluded.discord_name,
          character_name=excluded.character_name,
          realm=excluded.realm,
          region=excluded.region,
          class_name=excluded.class_name,
          wcl_class=excluded.wcl_class,
          spec_name=excluded.spec_name,
          wcl_spec=excluded.wcl_spec,
          role=excluded.role,
          status=excluded.status,
          avg_parse=excluded.avg_parse,
          best_parse=excluded.best_parse,
          bosses_json=excluded.bosses_json,
          profile_url=excluded.profile_url,
          updated_at=excluded.updated_at
        """, (
            data["channel_id"], data["discord_id"], data["discord_name"],
            data["character_name"], data["realm"], data["region"],
            data["class_name"], data["wcl_class"], data["spec_name"],
            data["wcl_spec"], data["role"], data["status"], data["avg_parse"],
            data["best_parse"], json.dumps(data.get("bosses", []), ensure_ascii=False),
            data.get("profile_url"), int(time.time()), data.get("group_no", 1)
        ))
        self.conn.commit()

    def set_group(self, channel_id: int, discord_id: int, group_no: int):
        self.conn.execute(
            "UPDATE players SET group_no=?, updated_at=? WHERE channel_id=? AND discord_id=?",
            (group_no, int(time.time()), channel_id, discord_id),
        )
        self.conn.commit()

    def get_group_players(self, channel_id: int, group_no: int):
        return self.conn.execute(
            "SELECT * FROM players WHERE channel_id=? AND group_no=? AND status='confirmed' "
            "ORDER BY updated_at ASC, discord_id ASC",
            (channel_id, group_no),
        ).fetchall()

    def rebalance_groups_if_needed(self, channel_id: int):
        """Migrate the old 5x6 layout to 6x5 once, without touching later manual grouping."""
        rows = self.get_players(channel_id)
        confirmed = [r for r in rows if r["status"] == "confirmed"]
        if not confirmed:
            return
        counts = {i: 0 for i in range(1, 7)}
        for row in confirmed:
            g = int(row["group_no"] or 1)
            if g in counts:
                counts[g] += 1
        # Detect the old layout: group 6 unused and at least one of groups 1-5 over capacity.
        if counts[6] != 0 or max(counts[i] for i in range(1, 6)) <= 5:
            return
        now = int(time.time())
        for pos, row in enumerate(confirmed):
            group_no = (pos // 5) + 1
            self.conn.execute(
                "UPDATE players SET group_no=?, updated_at=? WHERE channel_id=? AND discord_id=?",
                (group_no, now, channel_id, row["discord_id"]),
            )
        self.conn.commit()

    def set_status(self, channel_id: int, discord_id: int, status: str):
        self.conn.execute(
            "UPDATE players SET status=?, updated_at=? WHERE channel_id=? AND discord_id=?",
            (status, int(time.time()), channel_id, discord_id)
        )
        self.conn.commit()

    def remove_player(self, channel_id: int, discord_id: int):
        self.conn.execute("DELETE FROM players WHERE channel_id=? AND discord_id=?", (channel_id, discord_id))
        self.conn.commit()

    def update_parses(self, channel_id: int, discord_id: int, result: dict):
        self.conn.execute("""
        UPDATE players SET avg_parse=?, best_parse=?, bosses_json=?, profile_url=?, updated_at=?
        WHERE channel_id=? AND discord_id=?
        """, (
            result.get("avg_parse"), result.get("best_parse"),
            json.dumps(result.get("bosses", []), ensure_ascii=False),
            result.get("profile_url"), int(time.time()), channel_id, discord_id
        ))
        self.conn.commit()

class WCLClient:
    TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.token: Optional[str] = None
        self.token_expires = 0

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        await self.get_token(force=True)

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def get_token(self, force=False) -> str:
        if not force and self.token and time.time() < self.token_expires - 60:
            return self.token
        assert self.session is not None
        auth = aiohttp.BasicAuth(WCL_CLIENT_ID, WCL_CLIENT_SECRET)
        async with self.session.post(
            self.TOKEN_URL,
            auth=auth,
            data={"grant_type": "client_credentials"},
        ) as r:
            text = await r.text()
            if r.status != 200:
                raise RuntimeError(f"WCL OAuth {r.status}: {text[:500]}")
            data = json.loads(text)
        self.token = data["access_token"]
        self.token_expires = time.time() + int(data.get("expires_in", 3600))
        return self.token

    async def graphql(self, query: str, variables: Optional[dict] = None) -> dict:
        assert self.session is not None
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"query": query, "variables": variables or {}}
        async with self.session.post(WCL_API, json=payload, headers=headers) as r:
            text = await r.text()
            if r.status == 401:
                await self.get_token(force=True)
                headers["Authorization"] = f"Bearer {self.token}"
                async with self.session.post(WCL_API, json=payload, headers=headers) as rr:
                    text = await rr.text()
                    if rr.status != 200:
                        raise RuntimeError(f"WCL API {rr.status}: {text[:500]}")
                    data = json.loads(text)
            else:
                if r.status != 200:
                    raise RuntimeError(f"WCL API {r.status}: {text[:500]}")
                data = json.loads(text)
        if data.get("errors"):
            raise RuntimeError("; ".join(e.get("message", "GraphQL error") for e in data["errors"]))
        return data["data"]

    async def status(self) -> dict:
        q = """
        query {
          rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }
          worldData {
            zone(id: 46) {
              id name
              encounters { id name }
              difficulties { id name sizes }
            }
          }
        }
        """
        return await self.graphql(q)

    async def character(self, name: str, realm: str, region: str, raid_id: str, difficulty_id: int,
                        wcl_class: str, wcl_spec: str) -> dict:
        """Fetch character rankings with a compatibility fallback for WCL zone IDs.

        The current Venomous Abyss rankings page uses zone 54, while character
        profile/history links can still expose the same raid under zone 53.
        We therefore try the raid's configured zone first and, for Venomous
        Abyss only, fall back to zone 53 when no rankings are returned.

        Class/spec filters are deliberately omitted here. WCL can have slightly
        different historical spec labels, and filtering at the API level can
        make an existing character parse disappear. The character itself is
        already known, so we safely collect its rankings and let the UI display
        the stored class/spec.
        """
        raid = RAIDS[raid_id]

        zone_candidates = [raid["zone_id"]]
        if raid_id == "venomous_abyss" and 53 not in zone_candidates:
            zone_candidates.append(53)

        q = """
        query Character(
          $name:String!, $server:String!, $region:String!,
          $zone:Int!, $difficulty:Int!
        ) {
          characterData {
            character(name:$name, serverSlug:$server, serverRegion:$region) {
              id name hidden classID level
              server { name slug normalizedName region { name slug } }
              zoneRankings(
                zoneID:$zone
                difficulty:$difficulty
              )
            }
          }
        }
        """

        char = None
        rankings = {}
        used_zone = raid["zone_id"]
        last_error = None

        for zone_id in zone_candidates:
            try:
                data = await self.graphql(q, {
                    "name": name,
                    "server": normalize_realm(realm),
                    "region": region,
                    "zone": zone_id,
                    "difficulty": difficulty_id,
                })
            except Exception as e:
                last_error = str(e)
                continue

            char = ((data.get("characterData") or {}).get("character"))
            if not char:
                return {"found": False, "error": "Персонаж не найден в Warcraft Logs."}

            raw = char.get("zoneRankings")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    raw = {}
            rankings = raw or {}

            raw_rankings = rankings.get("rankings") or []
            all_stars = rankings.get("allStars") or []
            if raw_rankings or all_stars or rankings.get("rankPercent") is not None:
                used_zone = zone_id
                break

            # For Venomous Abyss, an empty zone 54 result is retried against
            # the legacy/profile zone 53. For every other raid we stop here.
            used_zone = zone_id

        if not char:
            return {"found": False, "error": last_error or "Не удалось получить персонажа из Warcraft Logs."}

        bosses = []
        raw_rankings = rankings.get("rankings") or []
        for item in raw_rankings:
            boss_name = normalize_boss_name(item.get("encounter", item.get("boss", "Босс")))
            p = percentile_number(item.get("rankPercent", item.get("percentile", item.get("performance"))))
            bosses.append({
                "boss": boss_name,
                "percentile": p,
                "amount": item.get("bestAmount", item.get("amount")),
            })

        # Some WCL responses expose All Stars separately. Keep it as a
        # fallback so a character with All Stars but no normal ranking list
        # is not treated as having no logs.
        if not bosses:
            for item in (rankings.get("allStars") or []):
                boss_name = normalize_boss_name(item.get("encounter", item.get("boss", "Все боссы")))
                p = percentile_number(item.get("rankPercent", item.get("percentile", item.get("performance"))))
                bosses.append({
                    "boss": boss_name,
                    "percentile": p,
                    "amount": item.get("bestAmount", item.get("amount")),
                })

        values = [b["percentile"] for b in bosses if b["percentile"] is not None]
        avg = rankings.get("medianPerformance")
        if avg is None:
            avg = rankings.get("averagePerformance")
        if avg is None and values:
            avg = sum(values) / len(values)

        best = rankings.get("rankPercent")
        if best is None and values:
            best = max(values)

        server_slug = char["server"]["slug"]
        profile = f"{WCL_SITE}/character/{region}/{server_slug}/{char['name']}?zone={used_zone}"

        print(
            f"[WCL character] {char.get('name')} / {char.get('server', {}).get('name')} "
            f"raid={raid_id} difficulty={difficulty_id} zone={used_zone} "
            f"rankings={len(raw_rankings)} allStars={len(rankings.get('allStars') or [])}"
        )

        return {
            "found": True,
            "character_name": char["name"],
            "server": char["server"]["name"],
            "server_slug": server_slug,
            "avg_parse": percentile_number(avg),
            "best_parse": percentile_number(best),
            "bosses": bosses,
            "profile_url": profile,
        }

    async def report(self, code: str) -> dict:
        q = """
        query Report($code:String!) {
          reportData {
            report(code:$code) {
              code title visibility startTime endTime exportedSegments
              zone { id name encounters { id name } }
              fights {
                id name difficulty kill encounterID
              }
              rankedCharacters { id name server { name slug } }
            }
          }
        }
        """
        data = await self.graphql(q, {"code": code})
        report = ((data.get("reportData") or {}).get("report"))
        if not report:
            raise RuntimeError("Отчёт не найден или недоступен через публичный API.")
        return report

    async def zone(self, zone_id: int) -> dict:
        q = """
        query Zone($id:Int!) {
          worldData {
            zone(id:$id) {
              id name
              encounters { id name }
              difficulties { id name sizes }
            }
          }
        }
        """
        data = await self.graphql(q, {"id": zone_id})
        zone = ((data.get("worldData") or {}).get("zone"))
        if not zone:
            raise RuntimeError(f"Зона {zone_id} не найдена.")
        return zone

    async def report_for_raid(self, url: str, raid_id: str, difficulty_id: int) -> dict:
        code = extract_report_code(url)
        if not code:
            raise ValueError("Не нашёл код отчёта в ссылке Warcraft Logs.")
        report = await self.report(code)
        expected_zone = RAIDS[raid_id]["zone_id"]
        if not report.get("zone") or report["zone"]["id"] != expected_zone:
            raise RuntimeError(
                f"Этот лог относится к зоне {report.get('zone', {}).get('name', 'неизвестно')}, "
                f"а выбранный рейд относится к зоне {expected_zone}."
            )
        valid_encounters = {e["id"]: e["name"] for e in (report["zone"].get("encounters") or [])}
        kills = []
        for fight in report.get("fights") or []:
            if not fight.get("kill"):
                continue
            if fight.get("difficulty") not in (None, difficulty_id):
                continue
            eid = fight.get("encounterID")
            if eid in valid_encounters:
                kills.append({"id": eid, "name": valid_encounters[eid], "fight_id": fight["id"]})
        unique = {}
        for k in kills:
            unique[k["id"]] = k
        return {
            "code": code,
            "title": report["title"],
            "zone": report["zone"]["name"],
            "kills": list(unique.values()),
            "total": len(unique),
            "url": f"https://www.warcraftlogs.com/reports/{code}",
        }

def extract_report_code(url: str) -> Optional[str]:
    m = re.search(r"/reports/([A-Za-z0-9]+)", url.strip())
    return m.group(1) if m else None

db = DB(DATABASE_PATH)
wcl = WCLClient()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

def normalize_boss_name(value: Any) -> str:
    """Return a clean boss name from WCL strings or encounter dictionaries.

    WCL may return encounter as {"id": ..., "name": ...}; older bot versions
    could also have stored that dict representation in SQLite.
    """
    if isinstance(value, dict):
        return str(value.get("name") or value.get("boss") or "Босс").strip()
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{"):
            try:
                parsed = ast.literal_eval(text)
                if isinstance(parsed, dict):
                    return str(parsed.get("name") or parsed.get("boss") or "Босс").strip()
            except (ValueError, SyntaxError):
                pass
        return text or "Босс"
    return str(value or "Босс").strip()


def make_player_dict(row: sqlite3.Row) -> dict:
    d = dict(row)

    # Older versions could accidentally save the whole WCL character object
    # into character_name. Normalize that data so old raid records render too.
    raw_name = d.get("character_name")
    if isinstance(raw_name, dict):
        d["character_name"] = raw_name.get("name") or raw_name.get("character_name") or "Неизвестный персонаж"
    elif isinstance(raw_name, str) and raw_name.lstrip().startswith("{"):
        try:
            parsed = ast.literal_eval(raw_name)
            if isinstance(parsed, dict):
                d["character_name"] = parsed.get("name") or parsed.get("character_name") or raw_name
        except (ValueError, SyntaxError):
            pass

    try:
        raw_bosses = json.loads(d.get("bosses_json") or "[]")
        d["bosses"] = []
        for boss in raw_bosses if isinstance(raw_bosses, list) else []:
            if isinstance(boss, dict):
                boss_copy = dict(boss)
                boss_copy["boss"] = normalize_boss_name(boss_copy.get("boss") or boss_copy.get("encounter"))
                d["bosses"].append(boss_copy)
    except (json.JSONDecodeError, TypeError):
        d["bosses"] = []
    return d

async def build_raid_embed(raid: sqlite3.Row, players: list[dict]) -> discord.Embed:
    """Build a compact public raid card close to the requested reference layout."""
    info = RAIDS[raid["raid_id"]]
    guild = bot.get_guild(int(raid["guild_id"])) if raid["guild_id"] else None
    try:
        db.rebalance_groups_if_needed(raid["channel_id"])
        players = [make_player_dict(r) for r in db.get_players(raid["channel_id"])]
    except Exception as e:
        print(f"[group migration] {e}")
    confirmed = [p for p in players if p.get("status") == "confirmed"]

    avg_values = [percentile_number(p.get("avg_parse")) for p in confirmed]
    avg_values = [v for v in avg_values if v is not None]
    avg = sum(avg_values) / len(avg_values) if avg_values else None

    roles = {"Танк": 0, "Хил": 0, "ДД": 0}
    for p in confirmed:
        role = p.get("role", "ДД")
        roles[role] = roles.get(role, 0) + 1

    embed = discord.Embed(
        title=raid["name"],
        url=info.get("wowhead_url") or info.get("url"),
        color=parse_color(avg),
    )

    avg_text = f"{parse_emoji(avg)} **{avg:.0f}**" if avg is not None else "—"
    embed.description = (
        f"`{raid['difficulty_name']}` · **{raid['date_text']}**\n"
        f"**Средний лог рейда:** {avg_text}\n"
        f"**Рейд:** `{info['name']}` · {info.get('name_en', '')}"
    )

    # Compact boss summary: one line per boss, like the reference screenshot.
    boss_values: dict[str, list[float]] = {}
    boss_order: list[str] = []
    for p in confirmed:
        for boss in p.get("bosses", []):
            boss_name = normalize_boss_name(boss.get("boss") or boss.get("encounter"))
            percentile = percentile_number(boss.get("percentile"))
            if boss_name not in boss_values:
                boss_values[boss_name] = []
                boss_order.append(boss_name)
            if percentile is not None:
                boss_values[boss_name].append(percentile)

    if boss_order:
        boss_lines = []
        for boss_name in boss_order:
            vals = boss_values[boss_name]
            boss_avg = sum(vals) / len(vals) if vals else None
            boss_lines.append(
                f"{parse_emoji(boss_avg)} **{boss_avg:.0f}** {boss_name}"
                if boss_avg is not None else f"⬜ **—** {boss_name}"
            )
        # Keep the field readable even for long raid names.
        embed.add_field(name="Опыт рейда", value="\n".join(boss_lines), inline=False)
    else:
        embed.add_field(name="Опыт рейда", value="Пока нет данных Warcraft Logs по боссам.", inline=False)

    # Leader is shown BEFORE the approved roster. Prefer the stored Discord display name.
    leader_name = raid["leader_name"] if "leader_name" in raid.keys() else None
    leader_id = int(raid["leader_id"]) if raid["leader_id"] else None

    # Resolve and cache the leader name, but display the actual Discord mention.
    if not leader_name and guild and leader_id:
        leader_member = guild.get_member(leader_id)
        if leader_member is None:
            try:
                leader_member = await guild.fetch_member(leader_id)
            except (discord.NotFound, discord.HTTPException, discord.Forbidden):
                leader_member = None
        if leader_member:
            leader_name = leader_member.display_name
            try:
                db.set_leader_name(raid["channel_id"], leader_name)
            except Exception:
                pass

    # <@ID> is a real clickable Discord mention, not plain text.
    leader_mention = f"<@{leader_id}>" if leader_id else (leader_name or "Неизвестно")

    embed.add_field(
        name="Рейд лидер",
        value=leader_mention,
        inline=False,
    )

    embed.add_field(
        name=f"Утвержденный состав ({len(confirmed)}/{RAID_LIMIT})",
        value=(
            f"{ROLE_EMOJI['Танк']} **{roles['Танк']}**  "
            f"{ROLE_EMOJI['Хил']} **{roles['Хил']}**  "
            f"{ROLE_EMOJI['ДД']} **{roles['ДД']}**"
        ),
        inline=False,
    )


    # Six groups, five players each. Force two groups per row with a blank inline field.
    group_fields = {}
    for group_no in range(1, 7):
        group = [p for p in confirmed if int(p.get("group_no") or 1) == group_no]
        lines = []
        for p in group:
            pval = percentile_number(p.get("avg_parse"))
            parse_text = f"{pval:.0f}" if pval is not None else "—"
            icons = player_icons(guild, p)
            name = str(p.get("character_name") or "Неизвестный")
            if p.get("profile_url"):
                name = f"[{name}]({p['profile_url']})"
            lines.append(f"{icons} **{parse_text}** {name}")
        group_fields[group_no] = (f"Группа {group_no}", "\n".join(lines) or "—")

    for left in (1, 3, 5):
        right = left + 1
        left_name, left_value = group_fields[left]
        right_name, right_value = group_fields[right]
        embed.add_field(name=left_name, value=left_value, inline=True)
        embed.add_field(name=right_name, value=right_value, inline=True)
        # Discord lays inline fields in rows of three; this spacer forces the next pair to a new row.
        embed.add_field(name="\u200b", value="\u200b", inline=True)

    # Show the remaining capacity without a wall of empty "Группа" fields.
    free_slots = max(0, RAID_LIMIT - len(confirmed))
    embed.add_field(
        name="Свободные места",
        value=f"**{free_slots}** из **{RAID_LIMIT}**",
        inline=False,
    )

    # Only the remaining status buttons are shown. The old "Не смогу"
    # status is no longer exposed; cancellation removes the player entirely.
    status_sections = []
    for status, label, emoji in ((
        ("unsure", "НЕ УВЕРЕН", "⚖️"),
        ("late", "ОПОЗДАЮ", "🕐"),
    )):
        users = [f"<@{p['discord_id']}>" for p in players if p.get("status") == status]
        if users:
            status_sections.append(f"{emoji} **{label} ({len(users)})**\n" + " ".join(users))

    status_value = "\n\n".join(status_sections) if status_sections else "Нет заявленных статусов."
    if raid["raid_log"]:
        code = extract_report_code(raid["raid_log"])
        log_url = f"{WCL_SITE}/reports/{code}" if code else raid["raid_log"]
        status_value += f"\n\n📊 **Лог рейда:** [Открыть отчёт]({log_url})"

    embed.add_field(name="Статусы и лог", value=status_value, inline=False)

    image_url = RAID_IMAGES.get(raid["raid_id"])
    if image_url:
        embed.set_image(url=image_url)

    embed.set_footer(text=f"{info['name']} · Warcraft Logs · /raid_refresh для обновления парсов")
    return embed


def raid_view():
    return RaidView()

async def refresh_raid_message(channel: discord.abc.Messageable, channel_id: int):
    raid = db.get_raid(channel_id)
    if not raid or not raid["message_id"]:
        return
    players = [make_player_dict(r) for r in db.get_players(channel_id)]
    try:
        msg = await channel.fetch_message(raid["message_id"])
    except Exception:
        return
    await msg.edit(
        embed=await build_raid_embed(raid, players),
        view=raid_view_with_log(raid["raid_log"]),
    )

class RaidSetupModal(discord.ui.Modal, title="Настройка рейда"):
    raid_name = discord.ui.TextInput(label="Название рейда", placeholder="Алорика", max_length=60)
    date_text = discord.ui.TextInput(label="Дата и время", placeholder="Сегодня 21:00", max_length=80)

    def __init__(self, raid_id: str, difficulty_name: str):
        super().__init__()
        self.raid_id = raid_id
        self.difficulty_name = difficulty_name

    async def on_submit(self, interaction: discord.Interaction):
        if db.get_raid(interaction.channel_id):
            await interaction.response.send_message("⚠️ В этом канале уже есть активный рейд.", ephemeral=True)
            return
        diff_id = DIFFICULTIES[self.difficulty_name]
        db.create_raid({
            "channel_id": interaction.channel_id,
            "guild_id": interaction.guild_id or 0,
            "name": self.raid_name.value.strip(),
            "date_text": self.date_text.value.strip(),
            "raid_id": self.raid_id,
            "difficulty_name": self.difficulty_name,
            "difficulty_id": diff_id,
            "leader_id": interaction.user.id,
            "leader_name": interaction.user.display_name,
        })
        await interaction.response.send_message(
            content="@here",
            embed=await build_raid_embed(db.get_raid(interaction.channel_id), []),
            view=raid_view_with_log(),
            allowed_mentions=discord.AllowedMentions(everyone=True),
        )
        msg = await interaction.original_response()
        db.set_message(interaction.channel_id, msg.id)

class RaidSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=v["name"], value=k, description=v["name_en"][:100], emoji="⚔️")
            for k, v in RAIDS.items()
        ]
        super().__init__(placeholder="Выберите рейд", options=options, custom_id="raid_select")

    async def callback(self, interaction: discord.Interaction):
        view = DifficultySelectView(self.values[0])
        await interaction.response.edit_message(
            content=f"**{RAIDS[self.values[0]]['name']}**\nВыберите сложность:",
            view=view
        )

class DifficultySelect(discord.ui.Select):
    def __init__(self, raid_id: str):
        self.raid_id = raid_id
        options = [
            discord.SelectOption(label=name, value=name, emoji="🎯")
            for name in DIFFICULTIES
        ]
        super().__init__(placeholder="Выберите сложность", options=options, custom_id="difficulty_select")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RaidSetupModal(self.raid_id, self.values[0]))

class DifficultySelectView(discord.ui.View):
    def __init__(self, raid_id: str):
        super().__init__(timeout=180)
        self.add_item(DifficultySelect(raid_id))

class RaidCreateView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(RaidSelect())

class CharacterModal(discord.ui.Modal, title="Запись в рейд"):
    character_name = discord.ui.TextInput(label="Имя персонажа", placeholder="Имя персонажа", max_length=30)
    realm = discord.ui.TextInput(
        label="Реалм",
        placeholder="howling-fjord или Ревущий фьорд",
        default=WCL_DEFAULT_REALM,
        max_length=40
    )

    def __init__(self, channel_id: int, class_name: str, spec_name: str, replace_existing: bool = False):
        super().__init__()
        self.channel_id = channel_id
        self.class_name = class_name
        self.spec_name = spec_name
        self.replace_existing = replace_existing

    async def on_submit(self, interaction: discord.Interaction):
        raid = db.get_raid(self.channel_id)
        if not raid or raid["closed"]:
            await interaction.response.send_message("❌ Рейд уже закрыт.", ephemeral=True)
            return
        old = db.get_player(self.channel_id, interaction.user.id)
        confirmed = sum(1 for r in db.get_players(self.channel_id) if r["status"] == "confirmed")
        # Replacing your own character does not consume an additional slot.
        if confirmed >= RAID_LIMIT and not (old and old["status"] == "confirmed"):
            await interaction.response.send_message("❌ Рейд уже заполнен.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        cd = CLASS_SPECS[self.class_name]
        wcl_spec = cd["specs"][self.spec_name]
        try:
            result = await wcl.character(
                self.character_name.value.strip(),
                self.realm.value.strip(),
                WCL_REGION,
                raid["raid_id"],
                raid["difficulty_id"],
                cd["wcl"],
                wcl_spec,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Ошибка Warcraft Logs: `{str(e)[:1500]}`", ephemeral=True)
            return

        if not result.get("found"):
            await interaction.followup.send(
                f"❌ {result.get('error', 'Персонаж не найден.')}\n"
                "Проверь имя и реалм.",
                ephemeral=True
            )
            return

        group_no = int(old["group_no"] or 1) if old else 1
        if not old:
            for candidate in range(1, 7):
                if len(db.get_group_players(self.channel_id, candidate)) < 5:
                    group_no = candidate
                    break

        db.upsert_player({
            "channel_id": self.channel_id,
            "discord_id": interaction.user.id,
            "discord_name": interaction.user.display_name,
            "character_name": result["character_name"],
            "realm": result["server_slug"],
            "region": WCL_REGION,
            "class_name": self.class_name,
            "wcl_class": cd["wcl"],
            "spec_name": self.spec_name,
            "wcl_spec": wcl_spec,
            "role": role_for_spec(wcl_spec),
            "status": "confirmed",
            "avg_parse": result.get("avg_parse"),
            "best_parse": result.get("best_parse"),
            "bosses": result.get("bosses", []),
            "profile_url": result.get("profile_url"),
            "group_no": group_no,
        })
        channel = interaction.channel
        await refresh_raid_message(channel, self.channel_id)

        p = result.get("avg_parse")
        text = (
            f"✅ **{result['character_name']}** записан.\n"
            f"{cd['emoji']} {self.class_name} · {self.spec_name}\n"
            f"📈 Средний лог: **{p:.0f}**" if p is not None else
            f"✅ **{result['character_name']}** {action_text}.\n{cd['emoji']} {self.class_name} · {self.spec_name}\n📈 Лог: `—`"
        )
        await interaction.followup.send(text, ephemeral=True)

class ClassSelect(discord.ui.Select):
    def __init__(self, channel_id: int, guild_id: Optional[int] = None, replace_existing: bool = False):
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.replace_existing = replace_existing
        guild = bot.get_guild(guild_id) if guild_id else None
        options = [
            discord.SelectOption(
                label=name,
                value=name,
                emoji=select_emoji(guild, name),
            )
            for name in CLASS_SPECS
        ]
        super().__init__(placeholder="Выберите класс", options=options, custom_id="class_select")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content=f"Выбран класс **{self.values[0]}**. Выберите специализацию:",
            view=SpecSelectView(self.channel_id, self.values[0], self.guild_id, self.replace_existing)
        )

class SpecSelect(discord.ui.Select):
    def __init__(self, channel_id: int, class_name: str, guild_id: Optional[int] = None, replace_existing: bool = False):
        self.channel_id = channel_id
        self.class_name = class_name
        self.guild_id = guild_id
        self.replace_existing = replace_existing
        guild = bot.get_guild(guild_id) if guild_id else None
        options = [
            discord.SelectOption(
                label=spec,
                value=spec,
                emoji=select_emoji(guild, class_name, wcl_spec),
            )
            for spec, wcl_spec in CLASS_SPECS[class_name]["specs"].items()
        ]
        super().__init__(placeholder="Выберите специализацию", options=options, custom_id="spec_select")

    async def callback(self, interaction: discord.Interaction):
        # Important: a modal must be sent as the interaction response itself.
        await interaction.response.send_modal(
            CharacterModal(self.channel_id, self.class_name, self.values[0], self.replace_existing)
        )

class ClassSelectView(discord.ui.View):
    def __init__(self, channel_id: int, guild_id: Optional[int] = None, replace_existing: bool = False):
        super().__init__(timeout=180)
        self.add_item(ClassSelect(channel_id, guild_id, replace_existing))

class SpecSelectView(discord.ui.View):
    def __init__(self, channel_id: int, class_name: str, guild_id: Optional[int] = None, replace_existing: bool = False):
        super().__init__(timeout=180)
        self.add_item(SpecSelect(channel_id, class_name, guild_id, replace_existing))

class JoinButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Запись", style=discord.ButtonStyle.primary, emoji="👥", custom_id="raid_join")

    async def callback(self, interaction: discord.Interaction):
        raid = db.get_raid(interaction.channel_id)
        if not raid or raid["closed"]:
            await interaction.response.send_message("❌ Рейд закрыт.", ephemeral=True)
            return
        existing = db.get_player(interaction.channel_id, interaction.user.id)
        confirmed = sum(1 for r in db.get_players(interaction.channel_id) if r["status"] == "confirmed")
        if confirmed >= RAID_LIMIT and not (existing and existing["status"] == "confirmed"):
            await interaction.response.send_message("❌ Рейд заполнен.", ephemeral=True)
            return
        if existing:
            await interaction.response.send_message(
                "Вы уже записаны. Выберите нового персонажа — текущая запись будет заменена:",
                view=ClassSelectView(interaction.channel_id, interaction.guild_id, replace_existing=True),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Выберите класс:",
            view=ClassSelectView(interaction.channel_id, interaction.guild_id),
            ephemeral=True,
        )

class StatusButton(discord.ui.Button):
    def __init__(self, status: str, label: str, emoji: str, style=discord.ButtonStyle.secondary):
        super().__init__(label=label, style=style, emoji=emoji, custom_id=f"raid_status_{status}")
        self.status = status

    async def callback(self, interaction: discord.Interaction):
        raid = db.get_raid(interaction.channel_id)
        if not raid or raid["closed"]:
            await interaction.response.send_message("❌ Рейд закрыт.", ephemeral=True)
            return
        player = db.get_player(interaction.channel_id, interaction.user.id)
        if not player:
            await interaction.response.send_message(
                "Сначала нажми **Запись** и добавь персонажа.", ephemeral=True
            )
            return
        if self.status == "cant":
            db.set_status(interaction.channel_id, interaction.user.id, "cant")
        else:
            db.set_status(interaction.channel_id, interaction.user.id, self.status)
        await refresh_raid_message(interaction.channel, interaction.channel_id)
        await interaction.response.send_message(
            f"Статус изменён: {STATUS_LABELS[self.status][1]} **{STATUS_LABELS[self.status][0]}**",
            ephemeral=True
        )

class CancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Отмена записи",
            style=discord.ButtonStyle.danger,
            emoji="🚫",
            custom_id="raid_cancel",
        )

    async def callback(self, interaction: discord.Interaction):
        raid = db.get_raid(interaction.channel_id)
        if not raid or raid["closed"]:
            await interaction.response.send_message("❌ Рейд закрыт.", ephemeral=True)
            return

        player = db.get_player(interaction.channel_id, interaction.user.id)
        if not player:
            await interaction.response.send_message("Вы не записаны в этот рейд.", ephemeral=True)
            return

        db.remove_player(interaction.channel_id, interaction.user.id)
        await refresh_raid_message(interaction.channel, interaction.channel_id)
        await interaction.response.send_message("✅ Ваша запись отменена.", ephemeral=True)


class GroupSourceSelect(discord.ui.Select):
    def __init__(self, channel_id: int):
        self.channel_id = channel_id
        options = [discord.SelectOption(label=f"Группа {i}", value=str(i), emoji="👥") for i in range(1, 7)]
        super().__init__(placeholder="Выберите группу игрока", options=options, custom_id="raid_group_source")

    async def callback(self, interaction: discord.Interaction):
        group_no = int(self.values[0])
        players = db.get_group_players(self.channel_id, group_no)
        if not players:
            await interaction.response.edit_message(content=f"Группа {group_no} пуста.", view=GroupSourceView(self.channel_id))
            return
        options = []
        for p in players:
            label = str(p["character_name"])[:100]
            options.append(discord.SelectOption(label=label, value=str(p["discord_id"]), description=f"{p['class_name']} · {p['spec_name']}"[:100]))
        await interaction.response.edit_message(
            content=f"**Группа {group_no}** — выберите игрока для перемещения:",
            view=GroupPlayerView(self.channel_id, group_no, options),
        )

class GroupSourceView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=180)
        self.add_item(GroupSourceSelect(channel_id))

class GroupPlayerSelect(discord.ui.Select):
    def __init__(self, channel_id: int, source_group: int, options):
        self.channel_id = channel_id
        self.source_group = source_group
        super().__init__(placeholder="Выберите игрока", options=options, custom_id=f"raid_group_player_{source_group}")

    async def callback(self, interaction: discord.Interaction):
        player_id = int(self.values[0])
        player = db.get_player(self.channel_id, player_id)
        if not player:
            await interaction.response.edit_message(content="❌ Игрок уже не найден.", view=GroupSourceView(self.channel_id))
            return
        await interaction.response.edit_message(
            content=f"Перемещаем **{player['character_name']}**. Выберите новую группу:",
            view=GroupDestinationView(self.channel_id, player_id, self.source_group),
        )

class GroupPlayerView(discord.ui.View):
    def __init__(self, channel_id: int, source_group: int, options):
        super().__init__(timeout=180)
        self.add_item(GroupPlayerSelect(channel_id, source_group, options))

class GroupDestinationSelect(discord.ui.Select):
    def __init__(self, channel_id: int, player_id: int, source_group: int):
        self.channel_id = channel_id
        self.player_id = player_id
        self.source_group = source_group
        options = [
            discord.SelectOption(label=f"Группа {i}", value=str(i), emoji="👥", default=(i == source_group))
            for i in range(1, 7)
        ]
        super().__init__(placeholder="Выберите новую группу", options=options, custom_id=f"raid_group_dest_{player_id}")

    async def callback(self, interaction: discord.Interaction):
        destination = int(self.values[0])
        player = db.get_player(self.channel_id, self.player_id)
        if not player:
            await interaction.response.edit_message(content="❌ Игрок уже не найден.", view=GroupSourceView(self.channel_id))
            return
        if destination != self.source_group and len(db.get_group_players(self.channel_id, destination)) >= 5:
            await interaction.response.edit_message(
                content=f"❌ Группа {destination} уже заполнена (5/5). Выберите другую:",
                view=GroupDestinationView(self.channel_id, self.player_id, self.source_group),
            )
            return
        db.set_group(self.channel_id, self.player_id, destination)
        channel = interaction.channel
        await refresh_raid_message(channel, self.channel_id)
        await interaction.response.edit_message(
            content=f"✅ **{player['character_name']}** перемещён в **Группу {destination}**.",
            view=GroupSourceView(self.channel_id),
        )

class GroupDestinationView(discord.ui.View):
    def __init__(self, channel_id: int, player_id: int, source_group: int):
        super().__init__(timeout=180)
        self.add_item(GroupDestinationSelect(channel_id, player_id, source_group))

class GroupManageButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Группы", style=discord.ButtonStyle.secondary, emoji="👥", custom_id="raid_groups")

    async def callback(self, interaction: discord.Interaction):
        raid = db.get_raid(interaction.channel_id)
        if not raid or raid["closed"]:
            await interaction.response.send_message("❌ Рейд закрыт.", ephemeral=True)
            return
        if raid["leader_id"] != interaction.user.id and not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ Только лидер рейда или модератор может менять группы.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Выберите группу, из которой нужно переместить игрока:",
            view=GroupSourceView(interaction.channel_id),
            ephemeral=True,
        )

class WebButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Web", style=discord.ButtonStyle.link, emoji="🌐", url=WCL_SITE)

class RaidView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(JoinButton())
        self.add_item(CancelButton())
        self.add_item(StatusButton("unsure", "Не уверен", "⚖️"))
        self.add_item(StatusButton("late", "Опоздаю", "🕐"))
        self.add_item(GroupManageButton())
        self.add_item(WebButton())

class AddLogModal(discord.ui.Modal, title="Добавить Warcraft Logs"):
    url = discord.ui.TextInput(
        label="Ссылка на отчёт",
        placeholder="https://www.warcraftlogs.com/reports/XXXXXXXX",
        max_length=200
    )
    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        raid = db.get_raid(self.channel_id)
        if not raid:
            await interaction.response.send_message("❌ Рейд не найден.", ephemeral=True)
            return
        if raid["leader_id"] != interaction.user.id and not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ Только лидер рейда или модератор.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await wcl.report_for_raid(self.url.value, raid["raid_id"], raid["difficulty_id"])
            db.set_log(self.channel_id, result["url"])
            await refresh_raid_message(interaction.channel, self.channel_id)
            await interaction.followup.send(
                f"✅ Лог добавлен: **{result['total']}** уникальных убийств.\n{result['url']}",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ {str(e)[:1500]}", ephemeral=True)

class OpenLogButton(discord.ui.Button):
    def __init__(self, url: str):
        super().__init__(
            label="Открыть лог",
            style=discord.ButtonStyle.link,
            emoji="📊",
            url=url,
        )

class AddLogButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Добавить лог", style=discord.ButtonStyle.secondary, emoji="📊", custom_id="raid_add_log")
    async def callback(self, interaction: discord.Interaction):
        raid = db.get_raid(interaction.channel_id)
        if not raid:
            await interaction.response.send_message("❌ Рейд не найден.", ephemeral=True)
            return
        if raid["leader_id"] != interaction.user.id and not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ Только лидер рейда или модератор.", ephemeral=True)
            return
        await interaction.response.send_modal(AddLogModal(interaction.channel_id))

# Rebuild view with optional leader-only log button.
# Kept separate so the screenshot-like public row stays compact.
def raid_view_with_log(log_url: Optional[str] = None):
    view = RaidView()
    view.add_item(AddLogButton())
    if log_url:
        code = extract_report_code(log_url)
        if code:
            view.add_item(OpenLogButton(f"https://www.warcraftlogs.com/reports/{code}"))
    return view

@bot.tree.command(name="raid_create", description="Создать рейд Midnight")
async def raid_create(interaction: discord.Interaction):
    if db.get_raid(interaction.channel_id):
        await interaction.response.send_message("⚠️ В этом канале уже есть активный рейд.", ephemeral=True)
        return
    await interaction.response.send_message("🏛️ **Выберите рейд:**", view=RaidCreateView())

@bot.tree.command(name="raid_close", description="Закрыть набор")
async def raid_close(interaction: discord.Interaction):
    raid = db.get_raid(interaction.channel_id)
    if not raid:
        await interaction.response.send_message("❌ Активного рейда нет.", ephemeral=True)
        return
    if raid["leader_id"] != interaction.user.id and not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ Только лидер рейда или модератор.", ephemeral=True)
        return
    db.close_raid(interaction.channel_id)
    players = [make_player_dict(r) for r in db.get_players(interaction.channel_id)]
    msg = await interaction.channel.fetch_message(raid["message_id"])
    await msg.edit(
        embed=await build_raid_embed(raid, players),
        view=None
    )
    await interaction.response.send_message("🔒 Набор закрыт.", ephemeral=True)

@bot.tree.command(name="raid_remove", description="Уйти из рейда")
async def raid_remove(interaction: discord.Interaction):
    player = db.get_player(interaction.channel_id, interaction.user.id)
    if not player:
        await interaction.response.send_message("Вы не записаны.", ephemeral=True)
        return
    db.remove_player(interaction.channel_id, interaction.user.id)
    await refresh_raid_message(interaction.channel, interaction.channel_id)
    await interaction.response.send_message("✅ Вы удалены из состава.", ephemeral=True)

@bot.tree.command(name="raid_log", description="Добавить/обновить Warcraft Logs отчёт")
@app_commands.describe(url="Ссылка на Warcraft Logs report")
async def raid_log(interaction: discord.Interaction, url: str):
    raid = db.get_raid(interaction.channel_id)
    if not raid:
        await interaction.response.send_message("❌ Активного рейда нет.", ephemeral=True)
        return
    if raid["leader_id"] != interaction.user.id and not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ Только лидер рейда или модератор.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        result = await wcl.report_for_raid(url, raid["raid_id"], raid["difficulty_id"])
        db.set_log(interaction.channel_id, result["url"])
        await refresh_raid_message(interaction.channel, interaction.channel_id)
        await interaction.followup.send(
            f"✅ **{result['title']}**\nУникальных убийств: **{result['total']}**\n{result['url']}",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ {str(e)[:1500]}", ephemeral=True)

@bot.tree.command(name="raid_refresh", description="Обновить парсы всех записавшихся")
async def raid_refresh(interaction: discord.Interaction):
    raid = db.get_raid(interaction.channel_id)
    if not raid:
        await interaction.response.send_message("❌ Активного рейда нет.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    rows = db.get_players(interaction.channel_id)
    updated, failed = 0, 0
    for row in rows:
        try:
            cd = CLASS_SPECS[row["class_name"]]
            result = await wcl.character(
                row["character_name"], row["realm"], row["region"],
                raid["raid_id"], raid["difficulty_id"], cd["wcl"], row["wcl_spec"]
            )
            if result.get("found"):
                db.update_parses(interaction.channel_id, row["discord_id"], result)
                updated += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    await refresh_raid_message(interaction.channel, interaction.channel_id)
    await interaction.followup.send(f"✅ Обновлено: **{updated}**. Ошибок: **{failed}**.", ephemeral=True)

@bot.tree.command(name="parse", description="Проверить парсы персонажа в Midnight")
@app_commands.describe(name="Имя персонажа", realm="Реалм, например howling-fjord", raid="Рейд", difficulty="Сложность")
@app_commands.choices(
    raid=[app_commands.Choice(name=v["name"], value=k) for k,v in RAIDS.items()],
    difficulty=[app_commands.Choice(name=k, value=k) for k in DIFFICULTIES],
)
async def parse_cmd(
    interaction: discord.Interaction,
    name: str,
    realm: str,
    raid: app_commands.Choice[str],
    difficulty: app_commands.Choice[str],
):
    await interaction.response.send_message("🔍 Запрашиваю Warcraft Logs…", ephemeral=True)
    # No class/spec supplied: fetch general zone rankings. This avoids guessing the player's spec.
    info = RAIDS[raid.value]
    q = """
    query Character($name:String!, $server:String!, $region:String!, $zone:Int!, $difficulty:Int!) {
      characterData {
        character(name:$name, serverSlug:$server, serverRegion:$region) {
          name
          server { name slug }
          zoneRankings(zoneID:$zone, difficulty:$difficulty)
        }
      }
    }
    """
    try:
        data = await wcl.graphql(q, {
            "name": name.strip(), "server": normalize_realm(realm),
            "region": WCL_REGION, "zone": info["zone_id"],
            "difficulty": DIFFICULTIES[difficulty.value]
        })
        char = (data.get("characterData") or {}).get("character")
        if not char:
            await interaction.edit_original_response(content="❌ Персонаж не найден.")
            return
        rankings = char.get("zoneRankings") or {}
        if isinstance(rankings, str):
            rankings = json.loads(rankings)
        vals = [
            percentile_number(x.get("rankPercent", x.get("percentile")))
            for x in (rankings.get("rankings") or [])
        ]
        vals = [x for x in vals if x is not None]
        avg = rankings.get("medianPerformance") or rankings.get("averagePerformance")
        if avg is None and vals:
            avg = sum(vals)/len(vals)
        embed = discord.Embed(
            title=f"📊 {char['name']} · {info['name']}",
            color=parse_color(percentile_number(avg))
        )
        embed.add_field(name="Средний лог", value=f"**{float(avg):.0f}**" if avg is not None else "—", inline=True)
        embed.add_field(name="Рейд", value=difficulty.name, inline=True)
        embed.add_field(name="Реалм", value=char["server"]["name"], inline=True)
        if rankings.get("allStars"):
            stars = rankings["allStars"]
            embed.add_field(
                name="All Stars",
                value=f"{stars.get('points','—')} очков · место {stars.get('rank','—')}",
                inline=False
            )
        await interaction.edit_original_response(content=None, embed=embed)
    except Exception as e:
        await interaction.edit_original_response(content=f"❌ WCL: {str(e)[:1500]}")

@bot.tree.command(name="emoji_status", description="Проверить WoW-эмодзи классов и специализаций")
async def emoji_status(interaction: discord.Interaction):
    missing_classes = []
    missing_specs = []

    for data in CLASS_SPECS.values():
        emoji_name = CLASS_EMOJI_NAMES.get(data["wcl"])
        if emoji_name and not discord.utils.get(interaction.guild.emojis, name=emoji_name):
            missing_classes.append(emoji_name)
        for spec, _wcl_spec in data["specs"].items():
            emoji_name = SPEC_EMOJI_NAMES.get(spec)
            if emoji_name and not discord.utils.get(interaction.guild.emojis, name=emoji_name):
                missing_specs.append(emoji_name)

    lines = [
        "### Эмодзи классов",
        "✅ Все загружены." if not missing_classes else "❌ Не хватает: " + ", ".join(missing_classes),
        "",
        "### Эмодзи специализаций",
        "✅ Все загружены." if not missing_specs else "❌ Не хватает: " + ", ".join(missing_specs),
    ]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

@bot.tree.command(name="wcl_status", description="Проверить подключение Warcraft Logs API")
async def wcl_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = await wcl.status()
        zone = (data.get("worldData") or {}).get("zone")
        rate = data.get("rateLimitData") or {}
        text = (
            f"✅ **Warcraft Logs API подключён**\n"
            f"Зона 46: **{zone['name'] if zone else 'не найдена'}**\n"
            f"Боссов: **{len(zone.get('encounters', [])) if zone else 0}**\n"
            f"API points: **{rate.get('pointsSpentThisHour','—')} / {rate.get('limitPerHour','—')}**"
        )
        await interaction.followup.send(text, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ WCL API: `{str(e)[:1500]}`", ephemeral=True)

@bot.tree.command(name="raid_clear", description="Удалить рейд из базы")
async def raid_clear(interaction: discord.Interaction):
    raid = db.get_raid(interaction.channel_id)
    if not raid:
        await interaction.response.send_message("❌ Рейда нет.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ Нужны права Manage Server.", ephemeral=True)
        return
    db.delete_raid(interaction.channel_id)
    await interaction.response.send_message("🧹 Рейд удалён из базы.", ephemeral=True)

@tasks.loop(minutes=REFRESH_MINUTES)
async def auto_refresh():
    for raid in db.active_raids():
        channel = bot.get_channel(raid["channel_id"])
        if not channel:
            continue
        rows = db.get_players(raid["channel_id"])
        for row in rows:
            if row["status"] == "cant":
                continue
            try:
                cd = CLASS_SPECS[row["class_name"]]
                result = await wcl.character(
                    row["character_name"], row["realm"], row["region"],
                    raid["raid_id"], raid["difficulty_id"], cd["wcl"], row["wcl_spec"]
                )
                if result.get("found"):
                    db.update_parses(raid["channel_id"], row["discord_id"], result)
            except Exception as e:
                print(f"[WCL refresh] {row['character_name']}: {e}")
        try:
            await refresh_raid_message(channel, raid["channel_id"])
        except Exception as e:
            print(f"[message refresh] {e}")

async def _setup_hook():
    await wcl.start()
    await load_raid_images()
    bot.add_view(raid_view_with_log())
    # Refresh active raid messages once on startup so old button layouts
    # (including the removed "Не смогу" button) are replaced immediately.
    for raid in db.active_raids():
        channel = bot.get_channel(raid["channel_id"])
        if channel:
            try:
                await refresh_raid_message(channel, raid["channel_id"])
            except Exception as e:
                print(f"[startup refresh] {raid['channel_id']}: {e}")
    if DISCORD_GUILD_ID:
        guild = discord.Object(id=int(DISCORD_GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"🔄 Slash-команды синхронизированы с guild {DISCORD_GUILD_ID}")
    else:
        await bot.tree.sync()
        print("🔄 Глобальные slash-команды синхронизированы.")

bot.setup_hook = _setup_hook

@bot.event
async def on_ready():
    print(f"✅ {bot.user} запущен.")
    print(f"   Midnight raids: {', '.join(RAIDS)}")
    print(f"   WCL API: {WCL_API}")
    if not auto_refresh.is_running():
        auto_refresh.start()

async def runner():
    try:
        await bot.start(DISCORD_TOKEN)
    finally:
        await wcl.close()

if __name__ == "__main__":
    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass
