import os
import logging
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8385598413:AAEaIzByLLFL4-Hp_BfbeUxux-v1cDiv4vY')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 6644276942))

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== FLASK ДЛЯ RENDER ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "🎅 Secret Santa Bot is running on Render"

@flask_app.route('/health')
def health():
    return "OK", 200

def run_flask():
    """Запуск Flask сервера на порту 8080"""
    flask_app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

# ========== TELEGRAM BOT ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if user.id == ADMIN_ID:
        await update.message.reply_text(
            '👑 АДМИН-ПАНЕЛЬ\n\n'
            'Доступные команды:\n'
            '/creategroup - создать новую группу\n'
            '/listgroups - список ваших групп'
        )
    else:
        await update.message.reply_text(
            '🎅 Привет! Я бот для организации Тайного Санты.\n\n'
            'Чтобы присоединиться, нужна ссылка от организатора.'
        )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text(
            '👑 АДМИН\n\n'
            'Бот работает 24/7 на Render!\n'
            'Используйте /creategroup для создания группы.'
        )
    else:
        await update.message.reply_text('⛔ Нет доступа')

async def create_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text('⛔ Нет доступа')
        return
    
    import uuid
    group_id = str(uuid.uuid4())[:8].upper()
    
    # Получаем username бота
    bot = await context.bot.get_me()
    
    await update.message.reply_text(
        f'✅ ГРУППА СОЗДАНА!\n\n'
        f'🔑 ID группы: {group_id}\n'
        f'🏢 Название: Группа {group_id}\n\n'
        f'🔗 ССЫЛКА ДЛЯ УЧАСТНИКОВ:\n'
        f't.me/{bot.username}?start={group_id}\n\n'
        f'Отправьте эту ссылку участникам.'
    )

def run_telegram_bot():
    """Запуск Telegram бота"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем команды
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("creategroup", create_group_command))
    
    logger.info("✅ Telegram бот запущен!")
    application.run_polling()

# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
def main():
    """Запуск и Flask, и Telegram бота"""
    
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask сервер запущен на порту 8080")
    
    # Запускаем Telegram бота
    run_telegram_bot()

if __name__ == '__main__':
    main()
