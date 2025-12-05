import os
import logging
import uuid
import threading
import random
import asyncio
import aiohttp
from datetime import datetime
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, 
    CallbackQueryHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)
import psycopg2
from psycopg2.extras import RealDictCursor

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8385598413:AAEaIzByLLFL4-Hp_BfbeUxux-v1cDiv4vY')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 6644276942))
DATABASE_URL = os.environ.get('DATABASE_URL')
RENDER = os.environ.get('RENDER', False)

# ========== СОСТОЯНИЯ ДЛЯ СОЗДАНИЯ ГРУППЫ ==========
(
    WAITING_NAME, WAITING_ORGANIZER, WAITING_BUDGET,
    WAITING_MAX_PARTICIPANTS, WAITING_DEADLINE, CONFIRM_CREATION
) = range(6)

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== БАЗА ДАННЫХ POSTGRESQL ==========
def get_db_connection():
    """Создать соединение с PostgreSQL"""
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    return conn

def init_db():
    """Инициализация базы данных"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # Таблица групп
    c.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            admin_id INTEGER NOT NULL,
            organizer TEXT NOT NULL,
            budget TEXT NOT NULL,
            max_participants INTEGER NOT NULL,
            reg_deadline TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            draw_status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица участников
    c.execute('''
        CREATE TABLE IF NOT EXISTS participants (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            username TEXT,
            group_id TEXT NOT NULL,
            full_name TEXT NOT NULL,
            nickname TEXT NOT NULL,
            pvz_address TEXT NOT NULL,
            postal_address TEXT,
            wishlist TEXT,
            giver_to INTEGER,
            receiver_from INTEGER,
            gift_sent BOOLEAN DEFAULT FALSE,
            sent_date TEXT,
            tracking_number TEXT,
            gift_status TEXT DEFAULT 'not_sent',
            confirmed BOOLEAN DEFAULT TRUE,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных PostgreSQL инициализирована")

def db_execute(query, params=()):
    """Выполнить SQL запрос"""
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute(query, params)
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка SQL: {e}, запрос: {query}, params: {params}")
        raise
    finally:
        conn.close()

def db_fetchone(query, params=()):
    """Получить одну запись"""
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute(query, params)
        result = c.fetchone()
    finally:
        conn.close()
    return result

def db_fetchall(query, params=()):
    """Получить все записи"""
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute(query, params)
        result = c.fetchall()
    finally:
        conn.close()
    return result

# Инициализируем базу при старте
init_db()

# ========== FLASK ДЛЯ RENDER И АВТОПИНГ ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "🎅 Secret Santa Bot is running 24/7"

@flask_app.route('/health')
def health():
    return "OK", 200

@flask_app.route('/ping')
def ping():
    return "PONG", 200

async def keep_alive():
    """Функция для поддержания активности бота и сервера"""
    ping_urls = []
    
    if RENDER:
        # Получаем URL нашего сервиса из переменных окружения
        service_url = os.environ.get('RENDER_SERVICE_URL')
        if service_url:
            ping_urls.append(service_url)
    
    # Добавляем стандартные эндпоинты
    ping_urls.append('https://api.telegram.org')
    
    while True:
        try:
            for url in ping_urls:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(f'{url}/ping' if '/ping' not in url else url, timeout=10) as response:
                            logger.debug(f"Пинг успешен: {url}, статус: {response.status}")
                except Exception as e:
                    logger.debug(f"Пинг не удался для {url}: {e}")
            
            # Также проверяем базу данных
            try:
                test = db_fetchone("SELECT 1")
                logger.debug("База данных доступна")
            except Exception as e:
                logger.error(f"Ошибка подключения к БД: {e}")
            
            await asyncio.sleep(300)  # Пинг каждые 5 минут
            
        except Exception as e:
            logger.error(f"Ошибка в keep_alive: {e}")
            await asyncio.sleep(60)

def run_flask():
    """Запуск Flask сервера"""
    port = int(os.environ.get('PORT', 8080))
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ========== TELEGRAM ФУНКЦИИ ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user
    
    if context.args:
        group_id = context.args[0]
        group = db_fetchone("SELECT * FROM groups WHERE id = %s", (group_id,))
        
        if group:
            if group[8] == 'completed':
                await update.message.reply_text(
                    f"❌ Регистрация в группе '{group[1]}' завершена.\nЖеребьевка уже проведена.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
                
            existing = db_fetchone(
                "SELECT * FROM participants WHERE user_id = %s AND group_id = %s",
                (user.id, group_id)
            )
            
            if existing:
                await update.message.reply_text(
                    f"✅ Вы уже зарегистрированы в группе '{group[1]}'!\nОжидайте жеребьевки.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            
            context.user_data['registration'] = {
                'group_id': group_id,
                'step': 1,
                'user_id': user.id,
                'username': user.username
            }
            
            await update.message.reply_text(
                f"🎅 РЕГИСТРАЦИЯ В ГРУППЕ: {group[1]}\n\n"
                f"💰 Бюджет: {group[4]}\n"
                f"📅 Регистрация до: {group[6]}\n\n"
                "Шаг 1 из 5\n"
                "📝 Введите ваше полное ФИО:\nПример: 'Иванов Иван Иванович'",
                reply_markup=ReplyKeyboardRemove()
            )
        else:
            await update.message.reply_text(
                "❌ Группа не найдена.",
                reply_markup=ReplyKeyboardRemove()
            )
        return
    
    if user.id == ADMIN_ID:
        await show_main_menu(update, context)
    else:
        await update.message.reply_text(
            "🎅 Привет! Я бот для организации Тайного Санты.\n\n"
            "Для участия нужна ссылка-приглашение от организатора.",
            reply_markup=ReplyKeyboardRemove()
        )

# ========== ГЛАВНОЕ МЕНЮ ==========
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню"""
    keyboard = [
        ["📋 МОИ ГРУППЫ"],
        ["➕ СОЗДАТЬ ГРУППУ"],
        ["👥 УЧАСТНИКИ"],
        ["🎁 КТО КОМУ ДАРИТ"],
        ["📦 СТАТУС ОТПРАВКИ"],
        ["🎲 ЗАПУСТИТЬ ЖЕРЕБЬЁВКУ"],
        ["📊 СТАТИСТИКА"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    if update.message:
        await update.message.reply_text(
            "👑 АДМИН-ПАНЕЛЬ\n\nВыберите действие:",
            reply_markup=reply_markup
        )
    else:
        await update.callback_query.edit_message_text(
            "👑 АДМИН-ПАНЕЛЬ\n\nВыберите действие:",
            reply_markup=reply_markup
        )

# ========== МОИ ГРУППЫ ==========
async def show_my_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать мои группы"""
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = %s ORDER BY created_at DESC",
        (ADMIN_ID,)
    )
    
    if not groups:
        keyboard = [["➕ СОЗДАТЬ ГРУППУ"], ["⬅️ НАЗАД"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "📭 У вас пока нет созданных групп.",
            reply_markup=reply_markup
        )
        return
    
    text = "📋 ВАШИ ГРУППЫ:\n\n"
    
    keyboard = []
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = %s AND confirmed = TRUE",
            (group[0],)
        )[0] or 0
        
        sent_gifts = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = %s AND gift_sent = TRUE",
            (group[0],)
        )[0] or 0
        
        # Получаем ссылку
        bot = await context.bot.get_me()
        invite_link = f"t.me/{bot.username}?start={group[0]}"
        
        draw_icon = "🎲" if group[8] == 'completed' else "⏳"
        text += f"🏢 <b>{group[1]}</b>\n"
        text += f"   🔗 <code>{invite_link}</code>\n"
        text += f"   🔑 ID: <code>{group[0]}</code>\n"
        text += f"   👤 Организатор: {group[3]}\n"
        text += f"   💰 Бюджет: {group[4]}\n"
        text += f"   👥 Участников: {participants}/{group[5]}\n"
        text += f"   📦 Отправлено: {sent_gifts}/{participants}\n"
        text += f"   📅 Рег. до: {group[6]}\n"
        text += f"   {draw_icon} Жеребьевка: {'ПРОВЕДЕНА' if group[8] == 'completed' else 'ОЖИДАЕТ'}\n\n"
        
        # Создаем кнопки для каждой группы
        keyboard.append([f"⚙️ {group[1][:20]}{'...' if len(group[1]) > 20 else ''}"])
    
    keyboard.append(["➕ СОЗДАТЬ ГРУППУ"])
    keyboard.append(["⬅️ НАЗАД"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )

async def manage_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Управление конкретной группой"""
    text = update.message.text
    
    if not text.startswith("⚙️ "):
        return
    
    group_name_part = text[3:].strip()
    
    # Ищем группу
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = %s",
        (ADMIN_ID,)
    )
    
    matching_groups = []
    for group in groups:
        if group_name_part.replace("...", "") in group[1]:
            matching_groups.append(group)
    
    if not matching_groups:
        await update.message.reply_text("❌ Группа не найдена.")
        return
    
    group = matching_groups[0]
    group_id = group[0]
    
    participants = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE group_id = %s AND confirmed = TRUE",
        (group_id,)
    )[0] or 0
    
    bot = await context.bot.get_me()
    invite_link = f"t.me/{bot.username}?start={group_id}"
    
    text = f"⚙️ <b>УПРАВЛЕНИЕ ГРУППОЙ</b>\n\n"
    text += f"🏢 Группа: {group[1]}\n"
    text += f"🔗 Ссылка: <code>{invite_link}</code>\n"
    text += f"👥 Участников: {participants}/{group[5]}\n"
    text += f"💰 Бюджет: {group[4]}\n"
    text += f"🎲 Жеребьевка: {'✅ ПРОВЕДЕНА' if group[8] == 'completed' else '⏳ ОЖИДАЕТ'}\n\n"
    
    keyboard = [
        ["🔗 СКОПИРОВАТЬ ССЫЛКУ"],
        ["🗑 УДАЛИТЬ ГРУППУ"],
        ["📋 МОИ ГРУППЫ"],
        ["⬅️ НАЗАД"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    context.user_data['selected_group'] = group_id
    
    await update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )

async def copy_group_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Копировать ссылку группы"""
    if 'selected_group' not in context.user_data:
        await update.message.reply_text("❌ Группа не выбрана.")
        return
    
    group_id = context.user_data['selected_group']
    group = db_fetchone("SELECT name FROM groups WHERE id = %s", (group_id,))
    
    if not group:
        await update.message.reply_text("❌ Группа не найдена.")
        return
    
    bot = await context.bot.get_me()
    invite_link = f"t.me/{bot.username}?start={group_id}"
    
    await update.message.reply_text(
        f"🔗 <b>ССЫЛКА ДЛЯ ПРИГЛАШЕНИЯ</b>\n\n"
        f"🏢 Группа: {group[0]}\n\n"
        f"<code>{invite_link}</code>\n\n"
        f"✅ Ссылка скопирована! Отправьте её участникам.",
        parse_mode='HTML'
    )

async def delete_group_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение удаления группы"""
    if 'selected_group' not in context.user_data:
        await update.message.reply_text("❌ Группа не выбрана.")
        return
    
    group_id = context.user_data['selected_group']
    group = db_fetchone("SELECT name FROM groups WHERE id = %s", (group_id,))
    
    if not group:
        await update.message.reply_text("❌ Группа не найдена.")
        return
    
    participants = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE group_id = %s",
        (group_id,)
    )[0] or 0
    
    keyboard = [["✅ ДА, УДАЛИТЬ"], ["❌ НЕТ, ОТМЕНА"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"⚠️ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b>\n\n"
        f"🏢 Группа: {group[0]}\n"
        f"👥 Участников: {participants}\n"
        f"💰 Бюджет: {group[1] if len(group) > 1 else 'не указан'}\n\n"
        f"<b>УДАЛИТЬ ГРУППУ И ВСЕХ УЧАСТНИКОВ?</b>\n"
        f"Это действие необратимо!",
        parse_mode='HTML',
        reply_markup=reply_markup
    )

async def delete_group_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удалить группу"""
    if 'selected_group' not in context.user_data:
        await update.message.reply_text("❌ Группа не выбрана.")
        return
    
    group_id = context.user_data['selected_group']
    
    # Удаляем участников и группу
    db_execute("DELETE FROM participants WHERE group_id = %s", (group_id,))
    db_execute("DELETE FROM groups WHERE id = %s", (group_id,))
    
    # Очищаем временные данные
    context.user_data.pop('selected_group', None)
    
    keyboard = [["📋 МОИ ГРУППЫ"], ["⬅️ НАЗАД"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "✅ Группа и все участники удалены!",
        reply_markup=reply_markup
    )

# ========== СПИСОК УЧАСТНИКОВ ==========
async def show_participants_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню участников"""
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = %s ORDER BY created_at DESC",
        (ADMIN_ID,)
    )
    
    if not groups:
        keyboard = [["➕ СОЗДАТЬ ГРУППУ"], ["⬅️ НАЗАД"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "📭 У вас пока нет групп с участниками.",
            reply_markup=reply_markup
        )
        return
    
    text = "👥 ВЫБЕРИТЕ ГРУППУ ДЛЯ ПРОСМОТРА УЧАСТНИКОВ:\n\n"
    
    keyboard = []
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = %s AND confirmed = TRUE",
            (group[0],)
        )[0] or 0
        
        if participants > 0:
            button_text = f"👥 {group[1][:15]}{'...' if len(group[1]) > 15 else ''} ({participants})"
            keyboard.append([button_text])
    
    if not keyboard:
        keyboard.append(["📭 НЕТ УЧАСТНИКОВ"])
    
    keyboard.append(["📋 МОИ ГРУППЫ"])
    keyboard.append(["⬅️ НАЗАД"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        text,
        reply_markup=reply_markup
    )

async def show_group_participants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать участников группы"""
    text = update.message.text
    
    if text.startswith("👥 "):
        group_name_part = text[2:].split(" (")[0].strip().replace("...", "")
    else:
        group_name_part = text
    
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = %s",
        (ADMIN_ID,)
    )
    
    matching_groups = []
    for group in groups:
        if group_name_part in group[1]:
            matching_groups.append(group)
    
    if not matching_groups:
        await update.message.reply_text("❌ Группа не найдена.")
        return
    
    group = matching_groups[0]
    group_id = group[0]
    
    participants = db_fetchall(
        "SELECT * FROM participants WHERE group_id = %s AND confirmed = TRUE ORDER BY registered_at DESC",
        (group_id,)
    )
    
    if not participants:
        keyboard = [["👥 УЧАСТНИКИ"], ["⬅️ НАЗАД"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"👥 В группе '{group[1]}' пока нет участников.",
            reply_markup=reply_markup
        )
        return
    
    text = f"👥 <b>УЧАСТНИКИ ГРУППЫ: {group[1]}</b>\n\n"
    text += f"📊 Всего участников: {len(participants)}\n\n"
    
    keyboard = []
    for idx, participant in enumerate(participants, 1):
        gift_status = "✅" if participant[12] else "❌"
        username = f"@{participant[2]}" if participant[2] else "нет username"
        
        text += f"<b>{idx}. {participant[4]}</b> {gift_status}\n"
        text += f"   🎭 Никнейм: {participant[5]}\n"
        text += f"   📱 {username}\n"
        
        if participant[9]:  # giver_to
            receiver = db_fetchone(
                "SELECT full_name FROM participants WHERE id = %s",
                (participant[9],)
            )
            if receiver:
                text += f"   🎅 Дарит: {receiver[0]}\n"
        
        text += "\n"
        
        # Кнопка для деталей
        button_text = f"ℹ️ {participant[4][:15]}{'...' if len(participant[4]) > 15 else ''}"
        keyboard.append([button_text])
    
    keyboard.append(["👥 УЧАСТНИКИ"])
    keyboard.append(["⬅️ НАЗАД"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    context.user_data['participants_group'] = group_id
    
    await update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )

async def show_participant_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Детали участника"""
    text = update.message.text
    
    if not text.startswith("ℹ️ "):
        return
    
    participant_name_part = text[2:].strip().replace("...", "")
    
    if 'participants_group' not in context.user_data:
        await update.message.reply_text("❌ Группа не выбрана.")
        return
    
    group_id = context.user_data['participants_group']
    
    participants = db_fetchall(
        "SELECT * FROM participants WHERE group_id = %s AND confirmed = TRUE",
        (group_id,)
    )
    
    if not participants:
        await update.message.reply_text("❌ Участники не найдены.")
        return
    
    matching_participants = []
    for participant in participants:
        if participant_name_part.lower() in participant[4].lower():
            matching_participants.append(participant)
    
    if not matching_participants:
        await update.message.reply_text("❌ Участник не найден.")
        return
    
    participant = matching_participants[0]
    group = db_fetchone("SELECT name, budget FROM groups WHERE id = %s", (group_id,))
    
    text = f"<b>👤 ПОДРОБНАЯ ИНФОРМАЦИЯ</b>\n\n"
    text += f"🏢 Группа: {group[0]}\n"
    text += f"💰 Бюджет: {group[1]}\n\n"
    
    text += f"📝 ФИО: {participant[4]}\n"
    text += f"🎭 Никнейм: {participant[5]}\n"
    text += f"📱 Username: @{participant[2] if participant[2] else 'нет'}\n"
    text += f"🆔 User ID: {participant[1]}\n"
    text += f"📦 Адрес ПВЗ: {participant[6]}\n"
    text += f"📮 Почтовый адрес: {participant[7] or 'не указан'}\n"
    text += f"🎁 Вишлист: {participant[8] or 'не указан'}\n"
    text += f"📅 Дата регистрации: {participant[17]}\n\n"
    
    gift_status = "✅ ОТПРАВЛЕН" if participant[12] else "❌ НЕ ОТПРАВЛЕН"
    text += f"📦 СТАТУС ПОДАРКА: {gift_status}\n"
    
    if participant[12]:
        text += f"📅 Дата отправки: {participant[13] or 'не указана'}\n"
        text += f"🚚 Трек-номер: {participant[14] or 'нет'}\n\n"
    
    if participant[9]:  # giver_to
        receiver = db_fetchone(
            "SELECT full_name, nickname, pvz_address FROM participants WHERE id = %s",
            (participant[9],)
        )
        if receiver:
            text += f"🎅 <b>ДАРИТ ПОДАРОК:</b>\n"
            text += f"   👤 {receiver[0]}\n"
            text += f"   🎭 {receiver[1]}\n"
            text += f"   📦 Адрес: {receiver[2]}\n"
    
    keyboard = [["👥 УЧАСТНИКИ"], ["⬅️ НАЗАД"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )

# ========== КТО КОМУ ДАРИТ ==========
async def show_draw_results_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню результатов жеребьевки"""
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = %s AND draw_status = 'completed' ORDER BY created_at DESC",
        (ADMIN_ID,)
    )
    
    if not groups:
        keyboard = [["🎲 ЗАПУСТИТЬ ЖЕРЕБЬЁВКУ"], ["⬅️ НАЗАД"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "🎁 Нет групп с проведенной жеребьевкой.",
            reply_markup=reply_markup
        )
        return
    
    text = "🎁 ВЫБЕРИТЕ ГРУППУ ДЛЯ ПРОСМОТРА РЕЗУЛЬТАТОВ:\n\n"
    
    keyboard = []
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = %s AND confirmed = TRUE AND giver_to IS NOT NULL",
            (group[0],)
        )[0] or 0
        
        if participants > 0:
            button_text = f"🎁 {group[1][:15]}{'...' if len(group[1]) > 15 else ''} ({participants})"
            keyboard.append([button_text])
    
    keyboard.append(["🎲 ЗАПУСТИТЬ ЖЕРЕБЬЁВКУ"])
    keyboard.append(["⬅️ НАЗАД"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        text,
        reply_markup=reply_markup
    )

async def show_draw_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать кто кому дарит"""
    text = update.message.text
    
    if text.startswith("🎁 "):
        group_name_part = text[2:].split(" (")[0].strip().replace("...", "")
    else:
        group_name_part = text
    
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = %s AND draw_status = 'completed'",
        (ADMIN_ID,)
    )
    
    matching_groups = []
    for group in groups:
        if group_name_part in group[1]:
            matching_groups.append(group)
    
    if not matching_groups:
        await update.message.reply_text("❌ Группа не найдена или жеребьевка не проведена.")
        return
    
    group = matching_groups[0]
    group_id = group[0]
    
    pairs = db_fetchall('''
        SELECT p1.full_name as giver, p1.nickname as giver_nick,
               p2.full_name as receiver, p2.nickname as receiver_nick,
               p1.gift_sent, p1.sent_date
        FROM participants p1
        JOIN participants p2 ON p1.giver_to = p2.id
        WHERE p1.group_id = %s AND p1.confirmed = TRUE
        ORDER BY p1.full_name
    ''', (group_id,))
    
    if not pairs:
        keyboard = [["🎁 КТО КОМУ ДАРИТ"], ["⬅️ НАЗАД"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"🎁 В группе '{group[1]}' нет данных о жеребьевке.",
            reply_markup=reply_markup
        )
        return
    
    text = f"🎅 <b>РЕЗУЛЬТАТЫ ЖЕРЕБЬЁВКИ: {group[1]}</b>\n\n"
    text += f"💰 Бюджет: {group[4]}\n"
    text += f"👥 Участников: {len(pairs)}\n\n"
    
    sent_count = sum(1 for p in pairs if p[4])
    text += f"📦 Отправлено подарков: {sent_count}/{len(pairs)}\n\n"
    
    for idx, (giver, giver_nick, receiver, receiver_nick, gift_sent, sent_date) in enumerate(pairs, 1):
        gift_status = "✅" if gift_sent else "❌"
        date_info = f"\n   📅 {sent_date}" if sent_date else ""
        
        text += f"<b>{idx}. {giver}</b> {gift_status}\n"
        text += f"   🎭 {giver_nick}\n"
        text += f"   ↓ дарит подарок ↓\n"
        text += f"   👤 {receiver}\n"
        text += f"   🎭 {receiver_nick}{date_info}\n\n"
    
    keyboard = [
        ["📦 СТАТУС ОТПРАВКИ"],
        ["👥 УЧАСТНИКИ ЭТОЙ ГРУППЫ"],
        ["🎁 КТО КОМУ ДАРИТ"],
        ["⬅️ НАЗАД"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    context.user_data['draw_results_group'] = group_id
    
    await update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )

# ========== СТАТУС ОТПРАВКИ ==========
async def show_gift_status_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню статуса отправки"""
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = %s AND draw_status = 'completed' ORDER BY created_at DESC",
        (ADMIN_ID,)
    )
    
    if not groups:
        keyboard = [["🎲 ЗАПУСТИТЬ ЖЕРЕБЬЁВКУ"], ["⬅️ НАЗАД"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "📦 Нет групп с проведенной жеребьевкой.",
            reply_markup=reply_markup
        )
        return
    
    text = "📦 ВЫБЕРИТЕ ГРУППУ ДЛЯ ПРОСМОТРА СТАТУСА:\n\n"
    
    keyboard = []
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = %s AND confirmed = TRUE",
            (group[0],)
        )[0] or 0
        
        sent_gifts = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = %s AND gift_sent = TRUE",
            (group[0],)
        )[0] or 0
        
        if participants > 0:
            button_text = f"📦 {group[1][:15]}{'...' if len(group[1]) > 15 else ''} ({sent_gifts}/{participants})"
            keyboard.append([button_text])
    
    keyboard.append(["🎁 КТО КОМУ ДАРИТ"])
    keyboard.append(["⬅️ НАЗАД"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        text,
        reply_markup=reply_markup
    )

async def show_gift_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статус отправки"""
    text = update.message.text
    
    if text.startswith("📦 "):
        group_name_part = text[2:].split(" (")[0].strip().replace("...", "")
    else:
        group_name_part = text
    
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = %s AND draw_status = 'completed'",
        (ADMIN_ID,)
    )
    
    matching_groups = []
    for group in groups:
        if group_name_part in group[1]:
            matching_groups.append(group)
    
    if not matching_groups:
        await update.message.reply_text("❌ Группа не найдена.")
        return
    
    group = matching_groups[0]
    group_id = group[0]
    
    pairs = db_fetchall('''
        SELECT p1.full_name as giver, p1.nickname as giver_nick,
               p2.full_name as receiver, p2.nickname as receiver_nick,
               p1.gift_sent, p1.sent_date, p1.tracking_number
        FROM participants p1
        JOIN participants p2 ON p1.giver_to = p2.id
        WHERE p1.group_id = %s AND p1.confirmed = TRUE
        ORDER BY p1.gift_sent DESC, p1.full_name
    ''', (group_id,))
    
    if not pairs:
        keyboard = [["📦 СТАТУС ОТПРАВКИ"], ["⬅️ НАЗАД"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"📦 В группе '{group[1]}' нет данных о жеребьевке.",
            reply_markup=reply_markup
        )
        return
    
    sent_count = sum(1 for p in pairs if p[4])
    total_count = len(pairs)
    
    text = f"📦 <b>СТАТУС ОТПРАВКИ: {group[1]}</b>\n\n"
    text += f"💰 Бюджет: {group[4]}\n"
    text += f"📅 Регистрация до: {group[6]}\n\n"
    text += f"📊 СТАТИСТИКА:\n"
    text += f"• Всего участников: {total_count}\n"
    text += f"• ✅ Отправлено: {sent_count} ({sent_count/total_count*100:.0f}%)\n"
    text += f"• ❌ Не отправлено: {total_count - sent_count}\n\n"
    
    if sent_count > 0:
        text += f"<b>✅ ОТПРАВЛЕНЫ ({sent_count}):</b>\n"
        for i, (giver, giver_nick, receiver, receiver_nick, gift_sent, sent_date, tracking) in enumerate(pairs[:10], 1):
            if gift_sent:
                date_info = f" ({sent_date})" if sent_date else ""
                track_info = f"\n   🚚 Трек: {tracking}" if tracking else ""
                text += f"{i}. {giver} → {receiver}{date_info}{track_info}\n"
    
    not_sent_pairs = [p for p in pairs if not p[4]]
    if not_sent_pairs:
        text += f"\n<b>❌ НЕ ОТПРАВЛЕНЫ ({len(not_sent_pairs)}):</b>\n"
        for i, (giver, giver_nick, receiver, receiver_nick, gift_sent, sent_date, tracking) in enumerate(not_sent_pairs[:10], 1):
            text += f"{i}. {giver} → {receiver}\n"
    
    keyboard = [
        ["🎁 КТО КОМУ ДАРИТ"],
        ["👥 УЧАСТНИКИ ЭТОЙ ГРУППЫ"],
        ["📦 СТАТУС ОТПРАВКИ"],
        ["⬅️ НАЗАД"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    context.user_data['gift_status_group'] = group_id
    
    await update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )

# ========== ЖЕРЕБЬЁВКА ==========
async def show_draw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню жеребьевки"""
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = %s AND draw_status = 'pending' ORDER BY created_at DESC",
        (ADMIN_ID,)
    )
    
    if not groups:
        keyboard = [["📋 МОИ ГРУППЫ"], ["⬅️ НАЗАД"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "🎲 У вас нет групп, ожидающих жеребьевки.",
            reply_markup=reply_markup
        )
        return
    
    text = "🎲 ВЫБЕРИТЕ ГРУППУ ДЛЯ ЖЕРЕБЬЁВКИ:\n\n"
    
    keyboard = []
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = %s AND confirmed = TRUE",
            (group[0],)
        )[0] or 0
        
        if participants >= 3:
            button_text = f"✅ {group[1][:20]}{'...' if len(group[1]) > 20 else ''} ({participants})"
        else:
            button_text = f"❌ {group[1][:20]}... ({participants}/3)"
        
        keyboard.append([button_text])
    
    keyboard.append(["📋 МОИ ГРУППЫ"])
    keyboard.append(["⬅️ НАЗАД"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        text,
        reply_markup=reply_markup
    )

async def start_draw_for_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запуск жеребьевки для группы"""
    text = update.message.text
    
    if text.startswith("✅ ") or text.startswith("❌ "):
        group_name_part = text[2:].split(" (")[0].strip().replace("...", "")
    else:
        group_name_part = text
    
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = %s AND draw_status = 'pending'",
        (ADMIN_ID,)
    )
    
    matching_groups = []
    for group in groups:
        if group_name_part in group[1]:
            matching_groups.append(group)
    
    if not matching_groups:
        await update.message.reply_text("❌ Группа не найдена.")
        return
    
    group = matching_groups[0]
    group_id = group[0]
    
    participants = db_fetchall(
        "SELECT * FROM participants WHERE group_id = %s AND confirmed = TRUE",
        (group_id,)
    )
    
    if len(participants) < 3:
        keyboard = [["🎲 ЗАПУСТИТЬ ЖЕРЕБЬЁВКУ"], ["⬅️ НАЗАД"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"❌ Недостаточно участников! Нужно минимум 3, а у вас {len(participants)}",
            reply_markup=reply_markup
        )
        return
    
    context.user_data['draw_group'] = group_id
    
    keyboard = [["✅ ДА, ЗАПУСТИТЬ"], ["❌ НЕТ, ОТМЕНА"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"🎲 <b>ПОДТВЕРЖДЕНИЕ ЖЕРЕБЬЁВКИ</b>\n\n"
        f"🏢 Группа: {group[1]}\n"
        f"👥 Участников: {len(participants)}\n"
        f"💰 Бюджет: {group[4]}\n\n"
        f"<b>Список участников:</b>\n"
        + "\n".join([f"{i+1}. {p[4]} (@{p[2] or 'нет username'})" for i, p in enumerate(participants[:10])])
        + (f"\n... и ещё {len(participants) - 10}" if len(participants) > 10 else "")
        + f"\n\n<b>После запуска:</b>\n"
        f"• Каждый участник получит своего тайного Санту\n"
        f"• Регистрация в группу будет закрыта\n"
        f"• Это действие необратимо!\n\n"
        f"Запустить жеребьёвку?",
        parse_mode='HTML',
        reply_markup=reply_markup
    )

async def execute_draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполнить жеребьевку"""
    if 'draw_group' not in context.user_data:
        await update.message.reply_text("❌ Группа не выбрана.")
        return
    
    group_id = context.user_data['draw_group']
    group = db_fetchone("SELECT * FROM groups WHERE id = %s", (group_id,))
    
    if not group:
        await update.message.reply_text("❌ Группа не найдена!")
        return
    
    participants = db_fetchall(
        "SELECT id, user_id, full_name, nickname, wishlist FROM participants WHERE group_id = %s AND confirmed = TRUE",
        (group_id,)
    )
    
    if len(participants) < 3:
        await update.message.reply_text("❌ Недостаточно участников для жеребьевки!")
        return
    
    participant_ids = [p[0] for p in participants]
    shuffled_ids = participant_ids.copy()
    
    random.shuffle(shuffled_ids)
    attempts = 0
    while any(pid == sid for pid, sid in zip(participant_ids, shuffled_ids)) and attempts < 100:
        random.shuffle(shuffled_ids)
        attempts += 1
    
    if attempts == 100:
        shuffled_ids = participant_ids[1:] + [participant_ids[0]]
    
    db_execute("UPDATE groups SET draw_status = 'completed' WHERE id = %s", (group_id,))
    
    success_count = 0
    for i, (participant_id, user_id, full_name, nickname, wishlist) in enumerate(participants):
        receiver_id = shuffled_ids[i]
        receiver_info = next(p for p in participants if p[0] == receiver_id)
        
        db_execute(
            "UPDATE participants SET giver_to = %s WHERE id = %s",
            (receiver_id, participant_id)
        )
        
        message = (
            f"🎅 <b>ТАЙНЫЙ САНТА!</b>\n\n"
            f"Жеребьёвка в группе '{group[1]}' завершена!\n\n"
            f"💰 Бюджет: {group[4]}\n\n"
            f"<b>Вы дарите подарок:</b>\n"
            f"👤 {receiver_info[2]}\n"
            f"🎭 Никнейм: {receiver_info[3]}\n\n"
        )
        
        if receiver_info[4]:
            message += f"<b>Пожелания:</b>\n{receiver_info[4]}\n\n"
        
        message += f"🎄 Удачи в выборе подарка!"
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode='HTML'
            )
            success_count += 1
        except Exception as e:
            logger.error(f"Ошибка отправки участнику {user_id}: {e}")
    
    context.user_data.pop('draw_group', None)
    
    keyboard = [["🎁 КТО КОМУ ДАРИТ"], ["📋 МОИ ГРУППЫ"], ["⬅️ НАЗАД"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"✅ <b>ЖЕРЕБЬЁВКА ЗАВЕРШЕНА!</b>\n\n"
        f"🏢 Группа: {group[1]}\n"
        f"👥 Участников: {len(participants)}\n"
        f"📨 Уведомлений отправлено: {success_count}/{len(participants)}\n\n"
        f"Все участники получили свои пары!\n"
        f"Теперь вы можете отслеживать статус отправки подарков.",
        parse_mode='HTML',
        reply_markup=reply_markup
    )

# ========== СТАТИСТИКА ==========
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика"""
    groups_count = db_fetchone("SELECT COUNT(*) FROM groups WHERE admin_id = %s", (ADMIN_ID,))[0] or 0
    participants_count = db_fetchone("SELECT COUNT(*) FROM participants WHERE confirmed = TRUE")[0] or 0
    completed_draws = db_fetchone("SELECT COUNT(*) FROM groups WHERE admin_id = %s AND draw_status = 'completed'", (ADMIN_ID,))[0] or 0
    sent_gifts = db_fetchone("SELECT COUNT(*) FROM participants WHERE gift_sent = TRUE")[0] or 0
    
    groups_stats = db_fetchall('''
        SELECT g.name, 
               COUNT(p.id) as total,
               SUM(CASE WHEN p.gift_sent = TRUE THEN 1 ELSE 0 END) as sent,
               g.draw_status
        FROM groups g
        LEFT JOIN participants p ON g.id = p.group_id AND p.confirmed = TRUE
        WHERE g.admin_id = %s
        GROUP BY g.id, g.name, g.draw_status
        ORDER BY g.created_at DESC
    ''', (ADMIN_ID,))
    
    text = f"📊 <b>СТАТИСТИКА</b>\n\n"
    text += f"<b>ОБЩАЯ СТАТИСТИКА:</b>\n"
    text += f"• Всего групп: {groups_count}\n"
    text += f"• Всего участников: {participants_count}\n"
    text += f"• Проведено жеребьевок: {completed_draws}\n"
    text += f"• Отправлено подарков: {sent_gifts}\n\n"
    
    if groups_stats:
        text += "<b>ПО ГРУППАМ:</b>\n"
        for name, total, sent, draw_status in groups_stats:
            if total > 0:
                draw_icon = "🎲" if draw_status == 'completed' else "⏳"
                sent_percent = (sent/total*100) if total > 0 else 0
                text += f"• {name[:15]}: {total} чел. {draw_icon} {sent}/{total} ({sent_percent:.0f}%)\n"
    
    text += f"\n📈 <b>АКТИВНОСТЬ:</b>\n"
    text += f"• Бот работает 24/7 на PostgreSQL\n"
    text += f"• Автоматический пинг каждые 5 минут\n"
    text += f"• Последнее обновление: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    
    keyboard = [
        ["📋 МОИ ГРУППЫ"],
        ["📦 СТАТУС ОТПРАВКИ"],
        ["⬅️ НАЗАД"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )

# ========== РЕГИСТРАЦИЯ УЧАСТНИКА ==========
async def handle_registration_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаги регистрации"""
    if 'registration' not in context.user_data:
        return
    
    reg_data = context.user_data['registration']
    step = reg_data['step']
    text = update.message.text
    
    if step == 1:
        reg_data['full_name'] = text
        reg_data['step'] = 2
        await update.message.reply_text(
            "✅ ФИО сохранено!\n\nШаг 2 из 5\n"
            "🎭 Введите ваш никнейм:\nПример: 'Сашенька', 'Коллега'",
            reply_markup=ReplyKeyboardRemove()
        )
    
    elif step == 2:
        reg_data['nickname'] = text
        reg_data['step'] = 3
        await update.message.reply_text(
            "✅ Никнейм сохранён!\n\nШаг 3 из 5\n"
            "📦 Введите адрес ПВЗ:\nПример: 'wildberries или ozon, Москва, ул. Ленина 1'",
            reply_markup=ReplyKeyboardRemove()
        )
    
    elif step == 3:
        reg_data['pvz_address'] = text
        reg_data['step'] = 4
        await update.message.reply_text(
            "✅ Адрес ПВЗ сохранён!\n\nШаг 4 из 5\n"
            "📮 Введите почтовый адрес:\nИли напишите 'нет'",
            reply_markup=ReplyKeyboardRemove()
        )
    
    elif step == 4:
        reg_data['postal_address'] = text
        reg_data['step'] = 5
        await update.message.reply_text(
            "✅ Адрес сохранён!\n\nШаг 5 из 5\n"
            "🎁 Введите ваш вишлист:\nПример: 'Книги, шоколад, настолки'",
            reply_markup=ReplyKeyboardRemove()
        )
    
    elif step == 5:
        reg_data['wishlist'] = text
        
        db_execute(
            '''INSERT INTO participants 
               (user_id, username, group_id, full_name, nickname, 
                pvz_address, postal_address, wishlist, confirmed)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)''',
            (reg_data['user_id'], reg_data['username'], reg_data['group_id'],
             reg_data['full_name'], reg_data['nickname'],
             reg_data['pvz_address'], reg_data['postal_address'],
             reg_data['wishlist'])
        )
        
        group = db_fetchone("SELECT name FROM groups WHERE id = %s", (reg_data['group_id'],))
        
        await update.message.reply_text(
            f"✅ <b>РЕГИСТРАЦИЯ УСПЕШНА!</b>\n\n"
            f"🏢 Группа: {group[0]}\n"
            f"👤 Вы: {reg_data['full_name']}\n"
            f"🎭 Никнейм: {reg_data['nickname']}\n\n"
            f"Ожидайте жеребьевки!",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardRemove()
        )
        
        context.user_data.pop('registration', None)

# ========== СОЗДАНИЕ ГРУППЫ ==========
async def create_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать создание группы"""
    await update.message.reply_text(
        "🏢 СОЗДАНИЕ НОВОЙ ГРУППЫ\n\n"
        "Шаг 1 из 5\n"
        "Введите название группы:\n"
        "Пример: 'Офис Альфа-Банк 2024'",
        reply_markup=ReplyKeyboardRemove()
    )
    
    return WAITING_NAME

async def group_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Название группы"""
    group_name = update.message.text
    context.user_data['new_group'] = {'name': group_name}
    
    await update.message.reply_text(
        "✅ Название сохранено!\n\n"
        "Шаг 2 из 5\n"
        "Введите контакт организатора:\n"
        "Пример: 'Анна Петрова, @anna_hr, +79991234567'",
        reply_markup=ReplyKeyboardRemove()
    )
    
    return WAITING_ORGANIZER

async def group_organizer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Организатор"""
    organizer = update.message.text
    context.user_data['new_group']['organizer'] = organizer
    
    await update.message.reply_text(
        "✅ Организатор сохранён!\n\n"
        "Шаг 3 из 5\n"
        "Введите бюджет подарков:\n"
        "Примеры:\n"
        "• '1000-1500 рублей'\n"
        "• 'до 2000 руб'\n"
        "• '1500-2000 ₽'",
        reply_markup=ReplyKeyboardRemove()
    )
    
    return WAITING_BUDGET

async def group_budget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Бюджет"""
    budget = update.message.text
    context.user_data['new_group']['budget'] = budget
    
    await update.message.reply_text(
        "✅ Бюджет сохранён!\n\n"
        "Шаг 4 из 5\n"
        "Введите максимальное количество участников:\n"
        "Пример: '25' или '50'\n"
        "(Можно от 3 до 100 человек)",
        reply_markup=ReplyKeyboardRemove()
    )
    
    return WAITING_MAX_PARTICIPANTS

async def group_max_participants_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Макс участников"""
    try:
        max_participants = int(update.message.text)
        if max_participants < 3:
            await update.message.reply_text("❌ Минимум 3 участника. Введите снова:")
            return WAITING_MAX_PARTICIPANTS
        if max_participants > 100:
            await update.message.reply_text("❌ Максимум 100 участников. Введите снова:")
            return WAITING_MAX_PARTICIPANTS
    except ValueError:
        await update.message.reply_text("❌ Введите число! Например: '20'")
        return WAITING_MAX_PARTICIPANTS
    
    context.user_data['new_group']['max_participants'] = max_participants
    
    await update.message.reply_text(
        "✅ Максимальное количество участников сохранено!\n\n"
        "Шаг 5 из 5\n"
        "Введите дедлайн регистрации:\n"
        "Примеры:\n"
        "• '15 декабря 2024'\n"
        "• '20.12.2024'\n"
        "• '25 декабря'\n"
        "• 'до 20 декабря'",
        reply_markup=ReplyKeyboardRemove()
    )
    
    return WAITING_DEADLINE

async def group_deadline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Дедлайн"""
    deadline = update.message.text
    context.user_data['new_group']['deadline'] = deadline
    
    group_data = context.user_data['new_group']
    
    summary = (
        "📋 ПРОВЕРЬТЕ ДАННЫЕ ГРУППЫ:\n\n"
        f"🏢 Название: {group_data['name']}\n"
        f"👤 Организатор: {group_data['organizer']}\n"
        f"💰 Бюджет: {group_data['budget']}\n"
        f"👥 Макс. участников: {group_data['max_participants']}\n"
        f"📅 Регистрация до: {group_data['deadline']}\n\n"
        "Всё верно?"
    )
    
    keyboard = [["✅ ДА, СОЗДАТЬ"], ["❌ НЕТ, ОТМЕНА"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(summary, reply_markup=reply_markup)
    
    return CONFIRM_CREATION

async def confirm_group_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создать группу"""
    text = update.message.text
    
    if text == "✅ ДА, СОЗДАТЬ":
        group_data = context.user_data['new_group']
        group_id = str(uuid.uuid4())[:8].upper()
        
        db_execute(
            '''INSERT INTO groups 
               (id, name, admin_id, organizer, budget, max_participants, reg_deadline)
               VALUES (%s, %s, %s, %s, %s, %s, %s)''',
            (group_id, group_data['name'], ADMIN_ID, 
             group_data['organizer'], group_data['budget'],
             group_data['max_participants'], group_data['deadline'])
        )
        
        bot = await context.bot.get_me()
        invite_link = f"t.me/{bot.username}?start={group_id}"
        
        keyboard = [["📋 МОИ ГРУППЫ"], ["⬅️ НАЗАД"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"✅ ГРУППА СОЗДАНА!\n\n"
            f"🏢 Название: {group_data['name']}\n"
            f"🔑 ID: {group_id}\n"
            f"👤 Организатор: {group_data['organizer']}\n"
            f"💰 Бюджет: {group_data['budget']}\n"
            f"👥 Макс. участников: {group_data['max_participants']}\n"
            f"📅 Регистрация до: {group_data['deadline']}\n\n"
            f"🔗 Ссылка:\n{invite_link}\n\n"
            f"Отправьте эту ссылку участникам!",
            reply_markup=reply_markup
        )
        
        context.user_data.pop('new_group', None)
        
    else:
        keyboard = [["➕ СОЗДАТЬ ГРУППУ"], ["⬅️ НАЗАД"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "❌ Создание отменено.",
            reply_markup=reply_markup
        )
    
    return ConversationHandler.END

# ========== ВСПОМОГАТЕЛЬНЫЕ ==========
async def show_group_participants_from_draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Участники группы из меню результатов"""
    if 'draw_results_group' in context.user_data:
        group_id = context.user_data['draw_results_group']
        group = db_fetchone("SELECT name FROM groups WHERE id = %s", (group_id,))
        
        if group:
            participants = db_fetchall(
                "SELECT * FROM participants WHERE group_id = %s AND confirmed = TRUE ORDER BY registered_at DESC",
                (group_id,)
            )
            
            if participants:
                text = f"👥 <b>УЧАСТНИКИ ГРУППЫ: {group[0]}</b>\n\n"
                text += f"📊 Всего участников: {len(participants)}\n\n"
                
                for idx, participant in enumerate(participants, 1):
                    gift_status = "✅" if participant[12] else "❌"
                    username = f"@{participant[2]}" if participant[2] else "нет username"
                    
                    text += f"<b>{idx}. {participant[4]}</b> {gift_status}\n"
                    text += f"   🎭 Никнейм: {participant[5]}\n"
                    text += f"   📱 {username}\n"
                    
                    if participant[9]:
                        receiver = db_fetchone(
                            "SELECT full_name FROM participants WHERE id = %s",
                            (participant[9],)
                        )
                        if receiver:
                            text += f"   🎅 Дарит: {receiver[0]}\n"
                    
                    text += "\n"
                
                keyboard = [
                    ["🎁 КТО КОМУ ДАРИТ"],
                    ["⬅️ НАЗАД"]
                ]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                
                await update.message.reply_text(
                    text,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                return
    
    await update.message.reply_text("❌ Нет данных для отображения.")

# ========== ОБРАБОТЧИК КОМАНД ==========
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главный обработчик"""
    text = update.message.text
    
    # Регистрация
    if 'registration' in context.user_data:
        await handle_registration_step(update, context)
        return
    
    # Создание группы
    if 'new_group' in context.user_data:
        return
    
    # Главное меню
    if text == "📋 МОИ ГРУППЫ":
        await show_my_groups(update, context)
    
    elif text == "➕ СОЗДАТЬ ГРУППУ":
        await create_group_start(update, context)
    
    elif text == "👥 УЧАСТНИКИ":
        await show_participants_menu(update, context)
    
    elif text == "🎁 КТО КОМУ ДАРИТ":
        await show_draw_results_menu(update, context)
    
    elif text == "📦 СТАТУС ОТПРАВКИ":
        await show_gift_status_menu(update, context)
    
    elif text == "🎲 ЗАПУСТИТЬ ЖЕРЕБЬЁВКУ":
        await show_draw_menu(update, context)
    
    elif text == "📊 СТАТИСТИКА":
        await show_stats(update, context)
    
    elif text == "⬅️ НАЗАД":
        await show_main_menu(update, context)
    
    elif text == "🔗 СКОПИРОВАТЬ ССЫЛКУ":
        await copy_group_link(update, context)
    
    elif text == "🗑 УДАЛИТЬ ГРУППУ":
        await delete_group_confirmation(update, context)
    
    elif text == "✅ ДА, УДАЛИТЬ":
        await delete_group_execute(update, context)
    
    elif text == "❌ НЕТ, ОТМЕНА":
        if 'selected_group' in context.user_data:
            await manage_group(update, context)
        else:
            await show_main_menu(update, context)
    
    elif text == "✅ ДА, ЗАПУСТИТЬ":
        await execute_draw(update, context)
    
    elif text == "👥 УЧАСТНИКИ ЭТОЙ ГРУППЫ":
        await show_group_participants_from_draw(update, context)
    
    # Группы
    elif text.startswith("⚙️ "):
        await manage_group(update, context)
    
    elif text.startswith("👥 "):
        await show_group_participants(update, context)
    
    elif text.startswith("ℹ️ "):
        await show_participant_details(update, context)
    
    elif text.startswith("🎁 "):
        await show_draw_results(update, context)
    
    elif text.startswith("📦 "):
        await show_gift_status(update, context)
    
    elif text.startswith("✅ ") or text.startswith("❌ "):
        await start_draw_for_group(update, context)
    
    else:
        keyboard = [["⬅️ НАЗАД"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            "❌ Неизвестная команда. Используйте кнопки меню.",
            reply_markup=reply_markup
        )

# ========== ЗАПУСК БОТА ==========
async def main_async():
    """Асинхронный запуск бота"""
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # ConversationHandler для создания группы
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & filters.Regex("^➕ СОЗДАТЬ ГРУППУ$"), create_group_start)],
        states={
            WAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, group_name_handler)],
            WAITING_ORGANIZER: [MessageHandler(filters.TEXT & ~filters.COMMAND, group_organizer_handler)],
            WAITING_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, group_budget_handler)],
            WAITING_MAX_PARTICIPANTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, group_max_participants_handler)],
            WAITING_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, group_deadline_handler)],
            CONFIRM_CREATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_group_creation)]
        },
        fallbacks=[]
    )
    
    # Обработчики
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    # Запускаем бота
    logger.info("✅ Бот запущен со всеми функциями и PostgreSQL!")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

def run_telegram_bot():
    """Запуск Telegram бота"""
    asyncio.run(main_async())

def main():
    """Главная функция"""
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask сервер запущен на порту 8080")
    
    # Запускаем автопинг в отдельном потоке
    if RENDER:
        asyncio_thread = threading.Thread(
            target=lambda: asyncio.run(keep_alive()),
            daemon=True
        )
        asyncio_thread.start()
        logger.info("✅ Автопинг запущен (каждые 5 минут)")
    
    # Запускаем бота
    run_telegram_bot()

if __name__ == '__main__':
    main()
