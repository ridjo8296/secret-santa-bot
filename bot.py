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
        group_id = context.args[0]
        group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
        
        if group:
            if group[8] == 'completed':
                await update.message.reply_text(
                    f"❌ Регистрация в группе '{group[1]}' завершена.\nЖеребьевка уже проведена."
                )
                return
                
            existing = db_fetchone(
                "SELECT * FROM participants WHERE user_id = ? AND group_id = ?",
                (user.id, group_id)
            )
            
            if existing:
                if existing[12] == 1:
                    await update.message.reply_text(
                        f"✅ Вы уже зарегистрированы в группе '{group[1]}'!\nОжидайте жеребьевки."
                    )
                else:
                    await update.message.reply_text(
                        f"⏳ Ваша регистрация в группе '{group[1]}' ожидает подтверждения."
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
                "📝 Введите ваше полное ФИО:\nПример: 'Иванов Иван Иванович'"
            )
        else:
            await update.message.reply_text("❌ Группа не найдена.")
        return
    
    if user.id == ADMIN_ID:
        await show_main_menu(update, context)
    else:
        await update.message.reply_text(
            "🎅 Привет! Я бот для организации Тайного Санты.\n\n"
            "Для участия нужна ссылка-приглашение от организатора."
        )

# ========== ГЛАВНОЕ МЕНЮ ==========
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню с кнопками в панели"""
    keyboard = [
        [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("➕ СОЗДАТЬ ГРУППУ", callback_data="create_group")],
        [InlineKeyboardButton("⚙️ УПРАВЛЕНИЕ", callback_data="manage_groups")],
        [InlineKeyboardButton("🎲 ЗАПУСТИТЬ ЖЕРЕБЬЁВКУ", callback_data="start_draw")],
        [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="stats")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "👑 АДМИН-ПАНЕЛЬ\n\nВыберите действие:"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=text,
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            text=text,
            reply_markup=reply_markup
        )

# ========== ОБРАБОТКА РЕГИСТРАЦИИ ==========
async def handle_registration_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка шагов регистрации участника"""
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
            "🎭 Введите ваш никнейм:\nПример: 'Сашенька', 'Коллега'"
        )
    
    elif step == 2:
        reg_data['nickname'] = text
        reg_data['step'] = 3
        await update.message.reply_text(
            "✅ Никнейм сохранён!\n\nШаг 3 из 5\n"
            "📦 Введите адрес ПВЗ:\nПример: 'СДЭК, Москва, ул. Ленина 1'"
        )
    
    elif step == 3:
        reg_data['pvz_address'] = text
        reg_data['step'] = 4
        await update.message.reply_text(
            "✅ Адрес ПВЗ сохранён!\n\nШаг 4 из 5\n"
            "📮 Введите почтовый адрес:\nИли напишите 'нет'"
        )
    
    elif step == 4:
        reg_data['postal_address'] = text
        reg_data['step'] = 5
        await update.message.reply_text(
            "✅ Адрес сохранён!\n\nШаг 5 из 5\n"
            "🎁 Введите ваш вишлист:\nПример: 'Книги, шоколад, настолки'"
        )
    
    elif step == 5:
        reg_data['wishlist'] = text
        
        db_execute(
            '''INSERT INTO participants 
               (user_id, username, group_id, full_name, nickname, 
                pvz_address, postal_address, wishlist, confirmed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (reg_data['user_id'], reg_data['username'], reg_data['group_id'],
             reg_data['full_name'], reg_data['nickname'],
             reg_data['pvz_address'], reg_data['postal_address'],
             reg_data['wishlist'], 1)  # Автоподтверждение для простоты
        )
        
        group = db_fetchone("SELECT name FROM groups WHERE id = ?", (reg_data['group_id'],))
        
        await update.message.reply_text(
            f"✅ <b>РЕГИСТРАЦИЯ УСПЕШНА!</b>\n\n"
            f"🏢 Группа: {group[0]}\n"
            f"👤 Вы: {reg_data['full_name']}\n"
            f"🎭 Никнейм: {reg_data['nickname']}\n\n"
            f"Ожидайте жеребьевки!",
            parse_mode='HTML'
        )
        
        context.user_data.pop('registration', None)

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
        keyboard = [
            [InlineKeyboardButton("➕ СОЗДАТЬ ГРУППУ", callback_data="create_group")],
            [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")]
        ]
        await query.edit_message_text(
            text="📭 У вас пока нет созданных групп.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    text = "📋 ВАШИ ГРУППЫ:\n\n"
    buttons = []
    
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1",
            (group[0],)
        )[0]
        
        display_name = f"{group[1][:20]}{'...' if len(group[1]) > 20 else ''}"
        button_text = f"🏢 {display_name} ({participants}/{group[5]})"
        
        buttons.append([
            InlineKeyboardButton(button_text, callback_data=f"group_details_{group[0]}")
        ])
    
    buttons.append([
        InlineKeyboardButton("➕ СОЗДАТЬ ГРУППУ", callback_data="create_group"),
        InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")
    ])
    
    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ========== ДЕТАЛИ ГРУППЫ ==========
async def show_group_details(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Показать детали группы"""
    query = update.callback_query
    await query.answer()
    
    group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
    
    if not group:
        await query.edit_message_text("❌ Группа не найдена!")
        return
    
    participants = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1",
        (group_id,)
    )[0]
    
    bot = await context.bot.get_me()
    invite_link = f"t.me/{bot.username}?start={group_id}"
    
    text = (
        f"🏢 <b>ГРУППА: {group[1]}</b>\n\n"
        f"🔑 ID: <code>{group[0]}</code>\n"
        f"👤 Организатор: {group[3]}\n"
        f"💰 Бюджет: {group[4]}\n"
        f"👥 Участников: {participants}/{group[5]}\n"
        f"📅 Рег. до: {group[6]}\n"
        f"🎲 Жеребьевка: {'✅ ПРОВЕДЕНА' if group[8] == 'completed' else '⏳ ОЖИДАЕТ'}\n\n"
        f"🔗 Ссылка для регистрации:\n<code>{invite_link}</code>"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔗 СКОПИРОВАТЬ ССЫЛКУ", callback_data=f"copy_link_{group_id}")],
    ]
    
    if group[8] == 'pending' and participants >= 3:
        keyboard.append([InlineKeyboardButton("🎲 ЗАПУСТИТЬ ЖЕРЕБЬЁВКУ", callback_data=f"start_draw_group_{group_id}")])
    
    keyboard.extend([
        [InlineKeyboardButton("🗑 УДАЛИТЬ ГРУППУ", callback_data=f"delete_group_{group_id}")],
        [InlineKeyboardButton("📋 ВСЕ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")]
    ])
    
    await query.edit_message_text(
        text=text,
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
            [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")]
        ]
        await query.edit_message_text(
            text="📭 У вас пока нет групп для управления.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    text = "⚙️ УПРАВЛЕНИЕ ГРУППАМИ:\n\n"
    buttons = []
    
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1",
            (group[0],)
        )[0]
        
        display_name = f"{group[1][:20]}{'...' if len(group[1]) > 20 else ''}"
        button_text = f"🏢 {display_name} ({participants}/{group[5]})"
        
        buttons.append([
            InlineKeyboardButton(button_text, callback_data=f"group_manage_{group[0]}")
        ])
    
    buttons.append([
        InlineKeyboardButton("➕ СОЗДАТЬ ГРУППУ", callback_data="create_group"),
        InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")
    ])
    
    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def manage_specific_group(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Управление конкретной группой"""
    query = update.callback_query
    await query.answer()
    
    group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
    
    if not group:
        await query.edit_message_text("❌ Группа не найдена!")
        return
    
    participants = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1",
        (group_id,)
    )[0]
    
    bot = await context.bot.get_me()
    invite_link = f"t.me/{bot.username}?start={group_id}"
    
    text = (
        f"⚙️ <b>УПРАВЛЕНИЕ ГРУППОЙ</b>\n\n"
        f"🏢 Группа: {group[1]}\n"
        f"🔑 ID: <code>{group[0]}</code>\n"
        f"💰 Бюджет: {group[4]}\n"
        f"👥 Участников: {participants}/{group[5]}\n"
        f"🎲 Жеребьевка: {'✅ ПРОВЕДЕНА' if group[8] == 'completed' else '⏳ ОЖИДАЕТ'}\n\n"
        f"🔗 Ссылка: <code>{invite_link}</code>"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔗 СКОПИРОВАТЬ ССЫЛКУ", callback_data=f"copy_link_{group_id}")],
        [InlineKeyboardButton("🗑 УДАЛИТЬ ГРУППУ", callback_data=f"delete_confirm_{group_id}")],
        [InlineKeyboardButton("⬅️ НАЗАД", callback_data="manage_groups")]
    ]
    
    await query.edit_message_text(
        text=text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== КОПИРОВАНИЕ ССЫЛКИ ==========
async def copy_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Копирование ссылки"""
    query = update.callback_query
    await query.answer()
    
    bot = await context.bot.get_me()
    invite_link = f"t.me/{bot.username}?start={group_id}"
    
    group = db_fetchone("SELECT name FROM groups WHERE id = ?", (group_id,))
    
    await query.answer(f"Ссылка скопирована!\n{invite_link}", show_alert=True)
    
    keyboard = [
        [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")]
    ]
    
    await query.edit_message_text(
        f"🔗 <b>ССЫЛКА ДЛЯ ПРИГЛАШЕНИЯ</b>\n\n"
        f"🏢 Группа: {group[0]}\n\n"
        f"<code>{invite_link}</code>\n\n"
        f"✅ Ссылка скопирована!",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ========== УДАЛЕНИЕ ГРУППЫ ==========
async def delete_group_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Подтверждение удаления группы"""
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
        [InlineKeyboardButton("✅ ДА, УДАЛИТЬ", callback_data=f"delete_execute_{group_id}")],
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

async def delete_group_execute(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Удаление группы"""
    query = update.callback_query
    await query.answer()
    
    db_execute("DELETE FROM participants WHERE group_id = ?", (group_id,))
    db_execute("DELETE FROM groups WHERE id = ?", (group_id,))
    
    keyboard = [
        [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")]
    ]
    
    await query.edit_message_text(
        "✅ Группа и все участники удалены!",
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
            "🎲 У вас нет групп, ожидающих жеребьевки.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
                [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")]
            ])
        )
        return
    
    text = "🎲 ВЫБЕРИТЕ ГРУППУ ДЛЯ ЖЕРЕБЬЁВКИ:\n\n"
    buttons = []
    
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1",
            (group[0],)
        )[0]
        
        if participants >= 3:
            display_name = f"✅ {group[1][:20]}{'...' if len(group[1]) > 20 else ''}"
            callback = f"draw_confirm_{group[0]}"
        else:
            display_name = f"❌ {group[1][:20]}... ({participants}/3)"
            callback = f"group_details_{group[0]}"
        
        buttons.append([
            InlineKeyboardButton(display_name, callback_data=callback)
        ])
    
    buttons.append([
        InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups"),
        InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")
    ])
    
    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def draw_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: str):
    """Подтверждение жеребьевки"""
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
            f"❌ Недостаточно участников! Нужно минимум 3, а у вас {len(participants)}",
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
        [InlineKeyboardButton("✅ ДА, ЗАПУСТИТЬ", callback_data=f"draw_execute_{group_id}")],
        [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data=f"group_details_{group_id}")]
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
    
    participants = db_fetchall(
        "SELECT id, user_id, full_name, nickname, wishlist FROM participants WHERE group_id = ? AND confirmed = 1",
        (group_id,)
    )
    
    if len(participants) < 3:
        await query.edit_message_text(
            "❌ Недостаточно участников для жеребьевки!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
                [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")]
            ])
        )
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
    
    db_execute("UPDATE groups SET draw_status = 'completed' WHERE id = ?", (group_id,))
    
    group = db_fetchone("SELECT name, budget FROM groups WHERE id = ?", (group_id,))
    
    success_count = 0
    for i, (participant_id, user_id, full_name, nickname, wishlist) in enumerate(participants):
        receiver_id = shuffled_ids[i]
        receiver_info = next(p for p in participants if p[0] == receiver_id)
        
        db_execute(
            "UPDATE participants SET giver_to = ? WHERE id = ?",
            (receiver_id, participant_id)
        )
        
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
            logger.error(f"Ошибка отправки участнику {user_id}: {e}")
    
    await query.edit_message_text(
        f"✅ <b>ЖЕРЕБЬЁВКА ЗАВЕРШЕНА!</b>\n\n"
        f"🏢 Группа: {group[0]}\n"
        f"👥 Участников: {len(participants)}\n"
        f"📨 Уведомлений отправлено: {success_count}/{len(participants)}\n\n"
        f"Все участники получили свои пары!",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
            [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")]
        ])
    )

# ========== СТАТИСТИКА ==========
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика"""
    query = update.callback_query
    await query.answer()
    
    groups_count = db_fetchone(
        "SELECT COUNT(*) FROM groups WHERE admin_id = ?", 
        (ADMIN_ID,)
    )[0]
    
    participants_count = db_fetchone("SELECT COUNT(*) FROM participants WHERE confirmed = 1")[0]
    
    text = (
        f"📊 <b>СТАТИСТИКА</b>\n\n"
        f"• Всего групп: {groups_count}\n"
        f"• Всего участников: {participants_count}\n\n"
        f"Бот работает на Render 24/7"
    )
    
    keyboard = [
        [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
        [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")]
    ]
    
    await query.edit_message_text(
        text,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

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
        
        db_execute(
            '''INSERT INTO groups 
               (id, name, admin_id, organizer, budget, max_participants, reg_deadline)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (group_id, group_data['name'], ADMIN_ID, 
             group_data['organizer'], group_data['budget'],
             group_data['max_participants'], group_data['deadline'])
        )
        
        bot = await context.bot.get_me()
        invite_link = f"t.me/{bot.username}?start={group_id}"
        
        keyboard = [
            [InlineKeyboardButton("📋 МОИ ГРУППЫ", callback_data="my_groups")],
            [InlineKeyboardButton("🔗 СКОПИРОВАТЬ ССЫЛКУ", callback_data=f"copy_link_{group_id}")],
            [InlineKeyboardButton("➕ СОЗДАТЬ ЕЩЁ", callback_data="create_group")],
            [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
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
        await query.edit_message_text(
            "❌ Создание отменено.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")]
            ])
        )
    
    return ConversationHandler.END

# ========== ОБРАБОТЧИК КНОПОК ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главный обработчик кнопок"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    try:
        # Главное меню и навигация
        if data == "back_to_main":
            await show_main_menu(update, context)
        
        # Группы
        elif data == "my_groups":
            await show_my_groups(update, context)
        elif data == "create_group":
            await create_group_start(update, context)
        elif data == "manage_groups":
            await show_manage_groups(update, context)
        elif data.startswith("group_details_"):
            group_id = data.split("_")[2]
            await show_group_details(update, context, group_id)
        elif data.startswith("group_manage_"):
            group_id = data.split("_")[2]
            await manage_specific_group(update, context, group_id)
        
        # Копирование ссылки
        elif data.startswith("copy_link_"):
            group_id = data.split("_")[2]
            await copy_link_handler(update, context, group_id)
        
        # Удаление
        elif data.startswith("delete_group_"):
            group_id = data.split("_")[2]
            await delete_group_confirm(update, context, group_id)
        elif data.startswith("delete_confirm_"):
            group_id = data.split("_")[2]
            await delete_group_confirm(update, context, group_id)
        elif data.startswith("delete_execute_"):
            group_id = data.split("_")[2]
            await delete_group_execute(update, context, group_id)
        
        # Жеребьевка
        elif data == "start_draw":
            await show_draw_menu(update, context)
        elif data.startswith("draw_confirm_"):
            group_id = data.split("_")[2]
            await draw_confirmation(update, context, group_id)
        elif data.startswith("draw_execute_"):
            group_id = data.split("_")[2]
            await execute_draw(update, context, group_id)
        elif data.startswith("start_draw_group_"):
            group_id = data.split("_")[3]
            await draw_confirmation(update, context, group_id)
        
        # Статистика
        elif data == "stats":
            await show_stats(update, context)
        
        # Создание группы (confirm/cancel)
        elif data == "confirm_create" or data == "cancel_create":
            await confirm_group_creation(update, context)
        
        else:
            await query.edit_message_text("❌ Неизвестная команда")
            
    except Exception as e:
        logger.error(f"Ошибка в обработчике кнопок: {e}")
        await query.edit_message_text(
            "❌ Произошла ошибка. Попробуйте снова.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ НАЗАД", callback_data="back_to_main")]
            ])
        )

# ========== ОБРАБОТЧИК СООБЩЕНИЙ ==========
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user = update.effective_user
    
    # Регистрация
    if 'registration' in context.user_data:
        await handle_registration_step(update, context)
        return
    
    # Создание группы
    if 'new_group' in context.user_data:
        # Обрабатывается ConversationHandler
        return
    
    if user.id == ADMIN_ID:
        await show_main_menu(update, context)
    else:
        await update.message.reply_text("Используйте /start")

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
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask сервер запущен на порту 8080")
    
    run_telegram_bot()

if __name__ == '__main__':
    main()
