import os
import logging
import sqlite3
import uuid
import threading
import random
from datetime import datetime
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
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
                  gift_sent BOOLEAN DEFAULT 0,
                  sent_date TEXT,
                  tracking_number TEXT,
                  gift_status TEXT DEFAULT 'not_sent',
                  confirmed BOOLEAN DEFAULT 1,
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
                    f"❌ Регистрация в группе '{group[1]}' завершена.\nЖеребьевка уже проведена.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
                
            existing = db_fetchone(
                "SELECT * FROM participants WHERE user_id = ? AND group_id = ?",
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
    """Главное меню с КЛАВИАТУРНЫМИ кнопками"""
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
        "SELECT * FROM groups WHERE admin_id = ? ORDER BY created_at DESC",
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
    
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1",
            (group[0],)
        )[0]
        
        sent_gifts = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ? AND gift_sent = 1",
            (group[0],)
        )[0]
        
        # Получаем ссылку для приглашения
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
    
    keyboard = [
        ["👥 УЧАСТНИКИ"],
        ["🎁 КТО КОМУ ДАРИТ"],
        ["📦 СТАТУС ОТПРАВКИ"],
        ["➕ СОЗДАТЬ ГРУППУ"],
        ["⬅️ НАЗАД"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=reply_markup
        )

# ========== СПИСОК УЧАСТНИКОВ ==========
async def show_participants_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню участников"""
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = ? ORDER BY created_at DESC",
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
            "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1",
            (group[0],)
        )[0]
        
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
    """Показать участников выбранной группы"""
    text = update.message.text
    
    if text.startswith("👥 "):
        group_name_part = text[2:].split(" (")[0].strip()
    else:
        group_name_part = text
    
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = ? AND name LIKE ?",
        (ADMIN_ID, f"%{group_name_part}%")
    )
    
    if not groups:
        await update.message.reply_text("❌ Группа не найдена.")
        return
    
    group = groups[0]
    group_id = group[0]
    
    participants = db_fetchall(
        "SELECT * FROM participants WHERE group_id = ? AND confirmed = 1 ORDER BY registered_at DESC",
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
        gift_status = "✅" if participant[12] == 1 else "❌"
        username = f"@{participant[2]}" if participant[2] else "нет username"
        
        text += f"<b>{idx}. {participant[4]}</b> {gift_status}\n"
        text += f"   🎭 Никнейм: {participant[5]}\n"
        text += f"   📱 {username}\n"
        
        if participant[9]:  # Если есть кому дарит
            receiver = db_fetchone(
                "SELECT full_name FROM participants WHERE id = ?",
                (participant[9],)
            )
            if receiver:
                text += f"   🎅 Дарит: {receiver[0]}\n"
        
        text += "\n"
        
        # Создаем кнопку для деталей
        button_text = f"ℹ️ {participant[4][:15]}{'...' if len(participant[4]) > 15 else ''}"
        keyboard.append([button_text])
    
    keyboard.append(["👥 УЧАСТНИКИ"])
    keyboard.append(["⬅️ НАЗАД"])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Сохраняем ID группы для деталей участников
    context.user_data['participants_group'] = group_id
    
    await update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )

async def show_participant_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать детальную информацию об участнике"""
    text = update.message.text
    
    if not text.startswith("ℹ️ "):
        return
    
    participant_name_part = text[2:].strip()
    
    if 'participants_group' not in context.user_data:
        await update.message.reply_text("❌ Ошибка: группа не выбрана.")
        return
    
    group_id = context.user_data['participants_group']
    
    participants = db_fetchall(
        "SELECT * FROM participants WHERE group_id = ? AND confirmed = 1 AND full_name LIKE ?",
        (group_id, f"%{participant_name_part}%")
    )
    
    if not participants:
        await update.message.reply_text("❌ Участник не найден.")
        return
    
    participant = participants[0]
    group = db_fetchone("SELECT name, budget FROM groups WHERE id = ?", (group_id,))
    
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
    
    # Статус отправки подарка
    gift_status = "✅ ОТПРАВЛЕН" if participant[12] == 1 else "❌ НЕ ОТПРАВЛЕН"
    text += f"📦 СТАТУС ПОДАРКА: {gift_status}\n"
    
    if participant[12] == 1:
        text += f"📅 Дата отправки: {participant[13] or 'не указана'}\n"
        text += f"🚚 Трек-номер: {participant[14] or 'нет'}\n\n"
    
    # Если жеребьевка проведена, показываем кому дарит
    if participant[9]:  # giver_to
        receiver = db_fetchone(
            "SELECT full_name, nickname, pvz_address FROM participants WHERE id = ?",
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
    """Меню результатов жеребьевки (кто кому дарит)"""
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = ? AND draw_status = 'completed' ORDER BY created_at DESC",
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
    
    text = "🎁 ВЫБЕРИТЕ ГРУППУ ДЛЯ ПРОСМОТРА РЕЗУЛЬТАТОВ ЖЕРЕБЬЁВКИ:\n\n"
    
    keyboard = []
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1 AND giver_to IS NOT NULL",
            (group[0],)
        )[0]
        
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
    """Показать кто кому дарит в выбранной группе"""
    text = update.message.text
    
    if text.startswith("🎁 "):
        group_name_part = text[2:].split(" (")[0].strip()
    else:
        group_name_part = text
    
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = ? AND name LIKE ? AND draw_status = 'completed'",
        (ADMIN_ID, f"%{group_name_part}%")
    )
    
    if not groups:
        await update.message.reply_text("❌ Группа не найдена или жеребьевка не проведена.")
        return
    
    group = groups[0]
    group_id = group[0]
    
    participants = db_fetchall('''
        SELECT p1.full_name as giver_name, p1.nickname as giver_nickname,
               p2.full_name as receiver_name, p2.nickname as receiver_nickname,
               p1.gift_sent, p1.sent_date
        FROM participants p1
        JOIN participants p2 ON p1.giver_to = p2.id
        WHERE p1.group_id = ? AND p1.confirmed = 1 AND p1.giver_to IS NOT NULL
        ORDER BY p1.full_name
    ''', (group_id,))
    
    if not participants:
        keyboard = [["🎁 КТО КОМУ ДАРИТ"], ["⬅️ НАЗАД"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"🎁 В группе '{group[1]}' нет данных о жеребьевке.",
            reply_markup=reply_markup
        )
        return
    
    text = f"🎅 <b>РЕЗУЛЬТАТЫ ЖЕРЕБЬЁВКИ: {group[1]}</b>\n\n"
    text += f"💰 Бюджет: {group[4]}\n"
    text += f"👥 Участников: {len(participants)}\n\n"
    
    sent_count = sum(1 for p in participants if p[4] == 1)
    text += f"📦 Отправлено подарков: {sent_count}/{len(participants)}\n\n"
    
    for idx, (giver_name, giver_nick, receiver_name, receiver_nick, gift_sent, sent_date) in enumerate(participants, 1):
        gift_status = "✅" if gift_sent == 1 else "❌"
        date_info = f"\n   📅 {sent_date}" if sent_date else ""
        
        text += f"<b>{idx}. {giver_name}</b> {gift_status}\n"
        text += f"   🎭 {giver_nick}\n"
        text += f"   ↓ дарит подарок ↓\n"
        text += f"   👤 {receiver_name}\n"
        text += f"   🎭 {receiver_nick}{date_info}\n\n"
    
    # Также покажем участников без пар (на всякий случай)
    solo_participants = db_fetchall(
        "SELECT full_name, nickname FROM participants WHERE group_id = ? AND confirmed = 1 AND giver_to IS NULL",
        (group_id,)
    )
    
    if solo_participants:
        text += f"<b>👤 УЧАСТНИКИ БЕЗ ПАРЫ:</b>\n"
        for full_name, nickname in solo_participants:
            text += f"• {full_name} ({nickname})\n"
    
    keyboard = [
        ["📦 СТАТУС ОТПРАВКИ"],
        ["👥 УЧАСТНИКИ ЭТОЙ ГРУППЫ"],
        ["🎁 КТО КОМУ ДАРИТ"],
        ["⬅️ НАЗАД"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Сохраняем ID группы
    context.user_data['draw_results_group'] = group_id
    
    await update.message.reply_text(
        text,
        parse_mode='HTML',
        reply_markup=reply_markup
    )

# ========== СТАТУС ОТПРАВКИ ПОДАРКОВ ==========
async def show_gift_status_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню статуса отправки подарков"""
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = ? AND draw_status = 'completed' ORDER BY created_at DESC",
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
    
    text = "📦 ВЫБЕРИТЕ ГРУППУ ДЛЯ ПРОСМОТРА СТАТУСА ОТПРАВКИ:\n\n"
    
    keyboard = []
    for group in groups:
        participants = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1",
            (group[0],)
        )[0]
        
        sent_gifts = db_fetchone(
            "SELECT COUNT(*) FROM participants WHERE group_id = ? AND gift_sent = 1",
            (group[0],)
        )[0]
        
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
    """Показать статус отправки подарков в группе"""
    text = update.message.text
    
    if text.startswith("📦 "):
        group_name_part = text[2:].split(" (")[0].strip()
    else:
        group_name_part = text
    
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = ? AND name LIKE ? AND draw_status = 'completed'",
        (ADMIN_ID, f"%{group_name_part}%")
    )
    
    if not groups:
        await update.message.reply_text("❌ Группа не найдена.")
        return
    
    group = groups[0]
    group_id = group[0]
    
    # Получаем все пары с статусом отправки
    pairs = db_fetchall('''
        SELECT p1.full_name as giver, p1.nickname as giver_nick,
               p2.full_name as receiver, p2.nickname as receiver_nick,
               p1.gift_sent, p1.sent_date, p1.tracking_number
        FROM participants p1
        JOIN participants p2 ON p1.giver_to = p2.id
        WHERE p1.group_id = ? AND p1.confirmed = 1
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
    
    sent_count = sum(1 for p in pairs if p[4] == 1)
    total_count = len(pairs)
    
    text = f"📦 <b>СТАТУС ОТПРАВКИ ПОДАРКОВ: {group[1]}</b>\n\n"
    text += f"💰 Бюджет: {group[4]}\n"
    text += f"📅 Регистрация до: {group[6]}\n\n"
    text += f"📊 СТАТИСТИКА:\n"
    text += f"• Всего участников: {total_count}\n"
    text += f"• ✅ Отправлено: {sent_count} ({sent_count/total_count*100:.0f}%)\n"
    text += f"• ❌ Не отправлено: {total_count - sent_count}\n\n"
    
    text += f"<b>✅ ОТПРАВЛЕНЫ ({sent_count}):</b>\n"
    sent_shown = 0
    for giver, giver_nick, receiver, receiver_nick, gift_sent, sent_date, tracking in pairs:
        if gift_sent == 1:
            sent_shown += 1
            if sent_shown <= 10:  # Показываем только первые 10
                date_info = f" ({sent_date})" if sent_date else ""
                track_info = f"\n   🚚 Трек: {tracking}" if tracking else ""
                text += f"{sent_shown}. {giver} → {receiver}{date_info}{track_info}\n"
    
    if sent_shown > 10:
        text += f"... и ещё {sent_shown - 10} отправленных\n\n"
    else:
        text += "\n"
    
    text += f"<b>❌ НЕ ОТПРАВЛЕНЫ ({total_count - sent_count}):</b>\n"
    not_sent_shown = 0
    for giver, giver_nick, receiver, receiver_nick, gift_sent, sent_date, tracking in pairs:
        if gift_sent == 0:
            not_sent_shown += 1
            if not_sent_shown <= 10:  # Показываем только первые 10
                text += f"{not_sent_shown}. {giver} → {receiver}\n"
    
    if not_sent_shown > 10:
        text += f"... и ещё {not_sent_shown - 10} не отправленных\n"
    
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
        "SELECT * FROM groups WHERE admin_id = ? AND draw_status = 'pending' ORDER BY created_at DESC",
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
            "SELECT COUNT(*) FROM participants WHERE group_id = ? AND confirmed = 1",
            (group[0],)
        )[0]
        
        if participants >= 3:
            button_text = f"✅ {group[1][:20]}{'...' if len(group[1]) > 20 else ''} ({participants})"
            callback_data = f"draw_{group[0]}"
        else:
            button_text = f"❌ {group[1][:20]}... ({participants}/3)"
            callback_data = f"info_{group[0]}"
        
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
        group_name_part = text[2:].split(" (")[0].strip()
    else:
        group_name_part = text
    
    groups = db_fetchall(
        "SELECT * FROM groups WHERE admin_id = ? AND name LIKE ? AND draw_status = 'pending'",
        (ADMIN_ID, f"%{group_name_part}%")
    )
    
    if not groups:
        await update.message.reply_text("❌ Группа не найдена.")
        return
    
    group = groups[0]
    group_id = group[0]
    
    participants = db_fetchall(
        "SELECT * FROM participants WHERE group_id = ? AND confirmed = 1",
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
    """Выполнение жеребьевки"""
    if 'draw_group' not in context.user_data:
        await update.message.reply_text("❌ Ошибка: группа не выбрана.")
        return
    
    group_id = context.user_data['draw_group']
    group = db_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
    
    if not group:
        await update.message.reply_text("❌ Группа не найдена!")
        return
    
    participants = db_fetchall(
        "SELECT id, user_id, full_name, nickname, wishlist FROM participants WHERE group_id = ? AND confirmed = 1",
        (group_id,)
    )
    
    if len(participants) < 3:
        await update.message.reply_text(
            "❌ Недостаточно участников для жеребьевки!",
            reply_markup=ReplyKeyboardMarkup([["⬅️ НАЗАД"]], resize_keyboard=True)
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
    # Общая статистика
    groups_count = db_fetchone(
        "SELECT COUNT(*) FROM groups WHERE admin_id = ?", 
        (ADMIN_ID,)
    )[0]
    
    participants_count = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE confirmed = 1"
    )[0]
    
    completed_draws = db_fetchone(
        "SELECT COUNT(*) FROM groups WHERE admin_id = ? AND draw_status = 'completed'",
        (ADMIN_ID,)
    )[0]
    
    sent_gifts = db_fetchone(
        "SELECT COUNT(*) FROM participants WHERE gift_sent = 1"
    )[0]
    
    # Статистика по группам
    groups_stats = db_fetchall('''
        SELECT g.name, 
               COUNT(p.id) as total,
               SUM(CASE WHEN p.gift_sent = 1 THEN 1 ELSE 0 END) as sent,
               g.draw_status
        FROM groups g
        LEFT JOIN participants p ON g.id = p.group_id AND p.confirmed = 1
        WHERE g.admin_id = ?
        GROUP BY g.id
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
    text += f"• Бот работает на Render 24/7\n"
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
            "🎭 Введите ваш никнейм:\nПример: 'Сашенька', 'Коллега'",
            reply_markup=ReplyKeyboardRemove()
        )
    
    elif step == 2:
        reg_data['nickname'] = text
        reg_data['step'] = 3
        await update.message.reply_text(
            "✅ Никнейм сохранён!\n\nШаг 3 из 5\n"
            "📦 Введите адрес ПВЗ:\nПример: 'СДЭК, Москва, ул. Ленина 1'",
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
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (reg_data['user_id'], reg_data['username'], reg_data['group_id'],
             reg_data['full_name'], reg_data['nickname'],
             reg_data['pvz_address'], reg_data['postal_address'],
             reg_data['wishlist'], 1)
        )
        
        group = db_fetchone("SELECT name FROM groups WHERE id = ?", (reg_data['group_id'],))
        
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
    """Начало создания группы"""
    await update.message.reply_text(
        "🏢 СОЗДАНИЕ НОВОЙ ГРУППЫ\n\n"
        "Шаг 1 из 5\n"
        "Введите название группы:\n"
        "Пример: 'Офис Альфа-Банк 2024'",
        reply_markup=ReplyKeyboardRemove()
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
        "Пример: 'Анна Петрова, @anna_hr, +79991234567'",
        reply_markup=ReplyKeyboardRemove()
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
        "• '1500-2000 ₽'",
        reply_markup=ReplyKeyboardRemove()
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
        "(Можно от 3 до 100 человек)",
        reply_markup=ReplyKeyboardRemove()
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
        "• 'до 20 декабря'",
        reply_markup=ReplyKeyboardRemove()
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
    
    keyboard = [["✅ ДА, СОЗДАТЬ"], ["❌ НЕТ, ОТМЕНА"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(summary, reply_markup=reply_markup)
    
    return CONFIRM_CREATION

async def confirm_group_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение создания группы"""
    text = update.message.text
    
    if text == "✅ ДА, СОЗДАТЬ":
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

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def show_group_participants_from_draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать участников группы из меню результатов"""
    if 'draw_results_group' in context.user_data:
        group_id = context.user_data['draw_results_group']
        group = db_fetchone("SELECT name FROM groups WHERE id = ?", (group_id,))
        
        if group:
            participants = db_fetchall(
                "SELECT * FROM participants WHERE group_id = ? AND confirmed = 1 ORDER BY registered_at DESC",
                (group_id,)
            )
            
            if participants:
                text = f"👥 <b>УЧАСТНИКИ ГРУППЫ: {group[0]}</b>\n\n"
                text += f"📊 Всего участников: {len(participants)}\n\n"
                
                for idx, participant in enumerate(participants, 1):
                    gift_status = "✅" if participant[12] == 1 else "❌"
                    username = f"@{participant[2]}" if participant[2] else "нет username"
                    
                    text += f"<b>{idx}. {participant[4]}</b> {gift_status}\n"
                    text += f"   🎭 Никнейм: {participant[5]}\n"
                    text += f"   📱 {username}\n"
                    
                    if participant[9]:  # Если есть кому дарит
                        receiver = db_fetchone(
                            "SELECT full_name FROM participants WHERE id = ?",
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

# ========== ОБРАБОТЧИК ТЕКСТОВЫХ КОМАНД ==========
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главный обработчик текстовых сообщений"""
    text = update.message.text
    
    # Если идет регистрация
    if 'registration' in context.user_data:
        await handle_registration_step(update, context)
        return
    
    # Если идет создание группы
    if 'new_group' in context.user_data:
        # Обрабатывается ConversationHandler
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
    
    # Специальные команды
    elif text == "✅ ДА, ЗАПУСТИТЬ":
        await execute_draw(update, context)
    
    elif text == "❌ НЕТ, ОТМЕНА":
        await show_main_menu(update, context)
    
    elif text == "👥 УЧАСТНИКИ ЭТОЙ ГРУППЫ":
        await show_group_participants_from_draw(update, context)
    
    # Обработка кнопок с группами
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
def run_telegram_bot():
    """Запуск Telegram бота"""
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
    
    # Команда /start
    application.add_handler(CommandHandler("start", start_command))
    
    # Conversation handler для создания группы
    application.add_handler(conv_handler)
    
    # Обработчик всех текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    logger.info("✅ Бот запущен со всеми функциями!")
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
