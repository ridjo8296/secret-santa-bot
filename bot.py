import os
import logging
import sqlite3
import uuid
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

BOT_TOKEN = os.environ.get('BOT_TOKEN', '8385598413:AAEaIzByLLFL4-Hp_BfbeUxux-v1cDiv4vY')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 6644276942))

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect('santa.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS groups
                 (id TEXT PRIMARY KEY,
                  name TEXT,
                  admin_id INTEGER,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS participants
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  username TEXT,
                  group_id TEXT,
                  full_name TEXT,
                  nickname TEXT,
                  pvz_address TEXT,
                  wishlist TEXT,
                  registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    conn.commit()
    conn.close()

init_db()

# ========== КОМАНДЫ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    
    if args and len(args) > 0:
        # Приглашение в группу
        group_id = args[0]
        conn = sqlite3.connect('santa.db')
        c = conn.cursor()
        c.execute("SELECT name FROM groups WHERE id = ?", (group_id,))
        group = c.fetchone()
        conn.close()
        
        if group:
            await update.message.reply_text(
                f'🎅 Приглашение в группу: {group[0]}\n'
                f'Для регистрации используйте /register'
            )
        else:
            await update.message.reply_text('❌ Группа не найдена')
    else:
        await update.message.reply_text(
            '🎅 Бот Тайного Санты\n\n'
            'Используйте /admin для управления'
        )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text('⛔ Нет доступа')
        return
    
    conn = sqlite3.connect('santa.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM groups WHERE admin_id = ?", (ADMIN_ID,))
    groups_count = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM participants")
    participants_count = c.fetchone()[0]
    conn.close()
    
    await update.message.reply_text(
        f'👑 Админ-панель\n\n'
        f'📊 Статистика:\n'
        f'• Групп: {groups_count}\n'
        f'• Участников: {participants_count}\n\n'
        f'Команды:\n'
        f'/creategroup - создать группу'
    )

async def create_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text('⛔ Нет доступа')
        return
    
    group_id = str(uuid.uuid4())[:8].upper()
    group_name = f"Группа {group_id}"
    
    conn = sqlite3.connect('santa.db')
    c = conn.cursor()
    c.execute("INSERT INTO groups (id, name, admin_id) VALUES (?, ?, ?)",
              (group_id, group_name, ADMIN_ID))
    conn.commit()
    conn.close()
    
    bot = await context.bot.get_me()
    await update.message.reply_text(
        f'✅ Группа создана!\n\n'
        f'Название: {group_name}\n'
        f'ID: {group_id}\n\n'
        f'🔗 Ссылка для участников:\n'
        f't.me/{bot.username}?start={group_id}'
    )

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CommandHandler("creategroup", create_group))
    
    print("✅ Бот запущен с SQLite базой данных!")
    application.run_polling()

if __name__ == '__main__':
    main()
