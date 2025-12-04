import logging
import random
import uuid
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, ConversationHandler,
    filters
)
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8385598413:AAEaIzByLLFL4-Hp_BfbeUxux-v1cDiv4vY')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 6644276942))

# ========== БАЗА ДАННЫХ ==========
Base = declarative_base()

class Group(Base):
    __tablename__ = 'groups_bot2'
    id = Column(String(20), primary_key=True)
    name = Column(String(100), nullable=False)
    admin_id = Column(Integer, nullable=False)
    organizer_name = Column(String(100))
    organizer_contact = Column(String(100))
    budget = Column(String(50))
    max_participants = Column(Integer, default=50)
    reg_deadline = Column(String(50))
    send_deadline = Column(String(50))
    status = Column(String(20), default='registration')
    invite_link = Column(String(200))
    created_at = Column(DateTime, default=datetime.now)
    participants = relationship("Participant", back_populates="group", cascade="all, delete-orphan")

class Participant(Base):
    __tablename__ = 'participants_bot2'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    username = Column(String(100))
    group_id = Column(String(20), ForeignKey('groups_bot2.id'))
    full_name = Column(String(100), nullable=False)
    nickname = Column(String(50), nullable=False)
    pvz_address = Column(Text, nullable=False)
    postal_address = Column(Text)
    wishlist = Column(Text)
    status = Column(String(20), default='registered')
    registered_at = Column(DateTime, default=datetime.now)
    group = relationship("Group", back_populates="participants")
    as_giver = relationship("Pair", foreign_keys="[Pair.giver_id]", back_populates="giver")
    as_receiver = relationship("Pair", foreign_keys="[Pair.receiver_id]", back_populates="receiver")

class Pair(Base):
    __tablename__ = 'pairs_bot2'
    id = Column(Integer, primary_key=True)
    group_id = Column(String(20), ForeignKey('groups_bot2.id'))
    giver_id = Column(Integer, ForeignKey('participants_bot2.id'))
    receiver_id = Column(Integer, ForeignKey('participants_bot2.id'))
    delivery_method = Column(String(20))
    track_number = Column(String(50))
    gift_sent = Column(Boolean, default=False)
    gift_received = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)
    giver = relationship("Participant", foreign_keys=[giver_id], back_populates="as_giver")
    receiver = relationship("Participant", foreign_keys=[receiver_id], back_populates="as_receiver")

class Database:
    def __init__(self, db_url=None):
        if db_url:
            self.engine = create_engine(db_url)
        else:
            self.engine = create_engine('sqlite:///secret_santa.db')
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
    
    def get_session(self):
        return self.Session()
    
    def add_group(self, group_data):
        session = self.get_session()
        try:
            group = Group(**group_data)
            session.add(group)
            session.commit()
            return group.id
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
    
    def add_participant(self, participant_data):
        session = self.get_session()
        try:
            participant = Participant(**participant_data)
            session.add(participant)
            session.commit()
            return participant.id
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
    
    def get_group(self, group_id):
        session = self.get_session()
        try:
            return session.query(Group).filter_by(id=group_id).first()
        finally:
            session.close()
    
    def get_participants_in_group(self, group_id):
        session = self.get_session()
        try:
            return session.query(Participant).filter_by(group_id=group_id).all()
        finally:
            session.close()
    
    def create_pairs(self, group_id, pairs):
        session = self.get_session()
        try:
            for giver_id, receiver_id in pairs:
                pair = Pair(
                    group_id=group_id,
                    giver_id=giver_id,
                    receiver_id=receiver_id
                )
                session.add(pair)
            session.commit()
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()
    
    def get_pairs_for_group(self, group_id):
        session = self.get_session()
        try:
            return session.query(Pair).filter_by(group_id=group_id).all()
        finally:
            session.close()
    
    def get_all_groups(self):
        session = self.get_session()
        try:
            return session.query(Group).all()
        finally:
            session.close()
    
    def delete_group(self, group_id):
        session = self.get_session()
        try:
            group = session.query(Group).filter_by(id=group_id).first()
            if group:
                session.delete(group)
                session.commit()
                return True
            return False
        except Exception as e:
            session.rollback()
            raise e
        finally:
            session.close()

# Для Render используем PostgreSQL, локально SQLite
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
db = Database(DATABASE_URL)

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
        "👑 АДМИН-PANEL\n\n"
        "Вы можете создавать и управлять группами Тайного Санты.",
        reply_markup=reply_markup
    )
    return START

async def show_admin_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    groups = db.get_all_groups()
    
    if not groups:
        await query.edit_message_text("У вас пока нет групп.")
        return
    
    text = "📋 ВАШИ ГРУППЫ:\n\n"
    buttons = []
    
    for group in groups:
        participants = db.get_participants_in_group(group.id)
        text += f"🏢 {group.name}\n"
        text += f"   👥 {len(participants)}/{group.max_participants} участников\n"
        text += f"   📅 Рег. до: {group.reg_deadline}\n"
        text += f"   🔗 Ссылка: t.me/{(await context.bot.get_me()).username}?start={group.id}\n\n"
        
        buttons.append([InlineKeyboardButton(
            f"⚙️ {group.name}", 
            callback_data=f"manage_{group.id}"
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
    
    group = {
        'id': group_id,
        'name': group_data['name'],
        'admin_id': ADMIN_ID,
        'organizer_name': group_data['organizer'],
        'budget': group_data['budget'],
        'reg_deadline': group_data['reg_deadline'],
        'max_participants': max_participants,
        'invite_link': f"https://t.me/{(await context.bot.get_me()).username}?start={group_id}"
    }
    
    db.add_group(group)
    
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

async def manage_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    group_id = query.data.split("_")[1]
    
    group = db.get_group(group_id)
    participants = db.get_participants_in_group(group_id)
    pairs = db.get_pairs_for_group(group_id)
    
    text = f"⚙️ УПРАВЛЕНИЕ ГРУППОЙ\n\n"
    text += f"🏢 {group.name}\n"
    text += f"👤 Организатор: {group.organizer_name}\n"
    text += f"💰 Бюджет: {group.budget}\n"
    text += f"👥 Участников: {len(participants)}/{group.max_participants}\n"
    text += f"📅 Регистрация до: {group.reg_deadline}\n\n"
    
    if pairs:
        text += f"🎲 Жеребьёвка проведена: {len(pairs)} пар\n"
    else:
        text += "🎲 Жеребьёвка ещё не проводилась\n"
    
    buttons = [
        [InlineKeyboardButton("👀 Список участников", callback_data=f"view_part_{group_id}")],
        [InlineKeyboardButton("🎲 Запустить жеребьёвку", callback_data=f"draw_{group_id}")],
        [InlineKeyboardButton("📊 Полный отчёт", callback_data=f"report_{group_id}")],
        [InlineKeyboardButton("🗑 Удалить группу", callback_data=f"delete_{group_id}")],
        [InlineKeyboardButton("⬅️ Назад к списку", callback_data="my_groups")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    return GROUP_MANAGEMENT

async def view_participants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    group_id = query.data.split("_")[2]
    
    participants = db.get_participants_in_group(group_id)
    group = db.get_group(group_id)
    
    text = f"👥 УЧАСТНИКИ ГРУППЫ: {group.name}\n\n"
    
    for i, p in enumerate(participants, 1):
        text += f"{i}. {p.full_name} (@{p.username or 'нет'})\n"
        text += f"   🎭 Ник: {p.nickname}\n"
        text += f"   📍 ПВЗ: {p.pvz_address[:50]}...\n"
        text += f"   🎁 Пожелания: {p.wishlist[:50] if p.wishlist else 'нет'}...\n\n"
    
    buttons = [[InlineKeyboardButton("⬅️ Назад", callback_data=f"manage_{group_id}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    return VIEW_PARTICIPANTS

async def start_draw_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    group_id = query.data.split("_")[1]
    
    participants = db.get_participants_in_group(group_id)
    
    if len(participants) < 3:
        await query.edit_message_text(
            f"❌ Невозможно запустить жеребьёвку!\n"
            f"Нужно минимум 3 участника, а сейчас {len(participants)}."
        )
        return GROUP_MANAGEMENT
    
    context.user_data['draw_group_id'] = group_id
    
    await query.edit_message_text(
        f"🎲 ПОДТВЕРЖДЕНИЕ ЖЕРЕБЬЁВКИ\n\n"
        f"Группа: {db.get_group(group_id).name}\n"
        f"Участников: {len(participants)}\n\n"
        f"После запуска:\n"
        f"1. Все участники получат своих получателей\n"
        f"2. Вам придёт полный отчёт в личку\n"
        f"3. Отменить будет невозможно\n\n"
        f"Запускаем жеребьёвку?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, запустить", callback_data=f"confirm_draw_{group_id}")],
            [InlineKeyboardButton("❌ Нет, отмена", callback_data=f"manage_{group_id}")]
        ])
    )
    return START_DRAW_CONFIRM

async def start_draw_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    group_id = query.data.split("_")[2]
    
    participants = db.get_participants_in_group(group_id)
    group = db.get_group(group_id)
    
    # Случайное распределение
    shuffled = participants.copy()
    random.shuffle(shuffled)
    
    pairs = []
    for i in range(len(shuffled)):
        giver = shuffled[i]
        receiver = shuffled[(i + 1) % len(shuffled)]
        pairs.append((giver.id, receiver.id))
    
    # Сохраняем пары в БД
    db.create_pairs(group_id, pairs)
    
    # Отправляем сообщения участникам
    sent_count = 0
    for giver, receiver in zip(shuffled, shuffled[1:] + shuffled[:1]):
        try:
            await context.bot.send_message(
                chat_id=giver.user_id,
                text=f"🎅 ВЫ ТАЙНЫЙ САНТА ДЛЯ: {receiver.nickname}\n\n"
                     f"👤 ФИО: {receiver.full_name}\n"
                     f"🎭 Ник в игре: {receiver.nickname}\n"
                     f"📍 Адрес ПВЗ: {receiver.pvz_address}\n"
                     f"📫 Почтовый адрес: {receiver.postal_address or 'Не указан'}\n"
                     f"🎁 Пожелания: {receiver.wishlist or 'Не указаны'}\n\n"
                     f"💰 Бюджет: {group.budget}\n"
                     f"📅 Отправьте подарок до: {group.send_deadline or 'не указано'}"
            )
            sent_count += 1
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение {giver.user_id}: {e}")
    
    # Отправляем отчёт админу
    report = f"📊 ОТЧЁТ ПО ЖЕРЕБЬЁВКЕ: {group.name}\n\n"
    report += f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
    report += f"👥 Участников: {len(participants)}\n"
    report += f"✅ Сообщений отправлено: {sent_count}/{len(participants)}\n\n"
    
    report += "🔀 ПАРЫ (даритель → получатель):\n"
    for i, (giver, receiver) in enumerate(zip(shuffled, shuffled[1:] + shuffled[:1]), 1):
        report += f"{i}. @{giver.username or giver.full_name} → @{receiver.username or receiver.full_name}\n"
    
    report += "\n📋 ПОЛНЫЕ ДАННЫХ УЧАСТНИКОВ:\n"
    for i, p in enumerate(participants, 1):
        report += f"\n{i}. {p.full_name} (@{p.username or 'нет'})\n"
        report += f"   Ник: {p.nickname}\n"
        report += f"   ПВЗ: {p.pvz_address}\n"
        if p.postal_address and p.postal_address != "Не указан":
            report += f"   Почта: {p.postal_address}\n"
        if p.wishlist:
            report += f"   Пожелания: {p.wishlist}\n"
    
    # Разбиваем отчёт если слишком длинный
    if len(report) > 4000:
        parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
        for part in parts:
            await context.bot.send_message(chat_id=ADMIN_ID, text=part)
    else:
        await context.bot.send_message(chat_id=ADMIN_ID, text=report)
    
    await query.edit_message_text(
        f"✅ Жеребьёвка завершена!\n\n"
        f"Сообщения отправлены {sent_count} из {len(participants)} участников.\n"
        f"Полный отчёт отправлен вам в личку."
    )
    
    return GROUP_MANAGEMENT

async def get_full_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    group_id = query.data.split("_")[1]
    
    participants = db.get_participants_in_group(group_id)
    group = db.get_group(group_id)
    pairs = db.get_pairs_for_group(group_id)
    
    report = f"📋 ПОЛНЫЙ ОТЧЁТ: {group.name}\n\n"
    
    report += "👥 УЧАСТНИКИ:\n"
    for i, p in enumerate(participants, 1):
        report += f"\n{i}. {p.full_name}\n"
        report += f"   TG: @{p.username or 'нет'}\n"
        report += f"   Ник: {p.nickname}\n"
        report += f"   ПВЗ: {p.pvz_address}\n"
        if p.postal_address and p.postal_address != "Не указан":
            report += f"   Почта: {p.postal_address}\n"
        if p.wishlist:
            report += f"   Пожелания: {p.wishlist}\n"
    
    if pairs:
        report += "\n\n🎲 ПАРЫ ПОСЛЕ ЖЕРЕБЬЁВКИ:\n"
        for i, pair in enumerate(pairs, 1):
            giver = db.get_session().query(Participant).filter_by(id=pair.giver_id).first()
            receiver = db.get_session().query(Participant).filter_by(id=pair.receiver_id).first()
            if giver and receiver:
                report += f"{i}. {giver.full_name} → {receiver.full_name}\n"
    
    # Отправляем админу
    if len(report) > 4000:
        parts = [report[i:i+4000] for i in range(0, len(report), 4000)]
        for part in parts:
            await context.bot.send_message(chat_id=ADMIN_ID, text=part)
        await query.answer("Отчёт отправлен вам в личку!")
    else:
        await context.bot.send_message(chat_id=ADMIN_ID, text=report)
        await query.answer("Отчёт отправлен вам в личку!")
    
    await query.edit_message_text("✅ Полный отчёт отправлен вам в личные сообщения.")

async def delete_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    group_id = query.data.split("_")[1]
    group = db.get_group(group_id)
    
    await query.edit_message_text(
        f"🗑 ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ\n\n"
        f"Группа: {group.name}\n"
        f"Участников: {len(db.get_participants_in_group(group_id))}\n\n"
        f"УДАЛИТЬ ГРУППУ И ВСЕХ УЧАСТНИКОВ?\n"
        f"Это действие необратимо!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_delete_{group_id}")],
            [InlineKeyboardButton("❌ Нет, отмена", callback_data=f"manage_{group_id}")]
        ])
    )

async def confirm_delete_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    group_id = query.data.split("_")[2]
    group = db.get_group(group_id)
    
    if db.delete_group(group_id):
        await query.edit_message_text(f"✅ Группа '{group.name}' удалена.")
    else:
        await query.edit_message_text(f"❌ Ошибка при удалении группы.")

# ========== РЕГИСТРАЦИЯ УЧАСТНИКОВ ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    if args and len(args) > 0:
        # Пришли по ссылке с кодом группы
        group_id = args[0]
        group = db.get_group(group_id)
        
        if not group:
            await update.message.reply_text("❌ Группа не найдена.")
            return
        
        participants = db.get_participants_in_group(group_id)
        
        # Проверяем, не зарегистрирован ли уже
        existing = [p for p in participants if p.user_id == user.id]
        if existing:
            await update.message.reply_text(
                f"✅ Вы уже зарегистрированы в группе '{group.name}'!\n"
                f"Ожидайте начала жеребьёвки."
            )
            return
        
        # Начинаем регистрацию
        context.user_data['reg_group_id'] = group_id
        
        await update.message.reply_text(
            f"🎅 ДОБРО ПОЖАЛОВАТЬ В ГРУППУ!\n\n"
            f"🏢 {group.name}\n"
            f"👤 Организатор: {group.organizer_name}\n"
            f"💰 Бюджет: {group.budget}\n"
            f"👥 Участников: {len(participants)}/{group.max_participants}\n"
            f"📅 Регистрация до: {group.reg_deadline}\n\n"
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
    
    participant_data = {
        'user_id': update.effective_user.id,
        'username': update.effective_user.username,
        'group_id': group_id,
        'full_name': context.user_data['reg_full_name'],
        'nickname': context.user_data['reg_nickname'],
        'pvz_address': context.user_data['reg_pvz_address'],
        'postal_address': context.user_data['reg_postal_address'],
        'wishlist': wishlist
    }
    
    try:
        db.add_participant(participant_data)
        group = db.get_group(group_id)
        participants = db.get_participants_in_group(group_id)
        
        # Уведомляем админа
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"👤 НОВЫЙ УЧАСТНИК В ГРУППЕ '{group.name}':\n"
                 f"Имя: {context.user_data['reg_full_name']}\n"
                 f"Ник: {context.user_data['reg_nickname']}\n"
                 f"Всего участников: {len(participants)}/{group.max_participants}"
        )
        
        await update.message.reply_text(
            f"✅ ВЫ УСПЕШНО ЗАРЕГИСТРИРОВАНЫ!\n\n"
            f"Группа: {group.name}\n"
            f"Ваш никнейм: {context.user_data['reg_nickname']}\n"
            f"👥 Участников: {len(participants)}/{group.max_participants}\n\n"
            f"Ожидайте начала жеребьёвки!"
        )
        
        # Очищаем временные данные
        for key in ['reg_group_id', 'reg_full_name', 'reg_nickname', 'reg_pvz_address', 'reg_postal_address']:
            context.user_data.pop(key, None)
            
    except Exception as e:
        logger.error(f"Ошибка регистрации: {e}")
        await update.message.reply_text("❌ Ошибка при регистрации. Попробуйте снова.")
    
    return ConversationHandler.END

# ========== ОСНОВНОЙ ЗАПУСК ==========
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # ConversationHandler для админа (создание групп)
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
    
    # Обработчики кнопок
    application.add_handler(CallbackQueryHandler(show_admin_groups, pattern="^my_groups$"))
    application.add_handler(CallbackQueryHandler(lambda u,c: admin_panel(u,c), pattern="^back_to_admin$"))
    application.add_handler(CallbackQueryHandler(lambda u,c: admin_panel(u,c), pattern="^stats$"))
    application.add_handler(CallbackQueryHandler(manage_group, pattern="^manage_"))
    application.add_handler(CallbackQueryHandler(view_participants, pattern="^view_part_"))
    application.add_handler(CallbackQueryHandler(start_draw_confirm, pattern="^draw_"))
    application.add_handler(CallbackQueryHandler(start_draw_execute, pattern="^confirm_draw_"))
    application.add_handler(CallbackQueryHandler(get_full_report, pattern="^report_"))
    application.add_handler(CallbackQueryHandler(delete_group, pattern="^delete_"))
    application.add_handler(CallbackQueryHandler(confirm_delete_group, pattern="^confirm_delete_"))
    
    # Добавляем ConversationHandlers
    application.add_handler(admin_conv_handler)
    application.add_handler(reg_conv_handler)
    
    # Команда /admin для админа
    application.add_handler(CommandHandler("admin", admin_panel))
    
    # Запуск бота
    print("✅ Бот запущен! Остановить: Ctrl+C")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
