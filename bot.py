import os
import sqlite3
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from telegram import Update, BotCommand, BotCommandScopeChat, BotCommandScopeDefault, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

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
    }
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
    conn.commit()
    conn.close()

def get_order(user_id):
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "SELECT * FROM orders WHERE user_id=? AND status IN ('pending', 'waiting') ORDER BY id DESC LIMIT 1",
        (user_id,)
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

def update_status(user_id, status):
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "UPDATE orders SET status=? WHERE user_id=? AND status IN ('pending', 'waiting')",
        (status, user_id)
    )
    conn.commit()
    conn.close()

def format_harga(harga):
    return f"Rp {harga:,}".replace(",", ".")

def simpan_admin_msg(context, user_id, message_id):
    context.bot_data.setdefault('admin_messages', {})
    context.bot_data['admin_messages'].setdefault(user_id, [])
    context.bot_data['admin_messages'][user_id].append(message_id)

async def hapus_admin_msg(context, user_id):
    msg_ids = context.bot_data.get('admin_messages', {}).pop(user_id, [])
    for msg_id in msg_ids:
        try:
            await context.bot.delete_message(chat_id=ADMIN_ID, message_id=msg_id)
        except:
            pass

# =================== POST INIT ===================

async def post_init(application: Application):
    await application.bot.set_my_commands(
        [BotCommand("start", "Buka toko")],
        scope=BotCommandScopeDefault()
    )
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Buka toko"),
            BotCommand("pending", "Lihat order pending"),
        ],
        scope=BotCommandScopeChat(chat_id=ADMIN_ID)
    )

# =================== HANDLERS ===================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🛍️ *HYPER FAMILY BUY*\n\n"
        "Selamat datang! Berikut produk tersedia:\n\n"
        "🔥 *Gb Biasa*\n"
        "• 160+ Video Premium — *Rp 5.000*\n\n"
        "👑 *Gb Vip*\n"
        "• 6.800+ Video Premium — *Rp 25.000*\n\n"
        "💳 QRIS _(semua e-wallet)_ · ⚡ Proses 1–5 menit"
    )

    keyboard = [
        [InlineKeyboardButton("🛒  Beli Sekarang", callback_data="buy")],
        [
            InlineKeyboardButton("⭐ Testimoni", url="https://t.me/+7zsdSrwYIG8wOTg1"),
            InlineKeyboardButton("💬 Admin", url="https://t.me/username_admin")
        ]
    ]

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    text = (
        "📦 *Pilih Paket*\n\n"
        "🔥 *Gb Biasa*\n"
        "• 160+ Video Premium\n"
        "• Harga: *Rp 5.000*\n\n"
        "👑 *Gb Vip*\n"
        "• 6.800+ Video Premium\n"
        "• Harga: *Rp 25.000*"
    )

    keyboard = [
        [InlineKeyboardButton("🔥  Gb Biasa — Rp 5.000", callback_data="pilih_gb_biasa")],
        [InlineKeyboardButton("👑  Gb Vip — Rp 25.000", callback_data="pilih_gb_vip")],
        [InlineKeyboardButton("← Kembali", callback_data="back_start")]
    ]

    await query.edit_message_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def pilih_paket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    paket_id = query.data.replace("pilih_", "")
    paket = PAKET[paket_id]
    user_id = query.from_user.id
    user_name = query.from_user.full_name

    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "DELETE FROM orders WHERE user_id=? AND status IN ('waiting', 'pending')",
        (user_id,)
    )
    c.execute(
        "INSERT INTO orders (user_id, user_name, paket_id, file_id, status, waktu) VALUES (?, ?, ?, '', 'waiting', ?)",
        (user_id, user_name, paket_id, datetime.now().strftime("%H:%M — %d/%m/%Y"))
    )
    conn.commit()
    conn.close()

    context.user_data['paket_id'] = paket_id
    expire = (datetime.now() + timedelta(minutes=30)).strftime("%H:%M")

    caption = (
        f"{paket['emoji']} *{paket['nama']}*\n\n"
        f"• Konten: {paket['deskripsi']}\n"
        f"• Total: *{format_harga(paket['harga'])}*\n"
        f"• Sesi: Berlaku hingga pukul *{expire}*\n\n"
        f"*Langkah Pembayaran:*\n"
        f"1. Scan QRIS di atas\n"
        f"2. Transfer tepat *{format_harga(paket['harga'])}*\n"
        f"3. Screenshot bukti transfer\n"
        f"4. Kirim screenshot ke chat ini\n\n"
        f"_Nominal harus sesuai & screenshot harus jelas._"
    )

    keyboard = [[InlineKeyboardButton("✕  Batalkan", callback_data="back_start")]]

    await query.message.delete()

    if not os.path.exists(QRIS_PHOTO_PATH):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ QRIS tidak tersedia. Hubungi admin."
        )
        return

    with open(QRIS_PHOTO_PATH, 'rb') as photo:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=photo,
            caption=caption,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📸 Kirim screenshot bukti transfer di sini."
    )

    context.job_queue.run_once(
        auto_cancel,
        timedelta(minutes=30),
        chat_id=user_id,
        user_id=user_id,
        name=str(user_id)
    )

async def auto_cancel(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "UPDATE orders SET status='expired' WHERE user_id=? AND status IN ('waiting', 'pending')",
        (user_id,)
    )
    conn.commit()
    conn.close()
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "⏰ *Sesi Berakhir*\n\n"
            "Order dibatalkan otomatis.\n"
            "Ketik /start untuk memulai order baru."
        ),
        parse_mode='Markdown'
    )

async def terima_bukti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id

    paket_id = context.user_data.get('paket_id')

    if not paket_id:
        conn = sqlite3.connect("orders.db")
        c = conn.cursor()
        c.execute(
            "SELECT paket_id FROM orders WHERE user_id=? AND status='waiting' ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        row = c.fetchone()
        conn.close()
        if row:
            paket_id = row[0]
        else:
            await update.message.reply_text(
                "⚠️ Kamu belum memilih paket.\nKetik /start untuk mulai."
            )
            return

    paket = PAKET[paket_id]
    file_id = update.message.photo[-1].file_id

    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "UPDATE orders SET file_id=?, status='pending' WHERE user_id=? AND status='waiting'",
        (file_id, user_id)
    )
    conn.commit()
    conn.close()

    for job in context.job_queue.get_jobs_by_name(str(user_id)):
        job.schedule_removal()

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"🧾 *Pembayaran Sedang Diverifikasi*\n\n"
            f"Bukti transfer diterima dan sedang diverifikasi.\n\n"
            f"• Paket: {paket['emoji']} {paket['nama']}\n"
            f"• Total: {format_harga(paket['harga'])}\n"
            f"• Waktu: {datetime.now().strftime('%H:%M, %d %b %Y')}\n\n"
            f"⏳ Estimasi 1–5 menit. Mohon tunggu."
        ),
        parse_mode='Markdown'
    )

    keyboard = [
        [InlineKeyboardButton("📋  Lihat Daftar Order", callback_data="admin_see_orders")]
    ]

    notif_msg = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"🔔 *Order Baru*\n\n"
            f"• Pembeli: {user.full_name}\n"
            f"• Paket: {paket['emoji']} {paket['nama']}\n"
            f"• Total: {format_harga(paket['harga'])}\n"
            f"• Waktu: {datetime.now().strftime('%H:%M, %d %b %Y')}"
        ),
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    simpan_admin_msg(context, user_id, notif_msg.message_id)

# =================== ADMIN ===================

async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return

    orders = get_all_pending()

    if not orders:
        await update.message.reply_text("✅ Tidak ada order pending saat ini.")
        return

    text = f"📋 *Order Pending ({len(orders)})*\n\n"
    keyboard = []

    for o in orders:
        paket = PAKET[o[3]]
        text += f"{paket['emoji']} *{o[2]}* — {paket['nama']} — {o[6]}\n"
        keyboard.append([
            InlineKeyboardButton(f"👤  Proses: {o[2]}", callback_data=f"proses_{o[1]}")
        ])

    await update.message.reply_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_see_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    orders = get_all_pending()

    if not orders:
        await query.answer("✅ Tidak ada order pending.", show_alert=True)
        return

    text = f"📋 *Order Pending ({len(orders)})*\n\n"
    keyboard = []

    for o in orders:
        paket = PAKET[o[3]]
        text += f"{paket['emoji']} *{o[2]}* — {paket['nama']} — {o[6]}\n"
        keyboard.append([
            InlineKeyboardButton(f"👤  Proses: {o[2]}", callback_data=f"proses_{o[1]}")
        ])

    await query.message.delete()
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_proses_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = int(query.data.replace("proses_", ""))
    order = get_order(user_id)

    if not order:
        await query.edit_message_text("⚠️ Order tidak ditemukan atau sudah diproses.")
        return

    paket = PAKET[order[3]]

    caption = (
        f"{paket['emoji']} *{paket['nama']}*\n\n"
        f"• Pembeli: *{order[2]}* (`{order[1]}`)\n"
        f"• Konten: {paket['deskripsi']}\n"
        f"• Total: {format_harga(paket['harga'])}\n"
        f"• Waktu: {order[6]}"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Konfirmasi", callback_data=f"confirm_{user_id}"),
            InlineKeyboardButton("❌ Tolak", callback_data=f"reject_{user_id}")
        ],
        [InlineKeyboardButton("← Kembali", callback_data="back_orders")]
    ]

    await query.message.delete()

    foto_msg = await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=order[4],
        caption=caption,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    simpan_admin_msg(context, user_id, foto_msg.message_id)

async def back_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()

    orders = get_all_pending()

    if not orders:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text="✅ Tidak ada order pending saat ini."
        )
        return

    text = f"📋 *Order Pending ({len(orders)})*\n\n"
    keyboard = []

    for o in orders:
        paket = PAKET[o[3]]
        text += f"{paket['emoji']} *{o[2]}* — {paket['nama']} — {o[6]}\n"
        keyboard.append([
            InlineKeyboardButton(f"👤  Proses: {o[2]}", callback_data=f"proses_{o[1]}")
        ])

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_konfirmasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    action = parts[0]
    user_id = int(parts[1])

    order = get_order(user_id)
    if not order:
        await query.edit_message_caption("⚠️ Order tidak ditemukan atau sudah diproses.")
        return

    paket = PAKET[order[3]]

    if action == "confirm":
        context.bot_data['waiting_link_for'] = user_id

        await query.edit_message_caption(
            (
                f"✅ *Dikonfirmasi*\n\n"
                f"• Pembeli: *{order[2]}*\n"
                f"• Paket: {paket['emoji']} {paket['nama']}\n\n"
                f"_Kirim link produk sekarang._"
            ),
            parse_mode='Markdown'
        )

    elif action == "reject":
        update_status(user_id, 'rejected')

        await query.edit_message_caption(
            (
                f"❌ *Ditolak*\n\n"
                f"• Pembeli: {order[2]}\n"
                f"• Paket: {paket['emoji']} {paket['nama']}"
            ),
            parse_mode='Markdown'
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "❌ *Pembayaran Ditolak*\n\n"
                "Bukti transfer tidak dapat diverifikasi.\n\n"
                "Kemungkinan penyebab:\n"
                "• Nominal tidak sesuai\n"
                "• Screenshot tidak jelas atau terpotong\n"
                "• Transfer belum masuk\n\n"
                "Ketik /start untuk mencoba lagi atau hubungi admin."
            ),
            parse_mode='Markdown'
        )

        await hapus_admin_msg(context, user_id)

async def admin_kirim_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.bot_data.get('waiting_link_for')

    if not user_id:
        return

    link = update.message.text
    order = get_order(user_id)

    if not order:
        await update.message.reply_text("⚠️ Order tidak ditemukan.")
        context.bot_data.pop('waiting_link_for', None)
        return

    paket = PAKET[order[3]]

    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"✅ *Pembayaran Berhasil*\n\n"
            f"• Paket: {paket['emoji']} {paket['nama']}\n"
            f"• Konten: {paket['deskripsi']}\n\n"
            f"🔗 *Link Produk:*\n{link}\n\n"
            f"_Simpan link ini. Produk dapat diakses kapan saja._\n\n"
            f"Terima kasih telah berbelanja! 🙏"
        ),
        parse_mode='Markdown'
    )

    update_status(user_id, 'completed')
    context.bot_data.pop('waiting_link_for', None)

    await hapus_admin_msg(context, user_id)

    await update.message.reply_text(
        f"✅ Link berhasil dikirim ke *{order[2]}*.",
        parse_mode='Markdown'
    )

async def back_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()

    user_id = query.from_user.id
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute("DELETE FROM orders WHERE user_id=? AND status='waiting'", (user_id,))
    conn.commit()
    conn.close()

    text = (
        "🛍️ *HYPER FAMILY BUY*\n\n"
        "Selamat datang! Berikut produk tersedia:\n\n"
        "🔥 *Gb Biasa*\n"
        "• 160+ Video Premium — *Rp 5.000*\n\n"
        "👑 *Gb Vip*\n"
        "• 6.800+ Video Premium — *Rp 25.000*\n\n"
        "💳 QRIS _(semua e-wallet)_ · ⚡ Proses 1–5 menit"
    )

    keyboard = [
        [InlineKeyboardButton("🛒  Beli Sekarang", callback_data="buy")],
        [
            InlineKeyboardButton("⭐ Testimoni", url="https://t.me/+7zsdSrwYIG8wOTg1"),
            InlineKeyboardButton("💬 Admin", url="https://t.me/username_admin")
        ]
    ]

    try:
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except:
        try:
            await query.message.delete()
        except:
            pass
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# =================== MAIN ===================

def main():
    init_db()

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pending", admin_pending))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy$"))
    app.add_handler(CallbackQueryHandler(back_start, pattern="^back_start$"))
    app.add_handler(CallbackQueryHandler(back_orders, pattern="^back_orders$"))
    app.add_handler(CallbackQueryHandler(admin_see_orders, pattern="^admin_see_orders$"))
    app.add_handler(CallbackQueryHandler(pilih_paket, pattern="^pilih_"))
    app.add_handler(CallbackQueryHandler(admin_proses_order, pattern="^proses_"))
    app.add_handler(CallbackQueryHandler(admin_konfirmasi, pattern="^(confirm|reject)_"))

    app.add_handler(
        MessageHandler(
            filters.PHOTO & ~filters.Chat(chat_id=ADMIN_ID),
            terima_bukti
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Chat(chat_id=ADMIN_ID),
            admin_kirim_link
        )
    )

    print("Bot aktif...")
    app.run_polling()

if __name__ == "__main__":
    main()
