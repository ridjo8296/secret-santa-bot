import os
import logging
import sqlite3
import random
import uuid
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, ConversationHandler,
    filters
)

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8385598413:AAEaIzByLLFL4-Hp_BfbeUxux-v1cDiv4vY')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 6644276942))

# ========== БАЗА ДАННЫХ SQLite ==========
def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect('santa.db')
    c = conn.cursor()
    
    # Таблица групп
    c.execute('''CREATE TABLE IF NOT EXISTS groups
                 (id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  admin_id INTEGER,
                  organizer_name TEXT,
                  budget TEXT,
                  max_participants INTEGER DEFAULT 50,
                  reg_deadline TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Таблица участников
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
    
    # Таблица пар
    c.execute('''CREATE TABLE IF NOT EXISTS pairs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  group_id TEXT,
                  giver_id INTEGER,
                  receiver_id INTEGER,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    conn.commit()
    conn.close()

# Инициализируем БД при запуске
init_db()

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С БД ==========
def db_execute(query, params=()):
    """Выполнить SQL запрос"""
    conn = sqlite3.connect('santa.db')
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    conn.close()

def db_fetchone(query, params=()):
    """Получить одну запись"""
    conn = sqlite3.connect('santa.db')
    c = conn.cursor()
    c.execute(query, params)
    result = c.fetchone()
    conn.close()
    return result

def db_fetchall(query, params=()):
    """Получить все записи"""
    conn = sqlite3.connect('santa.db')
    c = conn.cursor()
    c.execute(query, params)
    result = c.fetchall()
    conn.close()
    return result

# ========== СОСТОЯНИЯ ==========
(
    START, CREATE_GROUP_NAME, CREATE_GROUP_ORGANIZER,
    CREATE_GROUP_BUDGET, CREATE_GROUP_DEADLINE, CREATE_GROUP_MAX,
    REG_NAME, REG_NICKNAME, REG_PVZ, REG_ADDRESS, REG_WISHLIST,
    GROUP_MANAGEMENT, VIEW_PARTICIPANTS, START_DRAW_CONFIRM
) = range(14)

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== ФУНКЦИИ АДМИНА ==========
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ У вас нет доступа к админ-панели.")
        return
    
    keyboard = [
        [InlineKeyboardButton("📋 Мои группы", callback_data="my_groups")],
        [InlineKeyboardButton("➕ Создать группу", callback_data="create_group")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👑 АДМИН-ПАНЕЛЬ\n\n"
        "Вы можете создавать и управлять группами Тайного Санты.",
        reply_markup=reply_markup
    )
    return START

async def show_admin_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    groups_data = db_fetchall("SELECT * FROM groups WHERE admin_id = ?", (ADMIN_ID,))
    
    if not groups_data:
        await query.edit_message_text("У вас пока нет групп.")
        return
    
    text = "📋 ВАШИ ГРУППЫ:\n\n"
    buttons = []
    
    for group in groups_data:
        participants_count = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ?", 
            (group[0],)
        )[0]
        
        text += f"🏢 {group[1]}\n"
        text += f"   👥 {participants_count}/{group[5]} участников\n"
        text += f"   📅 Рег. до: {group[6] or 'не указано'}\n"
        text += f"   🔗 Ссылка: t.me/{(await context.bot.get_me()).username}?start={group[0]}\n\n"
        
        buttons.append([InlineKeyboardButton(
            f"⚙️ {group[1]}", 
            callback_data=f"manage_{group[0]}"
        )])
    
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_admin")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def create_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        "СОЗДАНИЕ НОВОЙ ГРУППЫ\n\n"
        "Введите название группы:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Отмена", callback_data="back_to_admin")]
        ])
    )
    return CREATE_GROUP_NAME

async def create_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_name = update.message.text
    context.user_data['new_group'] = {'name': group_name}
    
    await update.message.reply_text(
        "Введите контакт организатора (имя и телеграм/телефон):\n"
        "Пример: 'Анна Петрова, @anna_hr'"
    )
    return CREATE_GROUP_ORGANIZER

async def create_group_organizer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    organizer = update.message.text
    context.user_data['new_group']['organizer'] = organizer
    
    await update.message.reply_text(
        "Введите бюджет подарков:\n"
        "Пример: '1000-1500 руб' или 'до 2000 руб'"
    )
    return CREATE_GROUP_BUDGET

async def create_group_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    budget = update.message.text
    context.user_data['new_group']['budget'] = budget
    
    await update.message.reply_text(
        "Введите дедлайн регистрации:\n"
        "Пример: '15 декабря' или '20.12.2024'"
    )
    return CREATE_GROUP_DEADLINE

async def create_group_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deadline = update.message.text
    context.user_data['new_group']['reg_deadline'] = deadline
    
    await update.message.reply_text(
        "Введите максимальное количество участников:\n"
        "Пример: '20' или '50'"
    )
    return CREATE_GROUP_MAX

async def create_group_max(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        max_participants = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Введите число!")
        return CREATE_GROUP_MAX
    
    group_data = context.user_data['new_group']
    group_id = str(uuid.uuid4())[:8].upper()
    
    # Сохраняем в БД
    db_execute(
        '''INSERT INTO groups (id, name, admin_id, organizer_name, budget, max_participants, reg_deadline)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (group_id, group_data['name'], ADMIN_ID, group_data['organizer'], 
         group_data['budget'], max_participants, group_data['reg_deadline'])
    )
    
    await update.message.reply_text(
        f"✅ ГРУППА СОЗДАНА!\n\n"
        f"🏢 Название: {group_data['name']}\n"
        f"🔑 Код группы: {group_id}\n"
        f"👤 Организатор: {group_data['organizer']}\n"
        f"💰 Бюджет: {group_data['budget']}\n"
        f"📅 Регистрация до: {group_data['reg_deadline']}\n"
        f"👥 Макс. участников: {max_participants}\n\n"
        f"🔗 ССЫЛКА ДЛЯ УЧАСТНИКОВ:\n"
        f"t.me/{(await context.bot.get_me()).username}?start={group_id}\n\n"
        f"Отправьте эту ссылку организатору компании."
    )
    
    context.user_data.pop('new_group', None)
    return ConversationHandler.END

# ========== РЕГИСТРАЦИЯ УЧАСТНИКОВ ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    if args and len(args) > 0:
        group_id = args[0]
        group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
        
        if not group:
            await update.message.reply_text("❌ Группа не найдена.")
            return
        
        # Проверяем, не зарегистрирован ли уже
        existing = db_fetchone(
            "SELECT * FROM participants WHERE user_id = ? AND group_id = ?",
            (user.id, group_id)
        )
        if existing:
            await update.message.reply_text(
                f"✅ Вы уже зарегистрированы в группе '{group[1]}'!\n"
                f"Ожидайте начала жеребьёвки."
            )
            return
        
        context.user_data['reg_group_id'] = group_id
        
        participants_count = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ?", 
            (group_id,)
        )[0]
        
        await update.message.reply_text(
            f"🎅 ДОБРО ПОЖАЛОВАТЬ В ГРУППУ!\n\n"
            f"🏢 {group[1]}\n"
            f"👤 Организатор: {group[3] or 'не указан'}\n"
            f"💰 Бюджет: {group[4] or 'не указан'}\n"
            f"👥 Участников: {participants_count}/{group[5]}\n"
            f"📅 Регистрация до: {group[6] or 'не указано'}\n\n"
            f"Для регистрации введите ваше Фамилию и Имя:\n"
            f"Пример: Иванов Иван"
        )
        return REG_NAME
    
    # Обычный старт
    if user.id == ADMIN_ID:
        return await admin_panel(update, context)
    else:
        await update.message.reply_text(
            "🎅 Привет! Я бот для организации Тайного Санты.\n\n"
            "Чтобы присоединиться к игре, вам нужна ссылка-приглашение от организатора."
        )
        return ConversationHandler.END

async def reg_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    full_name = update.message.text
    context.user_data['reg_full_name'] = full_name
    
    await update.message.reply_text(
        "Придумайте Никнейм для игры (так вас будет видеть ваш Тайный Санта):\n"
        "Пример: Снежный_Санта, Новогодний_Эльф"
    )
    return REG_NICKNAME

async def reg_nickname_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nickname = update.message.text
    context.user_data['reg_nickname'] = nickname
    
    await update.message.reply_text(
        "Введите адрес ПВЗ Wildberries, где вам удобно забирать заказы:\n"
        "Пример: 'Красноярск, улица Кутузова 77А' или 'Москва, ТЦ Авиапарк'"
    )
    return REG_PVZ

async def reg_pvz_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pvz_address = update.message.text
    context.user_data['reg_pvz_address'] = pvz_address
    
    await update.message.reply_text(
        "Введите почтовый адрес (на всякий случай, если Санта захочет отправить почтой):\n"
        "Пример: '123456, Москва, ул. Ленина, д. 10, кв. 15'\n"
        "Или напишите 'пропустить' чтобы не указывать"
    )
    return REG_ADDRESS

async def reg_address_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    if text == 'пропустить':
        postal_address = "Не указан"
    else:
        postal_address = update.message.text
    context.user_data['reg_postal_address'] = postal_address
    
    await update.message.reply_text(
        "Напишите пожелания к подарку:\n"
        "Что бы вы хотели получить? Укажите интересы, размер одежды, аллергии.\n"
        "Пример: 'Люблю книги, размер М, аллергия на шоколад'"
    )
    return REG_WISHLIST

async def reg_wishlist_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wishlist = update.message.text
    group_id = context.user_data['reg_group_id']
    
    # Сохраняем в БД
    db_execute(
        '''INSERT INTO participants 
           (user_id, username, group_id, full_name, nickname, pvz_address, postal_address, wishlist)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (update.effective_user.id, update.effective_user.username, group_id,
         context.user_data['reg_full_name'], context.user_data['reg_nickname'],
         context.user_data['reg_pvz_address'], context.user_data['reg_postal_address'],
         wishlist)
    )
    
    # Получаем информацию о группе
    group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
    participants_count = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE group_id = ?", 
        (group_id,)
    )[0]
    
    # Уведомляем админа
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"👤 НОВЫЙ УЧАСТНИК В ГРУППЕ '{group[1]}':\n"
                 f"Имя: {context.user_data['reg_full_name']}\n"
                 f"Ник: {context.user_data['reg_nickname']}\n"
                 f"Всего участников: {participants_count}/{group[5]}"
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить админа: {e}")
    
    await update.message.reply_text(
        f"✅ ВЫ УСПЕШНО ЗАРЕГИСТРИРОВАНЫ!\n\n"
        f"Группа: {group[1]}\n"
        f"Ваш никнейм: {context.user_data['reg_nickname']}\n"
        f"👥 Участников: {participants_count}/{group[5]}\n\n"
        f"Ожидайте начала жеребьёвки!"
    )
    
    # Очищаем временные данные
    for key in ['reg_group_id', 'reg_full_name', 'reg_nickname', 'reg_pvz_address', 'reg_postal_address']:
        context.user_data.pop(key, None)
    
    return ConversationHandler.END

# ========== ОСНОВНОЙ ЗАПУСК ==========
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # ConversationHandler для админа
    admin_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(create_group_start, pattern="^create_group$")
        ],
        states={
            CREATE_GROUP_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_group_name),
                CallbackQueryHandler(lambda u,c: admin_panel(u,c), pattern="^back_to_admin$")
            ],
            CREATE_GROUP_ORGANIZER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_group_organizer)
            ],
            CREATE_GROUP_BUDGET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_group_budget)
            ],
            CREATE_GROUP_DEADLINE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_group_deadline)
            ],
            CREATE_GROUP_MAX: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_group_max)
            ]
        },
        fallbacks=[
            CommandHandler("admin", admin_panel),
            CallbackQueryHandler(lambda u,c: admin_panel(u,c), pattern="^back_to_admin$")
        ]
    )
    
    # ConversationHandler для регистрации участников
    reg_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start_command)
        ],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name_handler)],
            REG_NICKNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_nickname_handler)],
            REG_PVZ: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_pvz_handler)],
            REG_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_address_handler)],
            REG_WISHLIST: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_wishlist_handler)]
        },
        fallbacks=[]
    )
    
    # Обработчики
    application.add_handler(CallbackQueryHandler(show_admin_groups, pattern="^my_groups$"))
    application.add_handler(CallbackQueryHandler(lambda u,c: admin_panel(u,c), pattern="^back_to_admin$"))
    application.add_handler(CallbackQueryHandler(lambda u,c: admin_panel(u,c), pattern="^stats$"))
    
    application.add_handler(admin_conv_handler)
    application.add_handler(reg_conv_handler)
    
    application.add_handler(CommandHandler("admin", admin_panel))
    
    print("✅ Бот запущен! База данных: SQLite")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
