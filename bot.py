import os
import sqlite3
import threading
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
from telegram.error import BadRequest, Forbidden

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


# =================== HELPER: SIMPAN & HAPUS PESAN ===================


def simpan_msg_user(context, user_id, message_id):
    """Simpan message_id pesan /start user untuk dihapus nanti."""
    context.bot_data.setdefault("user_start_messages", {})
    context.bot_data["user_start_messages"].setdefault(user_id, [])
    context.bot_data["user_start_messages"][user_id].append(message_id)


async def hapus_msg_user_lama(context, chat_id):
    """Hapus semua pesan /start user sebelumnya."""
    msgs = context.bot_data.get("user_start_messages", {}).pop(chat_id, [])
    for msg_id in msgs:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass


def simpan_admin_msg(context, user_id, message_id):
    """Simpan message_id notif admin yang terkait order user tertentu."""
    context.bot_data.setdefault("admin_messages", {})
    context.bot_data["admin_messages"].setdefault(user_id, [])
    context.bot_data["admin_messages"][user_id].append(message_id)


async def hapus_admin_msg(context, user_id):
    """Hapus semua pesan admin yang terkait order user tertentu."""
    msg_ids = context.bot_data.get("admin_messages", {}).pop(user_id, [])
    for msg_id in msg_ids:
        try:
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=msg_id)
        except Exception:
            pass


def simpan_order_msg_admin(context, user_id, message_id):
    """Simpan message_id chat admin yang terkait proses order (foto bukti, konfirmasi, dll)."""
    context.bot_data.setdefault("order_process_messages", {})
    context.bot_data["order_process_messages"].setdefault(user_id, [])
    context.bot_data["order_process_messages"][user_id].append(message_id)


async def hapus_order_msg_admin(context, user_id):
    """Hapus semua chat admin terkait proses order setelah link dikirim."""
    msg_ids = context.bot_data.get("order_process_messages", {}).pop(user_id, [])
    for msg_id in msg_ids:
        try:
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=msg_id)
        except Exception:
            pass


# =================== TEKS & KEYBOARD ===================


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
    chat_id = update.effective_chat.id
    simpan_user(user.id, user.full_name)

    # Reset state
    context.bot_data.pop("waiting_broadcast", None)
    context.user_data.pop("paket_id", None)

    # Hapus pesan /start lama milik user ini
    await hapus_msg_user_lama(context, chat_id)

    # Hapus pesan command /start itu sendiri
    if update.message:
        try:
            await update.message.delete()
        except Exception:
            pass

    # Kirim menu baru & simpan message_id-nya
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=teks_menu_utama(),
        parse_mode="Markdown",
        reply_markup=keyboard_menu_utama(),
    )
    simpan_msg_user(context, chat_id, msg.message_id)


async def cek_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    order = get_last_order(user_id)

    # Hapus pesan command /cek
    if update.message:
        try:
            await update.message.delete()
        except Exception:
            pass

    if not order:
        msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "📭 *Belum Ada Pesanan*\n\n"
                "Kamu belum pernah melakukan pemesanan.\n"
                "Ketik /start untuk mulai berbelanja."
            ),
            parse_mode="Markdown",
        )
        simpan_msg_user(context, update.effective_chat.id, msg.message_id)
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
    msg = await context.bot.send_message(
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
    )
    simpan_msg_user(context, update.effective_chat.id, msg.message_id)


async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    order = get_order(user_id)
    if order and order[5] == "pending":
        try:
            await query.answer(
                "⚠️ Kamu masih memiliki pesanan yang sedang diverifikasi. Mohon tunggu konfirmasi admin.",
                show_alert=True,
            )
        except Exception:
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
    except Exception:
        pass


async def pilih_paket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    paket_id = query.data.replace("pilih_", "")
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
    except Exception:
        pass

    if not os.path.exists(QRIS_PHOTO_PATH):
        msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ QRIS tidak tersedia saat ini. Silakan hubungi admin.",
        )
        simpan_msg_user(context, update.effective_chat.id, msg.message_id)
        return

    with open(QRIS_PHOTO_PATH, "rb") as photo:
        msg = await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=photo,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    # Simpan pesan QRIS agar bisa dihapus saat /start lagi
    simpan_msg_user(context, update.effective_chat.id, msg.message_id)

    # Set auto cancel job
    # Hapus job lama kalau ada
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
    except Exception:
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
        except Exception:
            pass


async def terima_bukti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id

    # Abaikan foto dari admin supaya tidak bentrok
    if user_id == ADMIN_ID:
        return

    order = get_order(user_id)
    if order and order[5] == "pending":
        msg = await update.message.reply_text(
            "🔍 *Pembayaran Sedang Diverifikasi*\n\n"
            "Bukti pembayaran kamu sudah kami terima dan\n"
            "sedang dalam proses verifikasi oleh admin.\n\n"
            "_Mohon tunggu, proses biasanya memakan waktu 1–5 menit._",
            parse_mode="Markdown",
        )
        simpan_msg_user(context, update.effective_chat.id, msg.message_id)
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
            msg = await update.message.reply_text(
                "⚠️ *Tidak Ada Pesanan Aktif*\n\n"
                "Kamu belum memilih paket atau sesi telah berakhir.\n"
                "Ketik /start untuk memulai pemesanan baru.",
                parse_mode="Markdown",
            )
            simpan_msg_user(context, update.effective_chat.id, msg.message_id)
            return

    paket = PAKET.get(paket_id)
    if not paket:
        return

    file_id = update.message.photo[-1].file_id

    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "UPDATE orders SET file_id=?, status='pending' WHERE user_id=? AND status='waiting'",
        (file_id, user_id),
    )
    conn.commit()
    conn.close()

    # Cancel auto-expire job
    for job in context.job_queue.get_jobs_by_name(str(user_id)):
        job.schedule_removal()

    msg = await context.bot.send_message(
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
    )
    simpan_msg_user(context, user_id, msg.message_id)

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
    simpan_order_msg_admin(context, user_id, notif_msg.message_id)


# =================== ADMIN ===================


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return
    s = get_stats()
    await update.message.reply_text(
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
        f"⏳ Menunggu konfirmasi : *{s['pending_count']} pesanan*",
        parse_mode="Markdown",
    )


async def admin_riwayat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return
    orders = get_riwayat()
    if not orders:
        await update.message.reply_text("📭 Belum ada transaksi yang selesai.")
        return
    text = "📜 *Riwayat Transaksi (20 Terakhir)*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, o in enumerate(orders, 1):
        paket = PAKET.get(o[3], {"emoji": "❓", "nama": "Unknown", "harga": 0})
        text += f"{i}. *{o[2]}* — {paket['emoji']} {paket['nama']} — {format_harga(paket['harga'])}\n   _{o[6]}_\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def admin_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return
    users = get_all_users()
    context.bot_data["waiting_broadcast"] = True
    await update.message.reply_text(
        f"📢 *Mode Broadcast*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Total penerima: *{len(users)} buyer*\n\n"
        f"Ketik pesan yang ingin dikirim sekarang.\n\n"
        f"_Kirim /batal untuk membatalkan._",
        parse_mode="Markdown",
    )


async def admin_batal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return
    context.bot_data.pop("waiting_broadcast", None)
    context.bot_data.pop("waiting_link_for", None)
    await update.message.reply_text("❌ *Dibatalkan.*", parse_mode="Markdown")


async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return
    orders = get_all_pending()
    if not orders:
        await update.message.reply_text("✅ Tidak ada pesanan yang menunggu konfirmasi saat ini.")
        return
    text = f"📋 *Pesanan Menunggu Konfirmasi ({len(orders)})*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    for o in orders:
        paket = PAKET.get(o[3], {"emoji": "❓", "nama": "Unknown"})
        text += f"• {o[2]} — {paket['emoji']} {paket['nama']} — {o[6]}\n"
        keyboard.append(
            [InlineKeyboardButton(f"👤  Proses: {o[2]}", callback_data=f"proses_{o[1]}")]
        )
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_see_orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if query.from_user.id != ADMIN_ID:
        return
    orders = get_all_pending()
    if not orders:
        try:
            await query.edit_message_text("✅ Tidak ada pesanan yang menunggu konfirmasi saat ini.")
        except Exception:
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
    except Exception:
        msg = await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        # Simpan pesan daftar order agar bisa dihapus nanti (tidak terkait user tertentu)


async def proses_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
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
        except Exception:
            pass
        return

    paket = PAKET.get(order[3], {"emoji": "❓", "nama": "Unknown", "harga": 0})
    keyboard = [
        [
            InlineKeyboardButton("✅  Konfirmasi", callback_data=f"konfirm_{target_user_id}"),
            InlineKeyboardButton("❌  Tolak", callback_data=f"tolak_{target_user_id}"),
        ]
    ]

    # Hapus pesan list sebelumnya
    try:
        await query.message.delete()
    except Exception:
        pass

    try:
        foto_msg = await context.bot.send_photo(
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
        # Simpan pesan foto bukti agar bisa dihapus setelah order selesai
        simpan_order_msg_admin(context, target_user_id, foto_msg.message_id)
    except Exception as e:
        await context.bot.send_message(
            chat_id=ADMIN_ID, text=f"⚠️ Gagal memuat bukti pembayaran: {e}"
        )


async def konfirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
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
            await query.edit_message_caption(
                caption="⚠️ Pesanan tidak ditemukan.", parse_mode="Markdown"
            )
        except Exception:
            pass
        return

    paket = PAKET.get(order[3], {"emoji": "❓", "nama": "Unknown", "harga": 0})

    update_status(target_user_id, "completed")
    await hapus_admin_msg(context, target_user_id)

    # Update caption foto bukti di chat admin jadi status confirmed
    try:
        await query.edit_message_caption(
            caption=(
                f"✅ *Pembayaran Dikonfirmasi*\n\n"
                f"👤 {order[2]} — {paket['emoji']} {paket['nama']}\n\n"
                f"_Menunggu link dikirim ke buyer..._"
            ),
            parse_mode="Markdown",
        )
    except Exception:
        pass

    # Beritahu buyer bahwa pembayaran sudah dikonfirmasi
    try:
        konfirm_msg = await context.bot.send_message(
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
        simpan_msg_user(context, target_user_id, konfirm_msg.message_id)
    except Exception:
        pass

    # Simpan data untuk pengiriman link
    context.bot_data["waiting_link_for"] = {
        "user_id": target_user_id,
        "user_name": order[2],
        "paket": paket,
        # Simpan message_id foto bukti agar bisa dihapus setelah link terkirim
        "foto_msg_id": query.message.message_id,
    }

    # Minta admin kirim link konten
    link_req_msg = await context.bot.send_message(
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
    )
    simpan_order_msg_admin(context, target_user_id, link_req_msg.message_id)


async def tolak_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if query.from_user.id != ADMIN_ID:
        return

    target_user_id = int(query.data.split("_")[1])
    update_status(target_user_id, "rejected")
    await hapus_admin_msg(context, target_user_id)

    # Hapus semua chat admin terkait order ini
    await hapus_order_msg_admin(context, target_user_id)

    try:
        await query.edit_message_caption(
            caption="❌ *Pesanan telah ditolak.*", parse_mode="Markdown"
        )
    except Exception:
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
    except Exception:
        pass


async def back_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    chat_id = update.effective_chat.id

    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "UPDATE orders SET status='expired' WHERE user_id=? AND status='waiting'",
        (user_id,),
    )
    conn.commit()
    conn.close()

    # Hapus auto cancel job
    for job in context.job_queue.get_jobs_by_name(str(user_id)):
        job.schedule_removal()

    # Hapus pesan QRIS / menu lama
    try:
        await query.message.delete()
    except Exception:
        pass

    # Hapus sisa pesan lama
    await hapus_msg_user_lama(context, chat_id)

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=teks_menu_utama(),
        parse_mode="Markdown",
        reply_markup=keyboard_menu_utama(),
    )
    simpan_msg_user(context, chat_id, msg.message_id)


# =================== ADMIN TEXT HANDLER ===================


async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Menangani semua pesan teks dari admin.
    Prioritas:
      1. Jika waiting_link_for aktif → kirim link ke buyer, hapus chat order admin
      2. Jika waiting_broadcast aktif → broadcast ke semua buyer
    """
    if update.message.from_user.id != ADMIN_ID:
        return

    text_input = update.message.text

    # ── PRIORITAS 1: Kirim link ke buyer ──
    link_data = context.bot_data.get("waiting_link_for")
    if link_data:
        target_user_id = link_data["user_id"]
        target_name = link_data["user_name"]
        paket = link_data["paket"]
        foto_msg_id = link_data.get("foto_msg_id")

        context.bot_data.pop("waiting_link_for", None)

        try:
            link_msg = await context.bot.send_message(
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
            # Simpan pesan link agar bisa dihapus kalau user /start
            simpan_msg_user(context, target_user_id, link_msg.message_id)

            # Konfirmasi ke admin
            konfirm_admin_msg = await update.message.reply_text(
                f"✅ *Link Berhasil Dikirim!*\n\n"
                f"👤 Penerima : {target_name}\n"
                f"📦 Paket    : {paket['emoji']} {paket['nama']}\n"
                f"🔗 Link     : {text_input}",
                parse_mode="Markdown",
            )
            simpan_order_msg_admin(context, target_user_id, konfirm_admin_msg.message_id)

        except Exception as e:
            await update.message.reply_text(
                f"⚠️ Gagal mengirim link ke buyer.\nError: {e}\n\n"
                f"Coba kirim manual ke user ID: `{target_user_id}`",
                parse_mode="Markdown",
            )
            return

        # Hapus semua chat admin terkait order ini (foto bukti, request link, konfirmasi)
        # Termasuk foto bukti yang message_id-nya disimpan di link_data
        if foto_msg_id:
            try:
                await context.bot.delete_message(chat_id=ADMIN_ID, message_id=foto_msg_id)
            except Exception:
                pass

        await hapus_order_msg_admin(context, target_user_id)

        # Juga hapus pesan teks link yang baru dikirim admin itu sendiri
        try:
            await update.message.delete()
        except Exception:
            pass

        return

    # ── PRIORITAS 2: Broadcast ──
    if context.bot_data.get("waiting_broadcast"):
        context.bot_data.pop("waiting_broadcast", None)

        if text_input.startswith("/"):
            await update.message.reply_text("⚠️ Perintah tidak valid untuk broadcast.")
            return

        users = get_all_users()
        berhasil = 0
        gagal = 0

        status_msg = await update.message.reply_text(
            f"📤 *Mengirim broadcast ke {len(users)} buyer...*",
            parse_mode="Markdown",
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
            except (Forbidden, BadRequest):
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
        except Exception:
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

    # Admin text handler (link + broadcast) — harus SEBELUM terima_bukti
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.User(ADMIN_ID) & ~filters.COMMAND,
            handle_admin_text,
        )
    )

    # Bukti pembayaran dari user (foto) — admin diexclude
    app.add_handler(
        MessageHandler(
            filters.PHOTO & ~filters.COMMAND & ~filters.User(ADMIN_ID),
            terima_bukti,
        )
    )

    app.run_polling()


if __name__ == "__main__":
    main()
