import os
import sqlite3
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from telegram import (
    Update,
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.error import BadRequest, Forbidden, TelegramError

# =================== LOGGING ===================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =================== KEEP ALIVE ===================

class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot aktif!")

    def log_message(self, format, *args):
        pass


def keep_alive():
    server = HTTPServer(("0.0.0.0", 5000), PingHandler)
    server.serve_forever()


threading.Thread(target=keep_alive, daemon=True).start()

# =================== KONFIGURASI ===================
TOKEN = "8871249167:AAG5-RAoIwJmK61EiLZUps1vvaqH0ewk7Hs"
ADMIN_ID = 7836786174
QRIS_PHOTO_PATH = "qris.png"

PAKET = {
    "gb_biasa": {
        "nama": "Gb Biasa",
        "emoji": "🔥",
        "deskripsi": "160+ Video Premium",
        "harga": 5000,
    },
    "gb_vip": {
        "nama": "Gb Vip",
        "emoji": "👑",
        "deskripsi": "6.800+ Video Premium",
        "harga": 25000,
    },
}

# =================== MESSAGE TRACKING ===================

def track_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, user_id: int = None):
    """Track bot messages for auto-deletion when user sends new commands."""
    if "tracked_messages" not in context.bot_data:
        context.bot_data["tracked_messages"] = {}
    
    key = user_id if user_id else chat_id
    if key not in context.bot_data["tracked_messages"]:
        context.bot_data["tracked_messages"][key] = []
    
    context.bot_data["tracked_messages"][key].append({
        "chat_id": chat_id,
        "message_id": message_id,
        "timestamp": datetime.now()
    })
    
    # Keep only last 50 messages per user to prevent memory bloat
    if len(context.bot_data["tracked_messages"][key]) > 50:
        context.bot_data["tracked_messages"][key] = context.bot_data["tracked_messages"][key][-50:]


async def delete_tracked_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int, preserve_last: int = 0):
    """Delete all tracked messages for a user. preserve_last=N keeps N most recent."""
    if "tracked_messages" not in context.bot_data:
        return
    
    messages = context.bot_data["tracked_messages"].get(user_id, [])
    
    # Sort by timestamp, oldest first
    messages_to_delete = messages[:-preserve_last] if preserve_last > 0 else messages
    
    deleted_count = 0
    for msg in messages_to_delete:
        try:
            await context.bot.delete_message(
                chat_id=msg["chat_id"],
                message_id=msg["message_id"]
            )
            deleted_count += 1
        except (BadRequest, Forbidden, TelegramError) as e:
            # Message might be too old or already deleted
            logger.debug(f"Could not delete message {msg['message_id']}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error deleting message: {e}")
    
    # Update tracked messages list
    if preserve_last > 0:
        context.bot_data["tracked_messages"][user_id] = messages[-preserve_last:]
    else:
        context.bot_data["tracked_messages"][user_id] = []
    
    logger.info(f"Deleted {deleted_count} messages for user {user_id}")


async def send_and_track(context, chat_id, text=None, photo=None, caption=None, 
                         parse_mode="Markdown", reply_markup=None, user_id=None):
    """Send message and track it for auto-deletion. Returns sent message."""
    try:
        if photo:
            msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
        else:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
        
        track_message(context, chat_id, msg.message_id, user_id or chat_id)
        return msg
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        raise

# =================== DATABASE ===================

def init_db():
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            paket_id TEXT,
            file_id TEXT,
            status TEXT DEFAULT 'waiting',
            waktu TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT,
            first_seen TEXT
        )
    """)
    conn.commit()
    conn.close()


def simpan_user(user_id, user_name):
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO users (user_id, user_name, first_seen) VALUES (?, ?, ?)",
        (user_id, user_name, datetime.now().strftime("%d/%m/%Y %H:%M")),
    )
    conn.commit()
    conn.close()


def get_all_users():
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_order(user_id):
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "SELECT * FROM orders WHERE user_id=? AND status IN ('pending', 'waiting') ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = c.fetchone()
    conn.close()
    return row


def get_last_order(user_id):
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)
    )
    row = c.fetchone()
    conn.close()
    return row


def get_all_pending():
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE status='pending' ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()
    return rows


def get_riwayat():
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE status='completed' ORDER BY id DESC LIMIT 20")
    rows = c.fetchall()
    conn.close()
    return rows


def get_stats():
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    today = datetime.now().strftime("%d/%m/%Y")
    c.execute("""
        SELECT COUNT(*),
        SUM(CASE WHEN paket_id='gb_biasa' THEN 5000 WHEN paket_id='gb_vip' THEN 25000 ELSE 0 END)
        FROM orders WHERE status='completed'
    """)
    total_order, total_pendapatan = c.fetchone()
    c.execute(
        """
        SELECT COUNT(*),
        SUM(CASE WHEN paket_id='gb_biasa' THEN 5000 WHEN paket_id='gb_vip' THEN 25000 ELSE 0 END)
        FROM orders WHERE status='completed' AND waktu LIKE ?
        """,
        (f"%{today}%",),
    )
    hari_order, hari_pendapatan = c.fetchone()
    c.execute("SELECT COUNT(*) FROM orders WHERE status='pending'")
    pending_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users")
    total_user = c.fetchone()[0]
    conn.close()
    return {
        "total_order": total_order or 0,
        "total_pendapatan": total_pendapatan or 0,
        "hari_order": hari_order or 0,
        "hari_pendapatan": hari_pendapatan or 0,
        "pending_count": pending_count or 0,
        "total_user": total_user or 0,
    }


def update_status(user_id, status):
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "UPDATE orders SET status=? WHERE user_id=? AND status IN ('pending', 'waiting')",
        (status, user_id),
    )
    conn.commit()
    conn.close()


def format_harga(harga):
    return f"Rp {harga:,}".replace(",", ".")


def simpan_admin_msg(context, user_id, message_id):
    context.bot_data.setdefault("admin_messages", {})
    context.bot_data["admin_messages"].setdefault(user_id, [])
    context.bot_data["admin_messages"][user_id].append(message_id)


async def hapus_admin_msg(context, user_id):
    msg_ids = context.bot_data.get("admin_messages", {}).pop(user_id, [])
    for msg_id in msg_ids:
        try:
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=msg_id)
        except (BadRequest, Forbidden, TelegramError):
            pass
        except Exception as e:
            logger.error(f"Error deleting admin message: {e}")


def teks_menu_utama():
    return (
        "🏪 *HYPER FAMILY STORE*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Selamat datang! Kami menyediakan konten premium\n"
        "dengan harga terjangkau dan proses cepat.\n\n"
        "📦 *Paket Tersedia:*\n\n"
        "🔥 *Gb Biasa*\n"
        "   ┗ 160+ Video Premium  •  *Rp 5.000*\n\n"
        "👑 *Gb Vip*\n"
        "   ┗ 6.800+ Video Premium  •  *Rp 25.000*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💳 Pembayaran via QRIS semua e-wallet\n"
        "⚡ Proses pengiriman 1–5 menit setelah konfirmasi"
    )


def keyboard_menu_utama():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒  Beli Sekarang", callback_data="buy")],
            [
                InlineKeyboardButton("⭐ Testimoni", url="https://t.me/+7zsdSrwYIG8wOTg1"),
                InlineKeyboardButton("💬 Hubungi Admin", url=f"tg://user?id={ADMIN_ID}"),
            ],
        ]
    )

# =================== POST INIT ===================

async def post_init(application: Application):
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Buka toko"),
            BotCommand("cek", "Cek status pesanan kamu"),
        ],
        scope=BotCommandScopeDefault(),
    )
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Buka toko"),
            BotCommand("pending", "Pesanan menunggu konfirmasi"),
            BotCommand("stats", "Statistik penjualan"),
            BotCommand("riwayat", "Riwayat transaksi selesai"),
            BotCommand("broadcast", "Kirim pesan ke semua buyer"),
        ],
        scope=BotCommandScopeChat(chat_id=ADMIN_ID),
    )
    application.job_queue.run_repeating(
        cek_pending_lama, interval=timedelta(minutes=30), first=timedelta(minutes=30)
    )

# =================== HANDLERS ===================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    simpan_user(user.id, user.full_name)
    
    # Clear any pending states
    context.bot_data.pop("waiting_broadcast", None)
    if user.id == ADMIN_ID:
        context.bot_data.pop("waiting_link_for", None)
    
    # Delete previous BOT messages from this user only (NOT user commands)
    await delete_tracked_messages(context, user.id)
    
    # Send new menu
    await send_and_track(
        context,
        chat_id=update.effective_chat.id,
        text=teks_menu_utama(),
        parse_mode="Markdown",
        reply_markup=keyboard_menu_utama(),
        user_id=user.id
    )


async def cek_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Delete previous BOT messages only (NOT user commands)
    await delete_tracked_messages(context, user_id)
    
    order = get_last_order(user_id)
    if not order:
        await send_and_track(
            context,
            chat_id=update.effective_chat.id,
            text=(
                "📭 *Belum Ada Pesanan*\n\n"
                "Kamu belum pernah melakukan pemesanan.\n"
                "Ketik /start untuk mulai berbelanja."
            ),
            parse_mode="Markdown",
            user_id=user_id
        )
        return
    
    paket = PAKET.get(order[3], {"nama": "Tidak diketahui", "emoji": "❓", "harga": 0})
    status_map = {
        "waiting": ("⏳", "Menunggu bukti pembayaran"),
        "pending": ("🔍", "Sedang diverifikasi admin"),
        "completed": ("✅", "Pesanan selesai & terkirim"),
        "rejected": ("❌", "Pembayaran ditolak"),
        "expired": ("⌛", "Sesi pembayaran berakhir"),
    }
    emoji_s, label_s = status_map.get(order[5], ("❓", order[5]))
    
    await send_and_track(
        context,
        chat_id=update.effective_chat.id,
        text=(
            f"📦 *Status Pesanan Terakhir*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"• Paket   : {paket['emoji']} {paket['nama']}\n"
            f"• Harga   : {format_harga(paket['harga'])}\n"
            f"• Status  : {emoji_s} {label_s}\n"
            f"• Waktu   : {order[6]}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"_Butuh bantuan? Silakan hubungi admin._"
        ),
        parse_mode="Markdown",
        user_id=user_id
    )


async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except (BadRequest, TelegramError):
        pass
    
    user_id = query.from_user.id
    order = get_order(user_id)
    
    if order and order[5] == "pending":
        try:
            await query.answer(
                "⚠️ Kamu masih memiliki pesanan yang sedang diverifikasi. Mohon tunggu konfirmasi admin.",
                show_alert=True,
            )
        except (BadRequest, TelegramError):
            pass
        return
    
    text = (
        "📦 *Pilih Paket*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔥 *Gb Biasa*\n"
        "   ┗ 160+ Video Premium\n"
        "   ┗ Harga: *Rp 5.000*\n\n"
        "👑 *Gb Vip*\n"
        "   ┗ 6.800+ Video Premium\n"
        "   ┗ Harga: *Rp 25.000*\n\n"
        "_Pilih paket yang sesuai kebutuhanmu:_"
    )
    keyboard = [
        [InlineKeyboardButton("🔥  Gb Biasa — Rp 5.000", callback_data="pilih_gb_biasa")],
        [InlineKeyboardButton("👑  Gb Vip — Rp 25.000", callback_data="pilih_gb_vip")],
        [InlineKeyboardButton("← Kembali", callback_data="back_start")],
    ]
    
    try:
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except (BadRequest, TelegramError) as e:
        logger.error(f"Error editing buy message: {e}")
        # If edit fails, delete old and send new
        try:
            await query.message.delete()
        except:
            pass
        await send_and_track(
            context,
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
            user_id=user_id
        )


async def pilih_paket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except (BadRequest, TelegramError):
        pass
    
    paket_id = query.data.replace("pilih_", "")
    if paket_id not in PAKET:
        logger.error(f"Invalid paket_id: {paket_id}")
        return
    
    paket = PAKET[paket_id]
    user_id = query.from_user.id
    user_name = query.from_user.full_name

    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "DELETE FROM orders WHERE user_id=? AND status IN ('waiting', 'pending')",
        (user_id,),
    )
    c.execute(
        "INSERT INTO orders (user_id, user_name, paket_id, file_id, status, waktu) VALUES (?, ?, ?, '', 'waiting', ?)",
        (user_id, user_name, paket_id, datetime.now().strftime("%H:%M — %d/%m/%Y")),
    )
    conn.commit()
    conn.close()

    context.user_data["paket_id"] = paket_id
    expire = (datetime.now() + timedelta(minutes=30)).strftime("%H:%M")

    caption = (
        f"{paket['emoji']} *{paket['nama']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"• Konten  : {paket['deskripsi']}\n"
        f"• Total   : *{format_harga(paket['harga'])}*\n"
        f"• Berlaku : Hingga pukul *{expire}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"*Langkah Pembayaran:*\n\n"
        f"1️⃣ Scan QRIS di atas menggunakan aplikasi e-wallet\n"
        f"2️⃣ Masukkan nominal *tepat* {format_harga(paket['harga'])}\n"
        f"3️⃣ Selesaikan pembayaran & ambil screenshot\n"
        f"4️⃣ Kirim screenshot ke chat ini\n\n"
        f"⚠️ _Pastikan nominal sesuai dan screenshot terlihat jelas._\n"
        f"⏰ _Pesanan otomatis dibatalkan jika melebihi batas waktu._"
    )
    keyboard = [[InlineKeyboardButton("✕  Batalkan Pesanan", callback_data="back_start")]]

    try:
        await query.message.delete()
    except (BadRequest, TelegramError):
        pass

    if not os.path.exists(QRIS_PHOTO_PATH):
        await send_and_track(
            context,
            chat_id=update.effective_chat.id,
            text="⚠️ QRIS tidak tersedia saat ini. Silakan hubungi admin.",
            user_id=user_id
        )
        return

    with open(QRIS_PHOTO_PATH, "rb") as photo:
        msg = await send_and_track(
            context,
            chat_id=update.effective_chat.id,
            photo=photo,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
            user_id=user_id
        )

    # Cancel any existing auto-cancel jobs for this user
    for job in context.job_queue.get_jobs_by_name(str(user_id)):
        job.schedule_removal()

    context.job_queue.run_once(
        auto_cancel,
        timedelta(minutes=30),
        chat_id=user_id,
        user_id=user_id,
        name=str(user_id),
    )


async def auto_cancel(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "UPDATE orders SET status='expired' WHERE user_id=? AND status IN ('waiting', 'pending')",
        (user_id,),
    )
    conn.commit()
    conn.close()
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "⌛ *Sesi Pembayaran Berakhir*\n\n"
                "Pesanan kamu dibatalkan secara otomatis karena\n"
                "melebihi batas waktu 30 menit.\n\n"
                "Ketik /start untuk membuat pesanan baru."
            ),
            parse_mode="Markdown",
        )
    except (BadRequest, Forbidden, TelegramError):
        pass


async def cek_pending_lama(context: ContextTypes.DEFAULT_TYPE):
    orders = get_all_pending()
    if orders:
        text = f"🔔 *Pengingat: {len(orders)} Pesanan Belum Diproses*\n\n"
        for o in orders:
            paket = PAKET.get(o[3], {"emoji": "❓", "nama": "Unknown"})
            text += f"• {o[2]} — {paket['emoji']} {paket['nama']} ({o[6]})\n"
        text += "\n_Segera proses pesanan di atas._"
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📋 Lihat Pesanan", callback_data="admin_see_orders")]]
                ),
            )
        except (BadRequest, Forbidden, TelegramError):
            pass


async def terima_bukti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id

    # Delete the photo message from user to keep chat clean
    try:
        await update.message.delete()
    except (BadRequest, Forbidden, TelegramError):
        pass

    order = get_order(user_id)
    if order and order[5] == "pending":
        await send_and_track(
            context,
            chat_id=update.effective_chat.id,
            text=(
                "🔍 *Pembayaran Sedang Diverifikasi*\n\n"
                "Bukti pembayaran kamu sudah kami terima dan\n"
                "sedang dalam proses verifikasi oleh admin.\n\n"
                "_Mohon tunggu, proses biasanya memakan waktu 1–5 menit._"
            ),
            parse_mode="Markdown",
            user_id=user_id
        )
        return

    paket_id = context.user_data.get("paket_id")
    if not paket_id:
        conn = sqlite3.connect("orders.db")
        c = conn.cursor()
        c.execute(
            "SELECT paket_id FROM orders WHERE user_id=? AND status='waiting' ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = c.fetchone()
        conn.close()
        if row:
            paket_id = row[0]
        else:
            await send_and_track(
                context,
                chat_id=update.effective_chat.id,
                text=(
                    "⚠️ *Tidak Ada Pesanan Aktif*\n\n"
                    "Kamu belum memilih paket atau sesi telah berakhir.\n"
                    "Ketik /start untuk memulai pemesanan baru."
                ),
                parse_mode="Markdown",
                user_id=user_id
            )
            return

    paket = PAKET[paket_id]
    file_id = update.message.photo[-1].file_id

    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "UPDATE orders SET file_id=?, status='pending' WHERE user_id=? AND status='waiting'",
        (file_id, user_id),
    )
    conn.commit()
    conn.close()

    # Cancel auto-cancel job
    for job in context.job_queue.get_jobs_by_name(str(user_id)):
        job.schedule_removal()

    await send_and_track(
        context,
        chat_id=user_id,
        text=(
            f"✅ *Bukti Pembayaran Diterima*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"• Paket  : {paket['emoji']} {paket['nama']}\n"
            f"• Total  : {format_harga(paket['harga'])}\n"
            f"• Waktu  : {datetime.now().strftime('%H:%M, %d %b %Y')}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ Pembayaran sedang diverifikasi oleh admin.\n"
            f"Estimasi konfirmasi: *1–5 menit*.\n\n"
            f"_Harap tetap di chat ini dan jangan menutup aplikasi._"
        ),
        parse_mode="Markdown",
        user_id=user_id
    )

    notif_msg = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"🔔 *Pesanan Baru Masuk!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Pembeli : {user.full_name}\n"
            f"📦 Paket   : {paket['emoji']} {paket['nama']}\n"
            f"💰 Total   : {format_harga(paket['harga'])}\n"
            f"🕐 Waktu   : {datetime.now().strftime('%H:%M, %d %b %Y')}"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("📋 Proses Pesanan", callback_data="admin_see_orders")]]
        ),
    )
    simpan_admin_msg(context, user_id, notif_msg.message_id)


# =================== ADMIN ===================

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return
    
    s = get_stats()
    await send_and_track(
        context,
        chat_id=ADMIN_ID,
        text=(
            f"📊 *Statistik Penjualan*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Total buyer terdaftar : *{s['total_user']} orang*\n\n"
            f"📅 *Hari Ini:*\n"
            f"• Transaksi selesai : {s['hari_order']}\n"
            f"• Pendapatan        : *{format_harga(s['hari_pendapatan'])}*\n\n"
            f"📈 *Total Keseluruhan:*\n"
            f"• Transaksi selesai : {s['total_order']}\n"
            f"• Total pendapatan  : *{format_harga(s['total_pendapatan'])}*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ Menunggu konfirmasi : *{s['pending_count']} pesanan*"
        ),
        parse_mode="Markdown",
        user_id=ADMIN_ID
    )


async def admin_riwayat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return
    
    orders = get_riwayat()
    if not orders:
        await send_and_track(
            context,
            chat_id=ADMIN_ID,
            text="📭 Belum ada transaksi yang selesai.",
            user_id=ADMIN_ID
        )
        return
    
    text = "📜 *Riwayat Transaksi (20 Terakhir)*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, o in enumerate(orders, 1):
        paket = PAKET.get(o[3], {"emoji": "❓", "nama": "Unknown", "harga": 0})
        text += f"{i}. *{o[2]}* — {paket['emoji']} {paket['nama']} — {format_harga(paket['harga'])}\n   _{o[6]}_\n\n"
    
    await send_and_track(
        context,
        chat_id=ADMIN_ID,
        text=text,
        parse_mode="Markdown",
        user_id=ADMIN_ID
    )


async def admin_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return
    
    users = get_all_users()
    context.bot_data["waiting_broadcast"] = True
    # Clear waiting_link_for to prevent conflict
    context.bot_data.pop("waiting_link_for", None)
    
    await send_and_track(
        context,
        chat_id=ADMIN_ID,
        text=(
            f"📢 *Mode Broadcast*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Total penerima: *{len(users)} buyer*\n\n"
            f"Ketik pesan yang ingin dikirim sekarang.\n\n"
            f"_Kirim /batal untuk membatalkan._"
        ),
        parse_mode="Markdown",
        user_id=ADMIN_ID
    )


async def admin_batal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return
    
    context.bot_data.pop("waiting_broadcast", None)
    context.bot_data.pop("waiting_link_for", None)
    
    await send_and_track(
        context,
        chat_id=ADMIN_ID,
        text="❌ *Dibatalkan.*",
        parse_mode="Markdown",
        user_id=ADMIN_ID
    )


async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return
    
    orders = get_all_pending()
    if not orders:
        await send_and_track(
            context,
            chat_id=ADMIN_ID,
            text="✅ Tidak ada pesanan yang menunggu konfirmasi saat ini.",
            user_id=ADMIN_ID
        )
        return
    
    text = f"📋 *Pesanan Menunggu Konfirmasi ({len(orders)})*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    for o in orders:
        paket = PAKET.get(o[3], {"emoji": "❓", "nama": "Unknown"})
        text += f"• {o[2]} — {paket['emoji']} {paket['nama']} — {o[6]}\n"
        keyboard.append(
            [InlineKeyboardButton(f"👤  Proses: {o[2]}", callback_data=f"proses_{o[1]}")]
        )
    
    await send_and_track(
        context,
        chat_id=ADMIN_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
        user_id=ADMIN_ID
    )


async def admin_see_orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except (BadRequest, TelegramError):
        pass
    
    if query.from_user.id != ADMIN_ID:
        return
    
    orders = get_all_pending()
    if not orders:
        try:
            await query.edit_message_text("✅ Tidak ada pesanan yang menunggu konfirmasi saat ini.")
        except (BadRequest, TelegramError):
            pass
        return
    
    text = f"📋 *Pesanan Menunggu Konfirmasi ({len(orders)})*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    for o in orders:
        paket = PAKET.get(o[3], {"emoji": "❓", "nama": "Unknown"})
        text += f"• {o[2]} — {paket['emoji']} {paket['nama']} — {o[6]}\n"
        keyboard.append(
            [InlineKeyboardButton(f"👤  Proses: {o[2]}", callback_data=f"proses_{o[1]}")]
        )
    
    try:
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except (BadRequest, TelegramError):
        await send_and_track(
            context,
            chat_id=ADMIN_ID,
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
            user_id=ADMIN_ID
        )


async def proses_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except (BadRequest, TelegramError):
        pass
    
    if query.from_user.id != ADMIN_ID:
        return

    target_user_id = int(query.data.split("_")[1])

    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "SELECT * FROM orders WHERE user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
        (target_user_id,),
    )
    order = c.fetchone()
    conn.close()

    if not order:
        try:
            await query.edit_message_text("⚠️ Pesanan tidak ditemukan atau sudah diproses.")
        except (BadRequest, TelegramError):
            pass
        return

    paket = PAKET.get(order[3], {"emoji": "❓", "nama": "Unknown", "harga": 0})
    keyboard = [
        [
            InlineKeyboardButton("✅  Konfirmasi", callback_data=f"konfirm_{target_user_id}"),
            InlineKeyboardButton("❌  Tolak", callback_data=f"tolak_{target_user_id}"),
        ]
    ]

    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=order[4],
            caption=(
                f"📋 *Detail Pesanan*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 Pembeli : {order[2]}\n"
                f"📦 Paket   : {paket['emoji']} {paket['nama']}\n"
                f"💰 Total   : {format_harga(paket['harga'])}\n"
                f"🕐 Waktu   : {order[6]}\n\n"
                f"Pilih tindakan di bawah:"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        try:
            await query.message.delete()
        except (BadRequest, TelegramError):
            pass
    except Exception as e:
        logger.error(f"Error sending payment proof: {e}")
        await send_and_track(
            context,
            chat_id=ADMIN_ID,
            text=f"⚠️ Gagal memuat bukti pembayaran: {e}",
            user_id=ADMIN_ID
        )


async def konfirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Alur setelah admin tekan Konfirmasi:
    1. Status order → 'completed'
    2. Buyer diberi tahu pembayaran dikonfirmasi & link sedang disiapkan
    3. Bot minta admin kirim link konten
    4. Admin kirim link → bot teruskan ke buyer
    """
    query = update.callback_query
    try:
        await query.answer()
    except (BadRequest, TelegramError):
        pass
    
    if query.from_user.id != ADMIN_ID:
        return

    target_user_id = int(query.data.split("_")[1])

    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "SELECT * FROM orders WHERE user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
        (target_user_id,),
    )
    order = c.fetchone()
    conn.close()

    if not order:
        try:
            await query.edit_message_caption(caption="⚠️ Pesanan tidak ditemukan.", parse_mode="Markdown")
        except (BadRequest, TelegramError):
            pass
        return

    paket = PAKET.get(order[3], {"emoji": "❓", "nama": "Unknown", "harga": 0})

    update_status(target_user_id, "completed")
    await hapus_admin_msg(context, target_user_id)

    # Update caption foto bukti bayar di chat admin
    try:
        await query.edit_message_caption(
            caption=(
                f"✅ *Pembayaran Dikonfirmasi*\n\n"
                f"👤 {order[2]} — {paket['emoji']} {paket['nama']}\n\n"
                f"_Menunggu link dikirim ke buyer..._"
            ),
            parse_mode="Markdown",
        )
    except (BadRequest, TelegramError):
        pass

    # Beritahu buyer bahwa pembayaran sudah dikonfirmasi
    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=(
                "🎉 *Pembayaran Dikonfirmasi!*\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Terima kasih telah berbelanja di *Hyper Family Store*.\n\n"
                f"📦 Paket : {paket['emoji']} {paket['nama']}\n\n"
                "⏳ Link konten sedang disiapkan oleh admin\n"
                "dan akan segera dikirimkan ke chat ini.\n\n"
                "_Mohon tunggu sebentar..._"
            ),
            parse_mode="Markdown",
        )
    except (BadRequest, Forbidden, TelegramError):
        pass

    # Simpan data untuk pengiriman link - PRIORITAS TERTINGGI
    # Clear broadcast mode to prevent conflict
    context.bot_data.pop("waiting_broadcast", None)
    
    context.bot_data["waiting_link_for"] = {
        "user_id": target_user_id,
        "user_name": order[2],
        "paket": paket,
    }

    # Minta admin kirim link
    await send_and_track(
        context,
        chat_id=ADMIN_ID,
        text=(
            f"🔗 *Kirim Link Konten*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Pembayaran *{order[2]}* telah dikonfirmasi.\n\n"
            f"Sekarang kirimkan link konten untuk paket\n"
            f"{paket['emoji']} *{paket['nama']}* ke chat ini.\n\n"
            f"_Ketik /batal untuk membatalkan._"
        ),
        parse_mode="Markdown",
        user_id=ADMIN_ID
    )


async def tolak_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except (BadRequest, TelegramError):
        pass
    
    if query.from_user.id != ADMIN_ID:
        return

    target_user_id = int(query.data.split("_")[1])
    update_status(target_user_id, "rejected")
    await hapus_admin_msg(context, target_user_id)

    try:
        await query.edit_message_caption(caption="❌ *Pesanan telah ditolak.*", parse_mode="Markdown")
    except (BadRequest, TelegramError):
        pass

    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=(
                "❌ *Pembayaran Tidak Terverifikasi*\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Maaf, bukti pembayaran yang kamu kirim\n"
                "tidak dapat diverifikasi.\n\n"
                "Kemungkinan penyebab:\n"
                "• Nominal tidak sesuai\n"
                "• Screenshot tidak jelas\n"
                "• Transaksi tidak ditemukan\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "Silakan hubungi admin atau coba kembali\n"
                "dengan ketik /start."
            ),
            parse_mode="Markdown",
        )
    except (BadRequest, Forbidden, TelegramError):
        pass


async def back_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except (BadRequest, TelegramError):
        pass
    
    user_id = query.from_user.id

    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "UPDATE orders SET status='expired' WHERE user_id=? AND status='waiting'",
        (user_id,),
    )
    conn.commit()
    conn.close()

    for job in context.job_queue.get_jobs_by_name(str(user_id)):
        job.schedule_removal()

    try:
        await query.message.delete()
    except (BadRequest, Forbidden, TelegramError):
        pass

    await send_and_track(
        context,
        chat_id=update.effective_chat.id,
        text=teks_menu_utama(),
        parse_mode="Markdown",
        reply_markup=keyboard_menu_utama(),
        user_id=user_id
    )


# =================== ADMIN TEXT HANDLER ===================

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Menangani semua pesan teks dari admin.
    Prioritas:
      1. Jika waiting_link_for aktif → kirim link ke buyer
      2. Jika waiting_broadcast aktif → broadcast ke semua buyer
    """
    if update.message.from_user.id != ADMIN_ID:
        return

    text_input = update.message.text

    # Delete admin's text message to keep chat clean
    try:
        await update.message.delete()
    except (BadRequest, Forbidden, TelegramError):
        pass

    # ── PRIORITAS 1: Kirim link ke buyer ──
    link_data = context.bot_data.get("waiting_link_for")
    if link_data:
        target_user_id = link_data["user_id"]
        target_name = link_data["user_name"]
        paket = link_data["paket"]

        context.bot_data.pop("waiting_link_for", None)

        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    f"📦 *Pesanan Siap!*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"Halo! Berikut link konten paket\n"
                    f"{paket['emoji']} *{paket['nama']}* milikmu:\n\n"
                    f"🔗 {text_input}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Terima kasih sudah berbelanja di\n"
                    f"*Hyper Family Store*! 🙏\n\n"
                    f"_Jika ada kendala, jangan ragu hubungi admin._"
                ),
                parse_mode="Markdown",
                disable_web_page_preview=False,
            )
            await send_and_track(
                context,
                chat_id=ADMIN_ID,
                text=(
                    f"✅ *Link Berhasil Dikirim!*\n\n"
                    f"👤 Penerima : {target_name}\n"
                    f"📦 Paket    : {paket['emoji']} {paket['nama']}\n"
                    f"🔗 Link     : {text_input}"
                ),
                parse_mode="Markdown",
                user_id=ADMIN_ID
            )
        except Exception as e:
            logger.error(f"Error sending link to buyer: {e}")
            await send_and_track(
                context,
                chat_id=ADMIN_ID,
                text=(
                    f"⚠️ Gagal mengirim link ke buyer.\nError: {e}\n\n"
                    f"Coba kirim manual ke user ID: `{target_user_id}`"
                ),
                parse_mode="Markdown",
                user_id=ADMIN_ID
            )
        return

    # ── PRIORITAS 2: Broadcast ──
    if context.bot_data.get("waiting_broadcast"):
        context.bot_data.pop("waiting_broadcast", None)

        if text_input.startswith("/"):
            await send_and_track(
                context,
                chat_id=ADMIN_ID,
                text="⚠️ Perintah tidak valid untuk broadcast.",
                user_id=ADMIN_ID
            )
            return

        users = get_all_users()
        berhasil = 0
        gagal = 0

        status_msg = await send_and_track(
            context,
            chat_id=ADMIN_ID,
            text=f"📤 *Mengirim broadcast ke {len(users)} buyer...*",
            parse_mode="Markdown",
            user_id=ADMIN_ID
        )

        for uid in users:
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=(
                        f"📢 *Pesan dari Admin*\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"{text_input}"
                    ),
                    parse_mode="Markdown",
                )
                berhasil += 1
            except (Forbidden, BadRequest, TelegramError):
                gagal += 1
            except Exception:
                gagal += 1

        try:
            await status_msg.edit_text(
                f"✅ *Broadcast Selesai*\n\n"
                f"• Terkirim : {berhasil} buyer\n"
                f"• Gagal    : {gagal} buyer",
                parse_mode="Markdown",
            )
        except (BadRequest, TelegramError):
            pass
        return


# =================== MAIN ===================

def main():
    init_db()

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cek", cek_order))

    # Admin commands
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("riwayat", admin_riwayat))
    app.add_handler(CommandHandler("pending", admin_pending))
    app.add_handler(CommandHandler("broadcast", admin_broadcast_cmd))
    app.add_handler(CommandHandler("batal", admin_batal))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy$"))
    app.add_handler(CallbackQueryHandler(pilih_paket, pattern="^pilih_"))
    app.add_handler(CallbackQueryHandler(back_start_callback, pattern="^back_start$"))
    app.add_handler(CallbackQueryHandler(admin_see_orders_callback, pattern="^admin_see_orders$"))
    app.add_handler(CallbackQueryHandler(proses_order_callback, pattern="^proses_"))
    app.add_handler(CallbackQueryHandler(konfirm_callback, pattern="^konfirm_"))
    app.add_handler(CallbackQueryHandler(tolak_callback, pattern="^tolak_"))

    # Admin text handler (link + broadcast) — harus sebelum terima_bukti
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.User(ADMIN_ID) & ~filters.COMMAND,
            handle_admin_text,
        )
    )

    # Bukti pembayaran dari user (foto)
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, terima_bukti))

    app.run_polling()


if __name__ == "__main__":
    main()
