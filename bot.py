import os
import logging
import sqlite3
import uuid
import threading
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, 
    CallbackQueryHandler, ContextTypes
)

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8385598413:AAEaIzByLLFL4-Hp_BfbeUxux-v1cDiv4vY')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 6644276942))

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect('santa.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS groups
                 (id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  admin_id INTEGER,
                  organizer TEXT,
                  budget TEXT,
                  max_participants INTEGER DEFAULT 50,
                  reg_deadline TEXT,
                  status TEXT DEFAULT 'active',
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS participants
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  username TEXT,
                  group_id TEXT,
                  full_name TEXT NOT NULL,
                  nickname TEXT NOT NULL,
                  pvz_address TEXT NOT NULL,
                  postal_address TEXT,
                  wishlist TEXT,
                  status TEXT DEFAULT 'registered',
                  registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    conn.commit()
    conn.close()

init_db()

# ========== ФУНКЦИИ БАЗЫ ДАННЫХ ==========
def db_execute(query, params=()):
    conn = sqlite3.connect('santa.db')
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    conn.close()

def db_fetchone(query, params=()):
    conn = sqlite3.connect('santa.db')
    c = conn.cursor()
    c.execute(query, params)
    result = c.fetchone()
    conn.close()
    return result

def db_fetchall(query, params=()):
    conn = sqlite3.connect('santa.db')
    c = conn.cursor()
    c.execute(query, params)
    result = c.fetchall()
    conn.close()
    return result

# ========== FLASK ДЛЯ RENDER ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "🎅 Secret Santa Bot is running on Render"

@flask_app.route('/health')
def health():
    return "OK", 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

# ========== TELEGRAM ФУНКЦИИ ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    if args and len(args) > 0:
        # Участник пришёл по ссылке
        group_id = args[0]
        await show_group_info(update, context, group_id)
        return
    
    if user.id == ADMIN_ID:
        await show_admin_panel(update, context)
    else:
        await update.message.reply_text(
            "🎅 Привет! Я бот для организации Тайного Санты.\n\n"
            "Для участия нужна ссылка-приглашение от организатора."
        )

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать админ-панель с ВСЕМИ кнопками"""
    keyboard = [
        [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("➕ СОЗДАТЬ ГРУППУ", callback_data="create_group")],
        [InlineKeyboardButton("👥 УЧАСТНИКИ", callback_data="all_participants")],
        [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="stats")],
        [InlineKeyboardButton("⚙️ УПРАВЛЕНИЕ", callback_data="manage_groups")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Получаем статистику
    groups_count = db_fetchone("SELECT COUNT(*) FROM groups WHERE admin_id = ?", (ADMIN_ID,))[0]
    participants_count = db_fetchone("SELECT COUNT(*) FROM participants")[0]
    
    await update.message.reply_text(
        f"👑 АДМИН-ПАНЕЛЬ\n\n"
        f"📊 Статистика:\n"
        f"• Групп: {groups_count}\n"
        f"• Участников: {participants_count}\n\n"
        f"Выберите действие:",
        reply_markup=reply_markup
    )

async def show_group_info(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id):
    """Показать информацию о группе для участника"""
    group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
    
    if not group:
        await update.message.reply_text("❌ Группа не найдена.")
        return
    
    participants_count = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE group_id = ?", 
        (group_id,)
    )[0]
    
    # Проверяем, зарегистрирован ли уже
    existing = db_fetchone(
        "SELECT * FROM participants WHERE user_id = ? AND group_id = ?",
        (update.effective_user.id, group_id)
    )
    
    if existing:
        await update.message.reply_text(
            f"✅ Вы уже зарегистрированы в группе:\n"
            f"🏢 {group[1]}\n\n"
            f"👥 Участников: {participants_count}/{group[5]}\n"
            f"📅 Регистрация до: {group[6]}\n\n"
            f"Ожидайте начала жеребьёвки!"
        )
        return
    
    # Кнопки для регистрации
    keyboard = [
        [InlineKeyboardButton("✅ ЗАРЕГИСТРИРОВАТЬСЯ", callback_data=f"register_{group_id}")],
        [InlineKeyboardButton("❌ ОТМЕНА", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🎅 ПРИГЛАШЕНИЕ В ГРУППУ\n\n"
        f"🏢 {group[1]}\n"
        f"👤 Организатор: {group[3] or 'не указан'}\n"
        f"💰 Бюджет: {group[4] or 'не указан'}\n"
        f"👥 Участников: {participants_count}/{group[5]}\n"
        f"📅 Регистрация до: {group[6] or 'не указано'}\n\n"
        f"Хотите присоединиться?",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех кнопок"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "my_groups":
        await show_my_groups(query, context)
    elif data == "create_group":
        await create_new_group(query, context)
    elif data == "all_participants":
        await show_all_participants(query, context)
    elif data == "stats":
        await show_stats(query, context)
    elif data == "manage_groups":
        await show_manage_groups(query, context)
    elif data.startswith("register_"):
        group_id = data.split("_")[1]
        await start_registration(query, context, group_id)
    elif data.startswith("delete_group_"):
        group_id = data.split("_")[2]
        await delete_group_confirmation(query, context, group_id)
    elif data.startswith("confirm_delete_"):
        group_id = data.split("_")[2]
        await delete_group(query, context, group_id)
    elif data == "cancel":
        await query.edit_message_text("Действие отменено.")
    else:
        await query.edit_message_text(f"Неизвестная команда: {data}")

async def show_my_groups(query, context):
    """Показать список групп админа"""
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = ? ORDER BY created_at DESC",
        (ADMIN_ID,)
    )
    
    if not groups:
        await query.edit_message_text("У вас пока нет групп.")
        return
    
    text = "📋 ВАШИ ГРУППЫ:\n\n"
    buttons = []
    
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ?",
            (group[0],)
        )[0]
        
        text += f"🏢 {group[1]}\n"
        text += f"   👥 {participants}/{group[5]} участников\n"
        text += f"   📅 Рег. до: {group[6] or 'нет'}\n"
        text += f"   🔗 ID: {group[0]}\n\n"
        
        # Кнопки управления для каждой группы
        buttons.append([
            InlineKeyboardButton(f"⚙️ {group[1][:15]}...", callback_data=f"manage_group_{group[0]}")
        ])
    
    buttons.append([InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_admin")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def create_new_group(query, context):
    """Создание новой группы"""
    group_id = str(uuid.uuid4())[:8].upper()
    group_name = f"Группа {group_id}"
    
    db_execute(
        '''INSERT INTO groups (id, name, admin_id, organizer, budget, max_participants, reg_deadline)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (group_id, group_name, ADMIN_ID, "Организатор", "1000-1500 руб", 50, "25 декабря 2024")
    )
    
    bot = await context.bot.get_me()
    
    # Кнопки после создания
    keyboard = [
        [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("👥 УПРАВЛЕНИЕ", callback_data="manage_groups")],
        [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_admin")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"✅ ГРУППА СОЗДАНА!\n\n"
        f"🏢 Название: {group_name}\n"
        f"🔑 ID: {group_id}\n"
        f"👤 Организатор: Организатор\n"
        f"💰 Бюджет: 1000-1500 руб\n"
        f"👥 Макс. участников: 50\n"
        f"📅 Регистрация до: 25 декабря 2024\n\n"
        f"🔗 ССЫЛКА ДЛЯ УЧАСТНИКОВ:\n"
        f"t.me/{bot.username}?start={group_id}\n\n"
        f"Отправьте эту ссылку участникам.",
        reply_markup=reply_markup
    )

async def show_all_participants(query, context):
    """Показать всех участников"""
    participants = db_fetchall(
        "SELECT p.*, g.name FROM participants p LEFT JOIN groups g ON p.group_id = g.id ORDER BY p.registered_at DESC LIMIT 20"
    )
    
    if not participants:
        await query.edit_message_text("Пока нет зарегистрированных участников.")
        return
    
    text = "👥 ПОСЛЕДНИЕ УЧАСТНИКИ:\n\n"
    
    for p in participants:
        text += f"👤 {p[4]} (@{p[2] or 'нет'})\n"
        text += f"   🎭 Ник: {p[5]}\n"
        text += f"   🏢 Группа: {p[11] or 'нет'}\n"
        text += f"   📅 Зарегистрирован: {p[10][:10]}\n\n"
    
    keyboard = [[InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_admin")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_stats(query, context):
    """Показать статистику"""
    groups_count = db_fetchone("SELECT COUNT(*) FROM groups WHERE admin_id = ?", (ADMIN_ID,))[0]
    participants_count = db_fetchone("SELECT COUNT(*) FROM participants")[0]
    
    # Участники по группам
    group_stats = db_fetchall(
        "SELECT g.name, COUNT(p.id) FROM groups g LEFT JOIN participants p ON g.id = p.group_id WHERE g.admin_id = ? GROUP BY g.id",
        (ADMIN_ID,)
    )
    
    text = f"📊 СТАТИСТИКА\n\n"
    text += f"• Всего групп: {groups_count}\n"
    text += f"• Всего участников: {participants_count}\n\n"
    
    if group_stats:
        text += "👥 Участников по группам:\n"
        for group_name, count in group_stats:
            text += f"  {group_name}: {count}\n"
    
    keyboard = [[InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_admin")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_manage_groups(query, context):
    """Показать управление группами"""
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = ? ORDER BY created_at DESC",
        (ADMIN_ID,)
    )
    
    if not groups:
        keyboard = [
            [InlineKeyboardButton("➕ СОЗДАТЬ ГРУППУ", callback_data="create_group")],
            [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_admin")]
        ]
        await query.edit_message_text(
            "У вас пока нет групп для управления.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    text = "⚙️ УПРАВЛЕНИЕ ГРУППАМИ\n\n"
    buttons = []
    
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ?",
            (group[0],)
        )[0]
        
        # Кнопки для каждой группы
        group_buttons = []
        group_buttons.append(InlineKeyboardButton(
            f"🗑 {group[1][:10]}...", 
            callback_data=f"delete_group_{group[0]}"
        ))
        group_buttons.append(InlineKeyboardButton(
            f"👥 ({participants})", 
            callback_data=f"view_participants_{group[0]}"
        ))
        buttons.append(group_buttons)
    
    buttons.append([InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_admin")])
    
    await query.edit_message_text(
        text + "Выберите группу для управления (🗑 - удалить, 👥 - участники):",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def start_registration(query, context, group_id):
    """Начать регистрацию участника"""
    # Здесь можно добавить процесс регистрации через вопросы
    # Для простоты сразу регистрируем
    
    user = query.from_user
    group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
    
    # Проверяем, не зарегистрирован ли уже
    existing = db_fetchone(
        "SELECT * FROM participants WHERE user_id = ? AND group_id = ?",
        (user.id, group_id)
    )
    
    if existing:
        await query.edit_message_text("✅ Вы уже зарегистрированы в этой группе!")
        return
    
    # Регистрируем с тестовыми данными
    db_execute(
        '''INSERT INTO participants 
           (user_id, username, group_id, full_name, nickname, pvz_address, postal_address, wishlist)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (user.id, user.username, group_id, 
         f"{user.first_name} {user.last_name or ''}".strip(),
         f"Участник_{user.id}",
         "Адрес ПВЗ",
         "Почтовый адрес",
         "Пожелания")
    )
    
    participants_count = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE group_id = ?", 
        (group_id,)
    )[0]
    
    await query.edit_message_text(
        f"✅ ВЫ ЗАРЕГИСТРИРОВАНЫ!\n\n"
        f"Группа: {group[1]}\n"
        f"Ваш ник: Участник_{user.id}\n"
        f"👥 Участников: {participants_count}/{group[5]}\n\n"
        f"Ожидайте начала жеребьёвки!"
    )

async def delete_group_confirmation(query, context, group_id):
    """Подтверждение удаления группы"""
    group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
    
    if not group:
        await query.edit_message_text("❌ Группа не найдена.")
        return
    
    participants = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE group_id = ?",
        (group_id,)
    )[0]
    
    keyboard = [
        [InlineKeyboardButton("✅ ДА, УДАЛИТЬ", callback_data=f"confirm_delete_{group_id}")],
        [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data="manage_groups")]
    ]
    
    await query.edit_message_text(
        f"⚠️ ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ\n\n"
        f"Группа: {group[1]}\n"
        f"Участников: {participants}\n\n"
        f"УДАЛИТЬ ГРУППУ И ВСЕХ УЧАСТНИКОВ?\n"
        f"Это действие необратимо!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def delete_group(query, context, group_id):
    """Удалить группу"""
    db_execute("DELETE FROM participants WHERE group_id = ?", (group_id,))
    db_execute("DELETE FROM groups WHERE id = ?", (group_id,))
    
    keyboard = [[InlineKeyboardButton("⬅️ НАЗАД К УПРАВЛЕНИЮ", callback_data="manage_groups")]]
    await query.edit_message_text(
        "✅ Группа и все участники удалены!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== ЗАПУСК БОТА ==========
def run_telegram_bot():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", show_admin_panel))
    
    # Обработчики кнопок
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("✅ Telegram бот запущен с полной админ-панелью!")
    application.run_polling()

# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
def main():
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask сервер запущен на порту 8080")
    
    # Запускаем Telegram бота
    run_telegram_bot()

if __name__ == '__main__':
    main()
