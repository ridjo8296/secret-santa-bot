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
            # Проверяем, есть ли уже участник
            existing = db_fetchone(
                "SELECT * FROM participants WHERE user_id = ? AND group_id = ?",
                (user.id, group_id)
            )
            
            if existing:
                await update.message.reply_text(
                    f"Вы уже зарегистрированы в группе '{group[1]}'!"
                )
            else:
                # Сохраняем начало регистрации
                context.user_data['registration_group'] = group_id
                context.user_data['registration_step'] = 1
                
                await update.message.reply_text(
                    f"🎅 РЕГИСТРАЦИЯ В ГРУППЕ: {group[1]}\n\n"
                    f"💰 Бюджет: {group[4]}\n"
                    f"📅 Регистрация до: {group[6]}\n\n"
                    "Шаг 1 из 5\n"
                    "Введите ваше полное ФИО (как в паспорте):\n"
                    "Пример: 'Иванов Иван Иванович'"
                )
        else:
            await update.message.reply_text("❌ Группа не найдена или была удалена.")
        return
    
    if user.id == ADMIN_ID:
        await show_main_menu_admin(update, context)
    else:
        await update.message.reply_text(
            "🎅 Привет! Я бот для организации Тайного Санты.\n\n"
            "Для участия нужна ссылка-приглашение от организатора."
        )

async def show_main_menu_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню админа"""
    keyboard = [
        [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("➕ СОЗДАТЬ ГРУППУ", callback_data="create_group")],
        [InlineKeyboardButton("⚙️ УПРАВЛЕНИЕ ГРУППАМИ", callback_data="manage_groups")],
        [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="stats")],
        [InlineKeyboardButton("🔄 ОБНОВИТЬ МЕНЮ", callback_data="refresh")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "👑 АДМИН-ПАНЕЛЬ\n\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "👑 АДМИН-ПАНЕЛЬ\n\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )

async def back_to_main_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат в главное меню"""
    query = update.callback_query
    await query.answer()
    await show_main_menu_admin(update, context)

# ========== СОЗДАНИЕ ГРУППЫ (ШАГ ЗА ШАГОМ) ==========
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
            [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
            [InlineKeyboardButton("🔗 СКОПИРОВАТЬ ССЫЛКУ", callback_data=f"copy_link_{group_id}")],
            [InlineKeyboardButton("➕ СОЗДАТЬ ЕЩЁ", callback_data="create_group")],
            [InlineKeyboardButton("⬅️ ГЛАВНОЕ МЕНЮ", callback_data="back_to_main")]
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
            f"{invite_link}\n\n"
            f"Отправьте эту ссылку участникам!",
            reply_markup=reply_markup
        )
        
        # Очищаем временные данные
        context.user_data.pop('new_group', None)
        
    else:
        await query.edit_message_text(
            "❌ Создание отменено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ ГЛАВНОЕ МЕНЮ", callback_data="back_to_main")]
            ])
        )
    
    return ConversationHandler.END

# ========== ПОКАЗ ГРУПП ==========
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
                [InlineKeyboardButton("⬅️ ГЛАВНОЕ МЕНЮ", callback_data="back_to_main")]
            ])
        )
        return
    
    text = "📋 ВАШИ ГРУППЫ:\n\n"
    
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ?",
            (group[0],)
        )[0]
        
        text += f"🏢 <b>{group[1]}</b>\n"
        text += f"   🔑 ID: <code>{group[0]}</code>\n"
        text += f"   👤 Организатор: {group[3]}\n"
        text += f"   💰 Бюджет: {group[4]}\n"
        text += f"   👥 Участников: {participants}/{group[5]}\n"
        text += f"   📅 Рег. до: {group[6]}\n"
        text += f"   📅 Создана: {group[8]}\n\n"
    
    keyboard = [
        [InlineKeyboardButton("⚙️ УПРАВЛЕНИЕ ГРУППАМИ", callback_data="manage_groups")],
        [InlineKeyboardButton("➕ СОЗДАТЬ ГРУППУ", callback_data="create_group")],
        [InlineKeyboardButton("⬅️ ГЛАВНОЕ МЕНЮ", callback_data="back_to_main")]
    ]
    
    await query.edit_message_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_group_details(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Показать детали группы"""
    query = update.callback_query
    await query.answer()
    
    group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
    
    if not group:
        await query.edit_message_text("❌ Группа не найдена!")
        return
    
    participants = db_fetchall(
        "SELECT * FROM participants WHERE group_id = ? ORDER BY registered_at DESC",
        (group_id,)
    )
    
    text = f"🏢 <b>{group[1]}</b>\n\n"
    text += f"🔑 ID: <code>{group[0]}</code>\n"
    text += f"👤 Организатор: {group[3]}\n"
    text += f"💰 Бюджет: {group[4]}\n"
    text += f"👥 Макс. участников: {group[5]}\n"
    text += f"📅 Рег. до: {group[6]}\n"
    text += f"📅 Создана: {group[8]}\n\n"
    
    if participants:
        text += f"👥 УЧАСТНИКИ ({len(participants)}):\n"
        for idx, participant in enumerate(participants, 1):
            text += f"{idx}. {participant[4]} (@{participant[2] or 'без username'})\n"
    else:
        text += "👥 Участников пока нет\n"
    
    bot = await context.bot.get_me()
    invite_link = f"t.me/{bot.username}?start={group_id}"
    
    keyboard = [
        [InlineKeyboardButton("🔗 ССЫЛКА ДЛЯ ПРИГЛАШЕНИЯ", callback_data=f"copy_link_{group_id}")],
        [InlineKeyboardButton("🗑 УДАЛИТЬ ГРУППУ", callback_data=f"delete_group_{group_id}")],
        [InlineKeyboardButton("📋 ВСЕ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("⬅️ ГЛАВНОЕ МЕНЮ", callback_data="back_to_main")]
    ]
    
    await query.edit_message_text(
        text + f"\n🔗 Ссылка: {invite_link}",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== УПРАВЛЕНИЕ ГРУППАМИ ==========
async def show_manage_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Управление группами"""
    query = update.callback_query
    await query.answer()
    
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = ? ORDER BY created_at DESC",
        (ADMIN_ID,)
    )
    
    if not groups:
        keyboard = [
            [InlineKeyboardButton("➕ СОЗДАТЬ ГРУППУ", callback_data="create_group")],
            [InlineKeyboardButton("⬅️ ГЛАВНОЕ МЕНЮ", callback_data="back_to_main")]
        ]
        await query.edit_message_text(
            "📭 У вас пока нет групп для управления.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    text = "⚙️ ВЫБЕРИТЕ ГРУППУ ДЛЯ УПРАВЛЕНИЯ:\n\n"
    buttons = []
    
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ?",
            (group[0],)
        )[0]
        
        display_name = f"{group[1][:20]}{'...' if len(group[1]) > 20 else ''}"
        buttons.append([
            InlineKeyboardButton(
                f"🏢 {display_name} ({participants}/{group[5]})", 
                callback_data=f"group_details_{group[0]}"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton("➕ СОЗДАТЬ ГРУППУ", callback_data="create_group"),
        InlineKeyboardButton("📋 ВСЕ ГРУППЫ", callback_data="my_groups")
    ])
    buttons.append([InlineKeyboardButton("⬅️ ГЛАВНОЕ МЕНЮ", callback_data="back_to_main")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def delete_group_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Подтверждение удаления"""
    query = update.callback_query
    await query.answer()
    
    group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
    
    if not group:
        await query.edit_message_text("❌ Группа не найдена!")
        return
    
    participants = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE group_id = ?",
        (group_id,)
    )[0]
    
    keyboard = [
        [InlineKeyboardButton("✅ ДА, УДАЛИТЬ БЕЗВОЗВРАТНО", callback_data=f"confirm_delete_{group_id}")],
        [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data=f"group_details_{group_id}")]
    ]
    
    await query.edit_message_text(
        f"⚠️ <b>ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ</b>\n\n"
        f"🏢 Группа: {group[1]}\n"
        f"👥 Участников: {participants}\n"
        f"💰 Бюджет: {group[4]}\n\n"
        f"<b>УДАЛИТЬ ГРУППУ И ВСЕХ УЧАСТНИКОВ?</b>\n"
        f"Это действие необратимо!",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def delete_group(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Удаление группы"""
    query = update.callback_query
    await query.answer()
    
    # Удаляем сначала участников, потом группу
    db_execute("DELETE FROM participants WHERE group_id = ?", (group_id,))
    db_execute("DELETE FROM groups WHERE id = ?", (group_id,))
    
    keyboard = [
        [InlineKeyboardButton("⚙️ УПРАВЛЕНИЕ ГРУППАМИ", callback_data="manage_groups")],
        [InlineKeyboardButton("📋 ВСЕ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("⬅️ ГЛАВНОЕ МЕНЮ", callback_data="back_to_main")]
    ]
    
    await query.edit_message_text(
        "✅ Группа и все участники удалены!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def copy_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Копирование ссылки"""
    query = update.callback_query
    await query.answer()
    
    bot = await context.bot.get_me()
    invite_link = f"t.me/{bot.username}?start={group_id}"
    
    # Показываем ссылку в сообщении и в оповещении
    await query.edit_message_text(
        f"🔗 ССЫЛКА ДЛЯ ПРИГЛАШЕНИЯ:\n\n"
        f"<code>{invite_link}</code>\n\n"
        f"Отправьте эту ссылку участникам группы!",
        parse_mode='HTML'
    )
    
    # Также показываем в оповещении для быстрого копирования
    await query.answer(f"Ссылка скопирована в чат!\n{invite_link}", show_alert=True)
    
    # Кнопка для возврата
    keyboard = [
        [InlineKeyboardButton("📋 ВСЕ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("⬅️ ГЛАВНОЕ МЕНЮ", callback_data="back_to_main")]
    ]
    await query.message.reply_text(
        "Выберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== СТАТИСТИКА ==========
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика"""
    query = update.callback_query
    await query.answer()
    
    # Статистика по группам
    groups_count = db_fetchone(
        "SELECT COUNT(*) FROM groups WHERE admin_id = ?", 
        (ADMIN_ID,)
    )[0]
    
    active_groups = db_fetchone(
        "SELECT COUNT(*) FROM groups WHERE admin_id = ? AND status = 'active'",
        (ADMIN_ID,)
    )[0]
    
    # Статистика по участникам
    total_participants = db_fetchone("SELECT COUNT(*) FROM participants")[0]
    
    # Участники по группам
    participants_by_group = db_fetchall('''
        SELECT g.name, COUNT(p.id) as count 
        FROM groups g 
        LEFT JOIN participants p ON g.id = p.group_id 
        WHERE g.admin_id = ?
        GROUP BY g.id
        ORDER BY count DESC
    ''', (ADMIN_ID,))
    
    text = (
        f"📊 <b>СТАТИСТИКА</b>\n\n"
        f"<b>Общая статистика:</b>\n"
        f"• Всего групп: {groups_count}\n"
        f"• Активных групп: {active_groups}\n"
        f"• Всего участников: {total_participants}\n\n"
    )
    
    if participants_by_group:
        text += "<b>Участники по группам:</b>\n"
        for group_name, count in participants_by_group:
            text += f"• {group_name[:20]}: {count} чел.\n"
    
    keyboard = [
        [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("⚙️ УПРАВЛЕНИЕ", callback_data="manage_groups")],
        [InlineKeyboardButton("⬅️ ГЛАВНОЕ МЕНЮ", callback_data="back_to_main")]
    ]
    
    await query.edit_message_text(
        text,
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
        if data == "my_groups":
            await show_my_groups(update, context)
        elif data == "create_group":
            await create_group_start(update, context)
        elif data == "manage_groups":
            await show_manage_groups(update, context)
        elif data == "stats":
            await show_stats(update, context)
        elif data == "refresh":
            await show_main_menu_admin(update, context)
        elif data == "back_to_main":
            await back_to_main_handler(update, context)
        elif data.startswith("group_details_"):
            group_id = data.split("_")[2]
            await show_group_details(update, context, group_id)
        elif data.startswith("copy_link_"):
            group_id = data.split("_")[2]
            await copy_link_handler(update, context, group_id)
        elif data.startswith("delete_group_"):
            group_id = data.split("_")[2]
            await delete_group_confirmation(update, context, group_id)
        elif data.startswith("confirm_delete_"):
            group_id = data.split("_")[2]
            await delete_group(update, context, group_id)
        else:
            await query.edit_message_text("❌ Неизвестная команда")
            
    except Exception as e:
        logger.error(f"Ошибка в обработчике кнопок: {e}")
        await query.edit_message_text(
            "❌ Произошла ошибка. Попробуйте снова.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ ГЛАВНОЕ МЕНЮ", callback_data="back_to_main")]
            ])
        )

# ========== ОБРАБОТЧИК СООБЩЕНИЙ ==========
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user = update.effective_user
    
    if user.id == ADMIN_ID and not update.callback_query:
        # Если админ отправил текст без команды, показываем меню
        await show_main_menu_admin(update, context)
    elif 'registration_group' in context.user_data:
        # Обработка регистрации участника
        await handle_participant_registration(update, context)
    else:
        await update.message.reply_text(
            "Используйте команду /start для начала работы."
        )

async def handle_participant_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка регистрации участника (упрощенная версия)"""
    user = update.effective_user
    step = context.user_data.get('registration_step', 1)
    group_id = context.user_data['registration_group']
    
    if step == 1:
        # ФИО
        context.user_data['full_name'] = update.message.text
        context.user_data['registration_step'] = 2
        await update.message.reply_text(
            "✅ ФИО сохранено!\n\n"
            "Шаг 2 из 5\n"
            "Введите ваш никнейм (как к вам обращаться):\n"
            "Пример: 'Сашенька', 'Коллега', 'Аноним'"
        )
    elif step == 2:
        # Никнейм
        context.user_data['nickname'] = update.message.text
        context.user_data['registration_step'] = 3
        await update.message.reply_text(
            "✅ Никнейм сохранён!\n\n"
            "Шаг 3 из 5\n"
            "Введите адрес ПВЗ для получения подарка:\n"
            "Пример: 'СДЭК, Москва, ул. Ленина 1, пункт выдачи №123'"
        )
    elif step == 3:
        # Адрес ПВЗ
        context.user_data['pvz_address'] = update.message.text
        context.user_data['registration_step'] = 4
        await update.message.reply_text(
            "✅ Адрес ПВЗ сохранён!\n\n"
            "Шаг 4 из 5\n"
            "Введите почтовый адрес (если нужна доставка почтой):\n"
            "Или напишите 'нет', если не нужна почтовая доставка"
        )
    elif step == 4:
        # Почтовый адрес
        context.user_data['postal_address'] = update.message.text
        context.user_data['registration_step'] = 5
        await update.message.reply_text(
            "✅ Адрес сохранён!\n\n"
            "Шаг 5 из 5\n"
            "Введите ваш вишлист (что бы вы хотели получить):\n"
            "Пример: 'Книги, шоколад, настолки'"
        )
    elif step == 5:
        # Вишлист
        wishlist = update.message.text
        
        # Сохраняем участника в БД
        db_execute(
            '''INSERT INTO participants 
               (user_id, username, group_id, full_name, nickname, pvz_address, postal_address, wishlist)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (user.id, user.username, group_id,
             context.user_data['full_name'], context.user_data['nickname'],
             context.user_data['pvz_address'], context.user_data['postal_address'],
             wishlist)
        )
        
        # Очищаем временные данные
        context.user_data.pop('registration_group', None)
        context.user_data.pop('registration_step', None)
        context.user_data.pop('full_name', None)
        context.user_data.pop('nickname', None)
        context.user_data.pop('pvz_address', None)
        context.user_data.pop('postal_address', None)
        
        # Получаем информацию о группе
        group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
        
        await update.message.reply_text(
            f"🎉 <b>РЕГИСТРАЦИЯ УСПЕШНА!</b>\n\n"
            f"Вы зарегистрированы в группе:\n"
            f"<b>{group[1]}</b>\n\n"
            f"💰 Бюджет: {group[4]}\n"
            f"📅 Регистрация до: {group[6]}\n\n"
            f"Ожидайте жеребьевки!",
            parse_mode='HTML'
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
