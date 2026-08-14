import logging
import sqlite3
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# ============================================================
# НАСТРОЙКИ РАЗРАБОТЧИКА
# ============================================================

BOT_TOKEN = "8980714282:AAFwEhIzOMTtz_sGzGcI4IDbomKpgY6P9vg"

# Username разработчика/администратора.
# Только этот пользователь сможет выполнять /addbonus, /reset и т.д.
ADMIN_USERNAME = "mashusha_f"


# ============================================================
# ОБЩИЙ TELEGRAM-ЧАТ
# ============================================================

# Сюда нужно вписать ID общего чата, где находятся:
# Эрик + Маша + бот.
#
# Пример:
# GROUP_CHAT_ID = -1001234567890
#
# Как получить ID чата — объяснение ниже.
GROUP_CHAT_ID = -1001234567890


# ============================================================
# ВРЕМЕННАЯ ЗОНА
# ============================================================

TIMEZONE = ZoneInfo("Europe/Tallinn")


# ============================================================
# БОНУСЫ
# ============================================================

# Бонус за сам факт постановки задачи.
BONUS_FOR_CREATING_TASK = 10


# Бонус за выполнение.
#
# Здесь разработчик может менять суммы.
BONUS_FOR_DEADLINE = {
    "asap": 10,
    "day": 20,
    "week": 50,
    "month": 100,
}


# ============================================================
# ПРИЗЫ
# ============================================================

PRIZES = {
    "поцелуй": 10,
    "рандомная вкусняшка": 100,
    "плацинда": 1000,
}


# ============================================================
# НАЗВАНИЯ СРОКОВ
# ============================================================

DEADLINE_NAMES = {
    "asap": "asap",
    "day": "в течение дня",
    "week": "до конца недели",
    "month": "в течение месяца",
}


# ============================================================
# БАЗА
# ============================================================

DB_FILE = "tasks_bot.db"


def get_db():
    """
    Открывает соединение с SQLite.
    """

    db = sqlite3.connect(DB_FILE)
    db.row_factory = sqlite3.Row

    # Включаем внешние ключи.
    db.execute("PRAGMA foreign_keys = ON")

    return db


def init_db():
    """
    Создаёт все необходимые таблицы.
    """

    db = get_db()

    # --------------------------------------------------------
    # Пользователи
    # --------------------------------------------------------

    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            bonuses INTEGER NOT NULL DEFAULT 0,
            small_tasks_completed INTEGER NOT NULL DEFAULT 0,
            big_tasks_completed INTEGER NOT NULL DEFAULT 0
        )
    """)

    def ensure_user(update: Update):
        user = update.effective_user

        db = get_db()

        db.execute(
            """
            INSERT INTO users (
                telegram_id,
                username,
                first_name
            )
            VALUES (?, ?, ?)

            ON CONFLICT(telegram_id)
            DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (
                user.id,
                user.username,
                user.first_name,
            ),
        )

        db.commit()
        db.close()
    # --------------------------------------------------------
    # Задачи
    # --------------------------------------------------------

    db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            title TEXT NOT NULL,

            creator_username TEXT NOT NULL,

            deadline_type TEXT NOT NULL,

            created_at TEXT NOT NULL,

            expires_at TEXT NOT NULL,

            status TEXT NOT NULL DEFAULT 'active',

            first_reminder_at TEXT,

            last_reminder_at TEXT,

            FOREIGN KEY (creator_username)
                REFERENCES users(username)
        )
    """)

    # --------------------------------------------------------
    # Выполнения задач
    # --------------------------------------------------------

    db.execute("""
        CREATE TABLE IF NOT EXISTS completions (
            task_id INTEGER PRIMARY KEY,

            username TEXT NOT NULL,

            completed_at TEXT NOT NULL,

            source TEXT NOT NULL,

            FOREIGN KEY (task_id)
                REFERENCES tasks(id),

            FOREIGN KEY (username)
                REFERENCES users(username)
        )
    """)

    # --------------------------------------------------------
    # Журнал операций
    # --------------------------------------------------------

    db.execute("""
        CREATE TABLE IF NOT EXISTS operation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            username TEXT,

            operation TEXT NOT NULL,

            amount INTEGER,

            task_id INTEGER,

            description TEXT NOT NULL,

            created_at TEXT NOT NULL
        )
    """)

    # --------------------------------------------------------
    # Пользователи
    # --------------------------------------------------------


# ============================================================
# ВРЕМЯ
# ============================================================

def now():
    """
    Возвращает текущее время в часовом поясе бота.
    """

    return datetime.now(TIMEZONE)


# ============================================================
# ПОЛЬЗОВАТЕЛИ
# ============================================================
def get_user(update: Update):
    """
    Возвращает данные пользователя Telegram.

    Пользователь автоматически считается
    разрешённым, если он взаимодействует с ботом
    в этом чате.
    """

    user = update.effective_user

    return {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }

def get_user_by_username(username):

    db = get_db()

    row = db.execute(
        """
        SELECT *
        FROM users
        WHERE username = ?
        """,
        (username,),
    ).fetchone()

    db.close()

    return row


# ============================================================
# ЖУРНАЛ ОПЕРАЦИЙ
# ============================================================

def log_operation(
    username,
    operation,
    amount=None,
    task_id=None,
    description="",
):
    """
    Записывает действие в журнал.
    """

    db = get_db()

    db.execute(
        """
        INSERT INTO operation_log (
            username,
            operation,
            amount,
            task_id,
            description,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            username,
            operation,
            amount,
            task_id,
            description,
            now().isoformat(),
        ),
    )

    db.commit()
    db.close()


# ============================================================
# БОНУСЫ
# ============================================================

def add_bonuses(
    username,
    amount,
    operation="bonus",
    description="",
    task_id=None,
):
    """
    Начисляет бонусы и одновременно пишет операцию
    в журнал.
    """

    db = get_db()

    db.execute(
        """
        UPDATE users
        SET bonuses = bonuses + ?
        WHERE username = ?
        """,
        (amount, username),
    )

    db.commit()
    db.close()

    log_operation(
        username=username,
        operation=operation,
        amount=amount,
        task_id=task_id,
        description=description,
    )


def subtract_bonuses(
    username,
    amount,
    description="",
):
    """
    Списывает бонусы.

    Возвращает True, если бонусов хватило.
    """

    db = get_db()

    cursor = db.execute(
        """
        UPDATE users
        SET bonuses = bonuses - ?
        WHERE username = ?
        AND bonuses >= ?
        """,
        (
            amount,
            username,
            amount,
        ),
    )

    success = cursor.rowcount == 1

    db.commit()
    db.close()

    if success:
        log_operation(
            username=username,
            operation="spend",
            amount=-amount,
            description=description,
        )

    return success


# ============================================================
# СРОК ЗАДАЧИ
# ============================================================

def calculate_expiration(
    deadline_type,
    created,
):
    """
    Вычисляет дату окончания срока.
    """

    # --------------------------------------------------------
    # ASAP
    # --------------------------------------------------------

    if deadline_type in ("asap", "day"):

        expiration = created.replace(
            hour=1,
            minute=0,
            second=0,
            microsecond=0,
        )

        if expiration <= created:
            expiration += timedelta(days=1)

        return expiration

    # --------------------------------------------------------
    # НЕДЕЛЯ
    # --------------------------------------------------------

    if deadline_type == "week":

        days_until_sunday = (
            6 - created.weekday()
        ) % 7

        expiration = created + timedelta(
            days=days_until_sunday
        )

        expiration = expiration.replace(
            hour=14,
            minute=0,
            second=0,
            microsecond=0,
        )

        if expiration <= created:
            expiration += timedelta(days=7)

        return expiration

    # --------------------------------------------------------
    # МЕСЯЦ
    # --------------------------------------------------------

    if deadline_type == "month":

        return created + relativedelta(
            months=1
        )

    raise ValueError(
        f"Неизвестный тип срока: {deadline_type}"
    )


# ============================================================
# ПЕРВОЕ НАПОМИНАНИЕ
# ============================================================

def calculate_first_reminder(
    deadline_type,
    created,
):
    """
    Вычисляет первое допустимое время напоминания.

    Напоминания разрешены:
        12:00 - 01:00

    Если задача поставлена вне этого периода,
    первое напоминание переносится на 13:00.
    """

    # --------------------------------------------------------
    # Если задача поставлена между 12:00 и 00:59,
    # первое напоминание можно делать по обычной логике.
    # --------------------------------------------------------

    if created.hour >= 12 or created.hour == 0:

        # Если ровно 00:xx — это ещё разрешённый период.
        return created

    # --------------------------------------------------------
    # Если задача поставлена с 01:00 до 11:59,
    # первое напоминание — в 13:00 этого же дня.
    # --------------------------------------------------------

    return created.replace(
        hour=13,
        minute=0,
        second=0,
        microsecond=0,
    )


# ============================================================
# МЕНЮ
# ============================================================

def main_menu():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Добавить задачу",
                callback_data="menu_add",
            )
        ],
        [
            InlineKeyboardButton(
                "Посмотреть задачи",
                callback_data="menu_tasks",
            )
        ],
        [
            InlineKeyboardButton(
                "Аккаунт",
                callback_data="menu_account",
            )
        ],
    ])


def deadline_menu():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "asap",
                callback_data="deadline_asap",
            )
        ],
        [
            InlineKeyboardButton(
                "в течение дня",
                callback_data="deadline_day",
            )
        ],
        [
            InlineKeyboardButton(
                "до конца недели",
                callback_data="deadline_week",
            )
        ],
        [
            InlineKeyboardButton(
                "в течение месяца",
                callback_data="deadline_month",
            )
        ],
    ])


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    known_user = get_known_user(update)

    if known_user is None:

        await update.message.reply_text(
            "Извините, этот бот предназначен "
            "только для зарегистрированных пользователей."
        )

        return

    username, name = known_user

    await update.message.reply_text(
        f"Привет, {name}!\n\n"
        f"Выберите действие:",
        reply_markup=main_menu(),
    )


# ============================================================
# ДОБАВЛЕНИЕ ЗАДАЧИ
# ============================================================

WAITING_FOR_TASK_NAME = 1


async def add_task_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    known_user = get_known_user(update)

    if known_user is None:
        return ConversationHandler.END

    await query.message.reply_text(
        "Введите название задачи:"
    )

    return WAITING_FOR_TASK_NAME


async def receive_task_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    known_user = get_known_user(update)

    if known_user is None:
        return ConversationHandler.END

    task_name = update.message.text.strip()

    if not task_name:

        await update.message.reply_text(
            "Название задачи не может быть пустым."
        )

        return WAITING_FOR_TASK_NAME

    if len(task_name) > 500:

        await update.message.reply_text(
            "Название слишком длинное. "
            "Максимум 500 символов."
        )

        return WAITING_FOR_TASK_NAME

    context.user_data["task_name"] = task_name

    await update.message.reply_text(
        "Выберите срок задачи:",
        reply_markup=deadline_menu(),
    )

    return ConversationHandler.END


async def create_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    known_user = get_known_user(update)

    if known_user is None:
        return

    username, name = known_user

    deadline_type = query.data.replace(
        "deadline_",
        "",
    )

    task_name = context.user_data.get(
        "task_name"
    )

    if not task_name:

        await query.message.reply_text(
            "Не удалось получить название задачи. "
            "Попробуйте ещё раз."
        )

        return

    created = now()

    expires = calculate_expiration(
        deadline_type,
        created,
    )

    first_reminder = calculate_first_reminder(
        deadline_type,
        created,
    )

    # Если первое напоминание оказалось
    # после окончания задачи, оно не нужно.
    if first_reminder >= expires:
        first_reminder = None

    db = get_db()

    cursor = db.execute(
        """
        INSERT INTO tasks (
            title,
            creator_username,
            deadline_type,
            created_at,
            expires_at,
            status,
            first_reminder_at
        )
        VALUES (?, ?, ?, ?, ?, 'active', ?)
        """,
        (
            task_name,
            username,
            deadline_type,
            created.isoformat(),
            expires.isoformat(),
            (
                first_reminder.isoformat()
                if first_reminder
                else None
            ),
        ),
    )

    task_id = cursor.lastrowid

    db.commit()
    db.close()

    # Бонус за создание задачи.
    add_bonuses(
        username,
        BONUS_FOR_CREATING_TASK,
        operation="task_created",
        description=(
            f"Постановка задачи «{task_name}»"
        ),
        task_id=task_id,
    )

    context.user_data.pop(
        "task_name",
        None,
    )

    await query.message.reply_text(
        f"Задача «{task_name}» добавлена.\n\n"
        f"За постановку задачи начислено "
        f"{BONUS_FOR_CREATING_TASK} бонусов.\n\n"
        f"Срок: "
        f"{DEADLINE_NAMES[deadline_type]}\n"
        f"Выполнить до: "
        f"{expires.strftime('%d.%m.%Y %H:%M')}",
        reply_markup=main_menu(),
    )


# ============================================================
# СПИСОК ЗАДАЧ
# ============================================================

def get_active_tasks():

    db = get_db()

    rows = db.execute(
        """
        SELECT *
        FROM tasks
        WHERE status = 'active'
        ORDER BY
            CASE deadline_type
                WHEN 'asap' THEN 1
                WHEN 'day' THEN 2
                WHEN 'week' THEN 3
                WHEN 'month' THEN 4
            END,
            expires_at ASC
        """
    ).fetchall()

    db.close()

    return rows


async def show_tasks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    known_user = get_known_user(update)

    if known_user is None:
        return

    tasks = get_active_tasks()

    if not tasks:

        await query.message.reply_text(
            "Сейчас активных задач нет.",
            reply_markup=main_menu(),
        )

        return

    keyboard = []

    for task in tasks:

        button_text = (
            f"{task['title']} — "
            f"{DEADLINE_NAMES[task['deadline_type']]}"
        )

        keyboard.append([
            InlineKeyboardButton(
                button_text[:60],
                callback_data=f"task_{task['id']}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "Главное меню",
            callback_data="back_menu",
        )
    ])

    await query.message.reply_text(
        "Выберите задачу:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


async def show_single_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    task_id = int(
        query.data.replace(
            "task_",
            "",
        )
    )

    db = get_db()

    task = db.execute(
        """
        SELECT *
        FROM tasks
        WHERE id = ?
        AND status = 'active'
        """,
        (task_id,),
    ).fetchone()

    db.close()

    if task is None:

        await query.message.reply_text(
            "Эта задача уже выполнена "
            "или сгорела."
        )

        return

    created = datetime.fromisoformat(
        task["created_at"]
    )

    expires = datetime.fromisoformat(
        task["expires_at"]
    )

    text = (
        f"{task['title']}\n"
        f"{DEADLINE_NAMES[task['deadline_type']]}\n\n"
        f"Поставлена: "
        f"{created.strftime('%d.%m.%Y %H:%M')}\n"
        f"Выполнить до: "
        f"{expires.strftime('%d.%m.%Y %H:%M')}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Просьба выполнена",
                callback_data=(
                    f"complete_view_{task_id}"
                ),
            )
        ]
    ])

    await query.message.reply_text(
        text,
        reply_markup=keyboard,
    )


# ============================================================
# ВЫПОЛНЕНИЕ ЗАДАЧИ
# ============================================================

def complete_task(
    task_id,
    username,
    source,
):
    """
    Пытается выполнить задачу.

    Важно:
    операция атомарная.
    Это защищает от двойного начисления,
    если два человека нажмут кнопку одновременно.
    """

    db = get_db()

    try:

        db.execute("BEGIN IMMEDIATE")

        task = db.execute(
            """
            SELECT *
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()

        if task is None:

            db.rollback()
            return False, 0, None

        # Если уже была запись выполнения —
        # повторно начислить бонус нельзя.
        completion = db.execute(
            """
            SELECT *
            FROM completions
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()

        if completion is not None:

            db.rollback()

            return False, 0, task

        bonus = BONUS_FOR_DEADLINE[
            task["deadline_type"]
        ]

        # ----------------------------------------------------
        # Записываем выполнение
        # ----------------------------------------------------

        db.execute(
            """
            INSERT INTO completions (
                task_id,
                username,
                completed_at,
                source
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                task_id,
                username,
                now().isoformat(),
                source,
            ),
        )

        # ----------------------------------------------------
        # Начисляем бонус
        # ----------------------------------------------------

        db.execute(
            """
            UPDATE users
            SET bonuses = bonuses + ?
            WHERE username = ?
            """,
            (
                bonus,
                username,
            ),
        )

        # ----------------------------------------------------
        # Статистика
        #
        # ВАЖНО:
        # теперь статистика увеличивается для ЛЮБОГО
        # способа выполнения задачи:
        #
        # - Просьба выполнена
        # - Я это сделал/а
        # - Вообще-то я это сделал/а
        # ----------------------------------------------------

        if task["deadline_type"] == "month":

            db.execute(
                """
                UPDATE users
                SET big_tasks_completed =
                    big_tasks_completed + 1
                WHERE username = ?
                """,
                (username,),
            )

        elif task["deadline_type"] in (
            "asap",
            "day",
            "week",
        ):

            db.execute(
                """
                UPDATE users
                SET small_tasks_completed =
                    small_tasks_completed + 1
                WHERE username = ?
                """,
                (username,),
            )

        # ----------------------------------------------------
        # Задача становится выполненной
        # ----------------------------------------------------

        db.execute(
            """
            UPDATE tasks
            SET status = 'completed'
            WHERE id = ?
            """,
            (task_id,),
        )

        # ----------------------------------------------------
        # Журнал
        # ----------------------------------------------------

        db.execute(
            """
            INSERT INTO operation_log (
                username,
                operation,
                amount,
                task_id,
                description,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                "task_completed",
                bonus,
                task_id,
                (
                    f"Выполнена задача "
                    f"«{task['title']}» "
                    f"способом: {source}"
                ),
                now().isoformat(),
            ),
        )

        db.commit()

        return True, bonus, task

    except Exception:

        db.rollback()
        raise

    finally:

        db.close()


async def complete_from_view(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    known_user = get_known_user(update)

    if known_user is None:

        await query.answer()

        return

    username, name = known_user

    task_id = int(
        query.data.replace(
            "complete_view_",
            "",
        )
    )

    success, bonus, task = complete_task(
        task_id,
        username,
        "view",
    )

    await query.answer()

    if not success:

        await query.message.reply_text(
            "Эта задача уже была выполнена."
        )

        return

    await query.message.reply_text(
        f"Спасибо {name}!\n"
        f"Зачислено {bonus}"
    )


async def complete_from_reminder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    known_user = get_known_user(update)

    if known_user is None:

        await query.answer()

        return

    username, name = known_user

    task_id = int(
        query.data.replace(
            "complete_reminder_",
            "",
        )
    )

    success, bonus, task = complete_task(
        task_id,
        username,
        "reminder",
    )

    await query.answer()

    if not success:

        await query.message.reply_text(
            "Эта задача уже была выполнена."
        )

        return

    await query.message.reply_text(
        f"Спасибо, {name}!\n"
        f"Зачислено {bonus} бонусов."
    )


async def complete_expired(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    known_user = get_known_user(update)

    if known_user is None:

        await query.answer()

        return

    username, name = known_user

    task_id = int(
        query.data.replace(
            "complete_expired_",
            "",
        )
    )

    success, bonus, task = complete_task(
        task_id,
        username,
        "expired",
    )

    await query.answer()

    if not success:

        await query.message.reply_text(
            "Эта задача уже была закрыта."
        )

        return

    await query.message.reply_text(
        f"Хорошо, {name}!\n"
        f"Зачислено {bonus} бонусов."
    )


# ============================================================
# ТЕКСТЫ НАПОМИНАНИЙ
# ============================================================

def reminder_text(task):

    title = task["title"]

    if task["deadline_type"] == "asap":

        return (
            f"Пожалуйста, {title} "
            f"в ближайшее время"
        )

    if task["deadline_type"] == "day":

        return (
            f"Не забудь, пожалуйста, "
            f"{title} сегодня"
        )

    if task["deadline_type"] == "week":

        return (
            f"Пожалуйста, {title} "
            f"до конца недели"
        )

    if task["deadline_type"] == "month":

        return f"Пожалуйста, {title}"


# ============================================================
# РАЗРЕШЕНО ЛИ СЕЙЧАС НАПОМИНАТЬ
# ============================================================

def reminder_allowed(current):

    # 12:00–23:59
    if current.hour >= 12:
        return True

    # 00:00–00:59
    if current.hour == 0:
        return True

    return False


# ============================================================
# СЛЕДУЮЩЕЕ НАПОМИНАНИЕ
# ============================================================

def calculate_next_reminder(
    task,
    current,
):
    """
    Возвращает время следующего напоминания.

    None означает, что напоминать больше не нужно.
    """

    created = datetime.fromisoformat(
        task["created_at"]
    )

    expires = datetime.fromisoformat(
        task["expires_at"]
    )

    deadline_type = task["deadline_type"]

    # --------------------------------------------------------
    # Если срок уже закончился
    # --------------------------------------------------------

    if current >= expires:
        return None

    # --------------------------------------------------------
    # ASAP
    #
    # Каждые 10 минут.
    # --------------------------------------------------------

    if deadline_type == "asap":

        # Если задача создана вне периода уведомлений,
        # первое уведомление в 13:00.
        first = datetime.fromisoformat(
            task["first_reminder_at"]
        )

        last = task["last_reminder_at"]

        if last is None:

            if current >= first:
                return current.replace(
                    second=0,
                    microsecond=0,
                )

            return first

        last_dt = datetime.fromisoformat(last)

        return last_dt + timedelta(minutes=10)

    # --------------------------------------------------------
    # DAY
    # --------------------------------------------------------

    if deadline_type == "day":

        last = task["last_reminder_at"]

        if last is None:

            first = datetime.fromisoformat(
                task["first_reminder_at"]
            )

            if current >= first:
                return current.replace(
                    second=0,
                    microsecond=0,
                )

            return first

        last_dt = datetime.fromisoformat(last)

        remaining = expires - current

        if remaining <= timedelta(hours=1):

            return last_dt + timedelta(
                minutes=10
            )

        return last_dt + timedelta(
            hours=1
        )

    # --------------------------------------------------------
    # WEEK
    # --------------------------------------------------------

    if deadline_type == "week":

        last = task["last_reminder_at"]

        if last is None:

            first = datetime.fromisoformat(
                task["first_reminder_at"]
            )

            if current >= first:
                return current.replace(
                    second=0,
                    microsecond=0,
                )

            return first

        last_dt = datetime.fromisoformat(last)

        remaining = expires - current

        if remaining <= timedelta(days=1):

            return last_dt + timedelta(
                hours=1
            )

        return last_dt + timedelta(
            days=1
        )

    # --------------------------------------------------------
    # MONTH
    # --------------------------------------------------------

    if deadline_type == "month":

        last = task["last_reminder_at"]

        if last is None:

            first = datetime.fromisoformat(
                task["first_reminder_at"]
            )

            if current >= first:
                return current.replace(
                    second=0,
                    microsecond=0,
                )

            return first

        last_dt = datetime.fromisoformat(last)

        remaining = expires - current

        if remaining <= timedelta(days=7):

            return last_dt + timedelta(
                days=1
            )

        return last_dt + timedelta(
            days=7
        )

    return None


# ============================================================
# ОТПРАВКА НАПОМИНАНИЯ
# ============================================================

async def send_reminder(
    task,
    context,
):
    """
    Отправляет напоминание в общий чат.
    """

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Я это сделал/а",
                callback_data=(
                    f"complete_reminder_{task['id']}"
                ),
            )
        ]
    ])

    await context.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=reminder_text(task),
        reply_markup=keyboard,
    )


# ============================================================
# СГОРЕВШАЯ ЗАДАЧА
# ============================================================

async def expire_task(
    task,
    context,
):
    """
    Переводит задачу в expired и отправляет
    сообщение в общий чат.
    """

    db = get_db()

    current_task = db.execute(
        """
        SELECT *
        FROM tasks
        WHERE id = ?
        AND status = 'active'
        """,
        (task["id"],),
    ).fetchone()

    if current_task is None:

        db.close()

        return

    db.execute(
        """
        UPDATE tasks
        SET status = 'expired'
        WHERE id = ?
        """,
        (task["id"],),
    )

    db.commit()
    db.close()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Вообще-то я это сделал/а",
                callback_data=(
                    f"complete_expired_{task['id']}"
                ),
            )
        ]
    ])

    await context.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=(
            f"Задача {task['title']} сгорела, "
            f"бонусы за её выполнение "
            f"начислены не будут :("
        ),
        reply_markup=keyboard,
    )


# ============================================================
# ПЛАНИРОВЩИК
# ============================================================

async def reminder_job(
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Проверяет задачи каждую минуту.
    """

    current = now()

    db = get_db()

    tasks = db.execute(
        """
        SELECT *
        FROM tasks
        WHERE status = 'active'
        """
    ).fetchall()

    db.close()

    for task in tasks:

        expires = datetime.fromisoformat(
            task["expires_at"]
        )

        # ----------------------------------------------------
        # Сначала проверяем сгорание.
        # ----------------------------------------------------

        if current >= expires:

            await expire_task(
                task,
                context,
            )

            continue

        # ----------------------------------------------------
        # Если сейчас запрещённый период —
        # ничего не отправляем.
        #
        # Следующий запуск произойдёт через минуту.
        # ----------------------------------------------------

        if not reminder_allowed(current):
            continue

        next_reminder = calculate_next_reminder(
            task,
            current,
        )

        if next_reminder is None:
            continue

        # ----------------------------------------------------
        # Если наступило время напоминания
        # ----------------------------------------------------

        if current >= next_reminder:

            try:

                await send_reminder(
                    task,
                    context,
                )

                # Запоминаем время последнего
                # успешно отправленного уведомления.
                db = get_db()

                db.execute(
                    """
                    UPDATE tasks
                    SET last_reminder_at = ?
                    WHERE id = ?
                    """,
                    (
                        current.isoformat(),
                        task["id"],
                    ),
                )

                db.commit()
                db.close()

            except Exception:

                logger.exception(
                    "Не удалось отправить "
                    "напоминание по задаче %s",
                    task["id"],
                )


# ============================================================
# АККАУНТ
# ============================================================

async def show_account(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    known_user = get_known_user(update)

    if known_user is None:
        return

    username, name = known_user

    user = get_user_by_username(
        username
    )

    text = (
        f"Пользователь {name}\n"
        f"Бонусов: {user['bonuses']}\n"
        f"Дел выполнено: "
        f"{user['small_tasks_completed']}\n"
        f"Больших дел выполнено: "
        f"{user['big_tasks_completed']}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Потратить бонусы",
                callback_data="spend_bonus",
            )
        ],
        [
            InlineKeyboardButton(
                "Главное меню",
                callback_data="back_menu",
            )
        ],
    ])

    await query.message.reply_text(
        text,
        reply_markup=keyboard,
    )


# ============================================================
# ПРИЗЫ
# ============================================================

async def show_prizes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    prizes_text = "\n".join(
        f"{key}: {value}"
        for key, value in PRIZES.items()
    )

    await query.message.reply_text(
        prizes_text
    )

    await query.message.reply_text(
        "Введите название приза:"
    )

    context.user_data[
        "waiting_for_prize"
    ] = True


async def receive_prize(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not context.user_data.get(
        "waiting_for_prize"
    ):
        return

    known_user = get_known_user(update)

    if known_user is None:
        return

    username, name = known_user

    prize_input = (
        update.message.text.strip().lower()
    )

    prize = None

    for key in PRIZES:

        if key.lower() == prize_input:

            prize = key

            break

    if prize is None:

        await update.message.reply_text(
            "Такого приза нет. "
            "Введите один из доступных вариантов."
        )

        return

    price = PRIZES[prize]

    success = subtract_bonuses(
        username,
        price,
        description=(
            f"Приз «{prize}»"
        ),
    )

    if not success:

        user = get_user_by_username(
            username
        )

        await update.message.reply_text(
            f"Недостаточно бонусов.\n"
            f"У вас: {user['bonuses']}\n"
            f"Нужно: {price}"
        )

        context.user_data.pop(
            "waiting_for_prize",
            None,
        )

        return

    context.user_data.pop(
        "waiting_for_prize",
        None,
    )

    await update.message.reply_text(
        f"{name} хочет списать бонусы "
        f"на приз {prize}"
    )


# ============================================================
# АДМИН
# ============================================================

def is_admin(update: Update):

    user = update.effective_user

    if user is None:
        return False

    if user.username is None:
        return False

    return (
        user.username.lower()
        == ADMIN_USERNAME.lower()
    )


async def admin_only_message(
    update,
):

    await update.message.reply_text(
        "Эта команда доступна только "
        "разработчику."
    )


# ------------------------------------------------------------
# /balance
# ------------------------------------------------------------

async def admin_balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_admin(update):

        await admin_only_message(update)

        return

    db = get_db()

    users = db.execute(
        """
        SELECT *
        FROM users
        ORDER BY name
        """
    ).fetchall()

    db.close()

    lines = [
        "Текущие счета:",
        "",
    ]

    for user in users:

        lines.append(
            f"{user['name']}: "
            f"{user['bonuses']} бонусов"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ------------------------------------------------------------
# /addbonus USERNAME AMOUNT
# ------------------------------------------------------------

async def admin_add_bonus(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_admin(update):

        await admin_only_message(update)

        return

    if len(context.args) != 2:

        await update.message.reply_text(
            "Использование:\n"
            "/addbonus username сумма\n\n"
            "Например:\n"
            "/addbonus mashusha_f 100"
        )

        return

    username = context.args[0].lower()

    try:

        amount = int(context.args[1])

    except ValueError:

        await update.message.reply_text(
            "Сумма должна быть целым числом."
        )

        return

    if username not in USERS:

        await update.message.reply_text(
            "Такого пользователя нет."
        )

        return

    add_bonuses(
        username,
        amount,
        operation="admin_add_bonus",
        description=(
            "Ручное начисление "
            "разработчиком"
        ),
    )

    user = get_user_by_username(
        username
    )

    await update.message.reply_text(
        f"{user['name']} начислено "
        f"{amount} бонусов.\n"
        f"Теперь на счёте: "
        f"{user['bonuses']}"
    )


# ------------------------------------------------------------
# /reset
# ------------------------------------------------------------

async def admin_reset(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_admin(update):

        await admin_only_message(update)

        return

    db = get_db()

    # Обнуляем бонусы
    db.execute(
        """
        UPDATE users
        SET
            bonuses = 0,
            small_tasks_completed = 0,
            big_tasks_completed = 0
        """
    )

    # Удаляем активные задачи
    db.execute(
        """
        DELETE FROM tasks
        WHERE status = 'active'
        """
    )

    # Удаляем историю выполнений
    db.execute(
        """
        DELETE FROM completions
        """
    )

    db.commit()
    db.close()

    # Сам факт сброса тоже записываем в журнал.
    log_operation(
        username=None,
        operation="admin_reset",
        description=(
            "Полный сброс счетов, "
            "статистики и активных задач"
        ),
    )

    await update.message.reply_text(
        "Счета, статистика и активные "
        "задачи сброшены."
    )


# ------------------------------------------------------------
# /log
# ------------------------------------------------------------

async def admin_log(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_admin(update):

        await admin_only_message(update)

        return

    db = get_db()

    rows = db.execute(
        """
        SELECT *
        FROM operation_log
        ORDER BY id DESC
        LIMIT 30
        """
    ).fetchall()

    db.close()

    if not rows:

        await update.message.reply_text(
            "Журнал пока пуст."
        )

        return

    lines = [
        "Последние операции:",
        "",
    ]

    for row in rows:

        username = row["username"] or "система"

        dt = datetime.fromisoformat(
            row["created_at"]
        )

        lines.append(
            f"{dt.strftime('%d.%m %H:%M')} | "
            f"{username} | "
            f"{row['description']}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

async def back_to_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    await query.message.reply_text(
        "Выберите действие:",
        reply_markup=main_menu(),
    )


# ============================================================
# ОШИБКИ
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.exception(
        "Произошла ошибка:",
        exc_info=context.error,
    )


# ============================================================
# ЗАПУСК
# ============================================================

def main():

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # Создание задачи
    # --------------------------------------------------------

    task_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                add_task_start,
                pattern=r"^menu_add$",
            )
        ],

        states={
            WAITING_FOR_TASK_NAME: [
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND,
                    receive_task_name,
                )
            ],
        },

        fallbacks=[],
    )

    application.add_handler(
        task_conversation
    )

    # --------------------------------------------------------
    # Основные команды
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    # --------------------------------------------------------
    # Админ-команды
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "balance",
            admin_balance,
        )
    )

    application.add_handler(
        CommandHandler(
            "addbonus",
            admin_add_bonus,
        )
    )

    application.add_handler(
        CommandHandler(
            "reset",
            admin_reset,
        )
    )

    application.add_handler(
        CommandHandler(
            "log",
            admin_log,
        )
    )

    # --------------------------------------------------------
    # Просмотр задач
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            show_tasks,
            pattern=r"^menu_tasks$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            show_single_task,
            pattern=r"^task_\d+$",
        )
    )

    # --------------------------------------------------------
    # Выбор срока
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            create_task,
            pattern=(
                r"^deadline_"
                r"(asap|day|week|month)$"
            ),
        )
    )

    # --------------------------------------------------------
    # Выполнение
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            complete_from_view,
            pattern=r"^complete_view_\d+$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            complete_from_reminder,
            pattern=r"^complete_reminder_\d+$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            complete_expired,
            pattern=r"^complete_expired_\d+$",
        )
    )

    # --------------------------------------------------------
    # Аккаунт
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            show_account,
            pattern=r"^menu_account$",
        )
    )

    # --------------------------------------------------------
    # Призы
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            show_prizes,
            pattern=r"^spend_bonus$",
        )
    )

    # --------------------------------------------------------
    # Главное меню
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            back_to_menu,
            pattern=r"^back_menu$",
        )
    )

    # --------------------------------------------------------
    # Ввод названия приза
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_prize,
        ),
        group=5,
    )

    # --------------------------------------------------------
    # Планировщик.
    #
    # Проверяем задачи каждую минуту.
    # --------------------------------------------------------

    application.job_queue.run_repeating(
        reminder_job,
        interval=60,
        first=5,
    )

    # --------------------------------------------------------
    # Ошибки
    # --------------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Бот запущен."
    )

    application.run_polling()


if __name__ == "__main__":
    main()