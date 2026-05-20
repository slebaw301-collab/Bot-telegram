import os
import sqlite3
import threading
import asyncio
import requests as req_lib
from urllib.parse import unquote
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

# ── Trakteer Config ──
TRAKTEER_USERNAME = "alfat_alfat"
TRAKTEER_EMAIL = "alfat7553@gmail.com"
TRAKTEER_PASSWORD = "3@UhkCiLE7km3fR"

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

# =================== TRAKTEER SESSION ===================

_trakteer_session = None
_trakteer_lock = threading.Lock()


def get_trakteer_session():
    """Login ke Trakteer dan return requests.Session yang sudah login."""
    global _trakteer_session
    with _trakteer_lock:
        s = req_lib.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        # Ambil CSRF token
        r = s.get("https://trakteer.id/login", timeout=15)
        csrf = ""
        for line in r.text.split("\n"):
            if "_token" in line and 'value="' in line:
                try:
                    csrf = line.split('value="')[1].split('"')[0]
                    break
                except Exception:
                    pass

        if not csrf:
            raise Exception("Gagal ambil CSRF token Trakteer")

        xsrf = s.cookies.get("XSRF-TOKEN", "")
        if xsrf:
            xsrf = unquote(xsrf)

        s.headers.update({
            "X-XSRF-TOKEN": xsrf,
            "Referer": "https://trakteer.id/login",
        })

        resp = s.post("https://trakteer.id/login", data={
            "_token": csrf,
            "email": TRAKTEER_EMAIL,
            "password": TRAKTEER_PASSWORD,
        }, allow_redirects=True, timeout=15)

        # Cek apakah login berhasil
        if "dashboard" not in resp.url and "manage" not in resp.url:
            # Coba cek cookie
            if not s.cookies.get("trakteer-sess") and not s.cookies.get("trakteer_session"):
                raise Exception("Login Trakteer gagal")

        _trakteer_session = s
        return s


def trakteer_create_qris(amount: int):
    """
    Buat transaksi QRIS di Trakteer.
    Return: (qr_image_url, transaction_id) atau raise Exception
    """
    s = get_trakteer_session()

    # Refresh XSRF token
    xsrf = s.cookies.get("XSRF-TOKEN", "")
    if xsrf:
        xsrf = unquote(xsrf)

    headers = {
        "X-XSRF-TOKEN": xsrf,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://trakteer.id/{TRAKTEER_USERNAME}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    # Hitung jumlah "pizza" (unit Trakteer) — 1 pizza = Rp 1.000 (bisa beda)
    # Trakteer pakai sistem unit, kita kirim amount langsung
    payload = {
        "amount": amount,
        "unit": amount,
        "message": "Pembelian konten",
        "anonymous": True,
        "payment_type": "qris",
        "customer_info": {
            "name": "Buyer",
            "email": "buyer@gmail.com",
            "phone": "08123456789",
        },
        "creator_id": TRAKTEER_USERNAME,
        "quantity": 1,
    }

    # Coba endpoint pembuatan transaksi
    r = s.post(
        f"https://trakteer.id/api/v1/public/supports",
        json=payload,
        headers=headers,
        timeout=20,
    )

    data = r.json()

    # Ambil QR image dan transaction ID dari response
    qr_url = None
    trx_id = None

    # Coba berbagai path response Trakteer
    if "data" in data:
        d = data["data"]
        qr_url = d.get("qr_image") or d.get("qr_url") or d.get("qris_url")
        trx_id = d.get("id") or d.get("transaction_id") or d.get("order_id")
    elif "qr_image" in data:
        qr_url = data["qr_image"]
        trx_id = data.get("id") or data.get("transaction_id")
    elif "payment_url" in data:
        qr_url = data["payment_url"]
        trx_id = data.get("id")

    if not qr_url or not trx_id:
        raise Exception(f"Response tidak valid: {data}")

    return qr_url, str(trx_id)


def trakteer_check_payment(transaction_id: str):
    """
    Cek status pembayaran transaksi.
    Return: True jika sudah bayar, False jika belum
    """
    s = get_trakteer_session()
    xsrf = s.cookies.get("XSRF-TOKEN", "")
    if xsrf:
        xsrf = unquote(xsrf)

    headers = {
        "X-XSRF-TOKEN": xsrf,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
    }

    r = s.get(
        f"https://trakteer.id/api/v1/public/supports/{transaction_id}",
        headers=headers,
        timeout=15,
    )

    try:
        data = r.json()
        status = ""
        if "data" in data:
            status = str(data["data"].get("status", "")).lower()
        elif "status" in data:
            status = str(data.get("status", "")).lower()

        return status in ("paid", "success", "completed", "settlement")
    except Exception:
        return False


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
            transaction_id TEXT,
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
        "SELECT * FROM orders WHERE user_id=? AND status IN ('waiting', 'paid') ORDER BY id DESC LIMIT 1",
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
    c.execute("SELECT COUNT(*) FROM orders WHERE status='paid'")
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
        "UPDATE orders SET status=? WHERE user_id=? AND status IN ('waiting', 'paid')",
        (status, user_id),
    )
    conn.commit()
    conn.close()


def format_harga(harga):
    return f"Rp {harga:,}".replace(",", ".")


def keyboard_hubungi_admin():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("💬 Hubungi Admin", url=f"tg://user?id={ADMIN_ID}")]]
    )


# =================== HELPER: SIMPAN & HAPUS PESAN ===================


def simpan_msg_user(context, user_id, message_id):
    context.bot_data.setdefault("user_start_messages", {})
    context.bot_data["user_start_messages"].setdefault(user_id, [])
    context.bot_data["user_start_messages"][user_id].append(message_id)


async def hapus_msg_user_lama(context, chat_id):
    msgs = context.bot_data.get("user_start_messages", {}).pop(chat_id, [])
    for msg_id in msgs:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass


def simpan_order_msg_admin(context, user_id, message_id):
    context.bot_data.setdefault("order_process_messages", {})
    context.bot_data["order_process_messages"].setdefault(user_id, [])
    context.bot_data["order_process_messages"][user_id].append(message_id)


async def hapus_order_msg_admin(context, user_id):
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
        "⚡ Proses pengiriman otomatis setelah pembayaran"
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
            BotCommand("stats", "Statistik penjualan"),
            BotCommand("riwayat", "Riwayat transaksi selesai"),
            BotCommand("broadcast", "Kirim pesan ke semua buyer"),
        ],
        scope=BotCommandScopeChat(chat_id=ADMIN_ID),
    )


# =================== HANDLERS ===================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    simpan_user(user.id, user.full_name)

    context.bot_data.pop("waiting_broadcast", None)
    context.user_data.pop("paket_id", None)

    # Cek apakah user masih punya order aktif (waiting/paid)
    order = get_order(user.id)
    if order:
        await hapus_msg_user_lama(context, chat_id)
        paket = PAKET.get(order[3], {"emoji": "📦", "nama": "Unknown"})
        if order[5] == "waiting":
            teks = (
                "⏳ *Pesanan Sedang Aktif*\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Kamu masih punya QRIS aktif untuk:\n"
                f"• Paket  : {paket['emoji']} {paket['nama']}\n"
                f"• Status : ⏳ Menunggu pembayaran\n\n"
                "Silakan selesaikan pembayaran atau batalkan."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✕ Batalkan Pesanan", callback_data="back_start")]
            ])
        else:
            teks = (
                "✅ *Pembayaran Diterima!*\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"• Paket  : {paket['emoji']} {paket['nama']}\n"
                f"• Status : ✅ Lunas\n\n"
                "⏳ Link konten sedang disiapkan admin.\n"
                "_Mohon tunggu sebentar..._"
            )
            keyboard = keyboard_hubungi_admin()

        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=teks,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        simpan_msg_user(context, chat_id, msg.message_id)
        return

    await hapus_msg_user_lama(context, chat_id)
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
        "waiting": ("⏳", "Menunggu pembayaran"),
        "paid": ("✅", "Lunas — menunggu link dari admin"),
        "completed": ("🎉", "Selesai & link sudah dikirim"),
        "rejected": ("❌", "Dibatalkan"),
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
        reply_markup=keyboard_hubungi_admin(),
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
    if order:
        paket = PAKET.get(order[3], {"emoji": "📦", "nama": "Unknown"})
        try:
            await query.edit_message_text(
                "⏳ *Pesanan Masih Aktif*\n\n"
                f"Kamu masih punya pesanan aktif:\n"
                f"• Paket  : {paket['emoji']} {paket['nama']}\n\n"
                "Selesaikan pembayaran atau batalkan dulu.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✕ Batalkan Pesanan", callback_data="back_start")]
                ]),
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
    chat_id = update.effective_chat.id

    # Hapus order lama
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "DELETE FROM orders WHERE user_id=? AND status IN ('waiting')",
        (user_id,),
    )
    conn.commit()
    conn.close()

    # Tampilkan loading
    try:
        await query.edit_message_text(
            f"⏳ *Membuat QRIS...*\n\nMohon tunggu sebentar.",
            parse_mode="Markdown",
        )
    except Exception:
        pass

    # Generate QRIS via Trakteer
    try:
        qr_url, trx_id = await asyncio.get_event_loop().run_in_executor(
            None, trakteer_create_qris, paket["harga"]
        )
    except Exception as e:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ *Gagal Membuat QRIS*\n\n"
                f"Terjadi kesalahan: {e}\n\n"
                "Silakan coba lagi atau hubungi admin."
            ),
            parse_mode="Markdown",
            reply_markup=keyboard_hubungi_admin(),
        )
        simpan_msg_user(context, chat_id, msg.message_id)
        return

    # Simpan order ke DB
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "INSERT INTO orders (user_id, user_name, paket_id, transaction_id, status, waktu) VALUES (?, ?, ?, ?, 'waiting', ?)",
        (user_id, user_name, paket_id, trx_id, datetime.now().strftime("%H:%M — %d/%m/%Y")),
    )
    conn.commit()
    conn.close()

    expire = (datetime.now() + timedelta(minutes=30)).strftime("%H:%M")
    caption = (
        f"{paket['emoji']} *{paket['nama']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"• Konten  : {paket['deskripsi']}\n"
        f"• Total   : *{format_harga(paket['harga'])}*\n"
        f"• Berlaku : Hingga pukul *{expire}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"*Cara Bayar:*\n\n"
        f"1️⃣ Scan QR di atas pakai GoPay/OVO/DANA/m-banking\n"
        f"2️⃣ Nominal sudah otomatis terisi *{format_harga(paket['harga'])}*\n"
        f"3️⃣ Selesaikan pembayaran\n"
        f"4️⃣ Link konten dikirim otomatis setelah lunas ✅\n\n"
        f"⏰ _QRIS expired otomatis pukul {expire}_"
    )

    keyboard = [[InlineKeyboardButton("✕  Batalkan Pesanan", callback_data="back_start")]]

    try:
        await query.message.delete()
    except Exception:
        pass

    # Kirim QR sebagai foto dari URL
    try:
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=qr_url,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        # Fallback: kirim sebagai teks dengan link
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=caption + f"\n\n🔗 [Buka QRIS]({qr_url})",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    simpan_msg_user(context, chat_id, msg.message_id)

    # Hapus job lama
    for job in context.job_queue.get_jobs_by_name(str(user_id)):
        job.schedule_removal()

    # Start polling pembayaran tiap 5 detik
    context.job_queue.run_repeating(
        cek_pembayaran_trakteer,
        interval=5,
        first=5,
        chat_id=user_id,
        user_id=user_id,
        name=f"poll_{user_id}",
        data={"trx_id": trx_id, "paket": paket, "user_name": user_name},
    )

    # Auto cancel setelah 30 menit
    context.job_queue.run_once(
        auto_cancel,
        timedelta(minutes=30),
        chat_id=user_id,
        user_id=user_id,
        name=f"cancel_{user_id}",
    )


async def cek_pembayaran_trakteer(context: ContextTypes.DEFAULT_TYPE):
    """Job polling cek status pembayaran Trakteer tiap 5 detik."""
    user_id = context.job.user_id
    trx_id = context.job.data["trx_id"]
    paket = context.job.data["paket"]
    user_name = context.job.data["user_name"]

    # Cek di DB apakah order masih waiting
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "SELECT status FROM orders WHERE user_id=? AND transaction_id=?",
        (user_id, trx_id),
    )
    row = c.fetchone()
    conn.close()

    if not row or row[0] != "waiting":
        # Order sudah tidak waiting, stop polling
        context.job.schedule_removal()
        return

    # Cek ke Trakteer
    try:
        sudah_bayar = await asyncio.get_event_loop().run_in_executor(
            None, trakteer_check_payment, trx_id
        )
    except Exception:
        return  # Gagal cek, coba lagi next interval

    if not sudah_bayar:
        return

    # ── PEMBAYARAN LUNAS ──
    context.job.schedule_removal()

    # Hapus auto cancel job
    for job in context.job_queue.get_jobs_by_name(f"cancel_{user_id}"):
        job.schedule_removal()

    # Update status ke paid
    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "UPDATE orders SET status='paid' WHERE user_id=? AND transaction_id=?",
        (user_id, trx_id),
    )
    conn.commit()
    conn.close()

    # Notif ke buyer
    try:
        paid_msg = await context.bot.send_message(
            chat_id=user_id,
            text=(
                "✅ *Pembayaran Diterima!*\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 Paket : {paket['emoji']} {paket['nama']}\n"
                f"💰 Total : {format_harga(paket['harga'])}\n\n"
                "⏳ Link konten sedang disiapkan admin\n"
                "dan akan dikirim ke chat ini sebentar lagi.\n\n"
                "_Mohon tunggu..._"
            ),
            parse_mode="Markdown",
        )
        simpan_msg_user(context, user_id, paid_msg.message_id)
    except Exception:
        pass

    # Notif ke admin
    notif_msg = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"💰 *Pembayaran Masuk!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Pembeli : {user_name}\n"
            f"📦 Paket   : {paket['emoji']} {paket['nama']}\n"
            f"💰 Total   : {format_harga(paket['harga'])}\n"
            f"🕐 Waktu   : {datetime.now().strftime('%H:%M, %d %b %Y')}\n\n"
            f"_Kirimkan link konten sekarang._"
        ),
        parse_mode="Markdown",
    )
    simpan_order_msg_admin(context, user_id, notif_msg.message_id)

    # Simpan data untuk pengiriman link
    context.bot_data["waiting_link_for"] = {
        "user_id": user_id,
        "user_name": user_name,
        "paket": paket,
        "notif_msg_id": notif_msg.message_id,
    }

    # Minta admin kirim link
    link_req = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"🔗 *Kirim Link Konten*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Pembayaran *{user_name}* sudah masuk.\n\n"
            f"Kirimkan link konten untuk paket\n"
            f"{paket['emoji']} *{paket['nama']}* sekarang.\n\n"
            f"_Ketik /batal untuk membatalkan._"
        ),
        parse_mode="Markdown",
    )
    simpan_order_msg_admin(context, user_id, link_req.message_id)


async def auto_cancel(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id

    # Stop polling job
    for job in context.job_queue.get_jobs_by_name(f"poll_{user_id}"):
        job.schedule_removal()

    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "UPDATE orders SET status='expired' WHERE user_id=? AND status='waiting'",
        (user_id,),
    )
    conn.commit()
    conn.close()

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "⌛ *QRIS Expired*\n\n"
                "Pesanan kamu dibatalkan otomatis karena\n"
                "melebihi batas waktu 30 menit.\n\n"
                "Ketik /start untuk membuat pesanan baru.\n"
                "Jika ada kendala, silakan hubungi admin."
            ),
            parse_mode="Markdown",
            reply_markup=keyboard_hubungi_admin(),
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

    # Stop semua job terkait user ini
    for job in context.job_queue.get_jobs_by_name(f"poll_{user_id}"):
        job.schedule_removal()
    for job in context.job_queue.get_jobs_by_name(f"cancel_{user_id}"):
        job.schedule_removal()

    conn = sqlite3.connect("orders.db")
    c = conn.cursor()
    c.execute(
        "UPDATE orders SET status='expired' WHERE user_id=? AND status='waiting'",
        (user_id,),
    )
    conn.commit()
    conn.close()

    try:
        await query.message.delete()
    except Exception:
        pass

    await hapus_msg_user_lama(context, chat_id)

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=teks_menu_utama(),
        parse_mode="Markdown",
        reply_markup=keyboard_menu_utama(),
    )
    simpan_msg_user(context, chat_id, msg.message_id)


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
        f"⏳ Menunggu link dikirim : *{s['pending_count']} pesanan*",
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


# =================== ADMIN TEXT HANDLER ===================


async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return

    text_input = update.message.text

    # ── PRIORITAS 1: Kirim link ke buyer ──
    link_data = context.bot_data.get("waiting_link_for")
    if link_data:
        target_user_id = link_data["user_id"]
        target_name = link_data["user_name"]
        paket = link_data["paket"]

        context.bot_data.pop("waiting_link_for", None)

        # Validasi link
        if not text_input.startswith("http"):
            await update.message.reply_text(
                "⚠️ *Bukan link valid!*\n\n"
                "Link harus dimulai dengan `http` atau `https`.\n"
                "Coba kirim ulang linknya.",
                parse_mode="Markdown",
            )
            context.bot_data["waiting_link_for"] = link_data
            return

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
            simpan_msg_user(context, target_user_id, link_msg.message_id)

            # Update status completed
            conn = sqlite3.connect("orders.db")
            c = conn.cursor()
            c.execute(
                "UPDATE orders SET status='completed' WHERE user_id=? AND status='paid'",
                (target_user_id,),
            )
            conn.commit()
            conn.close()

            konfirm_msg = await update.message.reply_text(
                f"✅ *Link Berhasil Dikirim!*\n\n"
                f"👤 Penerima : {target_name}\n"
                f"📦 Paket    : {paket['emoji']} {paket['nama']}\n"
                f"🔗 Link     : {text_input}",
                parse_mode="Markdown",
            )
            simpan_order_msg_admin(context, target_user_id, konfirm_msg.message_id)

        except Exception as e:
            await update.message.reply_text(
                f"⚠️ Gagal kirim link ke buyer.\nError: {e}\n\n"
                f"Coba kirim manual ke user ID: `{target_user_id}`",
                parse_mode="Markdown",
            )
            return

        # Hapus semua chat admin terkait order ini
        await hapus_order_msg_admin(context, target_user_id)
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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cek", cek_order))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("riwayat", admin_riwayat))
    app.add_handler(CommandHandler("broadcast", admin_broadcast_cmd))
    app.add_handler(CommandHandler("batal", admin_batal))

    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy$"))
    app.add_handler(CallbackQueryHandler(pilih_paket, pattern="^pilih_"))
    app.add_handler(CallbackQueryHandler(back_start_callback, pattern="^back_start$"))

    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.User(ADMIN_ID) & ~filters.COMMAND,
            handle_admin_text,
        )
    )

    app.run_polling()


if __name__ == "__main__":
    main()
