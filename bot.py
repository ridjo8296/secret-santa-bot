import os
import logging
import sqlite3
import uuid
import threading
import random
from datetime import datetime
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, 
    CallbackQueryHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8385598413:AAEaIzByLLFL4-Hp_BfbeUxux-v1cDiv4vY')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 6644276942))

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

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect('santa.db', check_same_thread=False)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS groups
                 (id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  admin_id INTEGER,
                  organizer TEXT,
                  budget TEXT,
                  max_participants INTEGER,
                  reg_deadline TEXT,
                  status TEXT DEFAULT 'active',
                  draw_status TEXT DEFAULT 'pending',
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
                  giver_to INTEGER,
                  receiver_from INTEGER,
                  status TEXT DEFAULT 'registered',
                  confirmed BOOLEAN DEFAULT 0,
                  registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    conn.commit()
    conn.close()

init_db()

# ========== ФУНКЦИИ БАЗЫ ДАННЫХ ==========
def db_execute(query, params=()):
    conn = sqlite3.connect('santa.db', check_same_thread=False)
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    conn.close()

def db_fetchone(query, params=()):
    conn = sqlite3.connect('santa.db', check_same_thread=False)
    c = conn.cursor()
    c.execute(query, params)
    result = c.fetchone()
    conn.close()
    return result

def db_fetchall(query, params=()):
    conn = sqlite3.connect('santa.db', check_same_thread=False)
    c = conn.cursor()
    c.execute(query, params)
    result = c.fetchall()
    conn.close()
    return result

# ========== FLASK ДЛЯ RENDER ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "🎅 Secret Santa Bot is running"

@flask_app.route('/health')
def health():
    return "OK", 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

# ========== TELEGRAM ФУНКЦИИ ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if context.args:
        # Если есть параметр (ссылка приглашения)
        group_id = context.args[0]
        group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
        
        if group:
            # Проверяем статус жеребьевки
            if group[8] == 'completed':
                await update.message.reply_text(
                    f"❌ Регистрация в группе '{group[1]}' завершена.\n"
                    f"Жеребьевка уже проведена."
                )
                return
                
            # Проверяем, есть ли уже участник
            existing = db_fetchone(
                "SELECT * FROM participants WHERE user_id = ? AND group_id = ?",
                (user.id, group_id)
            )
            
            if existing:
                if existing[12] == 1:  # Если подтвержден
                    await update.message.reply_text(
                        f"✅ Вы уже зарегистрированы в группе '{group[1]}'!\n"
                        f"Ожидайте жеребьевки."
                    )
                else:
                    await update.message.reply_text(
                        f"⏳ Ваша регистрация в группе '{group[1]}' ожидает подтверждения администратора."
                    )
                return
            
            # Начинаем регистрацию
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
                "Шаг 1 из 4\n"
                "📝 Введите ваше полное ФИО (как в паспорте):\n"
                "Пример: 'Иванов Иван Иванович'"
            )
        else:
            await update.message.reply_text("❌ Группа не найдена или была удалена.")
        return
    
    if user.id == ADMIN_ID:
        await show_admin_panel(update, context)
    else:
        await update.message.reply_text(
            "🎅 Привет! Я бот для организации Тайного Санты.\n\n"
            "Для участия нужна ссылка-приглашение от организатора."
        )

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ панель как на скриншоте"""
    # Проверяем есть ли неподтвержденные регистрации
    pending_count = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE confirmed = 0"
    )[0]
    
    pending_text = f" ({pending_count})" if pending_count > 0 else ""
    
    keyboard = [
        [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton(f"⏳ ОЖИДАЮТ ПОДТВЕРЖДЕНИЯ{pending_text}", callback_data="pending_registrations")],
        [InlineKeyboardButton("🎲 ЗАПУСТИТЬ ЖЕРЕБЬЁВКУ", callback_data="start_draw")],
        [InlineKeyboardButton("👥 УЧАСТНИКИ", callback_data="participants_list")],
        [InlineKeyboardButton("📊 АКТИВНОСТИ", callback_data="activities")],
        [InlineKeyboardButton("📢 РАССЫЛКА", callback_data="broadcast")],
        [InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data="settings")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "👑 АДМИН-ПАНЕЛЬ 'ДУБИНА'\n\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "👑 АДМИН-ПАНЕЛЬ 'ДУБИНА'\n\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )

# ========== РЕГИСТРАЦИЯ УЧАСТНИКА ==========
async def handle_registration_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка шагов регистрации"""
    if 'registration' not in context.user_data:
        return
    
    reg_data = context.user_data['registration']
    step = reg_data['step']
    text = update.message.text
    
    if step == 1:  # ФИО
        reg_data['full_name'] = text
        reg_data['step'] = 2
        await update.message.reply_text(
            "✅ ФИО сохранено!\n\n"
            "Шаг 2 из 4\n"
            "🎭 Введите ваш никнейм (как к вам обращаться):\n"
            "Пример: 'Сашенька', 'Коллега', 'Аноним'"
        )
    
    elif step == 2:  # Никнейм
        reg_data['nickname'] = text
        reg_data['step'] = 3
        await update.message.reply_text(
            "✅ Никнейм сохранён!\n\n"
            "Шаг 3 из 4\n"
            "📦 Введите адрес ПВЗ для получения подарка:\n"
            "Пример: 'СДЭК, Москва, ул. Ленина 1, пункт выдачи №123'"
        )
    
    elif step == 3:  # Адрес ПВЗ
        reg_data['pvz_address'] = text
        reg_data['step'] = 4
        await update.message.reply_text(
            "✅ Адрес ПВЗ сохранён!\n\n"
            "Шаг 4 из 4\n"
            "📮 Введите почтовый адрес (если нужна доставка почтой):\n"
            "Или напишите 'нет', если не нужна почтовая доставка"
        )
    
    elif step == 4:  # Почтовый адрес
        reg_data['postal_address'] = text
        
        # Запрашиваем вишлист отдельно
        reg_data['step'] = 5
        await update.message.reply_text(
            "✅ Адрес сохранён!\n\n"
            "🎁 Введите ваш вишлист (что бы вы хотели получить):\n"
            "Пример: 'Книги, шоколад, настолки, кофе'"
        )
    
    elif step == 5:  # Вишлист
        reg_data['wishlist'] = text
        
        # Сохраняем в БД как неподтвержденного
        db_execute(
            '''INSERT INTO participants 
               (user_id, username, group_id, full_name, nickname, 
                pvz_address, postal_address, wishlist, confirmed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (reg_data['user_id'], reg_data['username'], reg_data['group_id'],
             reg_data['full_name'], reg_data['nickname'],
             reg_data['pvz_address'], reg_data['postal_address'],
             reg_data['wishlist'], 0)
        )
        
        # Получаем информацию о группе
        group = db_fetchone("SELECT name FROM groups WHERE id = ?", (reg_data['group_id'],))
        
        # Уведомляем админа
        bot = context.bot
        notification_text = (
            f"🔔 <b>НОВАЯ РЕГИСТРАЦИЯ!</b>\n\n"
            f"🏢 Группа: {group[0]}\n"
            f"👤 Пользователь: {reg_data['full_name']}\n"
            f"📱 Username: @{reg_data['username'] or 'без username'}\n"
            f"🆔 ID: {reg_data['user_id']}\n"
            f"🎭 Никнейм: {reg_data['nickname']}\n"
            f"🎁 Вишлист: {reg_data['wishlist'][:100]}...\n\n"
            f"Для подтверждения используйте кнопку '⏳ ОЖИДАЮТ ПОДТВЕРЖДЕНИЯ'"
        )
        
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=notification_text,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу: {e}")
        
        # Отправляем сообщение участнику
        await update.message.reply_text(
            f"✅ <b>РЕГИСТРАЦИЯ ОТПРАВЛЕНА НА ПОДТВЕРЖДЕНИЕ!</b>\n\n"
            f"🏢 Группа: {group[0]}\n"
            f"👤 Вы: {reg_data['full_name']}\n"
            f"🎭 Никнейм: {reg_data['nickname']}\n\n"
            f"⏳ Ожидайте подтверждения администратора.\n"
            f"Вы получите уведомление, когда администратор подтвердит вашу регистрацию.",
            parse_mode='HTML'
        )
        
        # Очищаем временные данные
        context.user_data.pop('registration', None)

# ========== ПОДТВЕРЖДЕНИЕ РЕГИСТРАЦИЙ ==========
async def show_pending_registrations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать ожидающие подтверждения регистрации"""
    query = update.callback_query
    await query.answer()
    
    pending = db_fetchall('''
        SELECT p.*, g.name as group_name 
        FROM participants p
        JOIN groups g ON p.group_id = g.id
        WHERE p.confirmed = 0
        ORDER BY p.registered_at DESC
    ''')
    
    if not pending:
        await query.edit_message_text(
            "✅ Нет регистраций, ожидающих подтверждения.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_admin")]
            ])
        )
        return
    
    # Показываем первую регистрацию
    await show_pending_details(update, context, pending[0][0], 0, len(pending))

async def show_pending_details(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                               participant_id: int, current_index: int, total_count: int):
    """Показать детали регистрации"""
    query = update.callback_query
    await query.answer()
    
    participant = db_fetchone('''
        SELECT p.*, g.name as group_name 
        FROM participants p
        JOIN groups g ON p.group_id = g.id
        WHERE p.id = ?
    ''', (participant_id,))
    
    if not participant:
        await query.edit_message_text("Регистрация не найдена.")
        return
    
    text = (
        f"🔔 <b>НОВАЯ РЕГИСТРАЦИЯ!</b>\n\n"
        f"🏢 Группа: {participant[16]}\n"
        f"👤 Пользователь: {participant[4]}\n"
        f"📱 Username: @{participant[2] or 'без username'}\n"
        f"🆔 ID: {participant[1]}\n"
        f"🎭 Никнейм: {participant[5]}\n"
        f"📦 ПВЗ: {participant[6][:50]}...\n"
        f"📮 Почта: {participant[7][:50] if participant[7] else 'не указан'}...\n"
        f"🎁 Вишлист: {participant[8][:100]}...\n\n"
        f"📅 Зарегистрирован: {participant[14]}\n"
        f"📊 {current_index + 1} из {total_count}"
    )
    
    keyboard = []
    if total_count > 1:
        nav_buttons = []
        if current_index > 0:
            prev_participant = db_fetchone('''
                SELECT p.id FROM participants p
                WHERE p.confirmed = 0
                ORDER BY p.registered_at DESC
                LIMIT 1 OFFSET ?
            ''', (current_index - 1,))
            if prev_participant:
                nav_buttons.append(InlineKeyboardButton("◀️", callback_data=f"pending_{prev_participant[0]}_{current_index-1}_{total_count}"))
        
        nav_buttons.append(InlineKeyboardButton(f"{current_index + 1}/{total_count}", callback_data="noop"))
        
        if current_index < total_count - 1:
            next_participant = db_fetchone('''
                SELECT p.id FROM participants p
                WHERE p.confirmed = 0
                ORDER BY p.registered_at DESC
                LIMIT 1 OFFSET ?
            ''', (current_index + 1,))
            if next_participant:
                nav_buttons.append(InlineKeyboardButton("▶️", callback_data=f"pending_{next_participant[0]}_{current_index+1}_{total_count}"))
        
        keyboard.append(nav_buttons)
    
    keyboard.append([
        InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data=f"confirm_{participant_id}"),
        InlineKeyboardButton("❌ ОТКЛОНИТЬ", callback_data=f"reject_{participant_id}")
    ])
    keyboard.append([InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_admin")])
    
    await query.edit_message_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def confirm_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, participant_id: int):
    """Подтвердить регистрацию"""
    query = update.callback_query
    await query.answer()
    
    # Подтверждаем регистрацию
    db_execute("UPDATE participants SET confirmed = 1 WHERE id = ?", (participant_id,))
    
    # Получаем данные участника
    participant = db_fetchone('''
        SELECT p.*, g.name as group_name 
        FROM participants p
        JOIN groups g ON p.group_id = g.id
        WHERE p.id = ?
    ''', (participant_id,))
    
    if participant:
        # Уведомляем участника
        try:
            await context.bot.send_message(
                chat_id=participant[1],
                text=f"✅ <b>ВАША РЕГИСТРАЦИЯ ПОДТВЕРЖДЕНА!</b>\n\n"
                     f"🏢 Группа: {participant[16]}\n"
                     f"👤 Вы: {participant[4]}\n"
                     f"🎭 Никнейм: {participant[5]}\n\n"
                     f"Ожидайте жеребьевки!",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления участнику: {e}")
        
        # Показываем следующую регистрацию или возвращаем в меню
        pending = db_fetchall(
            "SELECT id FROM participants WHERE confirmed = 0 ORDER BY registered_at DESC"
        )
        
        if pending:
            next_id = pending[0][0] if pending else None
            await show_pending_details(update, context, next_id, 0, len(pending))
        else:
            await query.edit_message_text(
                f"✅ <b>Регистрация подтверждена!</b>\n\n"
                f"👤 Пользователь: {participant[4]}\n"
                f"📱 @{participant[2]}\n"
                f"🏢 Группа: {participant[16]}\n\n"
                f"✅ Участник уведомлен.",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_admin")]
                ])
            )
    else:
        await query.edit_message_text("Ошибка: участник не найден.")

async def reject_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, participant_id: int):
    """Отклонить регистрацию"""
    query = update.callback_query
    await query.answer()
    
    # Получаем данные перед удалением
    participant = db_fetchone(
        "SELECT user_id, full_name, group_id FROM participants WHERE id = ?",
        (participant_id,)
    )
    
    if participant:
        # Уведомляем участника
        try:
            group_name = db_fetchone(
                "SELECT name FROM groups WHERE id = ?",
                (participant[2],)
            )[0]
            
            await context.bot.send_message(
                chat_id=participant[0],
                text=f"❌ <b>ВАША РЕГИСТРАЦИЯ ОТКЛОНЕНА</b>\n\n"
                     f"🏢 Группа: {group_name}\n"
                     f"👤 Вы: {participant[1]}\n\n"
                     f"По вопросам обращайтесь к администратору.",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления участнику: {e}")
    
    # Удаляем регистрацию
    db_execute("DELETE FROM participants WHERE id = ?", (participant_id,))
    
    # Показываем следующую регистрацию
    pending = db_fetchall(
        "SELECT id FROM participants WHERE confirmed = 0 ORDER BY registered_at DESC"
    )
    
    if pending:
        next_id = pending[0][0]
        await show_pending_details(update, context, next_id, 0, len(pending))
    else:
        await query.edit_message_text(
            "✅ Регистрация отклонена и удалена.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_admin")]
            ])
        )

# ========== СОЗДАНИЕ ГРУППЫ ==========
async def create_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало создания группы"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🏢 СОЗДАНИЕ НОВОЙ ГРУППЫ\n\n"
        "Шаг 1 из 5\n"
        "Введите название группы:\n"
        "Пример: 'Офис Альфа-Банк 2024'"
    )
    
    return WAITING_NAME

async def group_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка названия группы"""
    group_name = update.message.text
    context.user_data['new_group'] = {'name': group_name}
    
    await update.message.reply_text(
        "✅ Название сохранено!\n\n"
        "Шаг 2 из 5\n"
        "Введите контакт организатора:\n"
        "Пример: 'Анна Петрова, @anna_hr, +79991234567'"
    )
    
    return WAITING_ORGANIZER

async def group_organizer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка организатора"""
    organizer = update.message.text
    context.user_data['new_group']['organizer'] = organizer
    
    await update.message.reply_text(
        "✅ Организатор сохранён!\n\n"
        "Шаг 3 из 5\n"
        "Введите бюджет подарков:\n"
        "Примеры:\n"
        "• '1000-1500 рублей'\n"
        "• 'до 2000 руб'\n"
        "• '1500-2000 ₽'"
    )
    
    return WAITING_BUDGET

async def group_budget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка бюджета"""
    budget = update.message.text
    context.user_data['new_group']['budget'] = budget
    
    await update.message.reply_text(
        "✅ Бюджет сохранён!\n\n"
        "Шаг 4 из 5\n"
        "Введите максимальное количество участников:\n"
        "Пример: '25' или '50'\n"
        "(Можно от 3 до 100 человек)"
    )
    
    return WAITING_MAX_PARTICIPANTS

async def group_max_participants_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка максимального количества участников"""
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
        "• 'до 20 декабря'"
    )
    
    return WAITING_DEADLINE

async def group_deadline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка дедлайна"""
    deadline = update.message.text
    context.user_data['new_group']['deadline'] = deadline
    
    # Показываем сводку для подтверждения
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
    
    keyboard = [
        [InlineKeyboardButton("✅ ДА, СОЗДАТЬ", callback_data="confirm_create")],
        [InlineKeyboardButton("❌ НЕТ, ИЗМЕНИТЬ", callback_data="cancel_create")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(summary, reply_markup=reply_markup)
    
    return CONFIRM_CREATION

async def confirm_group_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение создания группы"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirm_create":
        group_data = context.user_data['new_group']
        group_id = str(uuid.uuid4())[:8].upper()
        
        # Сохраняем в базу данных
        db_execute(
            '''INSERT INTO groups 
               (id, name, admin_id, organizer, budget, max_participants, reg_deadline)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (group_id, group_data['name'], ADMIN_ID, 
             group_data['organizer'], group_data['budget'],
             group_data['max_participants'], group_data['deadline'])
        )
        
        # Получаем username бота
        bot = await context.bot.get_me()
        invite_link = f"t.me/{bot.username}?start={group_id}"
        
        # Клавиатура после создания
        keyboard = [
            [InlineKeyboardButton("🔗 СКОПИРОВАТЬ ССЫЛКУ", callback_data=f"copy_link_{group_id}")],
            [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
            [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"✅ ГРУППА СОЗДАНА!\n\n"
            f"🏢 Название: {group_data['name']}\n"
            f"🔑 ID группы: {group_id}\n"
            f"👤 Организатор: {group_data['organizer']}\n"
            f"💰 Бюджет: {group_data['budget']}\n"
            f"👥 Макс. участников: {group_data['max_participants']}\n"
            f"📅 Регистрация до: {group_data['deadline']}\n\n"
            f"🔗 ССЫЛКА ДЛЯ УЧАСТНИКОВ:\n"
            f"<code>{invite_link}</code>\n\n"
            f"Отправьте эту ссылку участникам!",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        
        # Очищаем временные данные
        context.user_data.pop('new_group', None)
        
    else:
        await query.edit_message_text(
            "❌ Создание отменено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
            ])
        )
    
    return ConversationHandler.END

# ========== МОИ ГРУППЫ ==========
async def show_my_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать мои группы"""
    query = update.callback_query
    await query.answer()
    
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = ? ORDER BY created_at DESC",
        (ADMIN_ID,)
    )
    
    if not groups:
        await query.edit_message_text(
            "📭 У вас пока нет созданных групп.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ СОЗДАТЬ ГРУППУ", callback_data="create_group")],
                [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
            ])
        )
        return
    
    text = "📋 ВАШИ ГРУППЫ:\n\n"
    buttons = []
    
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1",
            (group[0],)
        )[0]
        
        draw_icon = "🎲" if group[8] == 'completed' else "⏳"
        display_name = f"{draw_icon} {group[1][:20]}{'...' if len(group[1]) > 20 else ''}"
        
        buttons.append([
            InlineKeyboardButton(
                f"{display_name} ({participants}/{group[5]})", 
                callback_data=f"group_manage_{group[0]}"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton("➕ СОЗДАТЬ ГРУППУ", callback_data="create_group"),
        InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")
    ])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def manage_group(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Управление группой"""
    query = update.callback_query
    await query.answer()
    
    group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
    
    if not group:
        await query.edit_message_text("❌ Группа не найдена!")
        return
    
    confirmed_participants = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1",
        (group_id,)
    )[0]
    
    pending_participants = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 0",
        (group_id,)
    )[0]
    
    bot = await context.bot.get_me()
    invite_link = f"t.me/{bot.username}?start={group_id}"
    
    text = (
        f"🏢 <b>УПРАВЛЕНИЕ ГРУППОЙ</b>\n\n"
        f"📝 Название: {group[1]}\n"
        f"🔑 ID: <code>{group[0]}</code>\n"
        f"👤 Организатор: {group[3]}\n"
        f"💰 Бюджет: {group[4]}\n"
        f"👥 Участников: {confirmed_participants}/{group[5]}\n"
        f"⏳ Ожидают: {pending_participants}\n"
        f"📅 Регистрация до: {group[6]}\n"
        f"🎲 Жеребьевка: {'✅ ПРОВЕДЕНА' if group[8] == 'completed' else '⏳ ОЖИДАЕТ'}\n\n"
        f"🔗 Ссылка для регистрации:\n"
        f"<code>{invite_link}</code>"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔗 СКОПИРОВАТЬ ССЫЛКУ", callback_data=f"copy_link_{group_id}")],
        [InlineKeyboardButton("👥 УЧАСТНИКИ ГРУППЫ", callback_data=f"group_participants_{group_id}")],
    ]
    
    if group[8] == 'pending' and confirmed_participants >= 3:
        keyboard.append([InlineKeyboardButton("🎲 ЗАПУСТИТЬ ЖЕРЕБЬЁВКУ", callback_data=f"start_draw_group_{group_id}")])
    
    keyboard.extend([
        [InlineKeyboardButton("✏️ ИЗМЕНИТЬ НАЗВАНИЕ", callback_data=f"edit_group_name_{group_id}")],
        [InlineKeyboardButton("🗑 УДАЛИТЬ ГРУППУ", callback_data=f"delete_group_{group_id}")],
        [InlineKeyboardButton("📋 ВСЕ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
    ])
    
    await query.edit_message_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== ЖЕРЕБЬЁВКА ==========
async def show_draw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню жеребьевки"""
    query = update.callback_query
    await query.answer()
    
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = ? AND draw_status = 'pending' ORDER BY created_at DESC",
        (ADMIN_ID,)
    )
    
    if not groups:
        await query.edit_message_text(
            "🎲 <b>ЖЕРЕБЬЁВКА</b>\n\n"
            "У вас нет групп, ожидающих жеребьевки.",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
                [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
            ])
        )
        return
    
    text = "🎲 <b>ВЫБЕРИТЕ ГРУППУ ДЛЯ ЖЕРЕБЬЁВКИ</b>\n\n"
    buttons = []
    
    for group in groups:
        confirmed_participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1",
            (group[0],)
        )[0]
        
        if confirmed_participants >= 3:
            display_name = f"✅ {group[1][:20]}{'...' if len(group[1]) > 20 else ''}"
            callback = f"start_draw_group_{group[0]}"
        else:
            display_name = f"❌ {group[1][:20]}... ({confirmed_participants}/3)"
            callback = f"group_manage_{group[0]}"
        
        buttons.append([
            InlineKeyboardButton(
                display_name, 
                callback_data=callback
            )
        ])
    
    buttons.append([
        InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups"),
        InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")
    ])
    
    await query.edit_message_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def start_draw_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Подтверждение начала жеребьевки"""
    query = update.callback_query
    await query.answer()
    
    group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
    
    if not group:
        await query.edit_message_text("❌ Группа не найдена!")
        return
    
    participants = db_fetchall(
        "SELECT * FROM participants WHERE group_id = ? AND confirmed = 1",
        (group_id,)
    )
    
    if len(participants) < 3:
        await query.answer(
            f"❌ Недостаточно участников! Нужно минимум 3 подтвержденных, а у вас {len(participants)}",
            show_alert=True
        )
        return
    
    text = f"🎲 <b>ПОДТВЕРЖДЕНИЕ ЖЕРЕБЬЁВКИ</b>\n\n"
    text += f"🏢 Группа: {group[1]}\n"
    text += f"👥 Участников: {len(participants)}\n"
    text += f"💰 Бюджет: {group[4]}\n\n"
    text += f"<b>После запуска:</b>\n"
    text += f"• Каждый участник получит своего тайного Санту\n"
    text += f"• Регистрация в группу будет закрыта\n"
    text += f"• Это действие необратимо!\n\n"
    text += f"Запустить жеребьёвку?"
    
    keyboard = [
        [InlineKeyboardButton("✅ ДА, ЗАПУСТИТЬ", callback_data=f"confirm_draw_{group_id}")],
        [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data=f"group_manage_{group_id}")]
    ]
    
    await query.edit_message_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def execute_draw(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Выполнение жеребьевки"""
    query = update.callback_query
    await query.answer()
    
    # Получаем всех подтвержденных участников
    participants = db_fetchall(
        "SELECT id, user_id, full_name, nickname, wishlist FROM participants WHERE group_id = ? AND confirmed = 1",
        (group_id,)
    )
    
    if len(participants) < 3:
        await query.edit_message_text(
            "❌ Недостаточно участников для жеребьевки! Нужно минимум 3.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 ВСЕ ГРУППЫ", callback_data="my_groups")],
                [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
            ])
        )
        return
    
    # Создаем пары
    participant_ids = [p[0] for p in participants]
    shuffled_ids = participant_ids.copy()
    
    # Гарантируем, что никто не получит сам себя
    random.shuffle(shuffled_ids)
    attempts = 0
    while any(pid == sid for pid, sid in zip(participant_ids, shuffled_ids)) and attempts < 100:
        random.shuffle(shuffled_ids)
        attempts += 1
    
    if attempts == 100:
        # Циклический сдвиг
        shuffled_ids = participant_ids[1:] + [participant_ids[0]]
    
    # Обновляем статус группы
    db_execute("UPDATE groups SET draw_status = 'completed' WHERE id = ?", (group_id,))
    
    # Отправляем сообщения участникам
    group = db_fetchone("SELECT name, budget FROM groups WHERE id = ?", (group_id,))
    
    success_count = 0
    for i, (participant_id, user_id, full_name, nickname, wishlist) in enumerate(participants):
        receiver_id = shuffled_ids[i]
        receiver_info = next(p for p in participants if p[0] == receiver_id)
        
        # Сохраняем пару
        db_execute(
            "UPDATE participants SET giver_to = ? WHERE id = ?",
            (receiver_id, participant_id)
        )
        db_execute(
            "UPDATE participants SET receiver_from = ? WHERE id = ?",
            (participant_id, receiver_id)
        )
        
        # Формируем сообщение
        message = (
            f"🎅 <b>ТАЙНЫЙ САНТА!</b>\n\n"
            f"Жеребьёвка в группе '{group[0]}' завершена!\n\n"
            f"💰 Бюджет: {group[1]}\n\n"
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
            logger.error(f"Ошибка отправки сообщения участнику {user_id}: {e}")
    
    await query.edit_message_text(
        f"✅ <b>ЖЕРЕБЬЁВКА ЗАВЕРШЕНА!</b>\n\n"
        f"🏢 Группа: {group[0]}\n"
        f"👥 Участников: {len(participants)}\n"
        f"📨 Уведомлений отправлено: {success_count}/{len(participants)}\n\n"
        f"Все участники получили свои пары для обмена подарками!",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
            [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
        ])
    )

# ========== УЧАСТНИКИ ==========
async def show_participants_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список участников по группам"""
    query = update.callback_query
    await query.answer()
    
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = ? ORDER BY created_at DESC",
        (ADMIN_ID,)
    )
    
    if not groups:
        await query.edit_message_text(
            "👥 Нет групп с участниками.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ СОЗДАТЬ ГРУППУ", callback_data="create_group")],
                [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
            ])
        )
        return
    
    text = "👥 <b>УЧАСТНИКИ ПО ГРУППАМ</b>\n\n"
    buttons = []
    
    for group in groups:
        participants_count = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1",
            (group[0],)
        )[0]
        
        if participants_count > 0:
            buttons.append([
                InlineKeyboardButton(
                    f"🏢 {group[1][:20]}{'...' if len(group[1]) > 20 else ''} ({participants_count})", 
                    callback_data=f"group_participants_{group[0]}"
                )
            ])
    
    if not buttons:
        text += "Нет участников в группах."
    
    buttons.append([
        InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups"),
        InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")
    ])
    
    await query.edit_message_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def show_group_participants(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Участники конкретной группы"""
    query = update.callback_query
    await query.answer()
    
    group = db_fetchone("SELECT name FROM groups WHERE id = ?", (group_id,))
    
    participants = db_fetchall(
        "SELECT * FROM participants WHERE group_id = ? AND confirmed = 1 ORDER BY registered_at DESC",
        (group_id,)
    )
    
    if not participants:
        await query.edit_message_text(
            f"👥 В группе '{group[0]}' пока нет подтвержденных участников.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ УПРАВЛЕНИЕ ГРУППОЙ", callback_data=f"group_manage_{group_id}")],
                [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
            ])
        )
        return
    
    text = f"👥 <b>УЧАСТНИКИ ГРУППЫ</b>\n\n"
    text += f"🏢 Группа: {group[0]}\n"
    text += f"📊 Всего: {len(participants)} участников\n\n"
    
    for idx, participant in enumerate(participants, 1):
        status = "🎁" if participant[9] else "👤"
        text += f"{idx}. {status} {participant[4]} (@{participant[2] or 'нет'})\n"
        text += f"   🎭 {participant[5]}\n"
        if participant[9]:
            receiver = db_fetchone(
                "SELECT full_name FROM participants WHERE id = ?",
                (participant[9],)
            )
            if receiver:
                text += f"   ➡️ Дарит: {receiver[0]}\n"
        text += "\n"
    
    keyboard = [
        [InlineKeyboardButton("⚙️ УПРАВЛЕНИЕ ГРУППОЙ", callback_data=f"group_manage_{group_id}")],
        [InlineKeyboardButton("👥 ВСЕ УЧАСТНИКИ", callback_data="participants_list")],
        [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
    ]
    
    await query.edit_message_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== РАССЫЛКА ==========
async def show_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню рассылки"""
    query = update.callback_query
    await query.answer()
    
    total_participants = db_fetchone("SELECT COUNT(*) FROM participants WHERE confirmed = 1")[0]
    
    text = (
        f"📢 <b>РАССЫЛКА</b>\n\n"
        f"Всего подтвержденных участников: {total_participants}\n\n"
        f"Выберите тип рассылки:"
    )
    
    keyboard = [
        [InlineKeyboardButton("📨 ВСЕМ УЧАСТНИКАМ", callback_data="broadcast_all")],
        [InlineKeyboardButton("🏢 ПО ГРУППАМ", callback_data="broadcast_groups")],
        [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
    ]
    
    await query.edit_message_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== АКТИВНОСТИ ==========
async def show_activities(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика активностей"""
    query = update.callback_query
    await query.answer()
    
    # Общая статистика
    total_groups = db_fetchone("SELECT COUNT(*) FROM groups WHERE admin_id = ?", (ADMIN_ID,))[0]
    total_participants = db_fetchone("SELECT COUNT(*) FROM participants WHERE confirmed = 1")[0]
    pending_registrations = db_fetchone("SELECT COUNT(*) FROM participants WHERE confirmed = 0")[0]
    
    # Статистика по группам
    groups_stats = db_fetchall('''
        SELECT g.name, COUNT(p.id) as count,
               SUM(CASE WHEN p.giver_to IS NOT NULL THEN 1 ELSE 0 END) as draw_count
        FROM groups g
        LEFT JOIN participants p ON g.id = p.group_id AND p.confirmed = 1
        WHERE g.admin_id = ?
        GROUP BY g.id
        ORDER BY count DESC
    ''', (ADMIN_ID,))
    
    text = (
        f"📊 <b>АКТИВНОСТИ</b>\n\n"
        f"<b>Общая статистика:</b>\n"
        f"• Групп: {total_groups}\n"
        f"• Участников: {total_participants}\n"
        f"• Ожидают подтверждения: {pending_registrations}\n\n"
    )
    
    if groups_stats:
        text += "<b>По группам:</b>\n"
        for group_name, count, draw_count in groups_stats:
            draw_status = f"🎲 {draw_count}" if draw_count > 0 else "⏳"
            text += f"• {group_name[:15]}: {count} чел. {draw_status}\n"
    
    keyboard = [
        [InlineKeyboardButton("⏳ ОЖИДАЮТ ПОДТВЕРЖДЕНИЯ", callback_data="pending_registrations")],
        [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
    ]
    
    await query.edit_message_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== НАСТРОЙКИ ==========
async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройки"""
    query = update.callback_query
    await query.answer()
    
    text = (
        f"⚙️ <b>НАСТРОЙКИ</b>\n\n"
        f"🆔 Ваш ID: {ADMIN_ID}\n"
        f"🤖 Бот: @{(await context.bot.get_me()).username}\n\n"
        f"<b>Функции:</b>\n"
        f"• Создание групп Тайного Санты\n"
        f"• Регистрация участников\n"
        f"• Подтверждение регистраций\n"
        f"• Автоматическая жеребьевка\n"
        f"• Уведомления участникам\n\n"
        f"Версия: 2.0"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔄 ОБНОВИТЬ СТАТИСТИКУ", callback_data="activities")],
        [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
    ]
    
    await query.edit_message_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def back_to_admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат в админ панель"""
    query = update.callback_query
    await query.answer()
    await show_admin_panel(update, context)

async def copy_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Копирование ссылки"""
    query = update.callback_query
    await query.answer()
    
    bot = await context.bot.get_me()
    invite_link = f"t.me/{bot.username}?start={group_id}"
    
    group = db_fetchone("SELECT name FROM groups WHERE id = ?", (group_id,))
    
    await query.answer(f"Ссылка скопирована!\n{invite_link}", show_alert=True)
    
    keyboard = [
        [InlineKeyboardButton("⚙️ УПРАВЛЕНИЕ ГРУППОЙ", callback_data=f"group_manage_{group_id}")],
        [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
    ]
    
    await query.edit_message_text(
        f"🔗 <b>ССЫЛКА ДЛЯ ПРИГЛАШЕНИЯ</b>\n\n"
        f"🏢 Группа: {group[0]}\n\n"
        f"<code>{invite_link}</code>\n\n"
        f"✅ Ссылка скопирована в буфер обмена!",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== ОБРАБОТЧИК КНОПОК ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    try:
        if data == "back_to_admin":
            await back_to_admin_handler(update, context)
        
        elif data == "my_groups":
            await show_my_groups(update, context)
        elif data.startswith("group_manage_"):
            group_id = data.split("_")[2]
            await manage_group(update, context, group_id)
        elif data.startswith("group_participants_"):
            group_id = data.split("_")[2]
            await show_group_participants(update, context, group_id)
        
        elif data == "pending_registrations":
            await show_pending_registrations(update, context)
        elif data.startswith("pending_"):
            parts = data.split("_")
            if len(parts) >= 4:
                participant_id = int(parts[1])
                current_index = int(parts[2])
                total_count = int(parts[3])
                await show_pending_details(update, context, participant_id, current_index, total_count)
        elif data.startswith("confirm_"):
            participant_id = int(data.split("_")[1])
            await confirm_registration(update, context, participant_id)
        elif data.startswith("reject_"):
            participant_id = int(data.split("_")[1])
            await reject_registration(update, context, participant_id)
        
        elif data == "start_draw":
            await show_draw_menu(update, context)
        elif data.startswith("start_draw_group_"):
            group_id = data.split("_")[3]
            await start_draw_confirmation(update, context, group_id)
        elif data.startswith("confirm_draw_"):
            group_id = data.split("_")[2]
            await execute_draw(update, context, group_id)
        
        elif data == "participants_list":
            await show_participants_list(update, context)
        elif data == "activities":
            await show_activities(update, context)
        elif data == "broadcast":
            await show_broadcast_menu(update, context)
        elif data == "settings":
            await show_settings(update, context)
        
        elif data.startswith("copy_link_"):
            group_id = data.split("_")[2]
            await copy_link_handler(update, context, group_id)
        
        elif data == "create_group":
            await create_group_start(update, context)
        elif data == "confirm_create" or data == "cancel_create":
            await confirm_group_creation(update, context)
        
        elif data.startswith("delete_group_"):
            # Простой вариант - сразу удаляем
            group_id = data.split("_")[2]
            db_execute("DELETE FROM participants WHERE group_id = ?", (group_id,))
            db_execute("DELETE FROM groups WHERE id = ?", (group_id,))
            await query.edit_message_text(
                "✅ Группа удалена!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
                    [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
                ])
            )
        
        elif data == "noop":
            pass  # Ничего не делаем
        
        else:
            await query.edit_message_text("❌ Неизвестная команда")
            
    except Exception as e:
        logger.error(f"Ошибка в обработчике кнопок: {e}")
        await query.edit_message_text(
            "❌ Произошла ошибка. Попробуйте снова.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ АДМИН ПАНЕЛЬ", callback_data="back_to_admin")]
            ])
        )

# ========== ОБРАБОТЧИК СООБЩЕНИЙ ==========
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user = update.effective_user
    
    # Проверяем, идет ли регистрация
    if 'registration' in context.user_data:
        await handle_registration_step(update, context)
        return
    
    # Проверяем, идет ли создание группы
    if 'new_group' in context.user_data:
        # Это обрабатывается ConversationHandler
        return
    
    if user.id == ADMIN_ID:
        # Админ отправляет текст - показываем меню
        await show_admin_panel(update, context)
    else:
        await update.message.reply_text(
            "Используйте команду /start для начала работы."
        )

# ========== ЗАПУСК БОТА ==========
def run_telegram_bot():
    """Запуск Telegram бота"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # ConversationHandler для создания группы
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(create_group_start, pattern="^create_group$")],
        states={
            WAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, group_name_handler)],
            WAITING_ORGANIZER: [MessageHandler(filters.TEXT & ~filters.COMMAND, group_organizer_handler)],
            WAITING_BUDGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, group_budget_handler)],
            WAITING_MAX_PARTICIPANTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, group_max_participants_handler)],
            WAITING_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, group_deadline_handler)],
            CONFIRM_CREATION: [CallbackQueryHandler(confirm_group_creation, pattern="^(confirm_create|cancel_create)$")]
        },
        fallbacks=[]
    )
    
    # Команды
    application.add_handler(CommandHandler("start", start_command))
    
    # Conversation handler
    application.add_handler(conv_handler)
    
    # Обработчик кнопок
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Обработчик текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    logger.info("✅ Бот запущен!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
def main():
    """Главная функция запуска"""
    # Запускаем Flask для Render
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask сервер запущен на порту 8080")
    
    # Запускаем Telegram бота
    run_telegram_bot()

if __name__ == '__main__':
    main()
