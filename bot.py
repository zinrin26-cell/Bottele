import os, re, sqlite3, secrets, string, threading
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template_string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("8363651531", "").split(",") if x.strip().isdigit()}
ADMIN_CHANNEL_ID = os.getenv("8363651531", "").strip()
WITHDRAW_GROUP_ID = os.getenv("WITHDRAW_GROUP_ID", "").strip()
WEB_BASE_URL = os.getenv("WEB_BASE_URL", "http://localhost:9898").rstrip("/")
WEB_ADMIN_SECRET = os.getenv("WEB_ADMIN_SECRET", "change-this-secret")
DB_PATH = os.getenv("DB_PATH", "bot.db")
PORT = int(os.getenv("PORT", "9898"))

app_web = Flask(__name__)

def db():
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          user_id INTEGER PRIMARY KEY, username TEXT, balance INTEGER NOT NULL DEFAULT 0,
          bank TEXT DEFAULT '', account_no TEXT DEFAULT '', account_name TEXT DEFAULT '',
          links_done INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks(
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, url TEXT NOT NULL,
          reward INTEGER NOT NULL, max_uses INTEGER NOT NULL, uses INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS claims(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, task_id INTEGER NOT NULL,
          key_code TEXT UNIQUE NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          created_at TEXT NOT NULL, UNIQUE(user_id, task_id)
        );
        CREATE TABLE IF NOT EXISTS withdrawals(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, paid_at TEXT DEFAULT ''
        );
        """)
init_db()

def is_admin(uid): return uid in ADMIN_IDS
def now(): return datetime.now(timezone.utc).isoformat()
def money(n): return f"{int(n):,}đ".replace(",", ".")

def ensure_user(tg_user):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id, username, created_at) VALUES(?,?,?)",
                  (tg_user.id, tg_user.username or "", now()))
        c.execute("UPDATE users SET username=? WHERE user_id=?", (tg_user.username or "", tg_user.id))

def home_kb(uid):
    rows = [
        [InlineKeyboardButton("👤 Thông tin tài khoản", callback_data="profile"),
         InlineKeyboardButton("➕ Thêm thông tin ngân hàng", callback_data="bank")],
        [InlineKeyboardButton("🔗 Vượt link kiếm tiền", callback_data="tasks"),
         InlineKeyboardButton("💸 Rút tiền", callback_data="withdraw")],
    ]
    if is_admin(uid): rows.append([InlineKeyboardButton("🛠 Quản trị", callback_data="admin")])
    return InlineKeyboardMarkup(rows)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await update.effective_message.reply_text(
        "Chào mừng bạn đến bot nhiệm vụ kiếm thưởng. Chọn chức năng bên dưới.",
        reply_markup=home_kb(update.effective_user.id))

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(q.from_user)
    if q.data == "home":
        await q.edit_message_text("Menu chính:", reply_markup=home_kb(uid)); return
    if q.data == "profile":
        with db() as c: u = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        await q.edit_message_text(
            f"👤 Tài khoản: {u['username'] or '(không có username)'}\n🆔 ID: {uid}\n"
            f"💰 Số dư: {money(u['balance'])}\n🔗 Link đã vượt: {u['links_done']}\n"
            f"🏦 Ngân hàng: {u['bank'] or 'Chưa cập nhật'}\n"
            f"💳 STK: {u['account_no'] or 'Chưa cập nhật'}\n"
            f"👤 Tên tài khoản: {u['account_name'] or 'Chưa cập nhật'}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="home")]])); return
    if q.data == "bank":
        context.user_data["state"] = "bank"
        await q.edit_message_text("Gửi theo mẫu 3 dòng:\nTên ngân hàng\nSố tài khoản\nTên chủ tài khoản",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="home")]])); return
    if q.data == "tasks":
        with db() as c:
            tasks = c.execute("SELECT * FROM tasks WHERE active=1 AND uses<max_uses ORDER BY id").fetchall()
            done = {r["task_id"] for r in c.execute("SELECT task_id FROM claims WHERE user_id=?", (uid,)).fetchall()}
        rows = []
        text = "🔗 NHIỆM VỤ HIỆN CÓ\nChọn một nhiệm vụ chưa làm. Sau khi hoàn thành link, nhập /key MÃ_KEY.\n"
        for t in tasks:
            remaining = t["max_uses"] - t["uses"]
            text += f"\n#{t['id']} {t['title']} — thưởng {money(t['reward'])} — còn {remaining} lượt"
            if t["id"] not in done:
                rows.append([InlineKeyboardButton(f"#{t['id']} · {t['title'][:30]}", callback_data=f"task:{t['id']}")])
        rows.append([InlineKeyboardButton("⬅️ Menu", callback_data="home")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows)); return
    if q.data.startswith("task:"):
        task_id = int(q.data.split(":")[1])
        with db() as c:
            t = c.execute("SELECT * FROM tasks WHERE id=? AND active=1 AND uses<max_uses", (task_id,)).fetchone()
            old = c.execute("SELECT id FROM claims WHERE user_id=? AND task_id=?", (uid, task_id)).fetchone()
            if t and not old:
                key = ''.join(secrets.choice(string.digits) for _ in range(10))
                while c.execute("SELECT 1 FROM claims WHERE key_code=?", (key,)).fetchone():
                    key = ''.join(secrets.choice(string.digits) for _ in range(10))
                c.execute("INSERT INTO claims(user_id,task_id,key_code,status,created_at) VALUES(?,?,?,'pending',?)",
                          (uid, task_id, key, now()))
        if not t:
            await q.edit_message_text("Nhiệm vụ không còn khả dụng.", reply_markup=home_kb(uid)); return
        if old:
            await q.edit_message_text("Bạn đã nhận nhiệm vụ này trước đó. Nếu đã có key, nhập /key MÃ_KEY.",
                reply_markup=home_kb(uid)); return
        # Key is generated by the web endpoint; bot stores a pending claim with a random one-time nonce.
        await q.edit_message_text(
            f"📌 {t['title']}\nThưởng: {money(t['reward'])}\n\nMở link nhiệm vụ: {t['url']}\n\n"
            f"Sau khi hoàn thành, mở trang nhận key:\n{WEB_BASE_URL}/claim/{key}\n"
            f"Sau đó gửi /key MÃ_KEY cho bot.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Mở nhiệm vụ", url=t['url']),
                                                 InlineKeyboardButton("🔑 Lấy key", url=f"{WEB_BASE_URL}/claim/{key}")],
                                                [InlineKeyboardButton("⬅️ Menu", callback_data="home")]])); return
    if q.data == "withdraw":
        context.user_data["state"] = "withdraw"
        await q.edit_message_text("Nhập số tiền muốn rút (5.000đ–1.000.000đ). Số dư phải đủ và cần có thông tin ngân hàng."); return
    if q.data == "admin":
        if not is_admin(uid): await q.answer("Không có quyền.", show_alert=True); return
        await q.edit_message_text(
            "🛠 QUẢN TRỊ\n"
            "/addlink Tên | URL | tiền_thưởng | số_lượt\n"
            "/links — danh sách nhiệm vụ\n"
            "/withdrawals — yêu cầu rút tiền\n"
            "/credit user_id số_tiền — cộng số dư thủ công\n"
            "/stats — thống kê",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="home")]])); return
    if q.data.startswith("approve:") or q.data.startswith("paid:"):
        if not is_admin(uid): return
        action, wid = q.data.split(":")
        wid = int(wid)
        with db() as c:
            w = c.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)).fetchone()
            if not w: await q.answer("Không tìm thấy yêu cầu."); return
            if action == "approve" and w["status"] == "pending":
                c.execute("UPDATE withdrawals SET status='approved' WHERE id=?", (wid,))
                await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Đã chuyển", callback_data=f"paid:{wid}")]]))
                await q.answer("Đã duyệt. Chuyển khoản rồi bấm Đã chuyển.")
            elif action == "paid" and w["status"] == "approved":
                c.execute("UPDATE withdrawals SET status='paid', paid_at=? WHERE id=?", (now(), wid))
                with db() as c2:
                    u = c2.execute("SELECT username FROM users WHERE user_id=?", (w["user_id"],)).fetchone()
                if WITHDRAW_GROUP_ID:
                    try: await context.bot.send_message(chat_id=WITHDRAW_GROUP_ID,
                        text=f"✅ ĐÃ THANH TOÁN\nYêu cầu #{wid}\nNgười dùng: @{u['username'] or 'N/A'} (ID {w['user_id']})\nSố tiền: {money(w['amount'])}")
                    except Exception: pass
                try: await context.bot.send_message(chat_id=w["user_id"], text=f"✅ Yêu cầu rút #{wid} đã được đánh dấu đã chuyển: {money(w['amount'])}.")
                except Exception: pass
                await q.edit_message_text(f"✅ Yêu cầu #{wid} đã đánh dấu đã chuyển.")
                await q.answer("Đã ghi nhận thanh toán.")
        return

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(update.effective_user)
    state = context.user_data.get("state")
    msg = (update.effective_message.text or "").strip()
    if state == "bank":
        parts = [x.strip() for x in msg.splitlines() if x.strip()]
        if len(parts) != 3:
            await update.effective_message.reply_text("Cần đúng 3 dòng: tên ngân hàng, STK, tên chủ tài khoản."); return
        with db() as c:
            c.execute("UPDATE users SET bank=?, account_no=?, account_name=? WHERE user_id=?", (*parts, uid))
        context.user_data.pop("state", None)
        await update.effective_message.reply_text("✅ Đã lưu thông tin ngân hàng.", reply_markup=home_kb(uid)); return
    if state == "withdraw":
        try: amount = int(msg.replace(".", "").replace(",", "").replace("đ", "").strip())
        except ValueError:
            await update.effective_message.reply_text("Hãy nhập số nguyên, ví dụ 50000."); return
        if not 5000 <= amount <= 1000000:
            await update.effective_message.reply_text("Số tiền rút phải từ 5.000đ đến 1.000.000đ."); return
        with db() as c:
            u = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
            if not u["bank"] or not u["account_no"] or not u["account_name"]:
                await update.effective_message.reply_text("Bạn cần cập nhật thông tin ngân hàng trước.", reply_markup=home_kb(uid)); return
            if u["balance"] < amount:
                await update.effective_message.reply_text(f"Số dư không đủ. Số dư hiện tại: {money(u['balance'])}"); return
            # Reserve/deduct funds when the request is created to prevent double withdrawals.
            c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, uid))
            cur = c.execute("INSERT INTO withdrawals(user_id,amount,status,created_at) VALUES(?,?,'pending',?)", (uid, amount, now()))
            wid = cur.lastrowid
        summary = f"💸 YÊU CẦU RÚT TIỀN #{wid}\nNgười dùng: @{u['username'] or 'N/A'} (ID {uid})\nSố tiền: {money(amount)}\nNgân hàng: {u['bank']}\nSTK: {u['account_no']}\nTên TK: {u['account_name']}"
        if ADMIN_CHANNEL_ID:
            try:
                await context.bot.send_message(chat_id=ADMIN_CHANNEL_ID, text=summary,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Duyệt", callback_data=f"approve:{wid}")]]))
            except Exception as e:
                await update.effective_message.reply_text("Không gửi được yêu cầu tới kênh admin. Liên hệ admin; số dư đã được giữ lại.")
        context.user_data.pop("state", None)
        await update.effective_message.reply_text(f"Đã tạo yêu cầu rút #{wid}. Tiền được giữ lại trong lúc chờ duyệt.", reply_markup=home_kb(uid)); return
    await update.effective_message.reply_text("Chọn chức năng bằng các nút bên dưới.", reply_markup=home_kb(uid))

async def key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Dùng đúng cú pháp: /key 1234567890"); return
    key = context.args[0]
    with db() as c:
        claim = c.execute("""SELECT claims.*, tasks.reward, tasks.max_uses, tasks.uses, tasks.active
          FROM claims JOIN tasks ON tasks.id=claims.task_id WHERE claims.key_code=?""", (key,)).fetchone()
        if not claim or claim["user_id"] != uid or claim["status"] != "pending":
            await update.effective_message.reply_text("❌ Key sai, đã dùng, hoặc không thuộc tài khoản của bạn."); return
        if not claim["active"] or claim["uses"] >= claim["max_uses"]:
            await update.effective_message.reply_text("Nhiệm vụ đã hết lượt."); return
        c.execute("UPDATE claims SET status='completed' WHERE id=?", (claim["id"],))
        c.execute("UPDATE users SET balance=balance+?, links_done=links_done+1 WHERE user_id=?", (claim["reward"], uid))
        c.execute("UPDATE tasks SET uses=uses+1 WHERE id=?", (claim["task_id"],))
    await update.effective_message.reply_text(f"✅ Vượt link thành công! Bạn nhận {money(claim['reward'])}.", reply_markup=home_kb(uid))

async def addlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    raw = update.effective_message.text.partition(" ")[2]
    parts = [x.strip() for x in raw.split("|")]
    if len(parts) != 4:
        await update.effective_message.reply_text("Dùng: /addlink Tên nhiệm vụ | https://... | tiền_thưởng | số_lượt"); return
    title, url, reward, uses = parts
    if not url.startswith("https://") and not url.startswith("http://"):
        await update.effective_message.reply_text("URL phải bắt đầu bằng http:// hoặc https://"); return
    try: reward, uses = int(reward), int(uses)
    except ValueError:
        await update.effective_message.reply_text("Tiền thưởng và số lượt phải là số nguyên."); return
    if reward <= 0 or uses <= 0:
        await update.effective_message.reply_text("Tiền thưởng và số lượt phải lớn hơn 0."); return
    with db() as c: c.execute("INSERT INTO tasks(title,url,reward,max_uses) VALUES(?,?,?,?)", (title, url, reward, uses))
    await update.effective_message.reply_text("✅ Đã thêm nhiệm vụ.")

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("Không có quyền admin."); return
    cmd = update.effective_message.text.split()[0].split("@")[0]
    if cmd == "/links":
        with db() as c: rows = c.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
        await update.effective_message.reply_text("\n".join([f"#{r['id']} {r['title']} | thưởng {money(r['reward'])} | {r['uses']}/{r['max_uses']} lượt | active={r['active']}" for r in rows]) or "Chưa có nhiệm vụ.")
    elif cmd == "/withdrawals":
        with db() as c: rows = c.execute("SELECT * FROM withdrawals WHERE status!='paid' ORDER BY id DESC LIMIT 20").fetchall()
        await update.effective_message.reply_text("\n".join([f"#{r['id']} user={r['user_id']} {money(r['amount'])} status={r['status']}" for r in rows]) or "Không có yêu cầu.")
    elif cmd == "/stats":
        with db() as c:
            users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            tasks = c.execute("SELECT COUNT(*) n FROM tasks WHERE active=1").fetchone()["n"]
            pending = c.execute("SELECT COUNT(*) n FROM withdrawals WHERE status='pending'").fetchone()["n"]
        await update.effective_message.reply_text(f"Người dùng: {users}\nNhiệm vụ đang bật: {tasks}\nYêu cầu rút chờ duyệt: {pending}")
    elif cmd == "/credit":
        args = update.effective_message.text.split()
        if len(args) != 3 or not args[1].isdigit() or not args[2].isdigit():
            await update.effective_message.reply_text("Dùng: /credit user_id số_tiền"); return
        with db() as c:
            c.execute("INSERT OR IGNORE INTO users(user_id,created_at) VALUES(?,?)", (int(args[1]), now()))
            c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (int(args[2]), int(args[1])))
        await update.effective_message.reply_text("Đã cộng số dư.")

@app_web.get("/")
def home_page():
    return """<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>
    <title>Key nhiệm vụ</title><body style='font:16px Arial;max-width:600px;margin:50px auto;padding:20px;background:#101827;color:#fff'>
    <h2>🔑 Cổng nhận key nhiệm vụ</h2><p>Mở đường dẫn nhiệm vụ từ bot, hoàn thành theo hướng dẫn rồi nhấn nút nhận key.</p>
    <p>Lưu ý: trang này chỉ phát key; nó không tự xác minh người dùng đã hoàn thành tác vụ trên website bên ngoài.</p></body>"""

@app_web.get("/claim/<key>")
def claim_page(key):
    with db() as c:
        row = c.execute("SELECT claims.*,tasks.title FROM claims JOIN tasks ON tasks.id=claims.task_id WHERE key_code=?", (key,)).fetchone()
    if not row or row["status"] != "pending":
        return "Key không hợp lệ hoặc đã được sử dụng.", 404
    html = """<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>
    <title>Nhận key</title><body style='font:16px Arial;max-width:600px;margin:50px auto;padding:24px;background:#101827;color:white'>
    <h2>🎉 Nhiệm vụ: {{title}}</h2><p>Nếu bạn đã hoàn thành nhiệm vụ, nhấn nút bên dưới để hiển thị key.</p>
    <p><b>Lưu ý:</b> nút này không xác minh được hành động trên website ngoài. Chỉ sử dụng với nguồn nhiệm vụ bạn tin cậy.</p>
    <form method=post><button style='padding:14px 20px;border:0;border-radius:10px;background:#5eead4;font-weight:bold'>Tạo / hiển thị key</button></form>
    {% if shown %}<h3>Key của bạn: <code>{{key}}</code></h3><p>Quay lại Telegram và gửi: <b>/key {{key}}</b></p>{% endif %}
    </body>"""
    return render_template_string(html, title=row["title"], key=key, shown=False)

@app_web.post("/claim/<key>")
def claim_post(key):
    with db() as c:
        row = c.execute("SELECT claims.*,tasks.title FROM claims JOIN tasks ON tasks.id=claims.task_id WHERE key_code=?", (key,)).fetchone()
    if not row or row["status"] != "pending": return "Key không hợp lệ hoặc đã được sử dụng.", 404
    html = """<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>
    <title>Key nhiệm vụ</title><body style='font:16px Arial;max-width:600px;margin:50px auto;padding:24px;background:#101827;color:white'>
    <h2>🔑 Key của bạn</h2><p>Nhiệm vụ: {{title}}</p><h1 style='letter-spacing:4px'>{{key}}</h1>
    <p>Quay lại Telegram và gửi <b>/key {{key}}</b></p></body>"""
    return render_template_string(html, title=row["title"], key=key)

if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("Thieu BOT_TOKEN trong bien moi truong.")

    import threading

    threading.Thread(
        target=lambda: app_web.run(
            host="0.0.0.0",
            port=PORT,
            use_reloader=False
        ),
        daemon=True
    ).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    for cmd in ["links", "withdrawals", "stats"]:
        application.add_handler(CommandHandler(cmd, handle_command))

    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot started")
    application.run_polling()
