"""
╔══════════════════════════════════════════════════════════════╗
║              DISCORD SHOP BOT – PHIÊN BẢN 2                 ║
╠══════════════════════════════════════════════════════════════╣
║  YÊU CẦU:                                                   ║
║  1. Discord Bot Token → đặt vào biến TOKEN bên dưới         ║
║  2. Server ID → đặt vào GUILD_ID (để sync lệnh nhanh)       ║
║  3. pip install discord.py flask pymongo dnspython aiohttp   ║
║     pip install chat-exporter                                ║
║  4. (Tuỳ chọn) MONGO_URI → lưu dữ liệu vĩnh viễn           ║
║     Không có MongoDB → tự lưu vào shop_data.json            ║
║                                                              ║
║  QR BANK: Dùng VietQR.io (MIỄN PHÍ, không cần API key)      ║
║                                                              ║
║  TÍNH NĂNG MỚI v2:                                           ║
║  • /rate – tính giá Robux theo bank/card/web                 ║
║  • /chinhrate – admin chỉnh rate + ảnh banner                ║
║  • Ticket Order Acc LQ/FF (nút riêng trong store)            ║
║  • Nút ⭐ đánh giá tự hiện khi Admin/CTV bấm "Hoàn Thành"    ║
║  • /setfeedback – admin chọn kênh nhận đánh giá              ║
║  • /setticketlog – admin chọn kênh lưu lịch sử ticket        ║
║  • /setpingrole /tatpingrole – admin chọn Role ping khi có   ║
║    ticket mới (Mua Hàng / Order Acc)                         ║
║  • Tự lưu transcript (ảnh + tên user) khi đóng ticket        ║
╚══════════════════════════════════════════════════════════════╝
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import Button, View, Modal, TextInput, Select
import os, sys, asyncio, threading, time, uuid, random, json, re
from datetime import datetime, timedelta
from typing import Literal
from flask import Flask, request, jsonify
from pymongo import MongoClient
import chat_exporter
import io

# ╔══════════════════════════════════════════════════════════════╗
# ║                    CẤU HÌNH – SỬA Ở ĐÂY                    ║
# ╚══════════════════════════════════════════════════════════════╝

TOKEN    = os.environ.get("TOKEN", "")
GUILD_ID = int(os.environ.get("GUILD_ID", "0"))
MONGO_URI= os.environ.get("MONGO_URI", "")

KEEP_ALIVE_PORT = int(os.environ.get("PORT", 8080))

# ── Guild-lock / độc quyền ──
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

def _parse_guild_ids(raw: str) -> set:
    out = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out

ALLOWED_GUILD_IDS = _parse_guild_ids(os.environ.get("ALLOWED_GUILD_IDS", ""))
if GUILD_ID:
    ALLOWED_GUILD_IDS.add(GUILD_ID)

ROBUX_BASE_PRICE = 10.0

BANK_MAP = {
    "vietcombank": "970436", "vcb": "970436",
    "bidv": "970418",
    "vietinbank": "970415", "ctg": "970415",
    "agribank": "970405",
    "mb": "970422", "mbbank": "970422",
    "techcombank": "970407", "tcb": "970407",
    "tpbank": "970423", "tp": "970423",
    "vpbank": "970432", "vp": "970432",
    "acb": "970416",
    "sacombank": "970403", "stb": "970403",
    "ocb": "970448",
    "shb": "970443",
    "hdbank": "970437", "hdb": "970437",
    "msb": "970426",
    "vib": "970441",
    "scb": "970429",
    "oceanbank": "970414",
    "seabank": "970440",
    "baovietbank": "970438",
    "pvcombank": "970412",
    "namabank": "970428",
    "kienlongbank": "970452",
    "momo": "970454",
    "zalopay": "970454",
}

def get_bank_bin(name: str) -> str:
    return BANK_MAP.get(name.lower().replace(" ", "").replace("-", ""), name)

def get_icon(d, key: str, fallback: str = "🔘"):
    """
    Trả về emoji cho 1 nút (VD key='nitro') dạng discord.PartialEmoji, hiểu cả emoji
    thường lẫn emoji tuỳ chỉnh/động (<:name:id> hoặc <a:name:id>) đã lưu qua /seticon.
    """
    raw = (d.get("config", {}).get("icons", {}) or {}).get(key) or fallback
    try:
        return discord.PartialEmoji.from_str(raw)
    except Exception:
        return fallback

# ╔══════════════════════════════════════════════════════════════╗
# ║                   WEB KEEP-ALIVE                            ║
# ╚══════════════════════════════════════════════════════════════╝

_app = Flask(__name__)

@_app.route("/")
def home(): return f"<h2>✅ Shop Bot Online</h2><p>{fmt()}</p>"

@_app.route("/ping")
def ping(): return "pong", 200

def _flask(): _app.run(host="0.0.0.0", port=KEEP_ALIVE_PORT, use_reloader=False)
def keep_alive(): threading.Thread(target=_flask, daemon=True).start()

# ╔══════════════════════════════════════════════════════════════╗
# ║   WATCHDOG – TỰ PHÁT HIỆN & RESTART KHI EVENT LOOP BỊ TREO  ║
# ╚══════════════════════════════════════════════════════════════╝
# Flask chạy ở thread riêng nên UptimeRobot vẫn thấy "Up" kể cả khi
# luồng Discord đã "chết lâm sàng" (event loop bị block/treo, không
# tự reconnect được). Watchdog này chạy ở 1 thread OS độc lập, không
# phụ thuộc event loop của discord.py, nên vẫn hoạt động được ngay cả
# khi event loop chính bị treo hoàn toàn. Nếu quá lâu không thấy dấu
# hiệu "còn sống" từ phía bot, nó sẽ buộc thoát tiến trình (os._exit)
# để Render/host tự khởi động lại toàn bộ container từ đầu.

_last_alive_tick = time.time()
_WATCHDOG_TIMEOUT = 180  # giây không "tick" -> coi như event loop bị treo

def _watchdog_loop():
    while True:
        time.sleep(30)
        if time.time() - _last_alive_tick > _WATCHDOG_TIMEOUT:
            print(f"[{fmt()}] 🚨 [Watchdog] Không phản hồi quá {_WATCHDOG_TIMEOUT}s "
                  f"(event loop có thể bị treo) → buộc restart process!")
            os._exit(1)  # thoát cứng, để Render/host tự khởi động lại tiến trình mới

def start_watchdog(): threading.Thread(target=_watchdog_loop, daemon=True).start()

# ╔══════════════════════════════════════════════════════════════╗
# ║                   DATABASE                                  ║
# ╚══════════════════════════════════════════════════════════════╝

MONGO_OK, _col = False, None

if MONGO_URI:
    try:
        _mc = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        _mc.server_info()
        _col = _mc["shopbot"]["data"]
        MONGO_OK = True
        print("✅ MongoDB Atlas kết nối thành công")
    except Exception as e:
        print(f"⚠️  MongoDB lỗi: {e} — dùng shop_data.json")

FILE = "shop_data.json"

def _blank():
    return {
        "config": {
            "admin_role_id":      0,
            "caythue_role_id":    0,
            "robux_role_id":      0,
            "feedback_channel_id":0,
            "ticket_log_channel_id":0,
            "ticket_ping_role_id":0,
            # Rate Robux (VNĐ / 1 Robux) theo từng hình thức thanh toán
            "rate_bank":  100,
            "rate_card":  120,
            "rate_web":    90,
            # Ảnh banner hiện trong lệnh /rate (để trống = không có ảnh)
            "rate_banner_url": "",
            # Tài khoản ngân hàng nhận tiền của shop (dùng cho /naptienqr)
            "bank_name": "",
            "bank_account_number": "",
            "bank_account_name": "",
            # Icon (emoji) cho từng nút trên panel store — dùng /seticon để đổi sang emoji động thật của server
            "icons": {
                "mua_hang":  "🛍️",
                "order_acc": "🕹️",
                "support":   "❗",
                "robux1s":   "💎",
                "nitro":     "🚀",
                "capwall":   "🏞️",
            },
        },
        "shop_items":    {},
        "shop_groups":   {},
        "robux_items":   {},
        "robux_market":  {"price": ROBUX_BASE_PRICE, "updated": ""},
        "farming_items": {},
        "farming_groups":{},
        "nitro_items":   {},
        "capwall_items": {},
        "discount_codes":{},
        "users":         {},
        "orders":        {},
        "revenue_log":   [],
    }

def _doc_id(guild_id):
    """Mỗi server (guild) có 1 document/1 file riêng — dữ liệu hoàn toàn độc lập giữa các server."""
    return f"main_{guild_id}"

def _file_for(guild_id):
    return f"shop_data_{guild_id}.json"

def load(guild_id):
    if guild_id is None:
        raise ValueError("load() cần guild_id — dữ liệu được lưu riêng theo từng server.")
    if MONGO_OK:
        try:
            d = _col.find_one({"_id": _doc_id(guild_id)})
            if d:
                d.pop("_id", None)
                base = _blank(); base.update(d); return base
            return _blank()
        except Exception as e:
            print(f"[load err] {e}")
    try:
        with open(_file_for(guild_id), "r", encoding="utf-8") as f:
            base = _blank(); base.update(json.load(f)); return base
    except:
        return _blank()

def save(d, guild_id):
    if guild_id is None:
        raise ValueError("save() cần guild_id — dữ liệu được lưu riêng theo từng server.")
    if MONGO_OK:
        try:
            _col.replace_one({"_id": _doc_id(guild_id)}, {"_id": _doc_id(guild_id), **d}, upsert=True); return
        except Exception as e:
            print(f"[save err] {e}")
    try:
        with open(_file_for(guild_id), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[file err] {e}")

def fmt(dt=None):
    return (dt or datetime.now()).strftime("%d/%m/%Y %H:%M:%S")

def new_id(): return uuid.uuid4().hex[:8].upper()

def ensure_user(d, uid):
    uid = str(uid)
    if uid not in d["users"]:
        d["users"][uid] = {"balance":0,"total_deposited":0,"history":[],"orders":[]}
    return d["users"][uid]

def log_revenue(d, uid, amount, note):
    d["revenue_log"].insert(0, {
        "date":     datetime.now().strftime("%d/%m/%Y"),
        "datetime": fmt(),
        "user_id":  str(uid),
        "amount":   amount,
        "note":     note,
    })
    if len(d["revenue_log"]) > 1000:
        d["revenue_log"] = d["revenue_log"][:1000]

# ╔══════════════════════════════════════════════════════════════╗
# ║   KHÓA CHỐNG RACE CONDITION – ngăn duyệt đơn 2 lần         ║
# ╚══════════════════════════════════════════════════════════════╝

_processing_orders: set = set()
_discount_locks: dict = {}

def _get_discount_lock(guild_id: int) -> asyncio.Lock:
    lock = _discount_locks.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        _discount_locks[guild_id] = lock
    return lock

# ╔══════════════════════════════════════════════════════════════╗
# ║                   BOT + PERMISSIONS                         ║
# ╚══════════════════════════════════════════════════════════════╝

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False),
)
tree = bot.tree

def _has_role(member, role_id):
    if role_id == 0: return False
    r = member.guild.get_role(role_id)
    return r in member.roles if r else False

def is_admin(member):
    d = load(member.guild.id)
    rid = d["config"].get("admin_role_id", 0)
    if rid and _has_role(member, rid): return True
    return member.guild_permissions.administrator

def is_caythue(member):
    d = load(member.guild.id)
    rid = d["config"].get("caythue_role_id", 0)
    return _has_role(member, rid) or is_admin(member)

def is_robux(member):
    d = load(member.guild.id)
    rid = d["config"].get("robux_role_id", 0)
    return _has_role(member, rid) or is_admin(member)

def is_staff(member):
    return is_admin(member) or is_caythue(member) or is_robux(member)

# ╔══════════════════════════════════════════════════════════════╗
# ║      CHỐNG SPAM TICKET – GIỚI HẠN 1 TICKET/USER +           ║
# ║      KHOÁ TẠM KHI ĐANG TẠO (chống bấm đúp/spam nút)          ║
# ╚══════════════════════════════════════════════════════════════╝

TICKET_PREFIXES = ["don-", "acc-", "support-", "robux-", "nitro-", "capwall-"]

_ticket_creation_lock = set()  # user_id đang trong quá trình tạo ticket (bộ nhớ tạm, không lưu file)

def find_open_ticket(guild: discord.Guild, user: discord.abc.User):
    """Quét TẤT CẢ loại ticket, trả về channel đầu tiên mà user này đang có mở (bất kể loại nào)."""
    uname = user.name.lower()
    for prefix in TICKET_PREFIXES:
        ch = discord.utils.get(guild.text_channels, name=f"{prefix}{uname}")
        if ch:
            return ch
    return None

async def guard_ticket_creation(interaction: discord.Interaction, guild: discord.Guild, user: discord.abc.User):
    """
    Chặn spam tạo ticket:
      1) Nếu user đang trong quá trình tạo 1 ticket khác (bấm đúp/spam nút) -> chặn.
      2) Nếu user đã có BẤT KỲ ticket nào (loại nào cũng tính) đang mở -> chặn,
         chỉ khi ticket đó được xử lý xong (đóng) mới cho mở ticket mới.
    Trả về True nếu được phép tạo tiếp (đã tự khoá user.id vào _ticket_creation_lock,
    nơi gọi PHẢI tự giải phóng bằng finally: _ticket_creation_lock.discard(user.id)).
    Trả về False nếu bị chặn (đã tự gửi followup báo lỗi, không cần làm gì thêm).
    """
    if user.id in _ticket_creation_lock:
        await interaction.followup.send(
            "⏳ Yêu cầu tạo ticket trước đó của bạn đang được xử lý, vui lòng đợi vài giây rồi thử lại.",
            ephemeral=True
        )
        return False

    existing = find_open_ticket(guild, user)
    if existing:
        await interaction.followup.send(
            f"❌ Bạn đang có 1 ticket khác đang mở: {existing.mention}\n"
            f"Vui lòng hoàn tất và đóng ticket đó trước khi mở ticket mới.",
            ephemeral=True
        )
        return False

    _ticket_creation_lock.add(user.id)
    return True

# ╔══════════════════════════════════════════════════════════════╗
# ║      LƯU LỊCH SỬ TICKET (TRANSCRIPT) KHI ĐÓNG TICKET       ║
# ╚══════════════════════════════════════════════════════════════╝

def get_ticket_opener(channel: discord.TextChannel):
    """
    Xác định đúng người ĐÃ MỞ ticket (không phải người bấm nút đóng),
    dựa vào ID được nhúng sẵn trong topic của channel lúc tạo ticket
    (dạng "...| opener:123456789").
    Trả về đối tượng Member nếu tìm thấy, ngược lại trả về None.
    """
    topic = channel.topic or ""
    m = re.search(r"opener:(\d+)", topic)
    if not m:
        return None
    return channel.guild.get_member(int(m.group(1)))


async def save_ticket_transcript(channel: discord.TextChannel, closed_by: discord.Member, ly_do: str = "", opener: discord.Member = None):
    """
    Xuất TOÀN BỘ lịch sử chat của ticket (bao gồm hình ảnh, file đính kèm,
    tên/avatar người dùng, embed...) thành 1 file HTML.
    - Gửi 1 bản vào kênh log staff đã cấu hình (nếu có).
    - Gửi thêm 1 bản riêng qua DM cho khách (người mở ticket), nếu xác định được.
    """
    if opener is None:
        opener = get_ticket_opener(channel)

    try:
        transcript = await chat_exporter.export(
            channel,
            limit=None,
            tz_info="Asia/Ho_Chi_Minh",
            military_time=True,
            bot=bot,
        )
    except Exception as e:
        print(f"[Transcript] Lỗi xuất lịch sử ticket #{channel.name}: {e}")
        return

    if transcript is None:
        return

    transcript_bytes = transcript.encode()
    file_name = f"transcript-{channel.name}-{int(time.time())}.html"

    embed = discord.Embed(
        title="📁 LỊCH SỬ TICKET ĐÃ ĐƯỢC LƯU",
        color=0x95A5A6,
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="🔖 Ticket",         value=f"`#{channel.name}`",                        inline=True)
    embed.add_field(name="👤 Người mở ticket", value=opener.mention if opener else "_Không rõ_",  inline=True)
    embed.add_field(name="🚪 Đóng bởi",        value=closed_by.mention,                           inline=True)
    embed.add_field(name="⏰ Thời gian",       value=fmt(),                                       inline=True)
    if ly_do:
        embed.add_field(name="📝 Lý do", value=ly_do, inline=False)
    embed.set_footer(text="File HTML đính kèm chứa toàn bộ tin nhắn, hình ảnh và tên người dùng trong ticket")

    # 1) Gửi vào kênh log staff (nếu đã cấu hình)
    d      = load(channel.guild.id)
    log_id = d["config"].get("ticket_log_channel_id", 0)
    log_ch = channel.guild.get_channel(log_id) if log_id else None
    if log_ch:
        try:
            await log_ch.send(embed=embed, file=discord.File(io.BytesIO(transcript_bytes), filename=file_name))
        except Exception as e:
            print(f"[Transcript] Lỗi gửi file vào kênh log: {e}")

    # 2) Gửi riêng 1 bản cho khách (người mở ticket) qua DM
    if opener:
        dm_embed = discord.Embed(
            title="📁 LỊCH SỬ TICKET CỦA BẠN",
            description=f"Ticket `#{channel.name}` tại **{channel.guild.name}** đã được đóng.\nĐây là bản lưu toàn bộ tin nhắn của ticket, gửi riêng cho bạn.",
            color=0x3498DB,
            timestamp=datetime.utcnow(),
        )
        if ly_do:
            dm_embed.add_field(name="📝 Lý do đóng", value=ly_do, inline=False)
        try:
            await opener.send(embed=dm_embed, file=discord.File(io.BytesIO(transcript_bytes), filename=file_name))
        except Exception as e:
            print(f"[Transcript] Không gửi được DM transcript cho {opener}: {e} (có thể khách đã tắt DM)")

# ╔══════════════════════════════════════════════════════════════╗
# ║              ROBUX MARKET – CẬP NHẬT MỖI PHÚT              ║
# ╚══════════════════════════════════════════════════════════════╝

@tasks.loop(minutes=1)
async def update_robux_market():
    # Mỗi server có giá Robux riêng, nên phải cập nhật lần lượt cho từng server bot đang ở.
    for g in list(bot.guilds):
        try:
            d = load(g.id)
            cur    = d["robux_market"].get("price", ROBUX_BASE_PRICE)
            change = random.uniform(-0.20, 0.20)
            new_p  = round(max(ROBUX_BASE_PRICE * 0.5, cur * (1 + change)), 2)
            d["robux_market"] = {"price": new_p, "updated": fmt()}
            save(d, g.id)
        except Exception as e:
            # Không để 1 lần lỗi Mongo/DB làm task này tự dừng vĩnh viễn
            print(f"[{fmt()}] ⚠️ Lỗi cập nhật Robux market (guild {g.id}): {e}")

@tasks.loop(seconds=30)
async def _heartbeat_tick():
    """Chạy trong event loop chính của discord.py. Nếu tick này ngừng chạy
    quá lâu (bị block bởi code CPU nặng, hoặc gateway treo), watchdog thread
    (chạy độc lập ở OS thread khác) sẽ phát hiện và buộc restart process."""
    global _last_alive_tick
    _last_alive_tick = time.time()

# ╔══════════════════════════════════════════════════════════════╗
# ║             HELPER – TẠO EMBED CỬA HÀNG                    ║
# ╚══════════════════════════════════════════════════════════════╝

def build_shop_embed(d, show_all=False, item_type="shop"):
    """
    Tạo embed cửa hàng / cày thuê.
    Không hiển thị bảng giá thị trường Robux ở đây – dùng /rate để xem giá Robux.
    """
    if item_type == "farming":
        groups = d["farming_groups"]
        items  = {iid: it for iid, it in d["farming_items"].items() if it.get("group_id") is None}
    else:
        groups = d["shop_groups"]
        items  = {iid: it for iid, it in d["shop_items"].items() if it.get("group_id") is None}

    embed = discord.Embed(
        title="🛍️ CỬA HÀNG" if item_type == "shop" else "⛏️ CÀY THUÊ",
        color=0x1ABC9C,
        timestamp=datetime.utcnow(),
    )
    has_item = False

    for gid, g in groups.items():
        if not show_all and not g.get("enabled", True): continue
        lines = []
        for iid in g.get("item_ids", []):
            it = d["shop_items"].get(iid) or d["farming_items"].get(iid)
            if not it: continue
            if not show_all and not it.get("enabled", True): continue
            lines.append(f"🔹 **{it['name']}** — `{it['price']:,} VNĐ`")
            has_item = True
        if lines:
            field_value = "\n".join(lines)
            if g.get("image"):
                field_value += f"\n[🖼️ Xem ảnh nhóm]({g['image']})"
            embed.add_field(name=f"📦 {g['name'].upper()}", value=field_value, inline=False)

    for iid, it in items.items():
        if not show_all and not it.get("enabled", True): continue
        embed.add_field(name=f"🔸 {it['name']}", value=f"`{it['price']:,} VNĐ`", inline=True)
        has_item = True

    if not has_item:
        embed.description = "_Shop đang cập nhật sản phẩm..._"
    embed.set_footer(text="Chọn sản phẩm bên dưới để đặt hàng")
    return embed

def build_robux_embed(d):
    market = d["robux_market"]
    price  = market.get("price", ROBUX_BASE_PRICE)
    embed  = discord.Embed(title="💎 BẢNG GIÁ ROBUX", color=0xFF6B35, timestamp=datetime.utcnow())
    embed.add_field(name="📈 Giá thị trường", value=f"`{price:.2f} VNĐ / 1 Robux`", inline=False)
    items = d.get("robux_items", {})
    if items:
        for iid, it in items.items():
            p = round(it["amount_rb"] * price) if it.get("auto") else it["price_vnd"]
            embed.add_field(name=f"💎 {it['label']}", value=f"`{p:,} VNĐ`", inline=True)
    else:
        embed.description = "_Chưa có gói Robux nào._"
    embed.set_footer(text=f"Cập nhật lúc: {market.get('updated','–')}")
    return embed

def build_nitro_embed(d):
    """Bảng giá Nitro Boost — mỗi dòng: Tên : Giá + icon (mặc định 🚀, đổi bằng /seticon nitro <emoji>)."""
    icon  = get_icon(d, "nitro", "🚀")
    items = d.get("nitro_items", {})
    embed = discord.Embed(title=f"{icon} DISCORD NITRO BOOST", color=0xFF73FA, timestamp=datetime.utcnow())
    if items:
        lines = [f"**{it['name']}** : `{it['price']:,} VNĐ` {icon}" for it in items.values() if it.get("enabled", True)]
        embed.description = "\n".join(lines) if lines else "_Chưa có gói Nitro nào._"
    else:
        embed.description = "_Chưa có gói Nitro nào._"
    embed.set_footer(text="Chọn gói bên dưới để đặt hàng")
    return embed

def build_capwall_embeds(d):
    """
    Bảng Capwall Store — 1 embed liệt kê tên+giá, kèm theo tối đa 9 embed ảnh (mỗi embed 1 ảnh sản phẩm)
    được Discord tự xếp thành dạng lưới ảnh nếu cùng gửi 1 message (mẹo dùng chung .url cho các embed).
    Giới hạn Discord: tối đa 10 embed/tin nhắn -> 1 bảng chữ + tối đa 9 ảnh.
    """
    icon  = get_icon(d, "capwall", "🏞️")
    items = {iid: it for iid, it in d.get("capwall_items", {}).items() if it.get("enabled", True)}
    same_url = "https://capwall.store/"  # giá trị .url giống nhau ở mọi embed để Discord gộp ảnh thành 1 dải

    main = discord.Embed(title=f"{icon} CAPWALL STORE", color=0x1ABC9C, timestamp=datetime.utcnow(), url=same_url)
    if items:
        lines = []
        for i, it in enumerate(items.values(), 1):
            line = f"{i}. **{it['name']}** — `{it['price']:,} VNĐ`"
            if it.get("thoigian"):
                line += f" — ⏱️ `{it['thoigian']}`"
            lines.append(line)
        main.description = "\n".join(lines)
    else:
        main.description = "_Chưa có sản phẩm nào._"
    main.set_footer(text="Chọn sản phẩm bên dưới để đặt hàng")

    embeds = [main]
    for it in list(items.values())[:9]:
        if it.get("image"):
            img = discord.Embed(url=same_url, color=0x1ABC9C)
            img.set_image(url=it["image"])
            embeds.append(img)
    return embeds


class CapwallAddModal(Modal, title="🏞️ Thêm Sản Phẩm Capwall"):
    ten     = TextInput(label="Tên sản phẩm", placeholder="VD: Hình nền núi tuyết", max_length=100)
    gia     = TextInput(label="Giá bán (VNĐ)", placeholder="VD: 20000", max_length=15)
    thoigian = TextInput(label="Thời gian hoàn thành", placeholder="VD: 1-3 giờ", max_length=50, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.gia.value.strip().replace(".", "").replace(",", "")
        if not raw.isdigit() or int(raw) <= 0:
            await interaction.response.send_message("❌ Giá phải là số nguyên dương.", ephemeral=True); return
        name  = self.ten.value.strip()
        price = int(raw)
        thoigian = self.thoigian.value.strip()

        # ── Chờ admin gửi ảnh đính kèm (chọn từ thư viện) ngay trong kênh ──
        await interaction.response.send_message(
            "📸 Vui lòng **gửi ảnh sản phẩm** (đính kèm file, chọn từ thư viện) vào kênh này trong vòng 120 giây...",
            ephemeral=True,
        )

        def _check(m: discord.Message):
            return (
                m.author.id == interaction.user.id
                and m.channel.id == interaction.channel.id
                and len(m.attachments) > 0
            )

        try:
            msg = await bot.wait_for("message", timeout=120.0, check=_check)
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Hết thời gian chờ ảnh. Sản phẩm chưa được thêm.", ephemeral=True)
            return

        att = msg.attachments[0]
        if not (att.content_type or "").startswith("image/"):
            await interaction.followup.send("❌ File gửi lên không phải ảnh. Sản phẩm chưa được thêm.", ephemeral=True)
            return

        image = att.url
        try:
            await msg.add_reaction("✅")
        except Exception:
            pass

        d   = load(interaction.guild_id)
        iid = new_id()
        d["capwall_items"][iid] = {"name": name, "price": price, "image": image, "thoigian": thoigian, "enabled": True}
        save(d, interaction.guild_id)

        # ── Đăng công khai sản phẩm kèm nút "🎫 Mua Ngay" riêng cho SP này ──
        post_embed = discord.Embed(title="🏞️ SẢN PHẨM MỚI – CAPWALL STORE", color=0x1ABC9C, timestamp=datetime.utcnow())
        post_embed.description = f"**{name}**\n💵 Giá: `{price:,} VNĐ`"
        if thoigian:
            post_embed.description += f"\n⏱️ Thời gian hoàn thành: `{thoigian}`"
        if image:
            post_embed.set_image(url=image)
        post_embed.set_footer(text="Bấm nút bên dưới để mua ngay!")
        buy_view = CapwallBuyView(iid)
        try:
            await interaction.channel.send(embed=post_embed, view=buy_view)
            bot.add_view(buy_view)  # đăng ký persistent ngay, khỏi cần chờ bot khởi động lại
        except Exception as e:
            print(f"[CapwallAdd] Lỗi đăng công khai sản phẩm: {e}")

        embed = discord.Embed(
            title="✅ Đã Thêm Sản Phẩm Capwall",
            description=f"**{name}** — `{price:,} VNĐ`\n📢 Đã đăng công khai kèm nút mua ngay ở kênh này.",
            color=0x2ECC71,
        )
        embed.set_thumbnail(url=image)
        await interaction.followup.send(embed=embed, view=CapwallAddMoreView(), ephemeral=True)


class CapwallAddMoreView(View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="➕ Thêm Sản Phẩm Khác", style=discord.ButtonStyle.primary)
    async def more(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(CapwallAddModal())


class CapwallBuyView(View):
    """
    Nút 🎫 Mua Ngay gắn trực tiếp dưới MỖI sản phẩm Capwall khi được đăng
    (mỗi lần dùng /addcapwall). Bấm vào sẽ tự mở 1 ticket riêng, bỏ qua
    bước chọn dropdown, đi thẳng vào bước xác nhận đặt hàng cho đúng
    sản phẩm đó. custom_id nhúng item_id để bot tự đăng ký lại persistent
    cho TẤT CẢ sản phẩm cũ mỗi lần khởi động lại (xem on_ready).
    """
    def __init__(self, item_id: str):
        super().__init__(timeout=None)
        self.item_id = item_id
        btn = Button(label="🎫 Mua Ngay", style=discord.ButtonStyle.success, custom_id=f"capwall_buy:{item_id}")
        btn.callback = self.buy
        self.add_item(btn)

    async def buy(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild, user = interaction.guild, interaction.user
        if not await guard_ticket_creation(interaction, guild, user):
            return
        try:
            d  = load(interaction.guild_id)
            it = d.get("capwall_items", {}).get(self.item_id)
            if not it or not it.get("enabled", True):
                await interaction.followup.send("❌ Sản phẩm này không còn tồn tại hoặc đã ngừng bán.", ephemeral=True)
                return

            ow = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            cfg = d["config"]
            for rid in ["admin_role_id"]:
                r = guild.get_role(cfg.get(rid, 0) or 0)
                if r: ow[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            ch = await guild.create_text_channel(
                name=f"capwall-{user.name.lower()}",
                overwrites=ow,
                topic=f"Capwall | {user.display_name} | opener:{user.id} | item:{self.item_id}"
            )
            ping_role_id = cfg.get("ticket_ping_role_id", 0)
            ping = f"<@&{ping_role_id}>" if ping_role_id else ""

            name, price = it["name"], it["price"]
            header = f"📦 **Đơn Capwall của {user.mention}**" + (f" {ping}" if ping else "")
            info_embed = discord.Embed(title="🏞️ SẢN PHẨM CAPWALL", color=0x1ABC9C, timestamp=datetime.utcnow())
            info_embed.description = f"**{name}**\n💵 Giá: `{price:,} VNĐ`"
            if it.get("thoigian"):
                info_embed.description += f"\n⏱️ Thời gian hoàn thành: `{it['thoigian']}`"
            if it.get("image"):
                info_embed.set_image(url=it["image"])
            await ch.send(content=header, embed=info_embed)

            confirm_view = OrderConfirmView(
                original_price=price, item_name=name, item_id=self.item_id,
                item_kind="capwall", user_id=user.id,
            )
            confirm_embed = discord.Embed(title="🛒 XÁC NHẬN ĐẶT HÀNG", color=0xF39C12, timestamp=datetime.utcnow())
            confirm_embed.description = (
                f"📦 Sản phẩm: **{name}**\n💵 Giá: `{price:,} VNĐ`\n"
            )
            if it.get("thoigian"):
                confirm_embed.description += f"⏱️ Thời gian hoàn thành: `{it['thoigian']}`\n"
            confirm_embed.description += (
                f"\nBấm **✅ Đặt Hàng** để xác nhận.\nBấm **🏷️ Mã Giảm Giá** nếu có mã."
            )
            await ch.send(content=f"{user.mention}", embed=confirm_embed, view=confirm_view)
            await ch.send(content="━━━━━━━━━━━━━━━━━━━━━━━━━", view=CloseTicketView())
            await interaction.followup.send(f"✅ Ticket đã tạo: {ch.mention}", ephemeral=True)
        except Exception as e:
            print(f"[CapwallBuy] Lỗi tạo ticket: {e}")
            try:
                await interaction.followup.send(f"❌ Có lỗi khi tạo ticket: `{e}`", ephemeral=True)
            except Exception:
                pass
        finally:
            _ticket_creation_lock.discard(user.id)


class NitroBuyView(View):
    """
    Nút 🎫 Mua Ngay gắn trực tiếp dưới MỖI gói Nitro khi được đăng công khai
    (mỗi lần dùng /addnitro). Bấm vào sẽ tự mở 1 ticket riêng, bỏ qua bước
    chọn dropdown, đi thẳng vào bước xác nhận đặt hàng cho đúng gói đó.
    custom_id nhúng item_id để bot tự đăng ký lại persistent cho TẤT CẢ
    gói cũ mỗi lần khởi động lại (xem on_ready).
    """
    def __init__(self, item_id: str):
        super().__init__(timeout=None)
        self.item_id = item_id
        btn = Button(label="🎫 Mua Ngay", style=discord.ButtonStyle.success, custom_id=f"nitro_buy:{item_id}")
        btn.callback = self.buy
        self.add_item(btn)

    async def buy(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild, user = interaction.guild, interaction.user
        if not await guard_ticket_creation(interaction, guild, user):
            return
        try:
            d  = load(interaction.guild_id)
            it = d.get("nitro_items", {}).get(self.item_id)
            if not it or not it.get("enabled", True):
                await interaction.followup.send("❌ Gói này không còn tồn tại hoặc đã ngừng bán.", ephemeral=True)
                return

            ow = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            cfg = d["config"]
            for rid in ["admin_role_id"]:
                r = guild.get_role(cfg.get(rid, 0) or 0)
                if r: ow[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            ch = await guild.create_text_channel(
                name=f"nitro-{user.name.lower()}",
                overwrites=ow,
                topic=f"Nitro | {user.display_name} | opener:{user.id} | item:{self.item_id}"
            )
            ping_role_id = cfg.get("ticket_ping_role_id", 0)
            ping = f"<@&{ping_role_id}>" if ping_role_id else ""

            name, price = it["name"], it["price"]
            icon = get_icon(d, "nitro", "🚀")
            header = f"📦 **Đơn Nitro của {user.mention}**" + (f" {ping}" if ping else "")
            info_embed = discord.Embed(title=f"{icon} GÓI NITRO", color=0xFF73FA, timestamp=datetime.utcnow())
            info_embed.description = f"**{name}**\n💵 Giá: `{price:,} VNĐ`"
            await ch.send(content=header, embed=info_embed)

            confirm_view = OrderConfirmView(
                original_price=price, item_name=name, item_id=self.item_id,
                item_kind="nitro", user_id=user.id,
            )
            confirm_embed = discord.Embed(title="🛒 XÁC NHẬN ĐẶT HÀNG", color=0xF39C12, timestamp=datetime.utcnow())
            confirm_embed.description = (
                f"📦 Sản phẩm: **{name}**\n💵 Giá: `{price:,} VNĐ`\n\n"
                f"Bấm **✅ Đặt Hàng** để xác nhận.\nBấm **🏷️ Mã Giảm Giá** nếu có mã."
            )
            await ch.send(content=f"{user.mention}", embed=confirm_embed, view=confirm_view)
            await ch.send(content="━━━━━━━━━━━━━━━━━━━━━━━━━", view=CloseTicketView())
            await interaction.followup.send(f"✅ Ticket đã tạo: {ch.mention}", ephemeral=True)
        except Exception as e:
            print(f"[NitroBuy] Lỗi tạo ticket: {e}")
            try:
                await interaction.followup.send(f"❌ Có lỗi khi tạo ticket: `{e}`", ephemeral=True)
            except Exception:
                pass
        finally:
            _ticket_creation_lock.discard(user.id)



# ╔══════════════════════════════════════════════════════════════╗
# ║              MODAL – NHẬP MÃ GIẢM GIÁ                      ║
# ╚══════════════════════════════════════════════════════════════╝

class DiscountModal(Modal, title="🏷️ Nhập Mã Giảm Giá"):
    ma_code = TextInput(label="Mã giảm giá", placeholder="VD: SALE20", max_length=30)

    def __init__(self, original_price: int, item_name: str, item_id: str, item_kind: str, user_id: int):
        super().__init__()
        self.original_price = original_price
        self.item_name  = item_name
        self.item_id    = item_id
        self.item_kind  = item_kind
        self.user_id    = user_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Không phải ticket của bạn!", ephemeral=True); return

        code = self.ma_code.value.strip().upper()

        async with _get_discount_lock(interaction.guild_id):
            d    = load(interaction.guild_id)
            info = d["discount_codes"].get(code)

            if not info:
                await interaction.response.send_message(f"❌ Mã **{code}** không tồn tại.", ephemeral=True); return
            if info.get("uses_left", 0) <= 0:
                await interaction.response.send_message(f"❌ Mã **{code}** đã hết lượt dùng.", ephemeral=True); return
            try:
                exp_date = datetime.strptime(info["expires_at"], "%d/%m/%Y")
                if datetime.now() > exp_date:
                    await interaction.response.send_message(f"❌ Mã **{code}** đã hết hạn vào {info['expires_at']}.", ephemeral=True); return
            except:
                pass

            used_by = info.setdefault("used_by", [])
            if str(interaction.user.id) in used_by:
                await interaction.response.send_message(f"❌ Bạn đã dùng mã **{code}** rồi — mỗi mã chỉ dùng được 1 lần/khách.", ephemeral=True); return

            pct          = info["pct"]
            final_price  = round(self.original_price * (1 - pct / 100))
            discount_amt = self.original_price - final_price

            used_by.append(str(interaction.user.id))
            d["discount_codes"][code]["uses_left"] -= 1
            save(d, interaction.guild_id)
        oid  = new_id()
        d2   = load(interaction.guild_id)
        d2["orders"][oid] = {
            "user_id":        str(interaction.user.id),
            "item":           self.item_name,
            "item_id":        self.item_id,
            "item_type":      self.item_kind,
            "price":          final_price,
            "original_price": self.original_price,
            "discount_code":  code,
            "discount_pct":   pct,
            "status":         "pending",
            "ch_id":          str(interaction.channel_id),
            "created":        fmt(),
            "completed":      None,
        }
        u = ensure_user(d2, interaction.user.id)
        u["orders"].append({"oid": oid, "item": self.item_name, "price": final_price, "status": "pending", "date": fmt()})
        save(d2, interaction.guild_id)

        cfg  = d2["config"]
        ping = ""
        if self.item_kind == "robux"  and cfg.get("robux_role_id"):   ping = f"<@&{cfg['robux_role_id']}>"
        elif self.item_kind == "farm" and cfg.get("caythue_role_id"): ping = f"<@&{cfg['caythue_role_id']}>"
        elif cfg.get("admin_role_id"):                                  ping = f"<@&{cfg['admin_role_id']}>"

        embed = discord.Embed(title="🛒 XÁC NHẬN ĐẶT HÀNG (MÃ GIẢM GIÁ)", color=0x27AE60, timestamp=datetime.utcnow())
        embed.description = (
            f"👤 Khách: {interaction.user.mention}\n"
            f"📦 Sản phẩm: **{self.item_name}**\n"
            f"~~💵 Giá gốc: `{self.original_price:,} VNĐ`~~\n"
            f"🏷️ Mã: `{code}` (giảm **{pct}%** = `-{discount_amt:,} VNĐ`)\n"
            f"✅ **Giá sau giảm: `{final_price:,} VNĐ`**\n"
            f"🔑 Mã đơn: `{oid}`\n\n"
            f"⏳ Đang chờ Admin liên hệ..."
        )
        embed.set_footer(text="Vui lòng chờ Admin xác nhận đơn hàng")
        await interaction.response.send_message(content=ping or "@Admin", embed=embed, view=AdminOrderView())

# ╔══════════════════════════════════════════════════════════════╗
# ║       MODAL – ĐẶT LỊCH ORDER ACC LQ / FF                   ║
# ╚══════════════════════════════════════════════════════════════╝

class AccOrderModal(Modal, title="🎮 Order Acc LQ / FF"):
    game        = TextInput(label="Game",              placeholder="Liên Quân / Free Fire", max_length=50)
    server_info = TextInput(label="Server / Máy chủ",  placeholder="VD: Server Việt Nam",   max_length=100)
    requirement = TextInput(
        label="Yêu cầu",
        placeholder="VD: Rank Kim Cương, cần Hero X, tướng Y...",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )
    contact     = TextInput(
        label="Liên hệ / Zalo / Facebook",
        placeholder="SĐT hoặc link FB để admin liên hệ",
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        user  = interaction.user
        if not await guard_ticket_creation(interaction, guild, user):
            return
        try:
            d     = load(interaction.guild_id)

            ow = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            cfg = d["config"]
            for rid in ["admin_role_id", "caythue_role_id", "robux_role_id"]:
                r = guild.get_role(cfg.get(rid, 0) or 0)
                if r: ow[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            ch = await guild.create_text_channel(
                name=f"acc-{user.name.lower()}",
                overwrites=ow,
                topic=f"Order Acc | {user.display_name} | opener:{user.id}"
            )

            # Ping role đã cấu hình (nếu có) khi mở ticket
            ping_role_id = cfg.get("ticket_ping_role_id", 0)
            ping = f"<@&{ping_role_id}>" if ping_role_id else ""

            embed = discord.Embed(title="🎮 YÊU CẦU ORDER ACC", color=0x9B59B6, timestamp=datetime.utcnow())
            embed.description = (
                f"👤 Khách: {user.mention}\n"
                f"🎮 Game: **{self.game.value}**\n"
                f"🌐 Server: **{self.server_info.value}**\n"
                f"📋 Yêu cầu:\n> {self.requirement.value}\n"
                f"📞 Liên hệ: **{self.contact.value}**\n\n"
                f"⏳ Vui lòng chờ Admin xem và báo giá."
            )
            embed.set_footer(text="Admin sẽ liên hệ trong thời gian sớm nhất")
            header = f"📦 **Đơn Order Acc của {user.mention}**"
            await ch.send(content=f"{header}\n{user.mention} {ping}", embed=embed, view=AccOrderStaffView())
            await ch.send(content="━━━━━━━━━━━━━━━━━━━━━━━━━", view=CloseTicketView())
            await interaction.followup.send(f"✅ Ticket order acc đã tạo: {ch.mention}", ephemeral=True)
        except Exception as e:
            print(f"[AccOrderModal] Lỗi tạo ticket: {e}")
            try:
                await interaction.followup.send(f"❌ Có lỗi khi tạo ticket: `{e}`\nVui lòng thử lại hoặc báo Admin kiểm tra quyền bot (Quản Lý Kênh).", ephemeral=True)
            except Exception:
                pass
        finally:
            _ticket_creation_lock.discard(user.id)

# ╔══════════════════════════════════════════════════════════════╗
# ║          MODAL – ĐÁNH GIÁ / FEEDBACK                        ║
# ╚══════════════════════════════════════════════════════════════╝

class FeedbackModal(Modal, title="⭐ Đánh Giá Dịch Vụ"):
    nhan_xet = TextInput(
        label="Nhận xét của bạn",
        placeholder="Dịch vụ như thế nào? Giao hàng nhanh không?...",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=False,
    )

    def __init__(self, stars: int):
        super().__init__()
        self.stars = stars

    async def on_submit(self, interaction: discord.Interaction):
        d   = load(interaction.guild_id)
        cfg = d["config"]
        ch_id = cfg.get("feedback_channel_id", 0)

        star_display = "⭐" * self.stars + "☆" * (5 - self.stars)
        colors = {1: 0xE74C3C, 2: 0xE67E22, 3: 0xF1C40F, 4: 0x2ECC71, 5: 0x1ABC9C}
        labels = {1: "Rất tệ 😤", 2: "Tệ 😞", 3: "Bình thường 😐", 4: "Tốt 😊", 5: "Xuất sắc 🤩"}

        embed = discord.Embed(
            title=f"⭐ ĐÁNH GIÁ MỚI – {labels.get(self.stars, '')}",
            color=colors.get(self.stars, 0xF1C40F),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="👤 Khách hàng", value=interaction.user.mention, inline=True)
        embed.add_field(name="⭐ Số sao",     value=f"`{self.stars}/5`  {star_display}", inline=True)
        comment = self.nhan_xet.value.strip()
        if comment:
            embed.add_field(name="💬 Nhận xét", value=comment, inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"Feedback | {fmt()}")

        # Gửi vào kênh feedback
        if ch_id:
            ch_fb = interaction.guild.get_channel(ch_id)
            if ch_fb:
                try:
                    await ch_fb.send(embed=embed)
                except Exception as e:
                    print(f"[Feedback] Lỗi gửi vào kênh {ch_id}: {e}")

        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Cảm ơn bạn đã đánh giá!",
                description=f"Đánh giá **{self.stars} sao** của bạn đã được ghi nhận.\n{star_display}",
                color=colors.get(self.stars, 0xF1C40F),
            ),
            ephemeral=True,
        )

# ╔══════════════════════════════════════════════════════════════╗
# ║                 FEEDBACK STAR VIEW                          ║
# ╚══════════════════════════════════════════════════════════════╝

class FeedbackStarView(View):
    """Cho khách chọn số sao (1-5) trước khi mở modal nhận xét."""
    def __init__(self):
        super().__init__(timeout=None)

    async def _open_modal(self, interaction: discord.Interaction, stars: int):
        await interaction.response.send_modal(FeedbackModal(stars=stars))
        # Disable tất cả nút sau khi chọn
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except:
            pass

    @discord.ui.button(label="1 ⭐", style=discord.ButtonStyle.danger,    custom_id="fb_1")
    async def s1(self, i, b): await self._open_modal(i, 1)
    @discord.ui.button(label="2 ⭐", style=discord.ButtonStyle.secondary,  custom_id="fb_2")
    async def s2(self, i, b): await self._open_modal(i, 2)
    @discord.ui.button(label="3 ⭐", style=discord.ButtonStyle.primary,    custom_id="fb_3")
    async def s3(self, i, b): await self._open_modal(i, 3)
    @discord.ui.button(label="4 ⭐", style=discord.ButtonStyle.success,    custom_id="fb_4")
    async def s4(self, i, b): await self._open_modal(i, 4)
    @discord.ui.button(label="5 ⭐", style=discord.ButtonStyle.success,    custom_id="fb_5")
    async def s5(self, i, b): await self._open_modal(i, 5)

# ╔══════════════════════════════════════════════════════════════╗
# ║          STORE PANEL – BẢNG SHOP TICKET                    ║
# ╚══════════════════════════════════════════════════════════════╝

class ShopSelectView(View):
    def __init__(self, d, user_id, only_kind=None):
        super().__init__(timeout=600)
        self.d = d; self.user_id = user_id
        icon_nitro   = get_icon(d, "nitro", "🚀")
        icon_capwall = get_icon(d, "capwall", "🏞️")
        options = []
        if only_kind in (None, "shop"):
            for iid, it in d["shop_items"].items():
                if it.get("enabled", True):
                    options.append(discord.SelectOption(label=it["name"][:100], value=f"shop:{iid}", description=f"{it['price']:,} VNĐ"))
        if only_kind in (None, "farm"):
            for iid, it in d["farming_items"].items():
                if it.get("enabled", True):
                    options.append(discord.SelectOption(label=it["name"][:100], value=f"farm:{iid}", description=f"{it['price']:,} VNĐ", emoji="⛏️"))
        if only_kind in (None, "robux"):
            for iid, it in d.get("robux_items", {}).items():
                price = d["robux_market"]["price"]
                p     = round(it["amount_rb"]*price) if it.get("auto") else it["price_vnd"]
                options.append(discord.SelectOption(label=it["label"][:100], value=f"robux:{iid}", description=f"{p:,} VNĐ", emoji="💎"))
        if only_kind in (None, "nitro"):
            for iid, it in d.get("nitro_items", {}).items():
                if it.get("enabled", True):
                    options.append(discord.SelectOption(label=it["name"][:100], value=f"nitro:{iid}", description=f"{it['price']:,} VNĐ", emoji=icon_nitro))
        if only_kind in (None, "capwall"):
            for iid, it in d.get("capwall_items", {}).items():
                if it.get("enabled", True):
                    options.append(discord.SelectOption(label=it["name"][:100], value=f"capwall:{iid}", description=f"{it['price']:,} VNĐ", emoji=icon_capwall))
        if not options:
            options = [discord.SelectOption(label="Không có sản phẩm", value="none")]

        placeholder = "🛒 Chọn sản phẩm muốn mua..."
        if only_kind == "robux":   placeholder = "💎 Chọn gói Robux muốn mua..."
        elif only_kind == "nitro":   placeholder = "🚀 Chọn gói Nitro muốn mua..."
        elif only_kind == "capwall": placeholder = "🏞️ Chọn sản phẩm muốn mua..."

        self.item_select = Select(placeholder=placeholder, options=options[:25])
        self.item_select.callback = self.on_select
        self.add_item(self.item_select)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        print(f"[ShopSelectView] Timeout cho user {self.user_id}")

    async def on_select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != str(self.user_id):
            await interaction.response.send_message("❌ Chỉ bạn mới có thể chọn sản phẩm này!", ephemeral=True); return
        val = interaction.data["values"][0]
        if val == "none":
            await interaction.response.send_message("❌ Chưa có sản phẩm!", ephemeral=True); return
        d    = load(interaction.guild_id)
        kind, iid = val.split(":", 1)
        it   = None
        if kind == "shop":   it = d["shop_items"].get(iid)
        elif kind == "farm": it = d["farming_items"].get(iid)
        elif kind == "robux":
            raw = d["robux_items"].get(iid)
            if raw:
                rp = d["robux_market"]["price"]
                it = {"name": raw["label"], "price": round(raw["amount_rb"]*rp) if raw.get("auto") else raw["price_vnd"]}
        elif kind == "nitro":   it = d.get("nitro_items", {}).get(iid)
        elif kind == "capwall": it = d.get("capwall_items", {}).get(iid)
        if not it:
            await interaction.response.send_message("❌ Sản phẩm không tồn tại.", ephemeral=True); return

        name  = it.get("name","?")
        price = it.get("price", 0)

        confirm_view = OrderConfirmView(
            original_price=price, item_name=name, item_id=iid,
            item_kind=kind, user_id=interaction.user.id
        )
        embed = discord.Embed(title="🛒 XÁC NHẬN ĐẶT HÀNG", color=0xF39C12, timestamp=datetime.utcnow())
        embed.description = (
            f"📦 Sản phẩm: **{name}**\n"
            f"💵 Giá: `{price:,} VNĐ`\n"
        )
        if kind == "capwall" and it.get("thoigian"):
            embed.description += f"⏱️ Thời gian hoàn thành: `{it['thoigian']}`\n"
        embed.description += (
            f"\nBấm **✅ Đặt Hàng** để xác nhận.\n"
            f"Bấm **🏷️ Mã Giảm Giá** nếu có mã."
        )
        for child in self.children: child.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message(embed=embed, view=confirm_view, ephemeral=False)


async def _complete_order(d, guild, oid, approver_label: str, approver_id: str, count_deposit: bool = True):
    """
    Nghiệp vụ 'đơn hàng đã được xác nhận thanh toán' — dùng chung cho 2 luồng:
      1) Admin/CTV bấm nút "Duyệt Tiền" (thanh toán qua chuyển khoản, admin tự kiểm tra)
      2) Tự động trừ số dư ngay khi khách đặt hàng (đủ tiền trong ví)
    Đơn cày thuê -> chuyển 'processing', trả (embed, CTVProcessView(), buyer, is_farm=True)
    Đơn thường/robux -> 'completed', trả (embed, FeedbackStarView(), buyer, is_farm=False)
    count_deposit=False khi trả bằng số dư có sẵn (tiền đó đã tính vào total_deposited lúc nạp rồi,
    tránh cộng đôi thống kê). Hàm KHÔNG tự save(d) — nơi gọi chịu trách nhiệm save (kèm guild_id).
    """
    order_info = d["orders"][oid]
    buyer_id   = int(order_info["user_id"])
    price      = order_info["price"]
    item       = order_info["item"]
    kind       = order_info.get("item_type", "")
    buyer      = guild.get_member(buyer_id)
    is_farm    = kind == "farm" or "cày thuê" in item.lower()

    if is_farm:
        d["orders"][oid].update({"status": "processing", "approved_by": approver_id, "approved_at": fmt()})
        for o in d["users"].get(str(buyer_id), {}).get("orders", []):
            if o.get("oid") == oid: o["status"] = "processing"
        embed_p = discord.Embed(title="⏳ ĐƠN CÀY THUÊ ĐANG XỬ LÝ", color=0xF1C40F, timestamp=datetime.utcnow())
        embed_p.description = (
            f"✅ {approver_label} đã xác nhận nhận tiền!\n"
            f"📢 **Trạng thái:** Đang chờ Cộng Tác Viên tiếp nhận đơn hàng.\n\n"
            f"📦 Dịch vụ: **{item}**\n💵 Giá: `{price:,} VNĐ`\n🔑 Mã đơn: `{oid}`\n"
            f"👤 Khách hàng: <@{buyer_id}>\n⏰ Giờ duyệt: {fmt()}\n\n"
            f"*Hệ thống đang đợi CTV bấm nút nhận đơn...*"
        )
        if buyer:
            try:
                await buyer.send(embed=discord.Embed(
                    title="⏳ Đơn cày thuê của bạn đã được duyệt tiền!",
                    description=f"📦 Dịch vụ: **{item}**\nShop đã nhận tiền và đang điều phối CTV.",
                    color=0xF1C40F))
            except Exception: pass
        return embed_p, CTVProcessView(), buyer, True

    # Đơn thường / robux
    d["orders"][oid].update({"status": "completed", "completed": fmt(), "done_by": approver_id})
    u = ensure_user(d, buyer_id)
    if count_deposit:
        u["total_deposited"] = u.get("total_deposited", 0) + price
        u["history"].insert(0, {"type": "purchase", "item": item, "amount": price, "date": fmt()})
    for o in u["orders"]:
        if o.get("oid") == oid: o["status"] = "completed"
    log_revenue(d, buyer_id, price, f"Mua: {item}")

    embed = discord.Embed(title="🎉 ĐƠN HÀNG HOÀN TẤT!", color=0x2ECC71, timestamp=datetime.utcnow())
    embed.description = (
        f"✅ {approver_label} đã xác nhận!\n\n"
        f"📦 Sản phẩm: **{item}**\n💵 Giá: `{price:,} VNĐ`\n🔑 Mã đơn: `{oid}`\n⏰ {fmt()}\n\n"
        f"*Cảm ơn {buyer.mention if buyer else 'bạn'} đã mua hàng!*\n\n"
        f"⭐ **Hãy bấm số sao bên dưới để đánh giá dịch vụ nhé!**"
    )
    if buyer:
        try:
            await buyer.send(embed=discord.Embed(
                title="✅ Đơn hàng của bạn hoàn tất!",
                description=f"📦 **{item}** — `{price:,} VNĐ`\nCảm ơn bạn đã ủng hộ shop!\n\n⭐ Bấm số sao bên dưới để đánh giá dịch vụ nhé!",
                color=0x2ECC71,
            ), view=FeedbackStarView())
        except Exception: pass
    return embed, FeedbackStarView(), buyer, False


class OrderConfirmView(View):
    def __init__(self, original_price, item_name, item_id, item_kind, user_id):
        super().__init__(timeout=300)
        self.original_price = original_price
        self.item_name  = item_name
        self.item_id    = item_id
        self.item_kind  = item_kind
        self.user_id    = user_id

    @discord.ui.button(label="✅ Đặt Hàng", style=discord.ButtonStyle.success, emoji="🛒")
    async def confirm(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Không phải ticket của bạn!", ephemeral=True); return
        await self._place_order(interaction, self.original_price, None, 0)

    @discord.ui.button(label="🏷️ Mã Giảm Giá", style=discord.ButtonStyle.secondary, emoji="🎟️")
    async def discount(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Không phải ticket của bạn!", ephemeral=True); return
        modal = DiscountModal(
            original_price=self.original_price, item_name=self.item_name,
            item_id=self.item_id, item_kind=self.item_kind, user_id=self.user_id,
        )
        await interaction.response.send_modal(modal)
        for child in self.children: child.disabled = True
        await interaction.message.edit(view=self)

    async def _place_order(self, interaction: discord.Interaction, price: int, code, pct: int):
        oid = new_id()
        d   = load(interaction.guild_id)
        d["orders"][oid] = {
            "user_id":   str(interaction.user.id),
            "item":      self.item_name,
            "item_id":   self.item_id,
            "item_type": self.item_kind,
            "price":     price,
            "status":    "pending",
            "ch_id":     str(interaction.channel_id),
            "created":   fmt(),
            "completed": None,
        }
        if code:
            d["orders"][oid]["discount_code"]   = code
            d["orders"][oid]["discount_pct"]    = pct
            d["orders"][oid]["original_price"]  = self.original_price
        u = ensure_user(d, interaction.user.id)
        u["orders"].append({"oid": oid, "item": self.item_name, "price": price, "status": "pending", "date": fmt()})

        # ── Đủ số dư trong ví -> tự động trừ tiền & hoàn tất ngay, khỏi cần chờ Admin duyệt ──
        if u.get("balance", 0) >= price:
            u["balance"] -= price
            u["history"].insert(0, {"type": "purchase", "item": self.item_name, "amount": -price, "date": fmt()})
            embed, view, buyer, is_farm = await _complete_order(
                d, interaction.guild, oid,
                approver_label="💳 Thanh toán tự động (trừ số dư)",
                approver_id="system_balance",
                count_deposit=False,
            )
            save(d, interaction.guild_id)
            for child in self.children: child.disabled = True
            await interaction.message.edit(view=self)
            await interaction.response.send_message(
                content=f"{interaction.user.mention}",
                embed=embed, view=view
            )
            return

        # ── Không đủ số dư -> giữ nguyên luồng cũ: chờ Admin gửi QR & duyệt tay ──
        save(d, interaction.guild_id)
        cfg  = d["config"]
        ping = ""
        if self.item_kind == "robux"  and cfg.get("robux_role_id"):   ping = f"<@&{cfg['robux_role_id']}>"
        elif self.item_kind == "farm" and cfg.get("caythue_role_id"): ping = f"<@&{cfg['caythue_role_id']}>"
        elif cfg.get("admin_role_id"):                                  ping = f"<@&{cfg['admin_role_id']}>"

        bal = u.get("balance", 0)
        embed = discord.Embed(title="🛒 ĐƠN HÀNG ĐÃ ĐẶT", color=0xF39C12, timestamp=datetime.utcnow())
        embed.description = (
            f"👤 Khách: {interaction.user.mention}\n"
            f"📦 Sản phẩm: **{self.item_name}**\n"
            f"💵 Giá: `{price:,} VNĐ`\n"
            f"🔑 Mã đơn: `{oid}`\n\n"
            + (f"💳 Số dư hiện có: `{bal:,} VNĐ` (chưa đủ)\n" if bal > 0 else "")
            + f"⏳ Đang chờ Admin liên hệ...\n"
            f"> Admin sẽ gửi QR thanh toán tại đây, hoặc khách dùng `/naptienqr` để tự nạp đủ rồi bot tự trừ."
        )
        embed.set_footer(text="Vui lòng chờ Admin xác nhận đơn hàng")
        for child in self.children: child.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message(content=ping or "@Admin", embed=embed, view=AdminOrderView())


class AdminOrderView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Duyệt Tiền (Hoàn Thành / Chờ Cày)", style=discord.ButtonStyle.success, emoji="💰", custom_id="order_done")
    async def done(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user) and not is_robux(interaction.user) and not is_caythue(interaction.user):
            await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return

        if not interaction.message.embeds:
            await interaction.response.send_message("⚠️ Không tìm thấy dữ liệu embed.", ephemeral=True); return

        desc  = interaction.message.embeds[0].description or ""
        match = re.search(r"🔑 Mã đơn: `([^`]+)`", desc)
        if not match:
            await interaction.response.send_message("⚠️ Không tìm thấy Mã đơn.", ephemeral=True); return
        oid = match.group(1)

        if oid in _processing_orders:
            await interaction.response.send_message("⚠️ Đơn đang được xử lý bởi người khác, vui lòng chờ.", ephemeral=True); return
        _processing_orders.add(oid)

        try:
            d = load(interaction.guild_id)
            if oid not in d["orders"]:
                await interaction.response.send_message("⚠️ Đơn không tồn tại.", ephemeral=True); return
            if d["orders"][oid]["status"] in ["completed", "processing"]:
                await interaction.response.send_message("⚠️ Đơn này đã được duyệt hoặc hoàn thành rồi.", ephemeral=True); return

            embed, view, buyer, is_farm = await _complete_order(
                d, interaction.guild, oid,
                approver_label=f"Admin **{interaction.user.display_name}**",
                approver_id=str(interaction.user.id),
                count_deposit=True,
            )
            save(d, interaction.guild_id)

            if is_farm:
                await interaction.message.edit(embed=embed, view=view)
                await interaction.response.send_message(f"💰 Đã duyệt đơn cày thuê `{oid}`. Chờ CTV nhận việc!", ephemeral=True)
            else:
                for b in self.children: b.disabled = True
                await interaction.message.edit(view=self)
                await interaction.response.send_message(embed=embed, view=view)
        finally:
            _processing_orders.discard(oid)

    @discord.ui.button(label="❌ Huỷ Đơn", style=discord.ButtonStyle.danger, emoji="🚫", custom_id="order_cancel")
    async def cancel(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ Chỉ Admin mới huỷ đơn!", ephemeral=True); return

        if not interaction.message.embeds:
            await interaction.response.send_message("⚠️ Không tìm thấy dữ liệu embed.", ephemeral=True); return

        desc  = interaction.message.embeds[0].description or ""
        match = re.search(r"🔑 Mã đơn: `([^`]+)`", desc)
        if not match:
            await interaction.response.send_message("⚠️ Không tìm thấy Mã đơn.", ephemeral=True); return
        oid = match.group(1)

        d = load(interaction.guild_id)
        if oid in d["orders"]:
            buyer_id = d["orders"][oid]["user_id"]
            d["orders"][oid]["status"] = "cancelled"
            for o in (ensure_user(d, buyer_id)).get("orders",[]):
                if o.get("oid") == oid: o["status"] = "cancelled"
            save(d, interaction.guild_id)

        for b in self.children: b.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message(embed=discord.Embed(
            title="❌ Đơn Bị Huỷ",
            description=f"Đơn `{oid}` đã bị huỷ bởi {interaction.user.mention}.",
            color=0xE74C3C
        ))


class CTVProcessView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="👷 Nhận Cày Thuê", style=discord.ButtonStyle.primary, emoji="🎮", custom_id="ctv_claim")
    async def claim(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user) and not is_caythue(interaction.user):
            await interaction.response.send_message("❌ Bạn không có quyền CTV Cày Thuê!", ephemeral=True); return

        if not interaction.message.embeds:
            await interaction.response.send_message("⚠️ Không tìm thấy dữ liệu Embed.", ephemeral=True); return

        desc      = interaction.message.embeds[0].description or ""
        match_oid = re.search(r"🔑 Mã đơn: `([^`]+)`", desc)
        match_uid = re.search(r"👤 Khách hàng: <@!?(\d+)>", desc)
        if not match_oid:
            await interaction.response.send_message("⚠️ Không thể trích xuất Mã đơn.", ephemeral=True); return

        oid      = match_oid.group(1)
        buyer_id = int(match_uid.group(1)) if match_uid else 0

        d = load(interaction.guild_id)
        if oid not in d["orders"]:
            await interaction.response.send_message("⚠️ Đơn không tồn tại.", ephemeral=True); return
        if d["orders"][oid].get("ctv_id"):
            await interaction.response.send_message(f"⚠️ Đơn này đã được CTV <@{d['orders'][oid]['ctv_id']}> nhận rồi!", ephemeral=True); return

        d["orders"][oid].update({"ctv_id": str(interaction.user.id), "claimed_at": fmt()})
        save(d, interaction.guild_id)

        button.disabled = True
        button.label    = f"👷 Đã nhận: {interaction.user.display_name}"
        button.style    = discord.ButtonStyle.secondary

        old_embed = interaction.message.embeds[0]
        new_desc  = desc.replace(
            "📢 **Trạng thái:** Đang chờ Cộng Tác Viên tiếp nhận đơn hàng.",
            f"📢 **Trạng thái:** Đang tiến hành cày cuốc...\n👷 **CTV phụ trách:** {interaction.user.mention}"
        )
        new_embed = discord.Embed(title=old_embed.title, description=new_desc, color=0x3498DB, timestamp=datetime.utcnow())
        for f in old_embed.fields:
            new_embed.add_field(name=f.name, value=f.value, inline=f.inline)

        await interaction.message.edit(embed=new_embed, view=self)
        await interaction.response.send_message("✅ Bạn đã nhận đơn cày thuê! Hãy tiến hành thực hiện.", ephemeral=True)

        if buyer_id:
            buyer = interaction.guild.get_member(buyer_id)
            if buyer:
                try: await buyer.send(embed=discord.Embed(title="🎮 Tài khoản của bạn đang được cày!", description=f"👷 CTV {interaction.user.mention} đã nhận đơn. Vui lòng không đăng nhập vào game!", color=0x3498DB))
                except: pass

    @discord.ui.button(label="🚀 Hoàn Thành Cày Thuê", style=discord.ButtonStyle.success, emoji="✅", custom_id="ctv_done")
    async def finish(self, interaction: discord.Interaction, button: Button):
        if not interaction.message.embeds:
            await interaction.response.send_message("⚠️ Không tìm thấy dữ liệu Embed.", ephemeral=True); return

        desc      = interaction.message.embeds[0].description or ""
        match_oid = re.search(r"🔑 Mã đơn: `([^`]+)`", desc)
        if not match_oid:
            await interaction.response.send_message("⚠️ Không thể trích xuất Mã đơn.", ephemeral=True); return
        oid = match_oid.group(1)

        d = load(interaction.guild_id)
        if oid not in d["orders"]:
            await interaction.response.send_message("⚠️ Đơn không tồn tại.", ephemeral=True); return

        order_info = d["orders"][oid]
        ctv_id     = order_info.get("ctv_id")
        if not ctv_id:
            await interaction.response.send_message("⚠️ Chưa có CTV nào nhận đơn này!", ephemeral=True); return
        if str(interaction.user.id) != ctv_id and not is_admin(interaction.user):
            await interaction.response.send_message("❌ Bạn không phải CTV phụ trách đơn này!", ephemeral=True); return
        if order_info["status"] == "completed":
            await interaction.response.send_message("⚠️ Đơn này đã hoàn tất từ trước.", ephemeral=True); return

        buyer_id = int(order_info["user_id"])
        price    = order_info["price"]
        item     = order_info["item"]

        d["orders"][oid].update({"status": "completed", "completed": fmt(), "done_by": str(interaction.user.id)})
        u = ensure_user(d, buyer_id)
        u["total_deposited"] = u.get("total_deposited", 0) + price
        for o in u["orders"]:
            if o.get("oid") == oid: o["status"] = "completed"
        u["history"].insert(0, {"type":"purchase","item":item,"amount":price,"date":fmt()})
        log_revenue(d, buyer_id, price, f"Cày thuê xong: {item} (CTV: {interaction.user.display_name})")
        save(d, interaction.guild_id)

        buyer = interaction.guild.get_member(buyer_id)
        embed_final = discord.Embed(title="🎉 ĐƠN HÀNG CÀY THUÊ HOÀN TẤT!", color=0x2ECC71, timestamp=datetime.utcnow())
        embed_final.description = (
            f"✅ **Dịch vụ đã hoàn thành xuất sắc!**\n\n"
            f"📦 Sản phẩm: **{item}**\n"
            f"💵 Giá: `{price:,} VNĐ`\n"
            f"🔑 Mã đơn: `{oid}`\n"
            f"👷 CTV: <@{ctv_id}>\n"
            f"⏰ Hoàn thành: {fmt()}\n\n"
            f"*Cảm ơn {buyer.mention if buyer else 'bạn'} đã tin tưởng shop!*\n\n"
            f"⭐ **Hãy bấm số sao bên dưới để đánh giá dịch vụ nhé!**"
        )
        await interaction.message.edit(embed=embed_final, view=None)
        await interaction.response.send_message(f"🎉 Đã hoàn thành đơn cày thuê `{oid}`!", ephemeral=True)
        await interaction.channel.send(
            content=buyer.mention if buyer else None,
            embed=discord.Embed(
                title="⭐ ĐÁNH GIÁ DỊCH VỤ",
                description="Dịch vụ cày thuê đã hoàn thành! Hãy chọn số sao bên dưới để đánh giá trải nghiệm của bạn nhé 👇",
                color=0xF1C40F,
            ),
            view=FeedbackStarView(),
        )
        if buyer:
            try: await buyer.send(embed=discord.Embed(
                title="🎉 Dịch vụ Cày Thuê hoàn thành!",
                description=f"📦 **{item}** — 100% xong!\nVui lòng đăng nhập kiểm tra và đổi mật khẩu.\n\n⭐ Bấm số sao bên dưới để đánh giá nhé!",
                color=0x2ECC71,
            ), view=FeedbackStarView())
            except: pass


class NapTienModal(Modal, title="💰 Nạp Tiền Cho Khách"):
    def __init__(self, target: discord.Member):
        super().__init__()
        self.target  = target
        self.so_tien = TextInput(label="Số tiền (VNĐ)", placeholder="VD: 50000", required=True, max_length=15)
        self.ghi_chu = TextInput(label="Ghi chú (tuỳ chọn)", placeholder="VD: CK qua MBBank lúc 14h", required=False, max_length=100)
        self.add_item(self.so_tien)
        self.add_item(self.ghi_chu)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.so_tien.value.strip().replace(".", "").replace(",", "").replace("đ", "").replace("vnđ", "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            await interaction.response.send_message("❌ Số tiền không hợp lệ, chỉ nhập số nguyên dương.", ephemeral=True); return
        so_tien = int(raw)
        ghi_chu = self.ghi_chu.value.strip() or "Nạp qua ticket"

        d = load(interaction.guild_id); u = ensure_user(d, self.target.id)
        u["balance"]         = u.get("balance", 0) + so_tien
        u["total_deposited"] = u.get("total_deposited", 0) + so_tien
        u["history"].insert(0, {"type": "deposit", "amount": so_tien, "note": ghi_chu, "date": fmt()})
        log_revenue(d, self.target.id, so_tien, f"Nạp qua ticket: {ghi_chu}")
        save(d, interaction.guild_id)

        em = discord.Embed(title="💰 NẠP TIỀN THÀNH CÔNG", color=0x2ECC71, timestamp=datetime.utcnow())
        em.description = (
            f"👤 **Khách:** {self.target.mention}\n💵 **Số tiền:** `+{so_tien:,} VNĐ`\n"
            f"💳 **Số dư mới:** `{u['balance']:,} VNĐ`\n📝 **Ghi chú:** {ghi_chu}\n"
            f"✅ Xác nhận bởi: {interaction.user.mention}"
        )
        await interaction.response.send_message(embed=em)
        try:
            await self.target.send(embed=discord.Embed(
                title="💰 Bạn được cộng tiền!", description=f"`+{so_tien:,} VNĐ` — {ghi_chu}\n💳 Số dư mới: `{u['balance']:,} VNĐ`", color=0x2ECC71))
        except Exception: pass


class TruTienModal(Modal, title="➖ Trừ Tiền Của Khách"):
    def __init__(self, target: discord.Member):
        super().__init__()
        self.target  = target
        self.so_tien = TextInput(label="Số tiền (VNĐ)", placeholder="VD: 50000", required=True, max_length=15)
        self.ghi_chu = TextInput(label="Ghi chú (tuỳ chọn)", placeholder="VD: Mua acc LQ Rank Kim Cương", required=False, max_length=100)
        self.add_item(self.so_tien)
        self.add_item(self.ghi_chu)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.so_tien.value.strip().replace(".", "").replace(",", "").replace("đ", "").replace("vnđ", "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            await interaction.response.send_message("❌ Số tiền không hợp lệ, chỉ nhập số nguyên dương.", ephemeral=True); return
        so_tien = int(raw)
        ghi_chu = self.ghi_chu.value.strip() or "Trừ qua ticket"

        d = load(interaction.guild_id); u = ensure_user(d, self.target.id)
        u["balance"] = max(0, u.get("balance", 0) - so_tien)
        u["history"].insert(0, {"type": "deduct", "amount": -so_tien, "note": ghi_chu, "date": fmt()})
        save(d, interaction.guild_id)

        em = discord.Embed(title="💸 TRỪ TIỀN THÀNH CÔNG", color=0xE74C3C, timestamp=datetime.utcnow())
        em.description = (
            f"👤 **Khách:** {self.target.mention}\n💵 **Số tiền:** `-{so_tien:,} VNĐ`\n"
            f"💳 **Số dư còn:** `{u['balance']:,} VNĐ`\n📝 **Ghi chú:** {ghi_chu}\n"
            f"✅ Xác nhận bởi: {interaction.user.mention}"
        )
        await interaction.response.send_message(embed=em)


class AccOrderStaffView(View):
    """
    Nút dành cho Admin/CTV đánh dấu đơn Order Acc (LQ/FF) đã bàn giao xong,
    thay cho việc gõ tay chữ "done" trong chat như trước. Bấm xong sẽ:
      - Khoá nút (tránh bấm nhầm 2 lần)
      - Đăng embed hoàn tất công khai trong ticket
      - Tự động hiện nút ⭐ đánh giá cho khách + gửi kèm DM cảm ơn
    """
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Đánh Dấu Hoàn Thành", style=discord.ButtonStyle.success,
                        emoji="🎉", custom_id="acc_order_done")
    async def done(self, interaction: discord.Interaction, button: Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Chỉ Admin/CTV mới có thể bấm nút này!", ephemeral=True)
            return

        buyer = get_ticket_opener(interaction.channel)

        button.disabled = True
        button.label = "✅ Đã Hoàn Thành"
        await interaction.message.edit(view=self)

        embed = discord.Embed(title="🎉 ĐƠN ORDER ACC HOÀN TẤT!", color=0x2ECC71, timestamp=datetime.utcnow())
        embed.description = (
            f"✅ {interaction.user.mention} xác nhận đã bàn giao tài khoản thành công!\n\n"
            f"*Cảm ơn {buyer.mention if buyer else 'bạn'} đã tin tưởng shop!*\n\n"
            f"⭐ **Hãy bấm số sao bên dưới để đánh giá dịch vụ nhé!**"
        )
        await interaction.response.send_message(
            content=buyer.mention if buyer else None,
            embed=embed, view=FeedbackStarView(),
        )
        if buyer:
            try:
                await buyer.send(embed=discord.Embed(
                    title="✅ Đơn Order Acc của bạn đã hoàn tất!",
                    description="Cảm ơn bạn đã ủng hộ shop!\n\n⭐ Bấm số sao bên dưới để đánh giá dịch vụ nhé!",
                    color=0x2ECC71,
                ), view=FeedbackStarView())
            except Exception:
                pass


class CloseTicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💰 Nạp Tiền", style=discord.ButtonStyle.success, emoji="💰", custom_id="ticket_naptien_btn")
    async def nap_tien_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ Chỉ Admin mới nạp được tiền!", ephemeral=True); return
        target = get_ticket_opener(interaction.channel)
        if not target:
            await interaction.response.send_message(
                "⚠️ Không xác định được khách của ticket này (ticket tạo trước bản cập nhật). Dùng lệnh `/naptien` thay thế.",
                ephemeral=True
            ); return
        await interaction.response.send_modal(NapTienModal(target))

    @discord.ui.button(label="➖ Trừ Tiền", style=discord.ButtonStyle.danger, emoji="➖", custom_id="ticket_trutien_btn")
    async def tru_tien_btn(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ Chỉ Admin mới trừ được tiền!", ephemeral=True); return
        target = get_ticket_opener(interaction.channel)
        if not target:
            await interaction.response.send_message(
                "⚠️ Không xác định được khách của ticket này (ticket tạo trước bản cập nhật). Dùng lệnh `/trutien` thay thế.",
                ephemeral=True
            ); return
        await interaction.response.send_modal(TruTienModal(target))

    @discord.ui.button(label="🔒 Đóng Ticket", style=discord.ButtonStyle.danger, emoji="🚪", custom_id="close_ticket_btn")
    async def close(self, interaction: discord.Interaction, button: Button):
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Chỉ Staff mới đóng được ticket!", ephemeral=True); return
        ch = interaction.channel
        embed = discord.Embed(
            title="🔒 TICKET SẼ ĐÓNG",
            description=f"🚪 Đóng bởi **{interaction.user.display_name}**\n⏰ {fmt()}\n\n*Channel tự xoá sau 5 giây...*",
            color=0xE74C3C,
        )
        await interaction.response.send_message(embed=embed)
        await save_ticket_transcript(ch, interaction.user, opener=get_ticket_opener(ch))
        await asyncio.sleep(5)
        try:
            await ch.delete(reason=f"Ticket đóng bởi {interaction.user}")
        except Exception as e:
            print(f"[CloseTicket] Lỗi xoá channel: {e}")


class OpenTicketView(View):
    """
    Panel store chính – 6 nút, xếp lưới 2×3, icon đọc động từ config (đổi bằng /seticon):
      Hàng 0: Mua Hàng      | Order Acc
      Hàng 1: Support       | Robux1s   (bảng giá + rate riêng)
      Hàng 2: Nitro Boost   | Capwall   (bảng riêng, mỗi cái ticket riêng)
    Buttons được tạo ĐỘNG trong __init__ (không dùng @discord.ui.button cố định) để mỗi lần
    panel được gửi lại (/store) sẽ lấy đúng icon mới nhất đã cấu hình.
    """
    def __init__(self, guild_id=None):
        super().__init__(timeout=None)
        # guild_id=None chỉ xảy ra lúc bot khởi động lại (đăng ký lại persistent view để nút không
        # bị "hỏng" sau restart) — message cũ đã có sẵn icon rồi nên dùng icon mặc định ở đây không sao,
        # icon thật của từng server chỉ được đọc khi admin chạy lại /store.
        d = load(guild_id) if guild_id is not None else _blank()

        specs = [
            ("mua_hang",  "🛍️", "Mua Hàng",  discord.ButtonStyle.primary,   "open_ticket_btn",         0),
            ("order_acc", "🕹️", "Order Acc", discord.ButtonStyle.secondary, "open_acc_ticket_btn",     0),
            ("support",   "❗",  "Support",   discord.ButtonStyle.secondary, "open_support_ticket_btn", 1),
            ("robux1s",   "💎",  "Robux1s",   discord.ButtonStyle.success,   "open_robux1s_ticket_btn", 1),
            ("nitro",     "🚀",  "Nitro",     discord.ButtonStyle.primary,   "open_nitro_ticket_btn",   2),
            ("capwall",   "🏞️", "Capwall",   discord.ButtonStyle.success,   "open_capwall_ticket_btn", 2),
        ]
        handlers = {
            "mua_hang":  self.open_ticket,
            "order_acc": self.open_acc_ticket,
            "support":   self.open_support_ticket,
            "robux1s":   self.open_robux1s_ticket,
            "nitro":     self.open_nitro_ticket,
            "capwall":   self.open_capwall_ticket,
        }
        for key, fallback, label, style, cid, row in specs:
            btn = Button(label=label, style=style, emoji=get_icon(d, key, fallback), custom_id=cid, row=row)
            btn.callback = handlers[key]
            self.add_item(btn)

    async def open_ticket(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        user  = interaction.user
        if not await guard_ticket_creation(interaction, guild, user):
            return
        try:
            d     = load(interaction.guild_id)

            ow = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            cfg = d["config"]
            for rid in ["admin_role_id","caythue_role_id","robux_role_id"]:
                r = guild.get_role(cfg.get(rid,0) or 0)
                if r: ow[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            ch = await guild.create_text_channel(
                name=f"don-{user.name.lower()}",
                overwrites=ow,
                topic=f"Đơn hàng | {user.display_name} | opener:{user.id}"
            )
            ping_role_id = cfg.get("ticket_ping_role_id", 0)
            ping = f"<@&{ping_role_id}>" if ping_role_id else ""
            shop_embed       = build_shop_embed(d)
            shop_embed.title = "🛍️ DANH MỤC SẢN PHẨM"
            shop_embed.description = f"Chào {user.mention}! Chọn sản phẩm bên dưới để đặt hàng."
            header = f"📦 **Đơn Mua Hàng của {user.mention}**" + (f" {ping}" if ping else "")
            await ch.send(content=header, embed=shop_embed)
            await ch.send(content=f"{user.mention}", view=ShopSelectView(d, user.id))
            await ch.send(content="━━━━━━━━━━━━━━━━━━━━━━━━━", view=CloseTicketView())
            await interaction.followup.send(f"✅ Ticket đã tạo: {ch.mention}", ephemeral=True)
        except Exception as e:
            print(f"[OpenTicket] Lỗi tạo ticket: {e}")
            try:
                await interaction.followup.send(f"❌ Có lỗi khi tạo ticket: `{e}`\nVui lòng thử lại hoặc báo Admin kiểm tra quyền bot (Quản Lý Kênh).", ephemeral=True)
            except Exception:
                pass
        finally:
            _ticket_creation_lock.discard(user.id)

    async def open_acc_ticket(self, interaction: discord.Interaction):
        """Mở modal để khách điền thông tin order acc Liên Quân / Free Fire."""
        await interaction.response.send_modal(AccOrderModal())

    async def open_support_ticket(self, interaction: discord.Interaction):
        """Ticket hỗ trợ chung – không gắn sản phẩm, chỉ để khách hỏi/báo lỗi."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        user  = interaction.user
        if not await guard_ticket_creation(interaction, guild, user):
            return
        try:
            d     = load(interaction.guild_id)

            ow = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            cfg = d["config"]
            for rid in ["admin_role_id","caythue_role_id","robux_role_id"]:
                r = guild.get_role(cfg.get(rid,0) or 0)
                if r: ow[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            ch = await guild.create_text_channel(
                name=f"support-{user.name.lower()}",
                overwrites=ow,
                topic=f"Support | {user.display_name} | opener:{user.id}"
            )
            ping_role_id = cfg.get("ticket_ping_role_id", 0)
            ping = f"<@&{ping_role_id}>" if ping_role_id else ""

            embed = discord.Embed(title="🆘 TICKET HỖ TRỢ", color=0x5865F2, timestamp=datetime.utcnow())
            embed.description = (
                f"Chào {user.mention}! Vui lòng mô tả bạn cần hỗ trợ gì "
                f"(lỗi đơn hàng, thắc mắc, khiếu nại...), Staff sẽ phản hồi sớm nhất."
            )
            header = f"📦 **Đơn Support của {user.mention}**"
            await ch.send(content=f"{header}\n{user.mention} {ping}".strip(), embed=embed, view=CloseTicketView())
            await interaction.followup.send(f"✅ Ticket support đã tạo: {ch.mention}", ephemeral=True)
        except Exception as e:
            print(f"[OpenSupportTicket] Lỗi tạo ticket: {e}")
            try:
                await interaction.followup.send(f"❌ Có lỗi khi tạo ticket: `{e}`\nVui lòng thử lại hoặc báo Admin.", ephemeral=True)
            except Exception:
                pass
        finally:
            _ticket_creation_lock.discard(user.id)

    async def open_robux1s_ticket(self, interaction: discord.Interaction):
        """Ticket riêng cho Robux – hiện bảng giá/rate Robux + dropdown chỉ chứa gói Robux."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        user  = interaction.user
        if not await guard_ticket_creation(interaction, guild, user):
            return
        try:
            d     = load(interaction.guild_id)

            ow = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            cfg = d["config"]
            for rid in ["admin_role_id","robux_role_id"]:
                r = guild.get_role(cfg.get(rid,0) or 0)
                if r: ow[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            ch = await guild.create_text_channel(
                name=f"robux-{user.name.lower()}",
                overwrites=ow,
                topic=f"Robux1s | {user.display_name} | opener:{user.id}"
            )
            ping_role_id = cfg.get("robux_role_id") or cfg.get("ticket_ping_role_id", 0)
            ping = f"<@&{ping_role_id}>" if ping_role_id else ""

            robux_embed = build_robux_embed(d)
            robux_embed.description = f"Chào {user.mention}! Đây là bảng giá Robux hiện tại. Chọn gói bên dưới để đặt hàng."
            header = f"📦 **Đơn Robux1s của {user.mention}**" + (f" {ping}" if ping else "")
            await ch.send(content=header, embed=robux_embed)
            await ch.send(content=f"{user.mention}", view=ShopSelectView(d, user.id, only_kind="robux"))
            await ch.send(content="━━━━━━━━━━━━━━━━━━━━━━━━━", view=CloseTicketView())
            await interaction.followup.send(f"✅ Ticket Robux1s đã tạo: {ch.mention}", ephemeral=True)
        except Exception as e:
            print(f"[OpenRobux1sTicket] Lỗi tạo ticket: {e}")
            try:
                await interaction.followup.send(f"❌ Có lỗi khi tạo ticket: `{e}`\nVui lòng thử lại hoặc báo Admin.", ephemeral=True)
            except Exception:
                pass
        finally:
            _ticket_creation_lock.discard(user.id)

    async def open_nitro_ticket(self, interaction: discord.Interaction):
        """Ticket riêng cho Nitro Boost – hiện bảng giá Nitro + dropdown chỉ chứa gói Nitro."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        user  = interaction.user
        if not await guard_ticket_creation(interaction, guild, user):
            return
        try:
            d     = load(interaction.guild_id)

            ow = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            cfg = d["config"]
            for rid in ["admin_role_id"]:
                r = guild.get_role(cfg.get(rid,0) or 0)
                if r: ow[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            ch = await guild.create_text_channel(
                name=f"nitro-{user.name.lower()}",
                overwrites=ow,
                topic=f"Nitro | {user.display_name} | opener:{user.id}"
            )
            ping_role_id = cfg.get("ticket_ping_role_id", 0)
            ping = f"<@&{ping_role_id}>" if ping_role_id else ""

            nitro_embed = build_nitro_embed(d)
            nitro_embed.description = (nitro_embed.description or "") + f"\n\nChào {user.mention}! Chọn gói bên dưới để đặt hàng."
            header = f"📦 **Đơn Nitro của {user.mention}**" + (f" {ping}" if ping else "")
            await ch.send(content=header, embed=nitro_embed)
            await ch.send(content=f"{user.mention}", view=ShopSelectView(d, user.id, only_kind="nitro"))
            await ch.send(content="━━━━━━━━━━━━━━━━━━━━━━━━━", view=CloseTicketView())
            await interaction.followup.send(f"✅ Ticket Nitro đã tạo: {ch.mention}", ephemeral=True)
        except Exception as e:
            print(f"[OpenNitroTicket] Lỗi tạo ticket: {e}")
            try:
                await interaction.followup.send(f"❌ Có lỗi khi tạo ticket: `{e}`\nVui lòng thử lại hoặc báo Admin.", ephemeral=True)
            except Exception:
                pass
        finally:
            _ticket_creation_lock.discard(user.id)

    async def open_capwall_ticket(self, interaction: discord.Interaction):
        """Ticket riêng cho Capwall – hiện bảng sản phẩm + ảnh + dropdown chỉ chứa sản phẩm Capwall."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        user  = interaction.user
        if not await guard_ticket_creation(interaction, guild, user):
            return
        try:
            d     = load(interaction.guild_id)

            ow = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            cfg = d["config"]
            for rid in ["admin_role_id"]:
                r = guild.get_role(cfg.get(rid,0) or 0)
                if r: ow[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            ch = await guild.create_text_channel(
                name=f"capwall-{user.name.lower()}",
                overwrites=ow,
                topic=f"Capwall | {user.display_name} | opener:{user.id}"
            )
            ping_role_id = cfg.get("ticket_ping_role_id", 0)
            ping = f"<@&{ping_role_id}>" if ping_role_id else ""

            capwall_embeds = build_capwall_embeds(d)
            header = f"📦 **Đơn Capwall của {user.mention}**" + (f" {ping}" if ping else "")
            await ch.send(content=header, embeds=capwall_embeds)
            await ch.send(content=f"Chào {user.mention}! Chọn sản phẩm bên dưới để đặt hàng.", view=ShopSelectView(d, user.id, only_kind="capwall"))
            await ch.send(content="━━━━━━━━━━━━━━━━━━━━━━━━━", view=CloseTicketView())
            await interaction.followup.send(f"✅ Ticket Capwall đã tạo: {ch.mention}", ephemeral=True)
        except Exception as e:
            print(f"[OpenCapwallTicket] Lỗi tạo ticket: {e}")
            try:
                await interaction.followup.send(f"❌ Có lỗi khi tạo ticket: `{e}`\nVui lòng thử lại hoặc báo Admin.", ephemeral=True)
            except Exception:
                pass
        finally:
            _ticket_creation_lock.discard(user.id)

# ╔══════════════════════════════════════════════════════════════╗
# ║        AUTOCOMPLETE – GÕ TÊN THAY VÌ PHẢI NHỚ ID             ║
# ╚══════════════════════════════════════════════════════════════╝
# Khi admin gõ lệnh có tham số ID (item_id, nhom_id...), Discord sẽ hiện
# gợi ý theo TÊN đã đặt (không cần nhớ mã ID) — chọn xong Discord tự
# điền đúng ID phía sau, admin không cần thấy/gõ ID thủ công nữa.

async def ac_shop_items(interaction: discord.Interaction, current: str):
    d = load(interaction.guild_id); cl = current.lower()
    out = [app_commands.Choice(name=f"{it['name']} ({it.get('price',0):,}đ)"[:100], value=iid)
           for iid, it in d.get("shop_items", {}).items() if cl in it.get("name","").lower()]
    return out[:25]

async def ac_shop_groups(interaction: discord.Interaction, current: str):
    d = load(interaction.guild_id); cl = current.lower()
    out = [app_commands.Choice(name=g.get("name","?")[:100], value=gid)
           for gid, g in d.get("shop_groups", {}).items() if cl in g.get("name","").lower()]
    return out[:25]

async def ac_farm_items(interaction: discord.Interaction, current: str):
    d = load(interaction.guild_id); cl = current.lower()
    out = [app_commands.Choice(name=f"{it['name']} ({it.get('price',0):,}đ)"[:100], value=iid)
           for iid, it in d.get("farming_items", {}).items() if cl in it.get("name","").lower()]
    return out[:25]

async def ac_farm_groups(interaction: discord.Interaction, current: str):
    d = load(interaction.guild_id); cl = current.lower()
    out = [app_commands.Choice(name=g.get("name","?")[:100], value=gid)
           for gid, g in d.get("farming_groups", {}).items() if cl in g.get("name","").lower()]
    return out[:25]

async def ac_robux_items(interaction: discord.Interaction, current: str):
    d = load(interaction.guild_id); cl = current.lower()
    out = [app_commands.Choice(name=it.get("label","?")[:100], value=iid)
           for iid, it in d.get("robux_items", {}).items() if cl in it.get("label","").lower()]
    return out[:25]

async def ac_nitro_items(interaction: discord.Interaction, current: str):
    d = load(interaction.guild_id); cl = current.lower()
    out = [app_commands.Choice(name=f"{it['name']} ({it.get('price',0):,}đ)"[:100], value=iid)
           for iid, it in d.get("nitro_items", {}).items() if cl in it.get("name","").lower()]
    return out[:25]

async def ac_capwall_items(interaction: discord.Interaction, current: str):
    d = load(interaction.guild_id); cl = current.lower()
    out = [app_commands.Choice(name=f"{it['name']} ({it.get('price',0):,}đ)"[:100], value=iid)
           for iid, it in d.get("capwall_items", {}).items() if cl in it.get("name","").lower()]
    return out[:25]

async def ac_toggle_all(interaction: discord.Interaction, current: str):
    """Autocomplete cho /onoff – gộp: nhóm/SP shop, nhóm/DV cày, Nitro, Capwall."""
    d = load(interaction.guild_id); cl = current.lower(); out = []
    for gid, g in d.get("shop_groups", {}).items():
        if cl in g.get("name","").lower(): out.append(app_commands.Choice(name=f"📁 [Nhóm Shop] {g['name']}"[:100], value=gid))
    for iid, it in d.get("shop_items", {}).items():
        if cl in it.get("name","").lower(): out.append(app_commands.Choice(name=f"🔸 [SP Shop] {it['name']}"[:100], value=iid))
    for gid, g in d.get("farming_groups", {}).items():
        if cl in g.get("name","").lower(): out.append(app_commands.Choice(name=f"📁 [Nhóm Cày] {g['name']}"[:100], value=gid))
    for iid, it in d.get("farming_items", {}).items():
        if cl in it.get("name","").lower(): out.append(app_commands.Choice(name=f"⛏️ [DV Cày] {it['name']}"[:100], value=iid))
    for iid, it in d.get("nitro_items", {}).items():
        if cl in it.get("name","").lower(): out.append(app_commands.Choice(name=f"🚀 [Nitro] {it['name']}"[:100], value=iid))
    for iid, it in d.get("capwall_items", {}).items():
        if cl in it.get("name","").lower(): out.append(app_commands.Choice(name=f"🏞️ [Capwall] {it['name']}"[:100], value=iid))
    return out[:25]

# ╔══════════════════════════════════════════════════════════════╗
# ║                  LỆNH ADMIN – STORE & ITEMS                 ║
# ╚══════════════════════════════════════════════════════════════╝

@tree.command(name="store", description="🔐 [ADMIN] Tạo bảng shop với nút mua hàng tại channel này")
async def store_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    shop_em = build_shop_embed(d)
    shop_em.title = "🏪 CHÀO MỪNG ĐẾN CỬA HÀNG"
    shop_em.description = (
        "Nhấn nút **🛒 Mua Hàng** để tạo ticket đặt hàng riêng tư.\n"
        "Nhấn nút **🎮 Order Acc LQ/FF** để đặt lịch order tài khoản.\n"
        "Nhấn nút **❗ Support** để mở ticket hỗ trợ, báo lỗi hoặc thắc mắc.\n"
        "Nhấn nút **💎 Robux1s** để xem bảng giá Robux và đặt mua trực tiếp.\n\n"
        "💎 **Xem giá Robux:** dùng lệnh `/rate <số_robux>`"
    )
    await interaction.channel.send(embed=shop_em)
    await interaction.channel.send(view=OpenTicketView(interaction.guild_id))
    await interaction.response.send_message("✅ Đã tạo bảng shop!", ephemeral=True)

@tree.command(name="dongcua", description="🔐 [STAFF] Đóng và xoá channel ticket hiện tại")
@app_commands.describe(ly_do="Lý do đóng ticket (tuỳ chọn)")
async def dong_cua(interaction: discord.Interaction, ly_do: str = "Đã hoàn thành"):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ Không có quyền đóng ticket!", ephemeral=True); return
    ch = interaction.channel
    if not (ch.name.startswith("don-") or ch.name.startswith("ticket-") or ch.name.startswith("acc-") or ch.name.startswith("support-") or ch.name.startswith("robux-") or ch.name.startswith("nitro-") or ch.name.startswith("capwall-")):
        await interaction.response.send_message(
            "❌ Lệnh này chỉ dùng trong channel ticket (tên bắt đầu bằng `don-` hoặc `acc-`).",
            ephemeral=True
        ); return
    embed = discord.Embed(
        title="🔒 TICKET ĐANG ĐÓNG",
        description=(
            f"🚪 Đóng bởi **{interaction.user.display_name}**\n"
            f"📝 Lý do: {ly_do}\n"
            f"⏰ {fmt()}\n\n"
            f"*Channel tự xoá sau 5 giây...*"
        ),
        color=0xE74C3C,
    )
    await interaction.response.send_message(embed=embed)
    await save_ticket_transcript(ch, interaction.user, ly_do, opener=get_ticket_opener(ch))
    await asyncio.sleep(5)
    try:
        await ch.delete(reason=f"Ticket đóng bởi {interaction.user}: {ly_do}")
    except Exception as e:
        print(f"[dongcua] Không xoá được channel: {e}")

@tree.command(name="themvatpham", description="🔐 [ADMIN] Thêm vật phẩm vào shop")
@app_commands.describe(ten="Tên vật phẩm", gia="Giá bán (VNĐ)", image="Link ảnh (tuỳ chọn)", nhom_id="Nhóm (tuỳ chọn) – gõ tên để tìm")
@app_commands.autocomplete(nhom_id=ac_shop_groups)
async def them_vp(interaction: discord.Interaction, ten: str, gia: int, image: str = "", nhom_id: str = ""):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id); iid = new_id()
    d["shop_items"][iid] = {"name":ten,"price":gia,"image":image,"group_id":nhom_id or None,"enabled":True}
    if nhom_id and nhom_id in d["shop_groups"]:
        d["shop_groups"][nhom_id]["item_ids"].append(iid)
    save(d, interaction.guild_id)
    em = discord.Embed(title="✅ Đã Thêm Vật Phẩm", color=0x2ECC71)
    em.add_field(name="📦 Tên",   value=ten,            inline=True)
    em.add_field(name="💵 Giá",   value=f"{gia:,} VNĐ", inline=True)
    em.add_field(name="🔑 ID",    value=f"`{iid}`",     inline=True)
    if image: em.set_image(url=image)
    await interaction.response.send_message(embed=em)

@tree.command(name="themnhom", description="🔐 [ADMIN] Tạo nhóm vật phẩm shop")
@app_commands.describe(ten="Tên nhóm", image="Link ảnh đại diện nhóm (tuỳ chọn)")
async def them_nhom(interaction: discord.Interaction, ten: str, image: str = ""):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id); gid = new_id()
    d["shop_groups"][gid] = {"name":ten,"image":image,"item_ids":[],"enabled":True}
    save(d, interaction.guild_id)
    em = discord.Embed(title="✅ Đã Tạo Nhóm", color=0x2ECC71)
    em.add_field(name="📁 Tên", value=ten,       inline=True)
    em.add_field(name="🔑 ID", value=f"`{gid}`", inline=True)
    if image: em.set_image(url=image)
    em.set_footer(text=f"Dùng /themvatpham nhom_id:{gid} để thêm sản phẩm vào nhóm này")
    await interaction.response.send_message(embed=em)

@tree.command(name="xoavatpham", description="🔐 [ADMIN] Xoá vật phẩm khỏi shop")
@app_commands.describe(item_id="Vật phẩm cần xoá – gõ tên để tìm")
@app_commands.autocomplete(item_id=ac_shop_items)
async def xoa_vp(interaction: discord.Interaction, item_id: str):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    if item_id not in d["shop_items"]:
        await interaction.response.send_message("❌ Không tìm thấy vật phẩm.", ephemeral=True); return
    name = d["shop_items"][item_id]["name"]
    gid  = d["shop_items"][item_id].get("group_id")
    del d["shop_items"][item_id]
    if gid and gid in d["shop_groups"] and item_id in d["shop_groups"][gid]["item_ids"]:
        d["shop_groups"][gid]["item_ids"].remove(item_id)
    save(d, interaction.guild_id)
    await interaction.response.send_message(f"🗑️ Đã xoá **{name}** (ID: `{item_id}`).")

@tree.command(name="xoanhom", description="🔐 [ADMIN] Xoá nhóm và toàn bộ vật phẩm trong nhóm")
@app_commands.describe(nhom_id="Nhóm cần xoá – gõ tên để tìm")
@app_commands.autocomplete(nhom_id=ac_shop_groups)
async def xoa_nhom(interaction: discord.Interaction, nhom_id: str):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    if nhom_id not in d["shop_groups"]:
        await interaction.response.send_message("❌ Không tìm thấy nhóm.", ephemeral=True); return
    g   = d["shop_groups"].pop(nhom_id); cnt = 0
    for iid in g.get("item_ids",[]):
        if iid in d["shop_items"]: del d["shop_items"][iid]; cnt += 1
    save(d, interaction.guild_id)
    await interaction.response.send_message(f"🗑️ Đã xoá nhóm **{g['name']}** và `{cnt}` vật phẩm.")

@tree.command(name="onoff", description="🔐 [ADMIN] Bật/tắt nhóm hoặc vật phẩm (shop + cày thuê)")
@app_commands.describe(id_item="Nhóm hoặc vật phẩm cần bật/tắt – gõ tên để tìm")
@app_commands.autocomplete(id_item=ac_toggle_all)
async def on_off(interaction: discord.Interaction, id_item: str):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    if id_item in d["shop_groups"]:
        d["shop_groups"][id_item]["enabled"] = not d["shop_groups"][id_item].get("enabled", True)
        st = "✅ BẬT" if d["shop_groups"][id_item]["enabled"] else "🔴 TẮT"
        save(d, interaction.guild_id); await interaction.response.send_message(f"📁 Nhóm shop **{d['shop_groups'][id_item]['name']}** → {st}")
    elif id_item in d["shop_items"]:
        d["shop_items"][id_item]["enabled"] = not d["shop_items"][id_item].get("enabled", True)
        st = "✅ BẬT" if d["shop_items"][id_item]["enabled"] else "🔴 TẮT"
        save(d, interaction.guild_id); await interaction.response.send_message(f"📦 Vật phẩm **{d['shop_items'][id_item]['name']}** → {st}")
    elif id_item in d["farming_groups"]:
        d["farming_groups"][id_item]["enabled"] = not d["farming_groups"][id_item].get("enabled", True)
        st = "✅ BẬT" if d["farming_groups"][id_item]["enabled"] else "🔴 TẮT"
        save(d, interaction.guild_id); await interaction.response.send_message(f"📁 Nhóm cày thuê **{d['farming_groups'][id_item]['name']}** → {st}")
    elif id_item in d["farming_items"]:
        d["farming_items"][id_item]["enabled"] = not d["farming_items"][id_item].get("enabled", True)
        st = "✅ BẬT" if d["farming_items"][id_item]["enabled"] else "🔴 TẮT"
        save(d, interaction.guild_id); await interaction.response.send_message(f"⛏️ Dịch vụ **{d['farming_items'][id_item]['name']}** → {st}")
    elif id_item in d.get("nitro_items", {}):
        d["nitro_items"][id_item]["enabled"] = not d["nitro_items"][id_item].get("enabled", True)
        st = "✅ BẬT" if d["nitro_items"][id_item]["enabled"] else "🔴 TẮT"
        save(d, interaction.guild_id); await interaction.response.send_message(f"🚀 Gói Nitro **{d['nitro_items'][id_item]['name']}** → {st}")
    elif id_item in d.get("capwall_items", {}):
        d["capwall_items"][id_item]["enabled"] = not d["capwall_items"][id_item].get("enabled", True)
        st = "✅ BẬT" if d["capwall_items"][id_item]["enabled"] else "🔴 TẮT"
        save(d, interaction.guild_id); await interaction.response.send_message(f"🏞️ Sản phẩm Capwall **{d['capwall_items'][id_item]['name']}** → {st}")
    else:
        await interaction.response.send_message("❌ Không tìm thấy ID nhóm hoặc vật phẩm.", ephemeral=True)

@tree.command(name="danhsachvatpham", description="🔐 [ADMIN] Xem toàn bộ vật phẩm (kể cả ẩn)")
async def ds_vp(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    em = discord.Embed(title="📋 TOÀN BỘ VẬT PHẨM SHOP", color=0xF39C12)
    for gid, g in d["shop_groups"].items():
        st = "✅" if g.get("enabled",True) else "🔴"
        lines = []
        for iid in g.get("item_ids",[]):
            it = d["shop_items"].get(iid)
            if not it: continue
            s2 = "✅" if it.get("enabled",True) else "🔴"
            lines.append(f"  {s2} `{iid}` **{it['name']}** — {it['price']:,}đ")
        em.add_field(name=f"{st} 📁 [{gid}] {g['name']}", value="\n".join(lines) or "_(trống)_", inline=False)
    lone = [(iid,it) for iid,it in d["shop_items"].items() if not it.get("group_id")]
    if lone:
        lines = [f"{'✅' if it.get('enabled',True) else '🔴'} `{iid}` **{it['name']}** — {it['price']:,}đ" for iid,it in lone]
        em.add_field(name="📦 Không có nhóm", value="\n".join(lines), inline=False)
    if not em.fields:
        em.description = "_Chưa có vật phẩm nào._"
    await interaction.response.send_message(embed=em, ephemeral=True)

# ╔══════════════════════════════════════════════════════════════╗
# ║              RATE ROBUX – TÍNH GIÁ THEO HÌNH THỨC           ║
# ╚══════════════════════════════════════════════════════════════╝

@tree.command(name="chinhrate", description="🔐 [ADMIN] Chỉnh rate Robux theo hình thức thanh toán")
@app_commands.describe(
    bank="Rate chuyển khoản Bank (VNĐ / 1 Robux)",
    card="Rate thẻ cào Card (VNĐ / 1 Robux)",
    web="Rate nạp qua Web (VNĐ / 1 Robux)",
    banner_url="Link ảnh banner hiện trong lệnh /rate (tuỳ chọn)",
)
async def chinh_rate(
    interaction: discord.Interaction,
    bank: int,
    card: int,
    web:  int,
    banner_url: str = "",
):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    if bank <= 0 or card <= 0 or web <= 0:
        await interaction.response.send_message("❌ Rate phải > 0!", ephemeral=True); return

    d = load(interaction.guild_id)
    d["config"]["rate_bank"] = bank
    d["config"]["rate_card"] = card
    d["config"]["rate_web"]  = web
    if banner_url:
        d["config"]["rate_banner_url"] = banner_url
    save(d, interaction.guild_id)

    em = discord.Embed(title="✅ Đã Cập Nhật Rate Robux", color=0xFF6B35, timestamp=datetime.utcnow())
    em.add_field(name="🏦 Bank",  value=f"`{bank:,} VNĐ / Robux`", inline=True)
    em.add_field(name="💳 Card",  value=f"`{card:,} VNĐ / Robux`", inline=True)
    em.add_field(name="🌐 Web",   value=f"`{web:,} VNĐ / Robux`",  inline=True)
    if banner_url:
        em.add_field(name="🖼️ Banner", value=f"[Xem ảnh]({banner_url})", inline=False)
    em.set_footer(text=f"Chỉnh bởi {interaction.user.display_name} | {fmt()}")
    await interaction.response.send_message(embed=em)


@tree.command(name="rate", description="💎 Tính giá Robux theo số lượng (bank / card / web)")
@app_commands.describe(so_robux="Số Robux muốn tính giá")
async def rate_cmd(interaction: discord.Interaction, so_robux: int):
    if so_robux <= 0:
        await interaction.response.send_message("❌ Số Robux phải > 0!", ephemeral=True); return

    d   = load(interaction.guild_id)
    cfg = d["config"]
    r_bank = cfg.get("rate_bank", 100)
    r_card = cfg.get("rate_card", 120)
    r_web  = cfg.get("rate_web",   90)

    p_bank = so_robux * r_bank
    p_card = so_robux * r_card
    p_web  = so_robux * r_web

    em = discord.Embed(
        title="💎 BẢNG GIÁ ROBUX",
        color=0xFF6B35,
        timestamp=datetime.utcnow(),
    )
    em.description = f"Giá cho **{so_robux:,} Robux**:"
    em.add_field(name="🏦 Chuyển khoản Bank",  value=f"**`{p_bank:,} VNĐ`**\n_(rate: {r_bank:,} VNĐ/rb)_",  inline=True)
    em.add_field(name="💳 Thẻ cào Card",        value=f"**`{p_card:,} VNĐ`**\n_(rate: {r_card:,} VNĐ/rb)_",  inline=True)
    em.add_field(name="🌐 Nạp qua Web",         value=f"**`{p_web:,} VNĐ`**\n_(rate: {r_web:,} VNĐ/rb)_",   inline=True)
    em.add_field(
        name="📌 Lưu ý",
        value=(
            "• Giá trên chưa bao gồm phí dịch vụ (nếu có)\n"
            "• Tạo ticket để đặt mua Robux"
        ),
        inline=False,
    )
    em.set_footer(text="Dùng /store để mở ticket mua Robux | Rate có thể thay đổi")

    # Ảnh banner (nếu admin đã đặt)
    banner = cfg.get("rate_banner_url", "")
    if banner:
        em.set_image(url=banner)

    await interaction.response.send_message(embed=em)

# ╔══════════════════════════════════════════════════════════════╗
# ║                  ROBUX COMMANDS                             ║
# ╚══════════════════════════════════════════════════════════════╝

@tree.command(name="robux", description="🔐 [ADMIN] Xem bảng giá thị trường Robux")
async def robux_market(interaction: discord.Interaction):
    if not is_admin(interaction.user) and not is_robux(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id); em = build_robux_embed(d)
    em.title = "💎 GIÁ THỊ TRƯỜNG ROBUX (ADMIN)"
    await interaction.response.send_message(embed=em, ephemeral=True)

@tree.command(name="addrobux", description="🔐 [ADMIN] Thêm gói Robux vào shop")
@app_commands.describe(label="Tên hiển thị", so_robux="Số lượng Robux", gia_ban="Giá VNĐ (0 = tự động)")
async def add_robux(interaction: discord.Interaction, label: str, so_robux: int, gia_ban: int = 0):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id); iid = new_id()
    auto = gia_ban == 0
    d["robux_items"][iid] = {"label":label,"amount_rb":so_robux,"price_vnd":gia_ban,"auto":auto}
    save(d, interaction.guild_id)
    rp    = d["robux_market"]["price"]
    final = round(so_robux * rp) if auto else gia_ban
    em = discord.Embed(title="✅ Đã Thêm Gói Robux", color=0xFF6B35)
    em.add_field(name="💎 Tên",      value=label,                                              inline=True)
    em.add_field(name="🔢 Số Robux", value=f"`{so_robux:,}`",                                 inline=True)
    em.add_field(name="💵 Giá",      value=f"`{final:,} VNĐ` {'(tự động)' if auto else '(cố định)'}", inline=True)
    em.add_field(name="🔑 ID",       value=f"`{iid}`",                                        inline=True)
    await interaction.response.send_message(embed=em)

@tree.command(name="xoarobux", description="🔐 [ADMIN] Xoá gói Robux")
@app_commands.describe(item_id="Gói Robux cần xoá – gõ tên để tìm")
@app_commands.autocomplete(item_id=ac_robux_items)
async def xoa_robux(interaction: discord.Interaction, item_id: str):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    if item_id not in d.get("robux_items",{}):
        await interaction.response.send_message("❌ Không tìm thấy.", ephemeral=True); return
    name = d["robux_items"].pop(item_id)["label"]; save(d, interaction.guild_id)
    await interaction.response.send_message(f"🗑️ Đã xoá gói Robux **{name}**.")

# ╔══════════════════════════════════════════════════════════════╗
# ║               NITRO BOOST STORE                             ║
# ╚══════════════════════════════════════════════════════════════╝

@tree.command(name="addnitro", description="🔐 [ADMIN] Thêm gói Nitro Boost vào bảng riêng")
@app_commands.describe(ten="VD: Nitro 1 tháng", gia="Giá bán (VNĐ)")
async def add_nitro(interaction: discord.Interaction, ten: str, gia: int):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    if gia <= 0:
        await interaction.response.send_message("❌ Giá phải > 0.", ephemeral=True); return
    d = load(interaction.guild_id); iid = new_id()
    d["nitro_items"][iid] = {"name": ten, "price": gia, "enabled": True}
    save(d, interaction.guild_id)
    icon = get_icon(d, "nitro", "🚀")

    # ── Đăng công khai gói mới kèm nút "🎫 Mua Ngay" riêng cho gói này ──
    post_embed = discord.Embed(title=f"{icon} GÓI NITRO MỚI", color=0xFF73FA, timestamp=datetime.utcnow())
    post_embed.description = f"**{ten}**\n💵 Giá: `{gia:,} VNĐ`"
    post_embed.set_footer(text="Bấm nút bên dưới để mua ngay!")
    buy_view = NitroBuyView(iid)
    try:
        await interaction.channel.send(embed=post_embed, view=buy_view)
        bot.add_view(buy_view)  # đăng ký persistent ngay, khỏi cần chờ bot khởi động lại
    except Exception as e:
        print(f"[AddNitro] Lỗi đăng công khai gói: {e}")

    await interaction.response.send_message(f"✅ Đã thêm **{ten}** : `{gia:,} VNĐ` {icon}\n📢 Đã đăng công khai kèm nút mua ngay ở kênh này.", ephemeral=True)

@tree.command(name="xoanitro", description="🔐 [ADMIN] Xoá gói Nitro Boost")
@app_commands.describe(item_id="Gói Nitro cần xoá – gõ tên để tìm")
@app_commands.autocomplete(item_id=ac_nitro_items)
async def xoa_nitro(interaction: discord.Interaction, item_id: str):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    if item_id not in d.get("nitro_items", {}):
        await interaction.response.send_message("❌ Không tìm thấy.", ephemeral=True); return
    name = d["nitro_items"].pop(item_id)["name"]; save(d, interaction.guild_id)
    await interaction.response.send_message(f"🗑️ Đã xoá gói Nitro **{name}**.")

@tree.command(name="nitro", description="🚀 Xem bảng giá Nitro Boost")
async def nitro_cmd(interaction: discord.Interaction):
    d = load(interaction.guild_id)
    embed = build_nitro_embed(d)
    greeting = (
        f"Chào {interaction.user.mention}! 👋 Chào mừng bạn đến với **Nitro Boost Store**.\n"
        f"Dưới đây là các gói Nitro hiện có kèm giá bán — chọn gói phù hợp và đặt hàng ngay nhé!\n\n"
    )
    embed.description = greeting + (embed.description or "")
    await interaction.response.send_message(embed=embed)

# ╔══════════════════════════════════════════════════════════════╗
# ║               CAPWALL STORE (hình nền / ảnh)                ║
# ╚══════════════════════════════════════════════════════════════╝

@tree.command(name="addcapwall", description="🔐 [ADMIN] Thêm sản phẩm vào Capwall Store (mở form nhập)")
async def add_capwall(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    await interaction.response.send_modal(CapwallAddModal())

@tree.command(name="xoacapwall", description="🔐 [ADMIN] Xoá sản phẩm Capwall")
@app_commands.describe(item_id="Sản phẩm cần xoá – gõ tên để tìm")
@app_commands.autocomplete(item_id=ac_capwall_items)
async def xoa_capwall(interaction: discord.Interaction, item_id: str):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    if item_id not in d.get("capwall_items", {}):
        await interaction.response.send_message("❌ Không tìm thấy.", ephemeral=True); return
    name = d["capwall_items"].pop(item_id)["name"]; save(d, interaction.guild_id)
    await interaction.response.send_message(f"🗑️ Đã xoá sản phẩm Capwall **{name}**.")

@tree.command(name="capwall", description="🏞️ Xem bảng sản phẩm Capwall Store")
async def capwall_cmd(interaction: discord.Interaction):
    d = load(interaction.guild_id)
    await interaction.response.send_message(embeds=build_capwall_embeds(d))

# ╔══════════════════════════════════════════════════════════════╗
# ║               CÀY THUÊ COMMANDS                             ║
# ╚══════════════════════════════════════════════════════════════╝

@tree.command(name="addcaythue", description="🔐 [ADMIN] Thêm dịch vụ cày thuê")
@app_commands.describe(ten="Tên dịch vụ", gia="Giá (VNĐ)", nhom_id="Nhóm (tuỳ chọn) – gõ tên để tìm")
@app_commands.autocomplete(nhom_id=ac_farm_groups)
async def add_caythue(interaction: discord.Interaction, ten: str, gia: int, nhom_id: str = ""):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id); iid = new_id()
    d["farming_items"][iid] = {"name":ten,"price":gia,"group_id":nhom_id or None,"enabled":True}
    if nhom_id and nhom_id in d["farming_groups"]:
        d["farming_groups"][nhom_id]["item_ids"].append(iid)
    save(d, interaction.guild_id)
    em = discord.Embed(title="✅ Đã Thêm Dịch Vụ Cày Thuê", color=0x9B59B6)
    em.add_field(name="⛏️ Tên", value=ten,            inline=True)
    em.add_field(name="💵 Giá", value=f"{gia:,} VNĐ", inline=True)
    em.add_field(name="🔑 ID",  value=f"`{iid}`",     inline=True)
    await interaction.response.send_message(embed=em)

@tree.command(name="nhomcaythue", description="🔐 [ADMIN] Tạo nhóm dịch vụ cày thuê")
@app_commands.describe(ten="Tên nhóm", image="Link ảnh (tuỳ chọn)")
async def nhom_caythue(interaction: discord.Interaction, ten: str, image: str = ""):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id); gid = new_id()
    d["farming_groups"][gid] = {"name":ten,"image":image,"item_ids":[],"enabled":True}
    save(d, interaction.guild_id)
    em = discord.Embed(title="✅ Đã Tạo Nhóm Cày Thuê", color=0x9B59B6)
    em.add_field(name="📁 Tên", value=ten,       inline=True)
    em.add_field(name="🔑 ID", value=f"`{gid}`", inline=True)
    if image: em.set_image(url=image)
    em.set_footer(text=f"Dùng /addcaythue nhom_id:{gid} để thêm dịch vụ vào nhóm")
    await interaction.response.send_message(embed=em)

@tree.command(name="xoacaythue", description="🔐 [ADMIN] Xoá dịch vụ cày thuê")
@app_commands.describe(item_id="Dịch vụ cần xoá – gõ tên để tìm")
@app_commands.autocomplete(item_id=ac_farm_items)
async def xoa_caythue(interaction: discord.Interaction, item_id: str):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    if item_id not in d["farming_items"]:
        await interaction.response.send_message("❌ Không tìm thấy.", ephemeral=True); return
    name = d["farming_items"].pop(item_id)["name"]
    for g in d["farming_groups"].values():
        if item_id in g["item_ids"]: g["item_ids"].remove(item_id)
    save(d, interaction.guild_id)
    await interaction.response.send_message(f"🗑️ Đã xoá dịch vụ **{name}**.")

@tree.command(name="xoanhomcaythue", description="🔐 [ADMIN] Xoá nhóm cày thuê và các dịch vụ trong đó")
@app_commands.describe(nhom_id="Nhóm cần xoá – gõ tên để tìm")
@app_commands.autocomplete(nhom_id=ac_farm_groups)
async def xoa_nhom_caythue(interaction: discord.Interaction, nhom_id: str):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    if nhom_id not in d["farming_groups"]:
        await interaction.response.send_message("❌ Không tìm thấy nhóm.", ephemeral=True); return
    g   = d["farming_groups"].pop(nhom_id); cnt = 0
    for iid in g.get("item_ids",[]):
        if iid in d["farming_items"]: del d["farming_items"][iid]; cnt += 1
    save(d, interaction.guild_id)
    await interaction.response.send_message(f"🗑️ Đã xoá nhóm cày thuê **{g['name']}** và `{cnt}` dịch vụ.")

# ╔══════════════════════════════════════════════════════════════╗
# ║               TÀI CHÍNH – NẠP/TRỪ/MÃ GIẢM GIÁ             ║
# ╚══════════════════════════════════════════════════════════════╝

@tree.command(name="naptien", description="🔐 [ADMIN] Nạp tiền thủ công cho người dùng")
@app_commands.describe(thanh_vien="Thành viên được nạp", so_tien="Số tiền nạp (VNĐ)", ghi_chu="Ghi chú")
async def nap_tien(interaction: discord.Interaction, thanh_vien: discord.Member, so_tien: int, ghi_chu: str = "Nạp thủ công"):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    if so_tien <= 0:
        await interaction.response.send_message("❌ Số tiền phải > 0.", ephemeral=True); return
    d = load(interaction.guild_id); u = ensure_user(d, thanh_vien.id)
    u["balance"]         = u.get("balance",0) + so_tien
    u["total_deposited"] = u.get("total_deposited",0) + so_tien
    u["history"].insert(0,{"type":"deposit","amount":so_tien,"note":ghi_chu,"date":fmt()})
    log_revenue(d, thanh_vien.id, so_tien, f"Nạp thủ công: {ghi_chu}")
    save(d, interaction.guild_id)
    em = discord.Embed(title="💰 NẠP TIỀN THÀNH CÔNG", color=0x2ECC71)
    em.description = (f"👤 **Người nhận:** {thanh_vien.mention}\n💵 **Số tiền:** `+{so_tien:,} VNĐ`\n"
                      f"💳 **Số dư:** `{u['balance']:,} VNĐ`\n📝 **Ghi chú:** {ghi_chu}")
    await interaction.response.send_message(embed=em)
    try: await thanh_vien.send(embed=discord.Embed(title="💰 Bạn được cộng tiền!", description=f"`+{so_tien:,} VNĐ` — {ghi_chu}", color=0x2ECC71))
    except: pass

@tree.command(name="trutien", description="🔐 [ADMIN] Trừ tiền thủ công của người dùng")
@app_commands.describe(thanh_vien="Thành viên bị trừ", so_tien="Số tiền trừ (VNĐ)", ghi_chu="Ghi chú")
async def tru_tien(interaction: discord.Interaction, thanh_vien: discord.Member, so_tien: int, ghi_chu: str = "Trừ thủ công"):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    if so_tien <= 0:
        await interaction.response.send_message("❌ Số tiền phải > 0.", ephemeral=True); return
    d = load(interaction.guild_id); u = ensure_user(d, thanh_vien.id)
    u["balance"] = max(0, u.get("balance",0) - so_tien)
    u["history"].insert(0,{"type":"deduct","amount":-so_tien,"note":ghi_chu,"date":fmt()})
    save(d, interaction.guild_id)
    em = discord.Embed(title="💸 TRỪ TIỀN THÀNH CÔNG", color=0xE74C3C)
    em.description = (f"👤 **Người bị trừ:** {thanh_vien.mention}\n💵 **Số tiền:** `-{so_tien:,} VNĐ`\n"
                      f"💳 **Số dư còn:** `{u['balance']:,} VNĐ`\n📝 **Ghi chú:** {ghi_chu}")
    await interaction.response.send_message(embed=em)

@tree.command(name="thongtinnap", description="💵 Xem thông tin nạp tiền (bỏ trống = xem của bạn, Admin có thể xem người khác)")
@app_commands.describe(thanh_vien="Bỏ trống để xem của chính bạn. Chỉ Admin mới xem được người khác")
async def thong_tin_nap(interaction: discord.Interaction, thanh_vien: discord.Member = None):
    target = thanh_vien or interaction.user
    if target.id != interaction.user.id and not is_admin(interaction.user):
        await interaction.response.send_message("❌ Bạn chỉ có thể xem thông tin nạp tiền của chính mình!", ephemeral=True); return
    d = load(interaction.guild_id)
    u = d.get("users", {}).get(str(target.id), {})
    em = discord.Embed(title=f"💵 THÔNG TIN NẠP TIỀN – {target.display_name}", color=0x3498DB, timestamp=datetime.utcnow())
    em.set_thumbnail(url=target.display_avatar.url)
    em.add_field(name="💰 Tổng đã nạp", value=f"`{u.get('total_deposited',0):,} VNĐ`", inline=True)
    em.add_field(name="💳 Số dư hiện tại", value=f"`{u.get('balance',0):,} VNĐ`", inline=True)
    em.add_field(name="🛒 Tổng đơn hàng", value=f"`{len(u.get('orders',[]))}`", inline=True)
    hist = u.get("history", [])[:10]
    if hist:
        lines = []
        for h in hist:
            amt  = h.get("amount", 0)
            sign = "+" if amt >= 0 else ""
            lines.append(f"`{h.get('date','?')}` — **{sign}{amt:,} VNĐ** — {h.get('note','')}")
        em.add_field(name="📜 10 giao dịch gần nhất", value="\n".join(lines), inline=False)
    else:
        em.description = "_Chưa có giao dịch nạp/trừ tiền nào._"
    await interaction.response.send_message(embed=em, ephemeral=True)

@tree.command(name="magiamgia", description="🔐 [ADMIN] Tạo mã giảm giá")
@app_commands.describe(ma="Tên mã code", phan_tram="% giảm (1-99)", so_ngay="Số ngày hiệu lực", so_lan="Số lần dùng tối đa")
async def ma_giam_gia(interaction: discord.Interaction, ma: str, phan_tram: int, so_ngay: int, so_lan: int):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    if not (1 <= phan_tram <= 99):
        await interaction.response.send_message("❌ Phần trăm phải từ 1-99.", ephemeral=True); return
    d = load(interaction.guild_id); ma = ma.upper()
    exp = (datetime.now() + timedelta(days=so_ngay)).strftime("%d/%m/%Y")
    d["discount_codes"][ma] = {"pct":phan_tram,"uses_left":so_lan,"expires_at":exp,"created_at":fmt()}
    save(d, interaction.guild_id)
    em = discord.Embed(title="🎟️ Tạo Mã Giảm Giá Thành Công", color=0x2ECC71)
    em.add_field(name="🏷️ Mã code",  value=f"`{ma}`",         inline=True)
    em.add_field(name="💰 Giảm giá", value=f"`{phan_tram}%`", inline=True)
    em.add_field(name="📅 Hết hạn",  value=exp,                inline=True)
    em.add_field(name="🔢 Số lần",   value=f"`{so_lan}`",     inline=True)
    em.set_footer(text="Khách dùng nút 🏷️ Mã Giảm Giá trong ticket để nhập mã")
    await interaction.response.send_message(embed=em)

@tree.command(name="xoamgg", description="🔐 [ADMIN] Xoá mã giảm giá")
@app_commands.describe(ma="Tên mã code cần xoá")
async def xoa_mgg(interaction: discord.Interaction, ma: str):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id); ma = ma.upper()
    if ma not in d["discount_codes"]:
        await interaction.response.send_message(f"❌ Không tìm thấy mã `{ma}`.", ephemeral=True); return
    del d["discount_codes"][ma]; save(d, interaction.guild_id)
    await interaction.response.send_message(f"🗑️ Đã xoá mã giảm giá `{ma}`.")

@tree.command(name="danhsachmgg", description="🔐 [ADMIN] Xem danh sách mã giảm giá")
async def ds_mgg(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id); codes = d.get("discount_codes", {})
    em = discord.Embed(title="🎟️ DANH SÁCH MÃ GIẢM GIÁ", color=0x9B59B6)
    if not codes:
        em.description = "_Chưa có mã giảm giá nào._"
    else:
        for code, info in codes.items():
            status = "✅" if info.get("uses_left", 0) > 0 else "🔴 Hết lượt"
            em.add_field(
                name=f"🏷️ `{code}` {status}",
                value=f"Giảm: **{info['pct']}%** | Còn: `{info.get('uses_left',0)}` lượt | Hết hạn: `{info.get('expires_at','?')}`",
                inline=False,
            )
    await interaction.response.send_message(embed=em, ephemeral=True)

# ╔══════════════════════════════════════════════════════════════╗
# ║               FEEDBACK – ĐÁNH GIÁ DỊCH VỤ                  ║
# ╚══════════════════════════════════════════════════════════════╝

@tree.command(name="setfeedback", description="🔐 [ADMIN] Chọn kênh nhận đánh giá từ khách")
@app_commands.describe(kenh="Kênh text nhận feedback")
async def set_feedback(interaction: discord.Interaction, kenh: discord.TextChannel):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    d["config"]["feedback_channel_id"] = kenh.id
    save(d, interaction.guild_id)
    await interaction.response.send_message(
        embed=discord.Embed(
            title="✅ Đã Cài Kênh Feedback",
            description=f"Tất cả đánh giá của khách sẽ gửi vào {kenh.mention}",
            color=0x2ECC71,
        )
    )

@tree.command(name="setticketlog", description="🔐 [ADMIN] Chọn kênh lưu lịch sử chat khi đóng ticket")
@app_commands.describe(kenh="Kênh text nhận file lịch sử (transcript) ticket")
async def set_ticket_log(interaction: discord.Interaction, kenh: discord.TextChannel):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    d["config"]["ticket_log_channel_id"] = kenh.id
    save(d, interaction.guild_id)
    await interaction.response.send_message(
        embed=discord.Embed(
            title="✅ Đã Cài Kênh Lưu Lịch Sử Ticket",
            description=f"Mỗi khi ticket đóng, toàn bộ tin nhắn (kèm hình ảnh & tên người dùng) sẽ được xuất thành file và gửi vào {kenh.mention}.",
            color=0x2ECC71,
        )
    )

@tree.command(name="setpingrole", description="🔐 [ADMIN] Chọn Role sẽ được ping mỗi khi có ticket mới")
@app_commands.describe(role="Role cần ping khi khách mở ticket (VD: role Staff/CTV trực)")
async def set_ping_role(interaction: discord.Interaction, role: discord.Role):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    d["config"]["ticket_ping_role_id"] = role.id
    save(d, interaction.guild_id)
    await interaction.response.send_message(
        embed=discord.Embed(
            title="✅ Đã Cài Role Ping Ticket",
            description=f"Từ giờ mỗi khi có ticket mới (Mua Hàng / Order Acc), **{role.mention}** sẽ được ping trong ticket đó.",
            color=0x2ECC71,
        )
    )

@tree.command(name="tatpingrole", description="🔐 [ADMIN] Tắt tính năng ping role khi có ticket mới")
async def tat_ping_role(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    d["config"]["ticket_ping_role_id"] = 0
    save(d, interaction.guild_id)
    await interaction.response.send_message(
        embed=discord.Embed(
            title="✅ Đã Tắt Ping Role",
            description="Từ giờ ticket mới sẽ không ping Role nào nữa.",
            color=0x2ECC71,
        )
    )

@tree.command(name="seticon", description="🔐 [ADMIN] Đổi icon (emoji, kể cả emoji động) cho 1 nút trên panel store")
@app_commands.describe(vi_tri="Nút cần đổi icon", emoji="Dán emoji vào đây — gõ :tên_emoji: để Discord gợi ý, dùng được cả emoji động của server bạn")
async def set_icon(
    interaction: discord.Interaction,
    vi_tri: Literal["mua_hang", "order_acc", "support", "robux1s", "nitro", "capwall"],
    emoji: str,
):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    try:
        parsed = discord.PartialEmoji.from_str(emoji.strip())
    except Exception:
        parsed = None
    if not parsed or (not parsed.is_unicode_emoji() and parsed.id is None):
        await interaction.response.send_message(
            "❌ Không đọc được emoji này. Gõ `:tên_emoji:` để Discord tự gợi ý, hoặc dán 1 emoji thường.",
            ephemeral=True
        ); return

    warn = ""
    if parsed.id and not bot.get_emoji(parsed.id):
        warn = "\n⚠️ Bot chưa thấy emoji này ở server nào bot có mặt — icon có thể lỗi trên nút. Nên dùng emoji của chính server bạn."

    d = load(interaction.guild_id)
    d["config"].setdefault("icons", {})[vi_tri] = str(parsed)
    save(d, interaction.guild_id)
    await interaction.response.send_message(
        embed=discord.Embed(
            title="✅ Đã Đổi Icon",
            description=f"Nút **{vi_tri}** giờ dùng: {parsed}{warn}\n\n*(Panel cũ đã gửi trước đó sẽ không tự đổi icon — dùng `/store` để gửi lại panel mới)*",
            color=0x2ECC71,
        ), ephemeral=True
    )
@app_commands.describe(ngan_hang="Tên ngân hàng (VD: MBBank, Techcombank...)", so_tai_khoan="Số tài khoản", ten_tai_khoan="Tên chủ tài khoản (không dấu)")
async def set_bank(interaction: discord.Interaction, ngan_hang: str, so_tai_khoan: str, ten_tai_khoan: str):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id)
    d["config"]["bank_name"]            = ngan_hang
    d["config"]["bank_account_number"]  = so_tai_khoan
    d["config"]["bank_account_name"]    = ten_tai_khoan
    save(d, interaction.guild_id)
    await interaction.response.send_message(
        embed=discord.Embed(
            title="✅ Đã Cài Tài Khoản Ngân Hàng",
            description=f"🏦 {ngan_hang.upper()}\n📱 `{so_tai_khoan}`\n👤 {ten_tai_khoan.upper()}\n\nKhách giờ có thể dùng `/naptienqr` để tự nạp tiền.",
            color=0x2ECC71,
        ), ephemeral=True
    )

# Giờ đây nút đánh giá ⭐ (FeedbackStarView) sẽ TỰ ĐỘNG hiện ra ngay khi
# Admin/CTV bấm nút "Hoàn Thành" đơn hàng (xem AdminOrderView & CTVProcessView).

# ╔══════════════════════════════════════════════════════════════╗
# ║               THỐNG KÊ DOANH THU                           ║
# ╚══════════════════════════════════════════════════════════════╝

@tree.command(name="thongke", description="🔐 [ADMIN] Thống kê doanh thu")
@app_commands.describe(khoang="Khoảng thời gian")
@app_commands.choices(khoang=[
    app_commands.Choice(name="Hôm nay",  value="today"),
    app_commands.Choice(name="7 ngày",   value="week"),
    app_commands.Choice(name="30 ngày",  value="month"),
    app_commands.Choice(name="Tất cả",   value="all"),
])
async def thong_ke(interaction: discord.Interaction, khoang: str = "today"):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    data  = load(interaction.guild_id); logs = data.get("revenue_log",[])
    now   = datetime.now()
    cuts  = {"today": now.replace(hour=0,minute=0,second=0), "week": now-timedelta(days=7), "month": now-timedelta(days=30)}
    cut   = cuts.get(khoang)
    labels= {"today":f"Hôm nay ({now.strftime('%d/%m/%Y')})","week":"7 ngày qua","month":"30 ngày qua","all":"Tất cả"}
    def pdt(s):
        try: return datetime.strptime(s, "%d/%m/%Y %H:%M:%S")
        except: return None
    filtered = [l for l in logs if cut is None or (pdt(l.get("datetime","")) or datetime.min) >= cut]
    total    = sum(l["amount"] for l in filtered)
    from collections import defaultdict
    daily: dict = defaultdict(int)
    for l in filtered: daily[l.get("date","?")] += l["amount"]
    day_sorted = sorted(daily.items(), key=lambda x: datetime.strptime(x[0],"%d/%m/%Y") if "/" in x[0] else datetime.min, reverse=True)[:10]
    user_t: dict = defaultdict(int)
    for l in filtered: user_t[l["user_id"]] += l["amount"]
    top5 = sorted(user_t.items(), key=lambda x: x[1], reverse=True)[:5]
    orders_done = [o for o in data.get("orders",{}).values() if o.get("status")=="completed"]
    if cut:
        orders_done = [o for o in orders_done if (pdt(o.get("completed","")) or datetime.min) >= cut]
    em = discord.Embed(
        title="📊 THỐNG KÊ DOANH THU",
        description=f"📅 {labels.get(khoang,'')} | {len(filtered)} giao dịch",
        color=0xF39C12, timestamp=datetime.utcnow()
    )
    em.add_field(name="💰 Tổng doanh thu", value=f"`{total:,} VNĐ`",         inline=True)
    em.add_field(name="🛒 Đơn hoàn thành", value=f"`{len(orders_done)} đơn`", inline=True)
    if day_sorted:
        em.add_field(name="📈 Doanh thu theo ngày", value="\n".join(f"`{dy}` — **{v:,} VNĐ**" for dy,v in day_sorted), inline=False)
    if top5:
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]
        lines  = []
        for i,(uid,t) in enumerate(top5):
            m = interaction.guild.get_member(int(uid)) if uid.isdigit() else None
            lines.append(f"{medals[i]} **{m.display_name if m else uid}** — `{t:,} VNĐ`")
        em.add_field(name="👑 Top 5 khách hàng", value="\n".join(lines), inline=False)
    em.set_footer(text=f"Xem bởi {interaction.user.display_name} | {fmt()}")
    await interaction.followup.send(embed=em, ephemeral=True)

# ╔══════════════════════════════════════════════════════════════╗
# ║               PHÂN QUYỀN                                   ║
# ╚══════════════════════════════════════════════════════════════╝

@tree.command(name="trao_quyen_admin", description="🔐 [ADMIN] Trao quyền Admin shop cho Role")
@app_commands.describe(role="Role được trao quyền Admin")
async def trao_admin(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Chỉ Server Owner/Admin mới làm được!", ephemeral=True); return
    d = load(interaction.guild_id); d["config"]["admin_role_id"] = role.id; save(d, interaction.guild_id)
    await interaction.response.send_message(f"✅ Đã trao quyền Admin cho **{role.name}**.")

@tree.command(name="quyen_caythue", description="🔐 [ADMIN] Trao quyền nhân viên cày thuê cho Role")
@app_commands.describe(role="Role được trao quyền cày thuê")
async def trao_caythue(interaction: discord.Interaction, role: discord.Role):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id); d["config"]["caythue_role_id"] = role.id; save(d, interaction.guild_id)
    await interaction.response.send_message(f"✅ Đã trao quyền cày thuê cho **{role.name}**.")

@tree.command(name="quyen_robux", description="🔐 [ADMIN] Trao quyền nhân viên Robux cho Role")
@app_commands.describe(role="Role được trao quyền Robux")
async def trao_robux(interaction: discord.Interaction, role: discord.Role):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d = load(interaction.guild_id); d["config"]["robux_role_id"] = role.id; save(d, interaction.guild_id)
    await interaction.response.send_message(f"✅ Đã trao quyền Robux cho **{role.name}**.")

# ╔══════════════════════════════════════════════════════════════╗
# ║              LỆNH NGƯỜI DÙNG (EVERYONE)                    ║
# ╚══════════════════════════════════════════════════════════════╝

@tree.command(name="qr", description="🏦 Tạo QR chuyển khoản ngân hàng")
@app_commands.describe(
    ngan_hang="Tên ngân hàng (VD: MBBank, Techcombank, VPBank...)",
    so_tai_khoan="Số tài khoản hoặc SĐT",
    ten_tai_khoan="Tên chủ tài khoản",
    so_tien="Số tiền (tuỳ chọn, VNĐ)",
    noi_dung="Nội dung chuyển khoản (tuỳ chọn)",
)
async def qr_cmd(
    interaction: discord.Interaction,
    ngan_hang: str,
    so_tai_khoan: str,
    ten_tai_khoan: str,
    so_tien: int = 0,
    noi_dung: str = "",
):
    bin_code = get_bank_bin(ngan_hang)
    params   = f"accountName={ten_tai_khoan.upper().replace(' ','+')}"
    if so_tien > 0: params += f"&amount={so_tien}"
    if noi_dung:    params += f"&addInfo={noi_dung.replace(' ','+')}"
    qr_url = f"https://img.vietqr.io/image/{bin_code}-{so_tai_khoan}-qr_only.jpg?{params}"
    em = discord.Embed(title="🏦 QR CHUYỂN KHOẢN", color=0x00D4FF, timestamp=datetime.utcnow())
    em.add_field(name="🏦 Ngân hàng", value=ngan_hang.upper(),     inline=True)
    em.add_field(name="👤 Tên TK",    value=ten_tai_khoan.upper(),  inline=True)
    em.add_field(name="📱 Số TK",     value=f"`{so_tai_khoan}`",    inline=True)
    if so_tien > 0:
        em.add_field(name="💵 Số tiền", value=f"`{so_tien:,} VNĐ`", inline=True)
    if noi_dung:
        em.add_field(name="📝 Nội dung", value=noi_dung,             inline=True)
    em.set_image(url=qr_url)
    em.set_footer(text="Quét mã QR để chuyển khoản | VietQR")
    await interaction.response.send_message(embed=em)

# ── !qr [số tiền] – QR nhanh với ngân hàng mặc định (không cần gõ đủ 3 thông tin) ──
_DEFAULT_BANK          = "MBBank"
_DEFAULT_ACCOUNT_NUM   = "0789094477"
_DEFAULT_ACCOUNT_NAME  = "Phạm Nguyễn Hoàng Long"

@bot.command(name="qr")
async def qr_prefix_cmd(ctx: commands.Context, *, so_tien: str = ""):
    """!qr [số tiền] – Tạo nhanh QR chuyển khoản với ngân hàng mặc định của shop."""
    raw    = re.sub(r"[^\d]", "", so_tien or "")
    amount = int(raw) if raw else 0

    bin_code = get_bank_bin(_DEFAULT_BANK)
    params   = f"accountName={_DEFAULT_ACCOUNT_NAME.upper().replace(' ', '+')}"
    if amount > 0:
        params += f"&amount={amount}"
    qr_url = f"https://img.vietqr.io/image/{bin_code}-{_DEFAULT_ACCOUNT_NUM}-qr_only.jpg?{params}"

    em = discord.Embed(title="🏦 QR CHUYỂN KHOẢN", color=0x00D4FF, timestamp=datetime.utcnow())
    em.add_field(name="🏦 Ngân hàng", value=_DEFAULT_BANK.upper(),        inline=True)
    em.add_field(name="👤 Tên TK",   value=_DEFAULT_ACCOUNT_NAME.upper(), inline=True)
    em.add_field(name="📱 Số TK",    value=f"`{_DEFAULT_ACCOUNT_NUM}`",   inline=True)
    if amount > 0:
        em.add_field(name="💵 Số tiền", value=f"`{amount:,} VNĐ`", inline=True)
    em.set_image(url=qr_url)
    em.set_footer(text="Quét mã QR để chuyển khoản | VietQR")
    await ctx.send(embed=em)

@tree.command(name="naptienqr", description="🔐 [ADMIN] Tạo QR nạp tiền cho 1 khách cụ thể")
@app_commands.describe(thanh_vien="Khách cần tạo QR nạp tiền", so_tien="Số tiền muốn nạp (tuỳ chọn)")
async def nap_tien_qr(interaction: discord.Interaction, thanh_vien: discord.Member, so_tien: int = 0):
    if not is_admin(interaction.user):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True); return
    d   = load(interaction.guild_id)
    cfg = d["config"]
    if not cfg.get("bank_account_number"):
        await interaction.response.send_message(
            "⚠️ Shop chưa cấu hình tài khoản ngân hàng. Dùng `/setbank` trước.",
            ephemeral=True
        ); return

    noi_dung = f"NAP{thanh_vien.id}"
    bin_code = get_bank_bin(cfg["bank_name"])
    params   = f"accountName={cfg['bank_account_name'].upper().replace(' ','+')}&addInfo={noi_dung}"
    if so_tien > 0: params += f"&amount={so_tien}"
    qr_url = f"https://img.vietqr.io/image/{bin_code}-{cfg['bank_account_number']}-qr_only.jpg?{params}"

    em = discord.Embed(title=f"💵 QR NẠP TIỀN CHO {thanh_vien.display_name}", color=0x00D4FF, timestamp=datetime.utcnow())
    em.description = (
        f"Gửi QR này cho {thanh_vien.mention} để chuyển khoản. Nên giữ nguyên nội dung `{noi_dung}`.\n\n"
        f"Sau khi khách chuyển khoản xong, vào ticket của khách bấm nút **💰 Nạp Tiền** để cộng số dư."
    )
    em.add_field(name="🏦 Ngân hàng", value=cfg["bank_name"].upper(), inline=True)
    em.add_field(name="📱 Số TK",     value=f"`{cfg['bank_account_number']}`", inline=True)
    if so_tien > 0:
        em.add_field(name="💵 Số tiền", value=f"`{so_tien:,} VNĐ`", inline=True)
    em.add_field(name="📝 Nội dung CK", value=f"`{noi_dung}`", inline=False)
    em.set_image(url=qr_url)
    em.set_footer(text="Số dư chỉ cộng khi Admin bấm 💰 Nạp Tiền trong ticket, không tự động")
    await interaction.response.send_message(embed=em)

@tree.command(name="lichsunap", description="💵 Xem lịch sử nạp tiền của bạn")
async def lich_su_nap(interaction: discord.Interaction):
    d = load(interaction.guild_id); u = d.get("users",{}).get(str(interaction.user.id),{})
    hist = [h for h in u.get("history",[]) if h.get("type") == "deposit"]
    em   = discord.Embed(title=f"💵 LỊCH SỬ NẠP TIỀN – {interaction.user.display_name}", color=0x3498DB, timestamp=datetime.utcnow())
    em.add_field(name="💰 Tổng nạp", value=f"`{u.get('total_deposited',0):,} VNĐ`", inline=True)
    em.add_field(name="💳 Số dư",    value=f"`{u.get('balance',0):,} VNĐ`",         inline=True)
    if hist:
        lines = [f"💵 `{h['date']}` — **+{h['amount']:,} VNĐ** — {h.get('note','')}" for h in hist[:10]]
        em.add_field(name="📜 10 lần nạp gần nhất", value="\n".join(lines), inline=False)
    else:
        em.description = "_Chưa có lịch sử nạp tiền._"
    await interaction.response.send_message(embed=em, ephemeral=True)

@tree.command(name="donhang", description="🛒 Xem lịch sử đơn hàng của bạn")
async def don_hang(interaction: discord.Interaction):
    d    = load(interaction.guild_id); u = d.get("users",{}).get(str(interaction.user.id),{})
    ords = u.get("orders",[])
    em   = discord.Embed(title=f"🛒 ĐƠN HÀNG – {interaction.user.display_name}", color=0x9B59B6, timestamp=datetime.utcnow())
    if not ords:
        em.description = "_Chưa có đơn hàng nào._"
    else:
        pending   = [o for o in ords if o.get("status") == "pending"]
        completed = [o for o in ords if o.get("status") == "completed"]
        if pending:
            em.add_field(name=f"⏳ Đang chờ ({len(pending)} đơn)",
                         value="\n".join(f"`{o.get('oid','?')}` **{o['item']}** — {o['price']:,}đ" for o in pending[:5]),
                         inline=False)
        if completed:
            em.add_field(name=f"✅ Đã hoàn thành ({len(completed)} đơn)",
                         value="\n".join(f"`{o.get('oid','?')}` **{o['item']}** — {o['price']:,}đ — {o.get('date','')}" for o in completed[:8]),
                         inline=False)
    await interaction.response.send_message(embed=em, ephemeral=True)

@tree.command(name="rank", description="🏆 Bảng xếp hạng khách nạp tiền nhiều nhất")
async def rank_cmd(interaction: discord.Interaction):
    d     = load(interaction.guild_id); users = d.get("users",{})
    ranks = sorted(users.items(), key=lambda x: x[1].get("total_deposited",0), reverse=True)[:10]
    em    = discord.Embed(title="🏆 BẢNG XẾP HẠNG NẠP TIỀN", color=0xF1C40F, timestamp=datetime.utcnow())
    if not ranks:
        em.description = "_Chưa có ai nạp tiền._"
    else:
        medals = ["🥇","🥈","🥉"] + [f"**{i}.**" for i in range(4,11)]
        lines  = []
        for i,(uid,u) in enumerate(ranks):
            m    = interaction.guild.get_member(int(uid)) if uid.isdigit() else None
            name = m.display_name if m else f"ID:{uid}"
            lines.append(f"{medals[i]} **{name}** — `{u.get('total_deposited',0):,} VNĐ`")
        em.description = "\n".join(lines)
    em.set_footer(text=f"Cập nhật: {fmt()}")
    await interaction.response.send_message(embed=em)

# ╔══════════════════════════════════════════════════════════════╗
# ║                    ON_READY & SYNC                          ║
# ╚══════════════════════════════════════════════════════════════╝

async def _enforce_guild_lock(guild: discord.Guild, *, just_joined: bool = False):
    if not ALLOWED_GUILD_IDS or guild.id in ALLOWED_GUILD_IDS:
        return
    print(f"🚫 [Guild-Lock] Server lạ '{guild.name}' (ID {guild.id}) không được phép — đang rời...")
    if OWNER_ID:
        try:
            owner = await bot.fetch_user(OWNER_ID)
            await owner.send(
                f"🚫 Bot vừa bị mời vào server không được phép:\n"
                f"• Tên: `{guild.name}`\n• ID: `{guild.id}`\n• Chủ server: `{guild.owner_id}`\n"
                f"Bot đã tự động rời khỏi server này."
            )
        except Exception:
            pass
    if just_joined:
        try:
            ch = guild.system_channel or next(
                (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None
            )
            if ch:
                await ch.send("🚫 Bot này là bot độc quyền riêng, chỉ hoạt động ở 1 server đã đăng ký. Đang rời server...")
        except Exception:
            pass
    try:
        await guild.leave()
    except Exception as e:
        print(f"⚠️ [Guild-Lock] Không thể rời server {guild.id}: {e}")

@bot.event
async def on_guild_join(guild: discord.Guild):
    await _enforce_guild_lock(guild, just_joined=True)

@bot.event
async def on_ready():
    print("╔════════════════════════════════════╗")
    print(f"║  ✅ Shop Bot v2 khởi động!         ║")
    print(f"║  👤 {bot.user.name:<32}║")
    print(f"║  🆔 {str(bot.user.id):<32}║")
    print(f"║  🍃 {'MongoDB Atlas ✅' if MONGO_OK else 'data.json ⚠️':<32}║")
    print("╚════════════════════════════════════╝")

    if ALLOWED_GUILD_IDS:
        for g in list(bot.guilds):
            if g.id not in ALLOWED_GUILD_IDS:
                await _enforce_guild_lock(g)
        print(f"🔒 [Guild-Lock] Đang bật — chỉ hoạt động ở {len(ALLOWED_GUILD_IDS)} server: {sorted(ALLOWED_GUILD_IDS)}")
    else:
        print("⚠️ [Guild-Lock] CHƯA bật (ALLOWED_GUILD_IDS trống)!")

    # Đăng ký persistent views
    bot.add_view(OpenTicketView())
    bot.add_view(AdminOrderView())
    bot.add_view(CTVProcessView())
    bot.add_view(CloseTicketView())
    bot.add_view(FeedbackStarView())
    bot.add_view(AccOrderStaffView())

    try:
        total_capwall = total_nitro = total_pending = 0
        for g in bot.guilds:
            d = load(g.id)
            for iid in d.get("capwall_items", {}).keys():
                bot.add_view(CapwallBuyView(iid))
            total_capwall += len(d.get("capwall_items", {}))
            for iid in d.get("nitro_items", {}).keys():
                bot.add_view(NitroBuyView(iid))
            total_nitro += len(d.get("nitro_items", {}))
            total_pending += sum(1 for o in d.get("orders", {}).values() if o.get("status") == "pending")
        print(f"🎫 Đã đăng ký lại {total_capwall} nút Mua Ngay Capwall (tất cả server).")
        print(f"🎫 Đã đăng ký lại {total_nitro} nút Mua Ngay Nitro (tất cả server).")
        print(f"📦 Sẵn sàng quản lý {total_pending} đơn hàng đang chờ (tất cả server).")
    except Exception as e:
        print(f"⚠️ Lỗi nạp dữ liệu: {e}")

    if not update_robux_market.is_running():
        update_robux_market.start()
    if not _heartbeat_tick.is_running():
        _heartbeat_tick.start()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="🛍️ Shop | /rate"))

    try:
        if GUILD_ID:
            go = discord.Object(id=GUILD_ID)
            tree.copy_global_to(guild=go)
            s  = await tree.sync(guild=go)
            print(f"✅ Sync {len(s)} lệnh → Guild {GUILD_ID}")
        else:
            s = await tree.sync()
            print(f"✅ Sync {len(s)} lệnh → Global (mất ~1 tiếng để Discord cập nhật)")
    except Exception as e:
        print(f"❌ Sync lỗi: {e}")
@bot.event
async def on_disconnect(): print(f"[{fmt()}] ⚠️ Mất kết nối...")
@bot.event
async def on_resumed():    print(f"[{fmt()}] ✅ Kết nối lại!")

# ╔══════════════════════════════════════════════════════════════╗
# ║   GÕ "done" TRONG TICKET = TỰ ĐỘNG HOÀN THÀNH ĐƠN + FEEDBACK ║
# ╚══════════════════════════════════════════════════════════════╝
# Admin/CTV không cần bấm nút nữa — chỉ cần gõ "done" (hoặc "hoàn thành",
# "xong") ngay trong kênh ticket, bot sẽ tự xử lý y hệt bấm nút:
#   • Nếu ticket có đơn hàng CHÍNH THỨC đang "pending" (Mua Hàng/Robux) ->
#     chạy đúng luồng _complete_order (cộng tiền, log doanh thu, gửi DM...)
#   • Nếu là ticket Order Acc / Capwall / Nitro (không có đơn giá cụ thể) ->
#     đăng embed hoàn tất chung + hiện nút ⭐ đánh giá
#   • Ticket Support thì bỏ qua (không phải đơn hàng, không cần đánh giá)

_DONE_KEYWORDS = {"done", "hoàn thành", "hoàn tất", "xong"}

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    # QUAN TRỌNG: định nghĩa on_message sẽ ghi đè dispatch mặc định của discord.py,
    # nên PHẢI tự gọi lại process_commands() thì các lệnh dùng prefix "!" (như !qr)
    # mới chạy được. Thiếu dòng này thì mọi lệnh "!..." sẽ bị im lặng, không phản hồi.
    await bot.process_commands(message)

    content = message.content.strip().lower()
    if content not in _DONE_KEYWORDS:
        return

    ch = message.channel
    name = getattr(ch, "name", "") or ""
    if not any(name.startswith(p) for p in TICKET_PREFIXES):
        return
    if not is_staff(message.author):
        return

    d = load(message.guild.id)

    # 1) Có đơn hàng CHÍNH THỨC đang chờ gắn với đúng kênh này? -> dùng luôn _complete_order
    oid_found = next(
        (oid for oid, o in d["orders"].items()
         if o.get("ch_id") == str(ch.id) and o.get("status") == "pending"),
        None
    )

    if oid_found:
        if oid_found in _processing_orders:
            await ch.send("⚠️ Đơn đang được xử lý, vui lòng chờ giây lát.")
            return
        _processing_orders.add(oid_found)
        try:
            embed, view, buyer, is_farm = await _complete_order(
                d, message.guild, oid_found,
                approver_label=f"{message.author.mention}",
                approver_id=str(message.author.id),
                count_deposit=True,
            )
            save(d, message.guild.id)
            await ch.send(content=(buyer.mention if buyer else None), embed=embed, view=view)
        except Exception as e:
            print(f"[on_message done] Lỗi hoàn thành đơn {oid_found}: {e}")
        finally:
            _processing_orders.discard(oid_found)
        return

    # 2) Không có đơn hàng chính thức -> ticket dạng Order Acc / Capwall / Nitro...
    topic = ch.topic or ""
    ticket_kind = topic.split("|")[0].strip().lower()
    if ticket_kind == "support":
        return  # ticket hỗ trợ, không phải đơn hàng -> không cần bảng đánh giá

    buyer = get_ticket_opener(ch)
    embed = discord.Embed(title="🎉 ĐƠN HÀNG HOÀN TẤT!", color=0x2ECC71, timestamp=datetime.utcnow())
    embed.description = (
        f"✅ {message.author.mention} xác nhận đã hoàn thành đơn!\n\n"
        f"*Cảm ơn {buyer.mention if buyer else 'bạn'} đã tin tưởng shop!*\n\n"
        f"⭐ **Hãy bấm số sao bên dưới để đánh giá dịch vụ nhé!**"
    )
    await ch.send(content=(buyer.mention if buyer else None), embed=embed, view=FeedbackStarView())
    if buyer:
        try:
            await buyer.send(embed=discord.Embed(
                title="✅ Đơn của bạn đã hoàn tất!",
                description="Cảm ơn bạn đã ủng hộ shop!\n\n⭐ Bấm số sao bên dưới để đánh giá dịch vụ nhé!",
                color=0x2ECC71,
            ), view=FeedbackStarView())
        except Exception:
            pass

# ╔══════════════════════════════════════════════════════════════╗
# ║                    KHỞI ĐỘNG                                ║
# ╚══════════════════════════════════════════════════════════════╝

def run():
    wait = 5
    while True:
        try:
            print(f"\n[{fmt()}] 🚀 Kết nối Discord...")
            bot.run(TOKEN, reconnect=True, log_handler=None)
        except discord.errors.LoginFailure:
            print("❌ TOKEN sai! Điền TOKEN vào biến ở đầu file."); sys.exit(1)
        except discord.errors.PrivilegedIntentsRequired:
            print("❌ Bật Server Members + Message Content Intents tại discord.com/developers!"); sys.exit(1)
        except KeyboardInterrupt:
            print("Bot dừng."); sys.exit(0)
        except Exception as e:
            print(f"⚠️ Lỗi: {e} — reconnect sau {wait}s")
            time.sleep(wait); wait = min(wait*2, 60)
        else:
            wait = 5

if __name__ == "__main__":
    if not TOKEN:
        print("❌ Chưa điền TOKEN! Mở file và điền TOKEN vào dòng TOKEN = ..."); sys.exit(1)
    keep_alive()
    start_watchdog()
    run()
