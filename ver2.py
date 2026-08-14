import logging
import sqlite3
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

# Вставь сюда токен, который выдал @BotFather
BOT_TOKEN = "8980714282:AAFwEhIzOMTtz_sGzGcI4IDbomKpgY6P9vg"

# Часовой пояс.
# Для Эстонии:
TIMEZONE = ZoneInfo("Europe/Tallinn")

# Файл базы данных.
DB_FILE = Path(".venv/tasks_bot.sqlite3")


# ============================================================
# БОНУСЫ
# ============================================================
#
# ЭТО ГЛАВНОЕ МЕСТО, ГДЕ МОЖНО МЕНЯТЬ БОНУСЫ.
#
# Награда за создание задачи:
TASK_CREATION_BONUS = 10

# Награда за выполнение в зависимости от срока:
TASK_BONUSES = {
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
# ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def get_db():
    """
    Открывает соединение с SQLite.
    """

    db = sqlite3.connect(DB_FILE)

    db.row_factory = sqlite3.Row

    return db


def init_db():
    """
    Создаёт таблицы, если их ещё нет.
    """

    db = get_db()

    # --------------------------------------------------------
    # Пользователи
    # --------------------------------------------------------

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            bonuses INTEGER NOT NULL DEFAULT 0,

            small_tasks_completed INTEGER NOT NULL DEFAULT 0,
            big_tasks_completed INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    # --------------------------------------------------------
    # Состояние пользователя
    # --------------------------------------------------------

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_states (
            telegram_id INTEGER PRIMARY KEY,

            state TEXT,

            task_name TEXT
        )
        """
    )

    # --------------------------------------------------------
    # Задачи
    # --------------------------------------------------------

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            chat_id INTEGER NOT NULL,

            creator_id INTEGER NOT NULL,

            title TEXT NOT NULL,

            deadline_type TEXT NOT NULL,

            created_at TEXT NOT NULL,

            expires_at TEXT NOT NULL,

            status TEXT NOT NULL DEFAULT 'active',

            last_reminder_at TEXT,

            next_reminder_at TEXT
        )
        """
    )

    # --------------------------------------------------------
    # Выполнения задач
    #
    # Отдельная таблица нужна, чтобы гарантировать:
    # одна задача → одно начисление.
    # --------------------------------------------------------

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS task_completions (
            task_id INTEGER PRIMARY KEY,

            user_id INTEGER NOT NULL,

            completed_at TEXT NOT NULL,

            bonus INTEGER NOT NULL,

            completion_type TEXT NOT NULL
        )
        """
    )

    # --------------------------------------------------------
    # Попытки потратить бонусы
    # --------------------------------------------------------

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS prize_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,

            prize_name TEXT NOT NULL,

            cost INTEGER NOT NULL,

            created_at TEXT NOT NULL
        )
        """
    )

    db.commit()
    db.close()


# ============================================================
# ВРЕМЯ
# ============================================================

def now():
    """
    Текущее время в нужном часовом поясе.
    """

    return datetime.now(TIMEZONE)


def dt_to_str(value: datetime):
    """
    datetime → строка для SQLite.
    """

    return value.isoformat()


def str_to_dt(value: str):
    """
    Строка SQLite → datetime.
    """

    return datetime.fromisoformat(value)


# ============================================================
# ПОЛЬЗОВАТЕЛЬ
# ============================================================

def ensure_user(update: Update):
    """
    Автоматически создаёт пользователя.

    Никаких списков USERS нет.

    Любой человек, который взаимодействует
    с ботом, автоматически получает аккаунт.
    """

    user = update.effective_user

    if user is None:
        return None

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
            user.first_name or "Пользователь",
        ),
    )

    db.commit()
    db.close()

    return user


# ============================================================
# МЕНЮ
# ============================================================

def main_menu():

    return InlineKeyboardMarkup(
        [
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
        ]
    )


def deadline_menu():

    return InlineKeyboardMarkup(
        [
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
        ]
    )


def back_menu():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Вернуться в меню",
                    callback_data="back_menu",
                )
            ]
        ]
    )


# ============================================================
# СРОКИ
# ============================================================

def calculate_expiration(
    created_at: datetime,
    deadline_type: str,
):
    """
    Вычисляет момент, когда задача сгорает.
    """

    if deadline_type in ("asap", "day"):

        # Ближайшие 01:00.
        expiration = datetime.combine(
            created_at.date(),
            time(1, 0),
            tzinfo=TIMEZONE,
        )

        if expiration <= created_at:

            expiration += timedelta(days=1)

        return expiration

    if deadline_type == "week":

        # Ближайшее воскресенье.
        days_until_sunday = (
            6 - created_at.weekday()
        ) % 7

        sunday = created_at + timedelta(
            days=days_until_sunday
        )

        expiration = datetime.combine(
            sunday.date(),
            time(14, 0),
            tzinfo=TIMEZONE,
        )

        # Если задача поставлена уже после 14:00
        # в воскресенье, сроком будет следующее воскресенье.
        if expiration <= created_at:

            expiration += timedelta(days=7)

        return expiration

    if deadline_type == "month":

        return created_at + relativedelta(months=1)

    raise ValueError(
        f"Неизвестный срок: {deadline_type}"
    )


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
# ВРЕМЯ НАПОМИНАНИЙ
# ============================================================

REMINDER_START_HOUR = 12
REMINDER_END_HOUR = 1


def in_reminder_period(dt: datetime):
    """
    Разрешённый период для напоминаний:

    12:00–00:59.

    В 01:00 напоминания прекращаются.
    """

    hour = dt.hour

    return (
        hour >= 12
        or hour < 1
    )


def next_13_00(dt: datetime):
    """
    Следующие 13:00.
    """

    result = datetime.combine(
        dt.date(),
        time(13, 0),
        tzinfo=TIMEZONE,
    )

    if result <= dt:

        result += timedelta(days=1)

    return result


def reminder_time_allowed(dt: datetime):
    """
    Если сейчас можно отправлять напоминание —
    возвращаем dt.

    Если сейчас ночь — возвращаем ближайшие 13:00.
    """

    if in_reminder_period(dt):

        return dt

    return next_13_00(dt)


# ============================================================
# РАСЧЁТ СЛЕДУЮЩЕГО НАПОМИНАНИЯ
# ============================================================

def calculate_next_reminder(task):
    """
    Вычисляет, когда отправить следующее напоминание.
    """

    current = now()

    expires = str_to_dt(
        task["expires_at"]
    )

    deadline_type = task["deadline_type"]

    # --------------------------------------------------------
    # ASAP
    # --------------------------------------------------------

    if deadline_type == "asap":

        candidate = current + timedelta(
            minutes=10
        )

    # --------------------------------------------------------
    # В течение дня
    # --------------------------------------------------------

    elif deadline_type == "day":

        remaining = expires - current

        if remaining > timedelta(hours=1):

            candidate = current + timedelta(
                hours=1
            )

        else:

            candidate = current + timedelta(
                minutes=10
            )

    # --------------------------------------------------------
    # Неделя
    # --------------------------------------------------------

    elif deadline_type == "week":

        remaining = expires - current

        if remaining > timedelta(days=1):

            # Каждый день примерно в то же время,
            # когда была поставлена задача.

            created = str_to_dt(
                task["created_at"]
            )

            candidate = current + timedelta(
                days=1
            )

            candidate = candidate.replace(
                hour=created.hour,
                minute=created.minute,
                second=0,
                microsecond=0,
            )

        else:

            candidate = current + timedelta(
                hours=1
            )

    # --------------------------------------------------------
    # Месяц
    # --------------------------------------------------------

    elif deadline_type == "month":

        remaining = expires - current

        if remaining > timedelta(days=7):

            candidate = current + timedelta(
                days=7
            )

        else:

            candidate = current + timedelta(
                days=1
            )

    else:

        return None

    # --------------------------------------------------------
    # Нельзя напоминать после истечения.
    # --------------------------------------------------------

    if candidate >= expires:

        return None

    # --------------------------------------------------------
    # Если попали в ночь — переносим на 13:00.
    # --------------------------------------------------------

    candidate = reminder_time_allowed(candidate)

    if candidate >= expires:

        return None

    return candidate


# ============================================================
# ТЕКСТ НАПОМИНАНИЯ
# ============================================================

def reminder_text(task):

    title = task["title"]

    deadline_type = task["deadline_type"]

    if deadline_type == "asap":

        return (
            f"Пожалуйста, {title} "
            f"в ближайшее время"
        )

    if deadline_type == "day":

        return (
            f"Не забудь, пожалуйста, "
            f"{title} сегодня"
        )

    if deadline_type == "week":

        return (
            f"Пожалуйста, {title} "
            f"до конца недели"
        )

    if deadline_type == "month":

        return (
            f"Пожалуйста, {title}"
        )

    return title


# ============================================================
# КНОПКА ВЫПОЛНЕНИЯ
# ============================================================

def completion_keyboard(task_id):

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Я это сделал/а",
                    callback_data=f"complete:{task_id}",
                )
            ]
        ]
    )


# ============================================================
# ДОБАВЛЕНИЕ ЗАДАЧИ
# ============================================================

async def add_task_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user = ensure_user(update)

    if user is None:
        return

    db = get_db()

    db.execute(
        """
        INSERT INTO user_states (
            telegram_id,
            state,
            task_name
        )
        VALUES (?, ?, ?)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            state = excluded.state,
            task_name = excluded.task_name
        """,
        (
            user.id,
            "waiting_task_name",
            None,
        ),
    )

    db.commit()
    db.close()

    await query.message.reply_text(
        f"{user.first_name}, "
        f"введите название задачи:"
    )

async def main_menu_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Команда /main.

    Показывает главное меню бота.
    Работает для любого участника чата.
    """

    user = ensure_user(update)

    if user is None:
        return

    await update.message.reply_text(
        "Главное меню:",
        reply_markup=main_menu(),
    )
# ============================================================
# ПОЛУЧЕНИЕ НАЗВАНИЯ ЗАДАЧИ
# ============================================================

async def receive_task_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = ensure_user(update)

    if user is None:
        return

    db = get_db()

    state = db.execute(
        """
        SELECT *
        FROM user_states
        WHERE telegram_id = ?
        """,
        (user.id,),
    ).fetchone()

    if state is None:

        db.close()
        return

    if state["state"] != "waiting_task_name":

        db.close()
        return

    task_name = (
        update.message.text.strip()
    )

    if not task_name:

        db.close()

        await update.message.reply_text(
            "Название задачи не может быть пустым."
        )

        return

    if len(task_name) > 500:

        db.close()

        await update.message.reply_text(
            "Название задачи слишком длинное. "
            "Максимум 500 символов."
        )

        return

    db.execute(
        """
        UPDATE user_states
        SET
            state = 'waiting_deadline',
            task_name = ?
        WHERE telegram_id = ?
        """,
        (
            task_name,
            user.id,
        ),
    )

    db.commit()
    db.close()

    await update.message.reply_text(
        f"Задача: {task_name}\n\n"
        f"Выберите срок:",
        reply_markup=deadline_menu(),
    )


# ============================================================
# СОЗДАНИЕ ЗАДАЧИ ПОСЛЕ ВЫБОРА СРОКА
# ============================================================

async def create_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user = ensure_user(update)

    if user is None:
        return

    deadline_type = query.data.replace(
        "deadline_",
        "",
    )

    db = get_db()

    state = db.execute(
        """
        SELECT *
        FROM user_states
        WHERE telegram_id = ?
        """,
        (user.id,),
    ).fetchone()

    if state is None:

        db.close()

        await query.message.reply_text(
            "Не удалось определить задачу. "
            "Попробуйте добавить её заново."
        )

        return

    if state["state"] != "waiting_deadline":

        db.close()

        return

    task_name = state["task_name"]

    created_at = now()

    expires_at = calculate_expiration(
        created_at,
        deadline_type,
    )

    next_reminder = calculate_next_reminder(
        {
            "deadline_type": deadline_type,
            "created_at": dt_to_str(created_at),
            "expires_at": dt_to_str(expires_at),
        }
    )

    db.execute(
        """
        INSERT INTO tasks (
            chat_id,
            creator_id,
            title,
            deadline_type,
            created_at,
            expires_at,
            next_reminder_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            update.effective_chat.id,
            user.id,
            task_name,
            deadline_type,
            dt_to_str(created_at),
            dt_to_str(expires_at),
            (
                dt_to_str(next_reminder)
                if next_reminder
                else None
            ),
        ),
    )

    # --------------------------------------------------------
    # +10 бонусов за постановку задачи
    # --------------------------------------------------------

    db.execute(
        """
        UPDATE users
        SET bonuses = bonuses + ?
        WHERE telegram_id = ?
        """,
        (
            TASK_CREATION_BONUS,
            user.id,
        ),
    )

    # Очищаем состояние.
    db.execute(
        """
        DELETE FROM user_states
        WHERE telegram_id = ?
        """,
        (user.id,),
    )

    db.commit()
    db.close()

    deadline_text = DEADLINE_NAMES[
        deadline_type
    ]

    await query.message.reply_text(
        f"Задача «{task_name}» добавлена.\n\n"
        f"Срок: {deadline_text}\n"
        f"Выполнить до: "
        f"{expires_at.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"За постановку задачи начислено "
        f"{TASK_CREATION_BONUS} бонусов.",
        reply_markup=main_menu(),
    )


# ============================================================
# СПИСОК ЗАДАЧ
# ============================================================

DEADLINE_ORDER = {
    "asap": 1,
    "day": 2,
    "week": 3,
    "month": 4,
}


async def show_tasks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    ensure_user(update)

    db = get_db()

    tasks = db.execute(
        """
        SELECT *
        FROM tasks
        WHERE chat_id = ?
          AND status = 'active'
        ORDER BY expires_at ASC
        """,
        (update.effective_chat.id,),
    ).fetchall()

    db.close()

    if not tasks:

        await query.message.reply_text(
            "Активных задач нет.",
            reply_markup=main_menu(),
        )

        return

    keyboard = []

    for task in tasks:

        keyboard.append(
            [
                InlineKeyboardButton(
                    (
                        f"{task['title']} — "
                        f"{DEADLINE_NAMES[task['deadline_type']]}"
                    ),
                    callback_data=f"view:{task['id']}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "Вернуться в меню",
                callback_data="back_menu",
            )
        ]
    )

    await query.message.reply_text(
        "Активные задачи:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


# ============================================================
# ПРОСМОТР ОДНОЙ ЗАДАЧИ
# ============================================================

async def view_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    task_id = int(
        query.data.split(":")[1]
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

    created = str_to_dt(
        task["created_at"]
    )

    expires = str_to_dt(
        task["expires_at"]
    )

    text = (
        f"{task['title']}\n"
        f"{DEADLINE_NAMES[task['deadline_type']]}\n\n"
        f"Поставлена "
        f"{created.strftime('%d.%m.%Y %H:%M')}\n"
        f"Выполнить до: "
        f"{expires.strftime('%d.%m.%Y %H:%M')}"
    )

    await query.message.reply_text(
        text,
        reply_markup=completion_keyboard(
            task_id
        ),
    )


# ============================================================
# ВЫПОЛНЕНИЕ ЗАДАЧИ
# ============================================================

async def complete_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user = ensure_user(update)

    if user is None:
        return

    task_id = int(
        query.data.split(":")[1]
    )

    db = get_db()

    # --------------------------------------------------------
    # Проверяем, что задача существует.
    # --------------------------------------------------------

    task = db.execute(
        """
        SELECT *
        FROM tasks
        WHERE id = ?
          AND status = 'active'
        """,
        (task_id,),
    ).fetchone()

    if task is None:

        db.close()

        await query.message.reply_text(
            "Эта задача уже была выполнена."
        )

        return

    # --------------------------------------------------------
    # Проверяем, не было ли начисления.
    # --------------------------------------------------------

    already_completed = db.execute(
        """
        SELECT *
        FROM task_completions
        WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()

    if already_completed:

        db.close()

        await query.message.reply_text(
            "Бонусы за эту задачу уже были начислены."
        )

        return

    bonus = TASK_BONUSES[
        task["deadline_type"]
    ]

    current = now()

    # --------------------------------------------------------
    # Записываем выполнение.
    # --------------------------------------------------------

    db.execute(
        """
        INSERT INTO task_completions (
            task_id,
            user_id,
            completed_at,
            bonus,
            completion_type
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            task_id,
            user.id,
            dt_to_str(current),
            bonus,
            "normal",
        ),
    )

    # --------------------------------------------------------
    # Начисляем бонус.
    # --------------------------------------------------------

    db.execute(
        """
        UPDATE users
        SET
            bonuses = bonuses + ?,

            small_tasks_completed =
                small_tasks_completed + ?

        WHERE telegram_id = ?
        """,
        (
            bonus,
            1 if task["deadline_type"] != "month" else 0,
            user.id,
        ),
    )

    # Большая задача.
    if task["deadline_type"] == "month":

        db.execute(
            """
            UPDATE users
            SET big_tasks_completed =
                big_tasks_completed + 1
            WHERE telegram_id = ?
            """,
            (user.id,),
        )

    # --------------------------------------------------------
    # Удаляем задачу из активных.
    # --------------------------------------------------------

    db.execute(
        """
        UPDATE tasks
        SET status = 'completed'
        WHERE id = ?
        """,
        (task_id,),
    )

    db.commit()
    db.close()

    await query.message.reply_text(
        f"Спасибо!\n"
        f"Зачислено {bonus} бонусов."
    )


# ============================================================
# СГОРЕВШАЯ ЗАДАЧА
# ============================================================

async def burn_expired_task(
    context: ContextTypes.DEFAULT_TYPE,
    task,
):
    """
    Обрабатывает задачу, срок которой закончился.
    """

    db = get_db()

    # --------------------------------------------------------
    # Проверяем, что задача всё ещё активна.
    # --------------------------------------------------------

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

    # Удаляем из списка активных задач.
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

    await context.bot.send_message(
        chat_id=task["chat_id"],
        text=(
            f"Задача {task['title']} сгорела, "
            f"бонусы за её выполнение "
            f"начислены не будут :("
        ),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Вообще-то я это сделал/а",
                        callback_data=(
                            f"burned_complete:{task['id']}"
                        ),
                    )
                ]
            ]
        ),
    )


# ============================================================
# ВЫПОЛНЕНИЕ СГОРЕВШЕЙ ЗАДАЧИ
# ============================================================

async def complete_burned_task(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user = ensure_user(update)

    if user is None:
        return

    task_id = int(
        query.data.split(":")[1]
    )

    db = get_db()

    task = db.execute(
        """
        SELECT *
        FROM tasks
        WHERE id = ?
          AND status = 'expired'
        """,
        (task_id,),
    ).fetchone()

    if task is None:

        db.close()

        await query.message.reply_text(
            "Эта задача уже обработана."
        )

        return

    # --------------------------------------------------------
    # Проверка повторного нажатия.
    # --------------------------------------------------------

    already_completed = db.execute(
        """
        SELECT *
        FROM task_completions
        WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()

    if already_completed:

        db.close()

        await query.message.reply_text(
            "Бонусы за эту задачу уже начислялись."
        )

        return

    bonus = TASK_BONUSES[
        task["deadline_type"]
    ]

    current = now()

    # Записываем выполнение.
    db.execute(
        """
        INSERT INTO task_completions (
            task_id,
            user_id,
            completed_at,
            bonus,
            completion_type
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            task_id,
            user.id,
            dt_to_str(current),
            bonus,
            "burned",
        ),
    )

    # Начисляем бонус.
    db.execute(
        """
        UPDATE users
        SET bonuses = bonuses + ?
        WHERE telegram_id = ?
        """,
        (
            bonus,
            user.id,
        ),
    )

    # Статистика.
    if task["deadline_type"] == "month":

        db.execute(
            """
            UPDATE users
            SET big_tasks_completed =
                big_tasks_completed + 1
            WHERE telegram_id = ?
            """,
            (user.id,),
        )

    else:

        db.execute(
            """
            UPDATE users
            SET small_tasks_completed =
                small_tasks_completed + 1
            WHERE telegram_id = ?
            """,
            (user.id,),
        )

    # Помечаем задачу обработанной.
    db.execute(
        """
        UPDATE tasks
        SET status = 'burned_completed'
        WHERE id = ?
        """,
        (task_id,),
    )

    db.commit()
    db.close()

    await query.message.reply_text(
        f"Хорошо, засчитано!\n"
        f"Зачислено {bonus} бонусов."
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

    user = ensure_user(update)

    db = get_db()

    account = db.execute(
        """
        SELECT *
        FROM users
        WHERE telegram_id = ?
        """,
        (user.id,),
    ).fetchone()

    db.close()

    text = (
        f"Пользователь {user.first_name}\n"
        f"Бонусов: {account['bonuses']}\n"
        f"Дел выполнено: "
        f"{account['small_tasks_completed']}\n"
        f"Больших дел выполнено: "
        f"{account['big_tasks_completed']}"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Потратить бонусы",
                    callback_data="spend_bonus",
                )
            ],
            [
                InlineKeyboardButton(
                    "Вернуться в меню",
                    callback_data="back_menu",
                )
            ],
        ]
    )

    await query.message.reply_text(
        text,
        reply_markup=keyboard,
    )


# ============================================================
# ПОКАЗ ПРИЗОВ
# ============================================================

async def show_prizes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    text = (
        "Доступные призы:\n\n"
        "поцелуй: 10\n"
        "рандомная вкусняшка: 100\n"
        "плацинда: 1000\n\n"
        "Введите название приза:"
    )

    user = ensure_user(update)

    db = get_db()

    db.execute(
        """
        INSERT INTO user_states (
            telegram_id,
            state,
            task_name
        )
        VALUES (?, ?, ?)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            state = excluded.state,
            task_name = excluded.task_name
        """,
        (
            user.id,
            "waiting_prize",
            None,
        ),
    )

    db.commit()
    db.close()

    await query.message.reply_text(
        text
    )


# ============================================================
# ОБРАБОТКА ПРИЗА
# ============================================================

async def receive_prize(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = ensure_user(update)

    db = get_db()

    state = db.execute(
        """
        SELECT *
        FROM user_states
        WHERE telegram_id = ?
        """,
        (user.id,),
    ).fetchone()

    if state is None:

        db.close()
        return

    if state["state"] != "waiting_prize":

        db.close()
        return

    prize_name = (
        update.message.text.strip().lower()
    )

    # --------------------------------------------------------
    # Ищем приз без учёта регистра.
    # --------------------------------------------------------

    selected_prize = None

    for key in PRIZES:

        if key.lower() == prize_name:

            selected_prize = key

            break

    if selected_prize is None:

        db.close()

        await update.message.reply_text(
            "Такого приза нет.\n\n"
            "Доступные варианты:\n"
            + "\n".join(
                f"{key}: {value}"
                for key, value in PRIZES.items()
            )
        )

        return

    cost = PRIZES[
        selected_prize
    ]

    account = db.execute(
        """
        SELECT bonuses
        FROM users
        WHERE telegram_id = ?
        """,
        (user.id,),
    ).fetchone()

    if account["bonuses"] < cost:

        db.close()

        await update.message.reply_text(
            f"Недостаточно бонусов.\n"
            f"Стоимость: {cost}\n"
            f"У вас: {account['bonuses']}"
        )

        return

    # --------------------------------------------------------
    # Списываем бонусы.
    # --------------------------------------------------------

    db.execute(
        """
        UPDATE users
        SET bonuses = bonuses - ?
        WHERE telegram_id = ?
        """,
        (
            cost,
            user.id,
        ),
    )

    # --------------------------------------------------------
    # Сохраняем запрос.
    # --------------------------------------------------------

    db.execute(
        """
        INSERT INTO prize_requests (
            user_id,
            prize_name,
            cost,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            user.id,
            selected_prize,
            cost,
            dt_to_str(now()),
        ),
    )

    # Удаляем состояние.
    db.execute(
        """
        DELETE FROM user_states
        WHERE telegram_id = ?
        """,
        (user.id,),
    )

    db.commit()
    db.close()

    await update.message.reply_text(
        f"{user.first_name} хочет списать "
        f"бонусы на приз {selected_prize}"
    )


# ============================================================
# ОБЩИЙ ОБРАБОТЧИК ТЕКСТА
# ============================================================

async def receive_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Один обработчик обычного текста.

    Это важно для группового чата:
    разные пользователи могут одновременно
    находиться на разных этапах диалога.
    """

    user = ensure_user(update)

    if user is None:
        return

    db = get_db()

    state = db.execute(
        """
        SELECT state
        FROM user_states
        WHERE telegram_id = ?
        """,
        (user.id,),
    ).fetchone()

    db.close()

    if state is None:
        return

    if state["state"] == "waiting_task_name":

        await receive_task_name(
            update,
            context,
        )

        return

    if state["state"] == "waiting_prize":

        await receive_prize(
            update,
            context,
        )

        return


# ============================================================
# ВОЗВРАТ В МЕНЮ
# ============================================================

async def back_to_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    await query.message.reply_text(
        "Главное меню:",
        reply_markup=main_menu(),
    )


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = ensure_user(update)

    if user is None:
        return

    await update.message.reply_text(
        f"Привет, {user.first_name}!\n\n"
        f"Главное меню:",
        reply_markup=main_menu(),
    )


# ============================================================
# ПЛАНИРОВЩИК
# ============================================================

async def task_scheduler(
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Проверяет задачи примерно каждую минуту.

    Здесь:
    1. сгорают просроченные задачи;
    2. отправляются напоминания.
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

        try:

            expires = str_to_dt(
                task["expires_at"]
            )

            # ------------------------------------------------
            # Задача истекла.
            # ------------------------------------------------

            if current >= expires:

                await burn_expired_task(
                    context,
                    task,
                )

                continue

            # ------------------------------------------------
            # Напоминание.
            # ------------------------------------------------

            if not task["next_reminder_at"]:
                continue

            next_reminder = str_to_dt(
                task["next_reminder_at"]
            )

            if current < next_reminder:
                continue

            # Ночью не отправляем.
            if not in_reminder_period(current):

                new_time = next_13_00(current)

                db = get_db()

                db.execute(
                    """
                    UPDATE tasks
                    SET next_reminder_at = ?
                    WHERE id = ?
                    """,
                    (
                        dt_to_str(new_time),
                        task["id"],
                    ),
                )

                db.commit()
                db.close()

                continue

            # ------------------------------------------------
            # Отправляем.
            # ------------------------------------------------

            await context.bot.send_message(
                chat_id=task["chat_id"],
                text=reminder_text(task),
                reply_markup=completion_keyboard(
                    task["id"]
                ),
            )

            # ------------------------------------------------
            # Планируем следующее.
            # ------------------------------------------------

            next_time = calculate_next_reminder(
                task
            )

            db = get_db()

            db.execute(
                """
                UPDATE tasks
                SET
                    last_reminder_at = ?,
                    next_reminder_at = ?
                WHERE id = ?
                """,
                (
                    dt_to_str(current),
                    (
                        dt_to_str(next_time)
                        if next_time
                        else None
                    ),
                    task["id"],
                ),
            )

            db.commit()
            db.close()

        except Exception:

            logger.exception(
                "Ошибка при обработке задачи %s",
                task["id"],
            )


# ============================================================
# ЗАПУСК
# ============================================================

def main():

    # Создаём БД.
    init_db()

    if (
        not BOT_TOKEN
        or BOT_TOKEN == "ВСТАВЬ_СЮДА_ТОКЕН"
    ):

        raise RuntimeError(
            "Не указан BOT_TOKEN."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # Команды
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )
    application.add_handler(
        CommandHandler(
            "main",
            main_menu_command,
        )
    )

    # --------------------------------------------------------
    # Кнопка «Добавить задачу»
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            add_task_start,
            pattern=r"^menu_add$",
        )
    )

    # --------------------------------------------------------
    # Кнопка «Посмотреть задачи»
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            show_tasks,
            pattern=r"^menu_tasks$",
        )
    )

    # --------------------------------------------------------
    # Просмотр конкретной задачи
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            view_task,
            pattern=r"^view:\d+$",
        )
    )

    # --------------------------------------------------------
    # Выбор срока
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            create_task,
            pattern=r"^deadline_(asap|day|week|month)$",
        )
    )

    # --------------------------------------------------------
    # Выполнение активной задачи
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            complete_task,
            pattern=r"^complete:\d+$",
        )
    )

    # --------------------------------------------------------
    # Выполнение сгоревшей задачи
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            complete_burned_task,
            pattern=r"^burned_complete:\d+$",
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
    # Потратить бонусы
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            show_prizes,
            pattern=r"^spend_bonus$",
        )
    )

    # --------------------------------------------------------
    # Возврат в меню
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            back_to_menu,
            pattern=r"^back_menu$",
        )
    )

    # --------------------------------------------------------
    # Обычный текст
    #
    # Здесь НЕ ConversationHandler.
    # Состояние хранится в SQLite отдельно для каждого
    # Telegram user ID.
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            receive_text,
        )
    )

    # --------------------------------------------------------
    # Планировщик
    # --------------------------------------------------------

    application.job_queue.run_repeating(
        task_scheduler,
        interval=60,
        first=5,
    )

    logger.info(
        "Бот запущен."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    main()