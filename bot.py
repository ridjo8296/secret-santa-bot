import os
import logging
import sqlite3
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext, ConversationHandler, MessageHandler, Filters

BOT_TOKEN = os.environ.get('BOT_TOKEN', '8385598413:AAEaIzByLLFL4-Hp_BfbeUxux-v1cDiv4vY')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 6644276942))

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация БД
def init_db():
    conn = sqlite3.connect('santa.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS groups
                 (id TEXT PRIMARY KEY, name TEXT, admin_id INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS participants
                 (user_id INTEGER, group_id TEXT, full_name TEXT)''')
    conn.commit()
    conn.close()

init_db()

# Команды
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        '🎅 Привет! Я бот Тайного Санты.\n'
        'Используйте /admin для управления группами.'
    )

def admin(update: Update, context: CallbackContext):
    if update.effective_user.id == ADMIN_ID:
        update.message.reply_text(
            '👑 Админ-панель\n\n'
            'Команды:\n'
            '/creategroup - создать группу\n'
            '/listgroups - список групп'
        )
    else:
        update.message.reply_text('⛔ Нет доступа')

def create_group(update: Update, context: CallbackContext):
    if update.effective_user.id != ADMIN_ID:
        update.message.reply_text('⛔ Нет доступа')
        return
    
    # Простая логика создания группы
    import uuid
    group_id = str(uuid.uuid4())[:8]
    
    conn = sqlite3.connect('santa.db')
    c = conn.cursor()
    c.execute("INSERT INTO groups (id, name, admin_id) VALUES (?, ?, ?)",
              (group_id, f"Группа {group_id}", ADMIN_ID))
    conn.commit()
    conn.close()
    
    bot_username = context.bot.username
    update.message.reply_text(
        f'✅ Группа создана!\n'
        f'ID: {group_id}\n'
        f'Ссылка: t.me/{bot_username}?start={group_id}'
    )

def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("admin", admin))
    dp.add_handler(CommandHandler("creategroup", create_group))
    
    updater.start_polling()
    logger.info("✅ Бот запущен!")
    updater.idle()

if __name__ == '__main__':
    main()
