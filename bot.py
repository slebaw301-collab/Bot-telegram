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
TOKEN = "8871249167:AAHFpAPMUq0JFBtaJgapJTjYL7sGF9x-sGg"
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


def simpan_admin_msg(context, user_id, message_id):
    context.bot_data.setdefault("admin_messages", {})
    context.bot_data["admin_messages"].setdefault(user_id, [])
    context.bot_data["admin_messages"][user_id].append(message_id)


async def hapus_admin_msg(context, user_id):
    msg_ids = context.bot_data.get("admin_messages", {}).pop(user_id, [])
    for msg_id in msg_ids:
        try:
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=msg_id)
        except:
            pass


def teks_menu_utama():
    return (
        "🏪 *HYPER FAMILY BUY*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Selamat datang! Pilih paket yang tersedia:\n\n"
        "🔥 *Gb Biasa*\n"
        "┗ 160+ Video Premium • *Rp 5.000*\n\n"
        "👑 *Gb Vip*\n"
        "┗ 6.800+ Video Premium • *Rp 25.000*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💳 QRIS semua e-wallet  •  ⚡ Proses 1–5 menit"
    )


def keyboard_menu_utama():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒  Beli Sekarang", callback_data="buy")],
            [
                InlineKeyboardButton(
                    "⭐ Testimoni", url="https://t.me/+7zsdSrwYIG8wOTg1"
                ),
                InlineKeyboardButton(
                    "💬 Hubungi Admin", url=f"tg://user?id={ADMIN_ID}"
                ),
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
            BotCommand("pending", "Order yang menunggu konfirmasi"),
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

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=teks_menu_utama(),
        parse_mode="Markdown",
        reply_markup=keyboard_menu_utama(),
    )


async def cek_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    order = get_last_order(user_id)

    if not order:
        await update.message.reply_text(
            "📭 Kamu belum pernah melakukan pemesanan.\n\n"
            "Ketik /start untuk mulai berbelanja."
        )
        return

    paket = PAKET.get(order[3], {"nama": "Tidak diketahui", "emoji": "❓", "harga": 0})
    status_map = {
        "waiting": ("⏳", "Menunggu bukti pembayaran"),
        "pending": ("🔍", "Sedang diverifikasi admin"),
        "completed": ("✅", "Pesanan selesai"),
        "rejected": ("❌", "Pembayaran ditolak"),
        "expired": ("⌛", "Sesi berakhir"),
    }
    emoji_s, label_s = status_map.get(order[5], ("❓", order[5]))

    await update.message.reply_text(
        f"📦 *Status Pesanan Terakhir*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"• Paket  : {paket['emoji']} {paket['nama']}\n"
        f"• Harga  : {format_harga(paket['harga'])}\n"
        f"• Status : {emoji_s} {label_s}\n"
        f"• Waktu  : {order[6]}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"_Butuh bantuan? Hubungi admin._",
        parse_mode="Markdown",
    )


async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass

    user_id = query.from_user.id
    order = get_order(user_id)

    if order and order[5] == "pending":
        try:
            await query.answer(
                "⚠️ Kamu masih memiliki pesanan yang sedang diverifikasi. Mohon tunggu konfirmasi admin.",
                show_alert=True,
            )
        except:
            pass
        return

    text = (
        "📦 *Pilih Paket*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔥 *Gb Biasa*\n"
        "┗ 160+ Video Premium\n"
        "┗ Harga: *Rp 5.000*\n\n"
        "👑 *Gb Vip*\n"
        "┗ 6.800+ Video Premium\n"
        "┗ Harga: *Rp 25.000*"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🔥  Gb Biasa — Rp 5.000", callback_data="pilih_gb_biasa"
            )
        ],
        [InlineKeyboardButton("👑  Gb Vip — Rp 25.000", callback_data="pilih_gb_vip")],
        [InlineKeyboardButton("← Kembali", callback_data="back_start")],
    ]

    try:
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except:
        pass


async def pilih_paket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
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
        f"• Konten : {paket['deskripsi']}\n"
        f"• Total  : *{format_harga(paket['harga'])}*\n"
        f"• Berlaku: Hingga pukul *{expire}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"*Cara Pembayaran:*\n"
        f"1️⃣ Scan QRIS di atas\n"
        f"2️⃣ Transfer *tepat* {format_harga(paket['harga'])}\n"
        f"3️⃣ Screenshot bukti transfer\n"
        f"4️⃣ Kirim screenshot ke chat ini\n\n"
        f"⚠️ _Nominal harus sesuai & screenshot harus jelas._"
    )

    keyboard = [
        [InlineKeyboardButton("✕  Batalkan Pesanan", callback_data="back_start")]
    ]

    try:
        await query.message.delete()
    except:
        pass

    if not os.path.exists(QRIS_PHOTO_PATH):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ QRIS tidak tersedia saat ini. Silakan hubungi admin.",
        )
        return

    with open(QRIS_PHOTO_PATH, "rb") as photo:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=photo,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "📸 *Kirim Screenshot Bukti Pembayaran*\n\n"
            "Pastikan screenshot menampilkan:\n"
            "• Nominal yang sesuai\n"
            "• Tanggal & waktu transaksi\n"
            "• Status berhasil"
        ),
        parse_mode="Markdown",
    )

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
                "Pesanan kamu telah dibatalkan secara otomatis karena melebihi batas waktu 30 menit.\n\n"
                "Ketik /start untuk memulai pesanan baru."
            ),
            parse_mode="Markdown",
        )
    except:
        pass


async def cek_pending_lama(context: ContextTypes.DEFAULT_TYPE):
    orders = get_all_pending()
    if orders:
        text = f"🔔 *Pengingat: {len(orders)} Pesanan Belum Diproses*\n\n"
        for o in orders:
            paket = PAKET[o[3]]
            text += f"• {o[2]} — {paket['emoji']} {paket['nama']} ({o[6]})\n"
        text += "\n_Segera proses pesanan di atas._"

        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "📋 Lihat Pesanan", callback_data="admin_see_orders"
                            )
                        ]
                    ]
                ),
            )
        except:
            pass


async def terima_bukti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id

    order = get_order(user_id)

    if order and order[5] == "pending":
        await update.message.reply_text(
            "⏳ Bukti pembayaran kamu sedang dalam proses verifikasi.\n"
            "Mohon tunggu, admin akan segera mengkonfirmasi."
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
            await update.message.reply_text(
                "⚠️ Kamu belum memilih paket.\nKetik /start untuk memulai pemesanan."
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

    for job in context.job_queue.get_jobs_by_name(str(user_id)):
        job.schedule_removal()

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"🧾 *Bukti Pembayaran Diterima*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"• Paket  : {paket['emoji']} {paket['nama']}\n"
            f"• Total  : {format_harga(paket['harga'])}\n"
            f"• Waktu  : {datetime.now().strftime('%H:%M, %d %b %Y')}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ Sedang diverifikasi oleh admin.\n"
            f"Estimasi konfirmasi: *1–5 menit*."
        ),
        parse_mode="Markdown",
    )

    notif_msg = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"🔔 *Pesanan Baru Masuk*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"• Pembeli : {user.full_name}\n"
            f"• Paket   : {paket['emoji']} {paket['nama']}\n"
            f"• Total   : {format_harga(paket['harga'])}\n"
            f"• Waktu   : {datetime.now().strftime('%H:%M, %d %b %Y')}"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📋 Lihat Pesanan", callback_data="admin_see_orders"
                    )
                ]
            ]
        ),
    )
    simpan_admin_msg(context, user_id, notif_msg.message_id)


# =================== ADMIN ===================


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return
    s = get_stats()
    await update.message.reply_text(
        f"📊 *Statistik Penjualan*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Total buyer terdaftar: *{s['total_user']} orang*\n\n"
        f"📅 *Hari Ini:*\n"
        f"• Transaksi selesai : {s['hari_order']}\n"
        f"• Pendapatan        : *{format_harga(s['hari_pendapatan'])}*\n\n"
        f"📈 *Keseluruhan:*\n"
        f"• Transaksi selesai : {s['total_order']}\n"
        f"• Total pendapatan  : *{format_harga(s['total_pendapatan'])}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏳ Menunggu konfirmasi: *{s['pending_count']} pesanan*",
        parse_mode="Markdown",
    )


async def admin_riwayat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return
    orders = get_riwayat()
    if not orders:
        await update.message.reply_text("📭 Belum ada transaksi yang selesai.")
        return
    text = f"📜 *Riwayat Transaksi (20 Terakhir)*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, o in enumerate(orders, 1):
        paket = PAKET[o[3]]
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
        f"Ketik pesan yang ingin dikirim sekarang.\n"
        f"_Kirim /batal untuk membatalkan._",
        parse_mode="Markdown",
    )


async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return
    orders = get_all_pending()
    if not orders:
        await update.message.reply_text(
            "✅ Tidak ada pesanan yang menunggu konfirmasi."
        )
        return

    text = f"📋 *Pesanan Menunggu Konfirmasi ({len(orders)})*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    for o in orders:
        paket = PAKET[o[3]]
        text += f"• {o[2]} — {paket['emoji']} {paket['nama']} — {o[6]}\n"
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"👤  Proses: {o[2]}", callback_data=f"proses_{o[1]}"
                )
            ]
        )

    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_see_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass

    orders = get_all_pending()
    if not orders:
        try:
            await query.answer("✅ Tidak ada pesanan yang menunggu.", show_alert=True)
        except:
            pass
        return

    text = f"📋 *Pesanan Menunggu Konfirmasi ({len(orders)})*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    for o in orders:
        paket = PAKET[o[3]]
        text += f"• {o[2]} — {paket['emoji']} {paket['nama']} — {o[6]}\n"
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"👤  Proses: {o[2]}", callback_data=f"proses_{o[1]}"
                )
            ]
        )

    try:
        await query.message.delete()
    except:
        pass

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def admin_proses_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass

    user_id = int(query.data.replace("proses_", ""))
    order = get_order(user_id)

    if not order:
        try:
            await query.edit_message_text(
                "⚠️ Pesanan tidak ditemukan atau sudah diproses."
            )
        except:
            pass
        return

    paket = PAKET[order[3]]
    caption = (
        f"{paket['emoji']} *{paket['nama']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"• Pembeli : *{order[2]}*\n"
        f"• ID      : `{order[1]}`\n"
        f"• Konten  : {paket['deskripsi']}\n"
        f"• Total   : {format_harga(paket['harga'])}\n"
        f"• Waktu   : {order[6]}"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Konfirmasi", callback_data=f"confirm_{user_id}"),
            InlineKeyboardButton("❌ Tolak", callback_data=f"reject_{user_id}"),
        ],
        [InlineKeyboardButton("← Kembali ke Daftar", callback_data="back_orders")],
    ]

    try:
        await query.message.delete()
    except:
        pass

    foto_msg = await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=order[4],
        caption=caption,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    simpan_admin_msg(context, user_id, foto_msg.message_id)


async def back_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass

    try:
        await query.message.delete()
    except:
        pass

    orders = get_all_pending()
    if not orders:
        await context.bot.send_message(
            chat_id=ADMIN_ID, text="✅ Tidak ada pesanan yang menunggu konfirmasi."
        )
        return

    text = f"📋 *Pesanan Menunggu Konfirmasi ({len(orders)})*\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    for o in orders:
        paket = PAKET[o[3]]
        text += f"• {o[2]} — {paket['emoji']} {paket['nama']} — {o[6]}\n"
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"👤  Proses: {o[2]}", callback_data=f"proses_{o[1]}"
                )
            ]
        )

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def admin_konfirmasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass

    parts = query.data.split("_")
    action = parts[0]
    user_id = int(parts[1])

    order = get_order(user_id)
    if not order:
        try:
            await query.edit_message_caption(
                "⚠️ Pesanan tidak ditemukan atau sudah diproses."
            )
        except:
            pass
        return

    paket = PAKET[order[3]]

    if action == "confirm":
        context.bot_data["waiting_link_for"] = user_id
        try:
            await query.edit_message_caption(
                f"✅ *Pembayaran Dikonfirmasi*\n\n"
                f"• Pembeli : *{order[2]}*\n"
                f"• Paket   : {paket['emoji']} {paket['nama']}\n\n"
                f"_Kirim link produk sekarang untuk diteruskan ke pembeli._",
                parse_mode="Markdown",
            )
        except:
            pass

    elif action == "reject":
        update_status(user_id, "rejected")
        try:
            await query.edit_message_caption(
                f"❌ *Pembayaran Ditolak*\n\n"
                f"• Pembeli : {order[2]}\n"
                f"• Paket   : {paket['emoji']} {paket['nama']}",
                parse_mode="Markdown",
            )
        except:
            pass

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "❌ *Pembayaran Tidak Dapat Dikonfirmasi*\n\n"
                    "Bukti transfer yang kamu kirimkan tidak dapat diverifikasi.\n\n"
                    "*Kemungkinan penyebab:*\n"
                    "• Nominal transfer tidak sesuai\n"
                    "• Screenshot tidak jelas atau terpotong\n"
                    "• Transaksi belum berhasil\n\n"
                    "Ketik /start untuk mencoba kembali atau hubungi admin jika ada pertanyaan."
                ),
                parse_mode="Markdown",
            )
        except:
            pass

        await hapus_admin_msg(context, user_id)


async def admin_kirim_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.bot_data.get("waiting_broadcast"):
        await kirim_broadcast(update, context)
        return

    user_id = context.bot_data.get("waiting_link_for")
    if not user_id:
        return

    teks = update.message.text

    if teks == "/batal":
        context.bot_data.pop("waiting_link_for", None)
        await update.message.reply_text("❌ Dibatalkan.")
        return

    order = get_order(user_id)
    if not order:
        await update.message.reply_text("⚠️ Pesanan tidak ditemukan.")
        context.bot_data.pop("waiting_link_for", None)
        return

    paket = PAKET[order[3]]

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ *Pembayaran Berhasil Dikonfirmasi!*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"• Paket   : {paket['emoji']} {paket['nama']}\n"
                f"• Konten  : {paket['deskripsi']}\n"
                f"• Waktu   : {datetime.now().strftime('%H:%M, %d %b %Y')}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🔗 *Link Akses Produk:*\n{teks}\n\n"
                f"_Simpan link ini baik-baik. Produk dapat diakses kapan saja._\n\n"
                f"Terima kasih telah berbelanja di Hyper Family Buy! 🙏"
            ),
            parse_mode="Markdown",
        )
    except:
        pass

    update_status(user_id, "completed")
    context.bot_data.pop("waiting_link_for", None)
    await hapus_admin_msg(context, user_id)

    await update.message.reply_text(
        f"✅ Link produk berhasil dikirim ke *{order[2]}*.\n"
        f"Pesanan telah ditandai selesai.",
        parse_mode="Markdown",
    )


async def kirim_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pesan = update.message.text
    if pesan == "/batal":
        context.bot_data.pop("waiting_broadcast", None)
        await update.message.reply_text("❌ Broadcast dibatalkan.")
        return

    context.bot_data.pop("waiting_broadcast", None)
    users = get_all_users()
    berhasil = 0
    gagal = 0

    await update.message.reply_text(
        f"📤 Mengirim pesan ke {len(users)} buyer, mohon tunggu..."
    )

    for uid in users:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📢 *Pesan dari Admin*\n━━━━━━━━━━━━━━━━━━━━\n\n{pesan}",
                parse_mode="Markdown",
            )
            berhasil += 1
        except (Forbidden, BadRequest):
            gagal += 1
        except:
            gagal += 1

    await update.message.reply_text(
        f"✅ *Broadcast Selesai*\n\n"
        f"• Terkirim : {berhasil} orang\n"
        f"• Gagal    : {gagal} orang",
        parse_mode="Markdown",
    )


async def back_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass

    context.user_data.clear()
    user_id = query.from_user.id

    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute("DELETE FROM orders WHERE user_id=? AND status='waiting'", (user_id,))
    conn.commit()
    conn.close()

    try:
        await query.edit_message_text(
            teks_menu_utama(), parse_mode="Markdown", reply_markup=keyboard_menu_utama()
        )
    except:
        try:
            await query.message.delete()
        except:
            pass
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=teks_menu_utama(),
            parse_mode="Markdown",
            reply_markup=keyboard_menu_utama(),
        )


# =================== MAIN ===================


def main():
    init_db()

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cek", cek_order))
    app.add_handler(CommandHandler("pending", admin_pending))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("riwayat", admin_riwayat))
    app.add_handler(CommandHandler("broadcast", admin_broadcast_cmd))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy$"))
    app.add_handler(CallbackQueryHandler(back_start, pattern="^back_start$"))
    app.add_handler(CallbackQueryHandler(back_orders, pattern="^back_orders$"))
    app.add_handler(
        CallbackQueryHandler(admin_see_orders, pattern="^admin_see_orders$")
    )
    app.add_handler(CallbackQueryHandler(pilih_paket, pattern="^pilih_"))
    app.add_handler(CallbackQueryHandler(admin_proses_order, pattern="^proses_"))
    app.add_handler(
        CallbackQueryHandler(admin_konfirmasi, pattern="^(confirm|reject)_")
    )

    app.add_handler(
        MessageHandler(filters.PHOTO & ~filters.Chat(chat_id=ADMIN_ID), terima_bukti)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Chat(chat_id=ADMIN_ID),
            admin_kirim_link,
        )
    )

    print("Bot aktif...")
    app.run_polling()


if __name__ == "__main__":
    main()
