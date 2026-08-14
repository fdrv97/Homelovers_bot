import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, time
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

# ------------------------------------------------------------
# ВСТАВЬ СЮДА ТОКЕН ОТ @BotFather
# ------------------------------------------------------------

import os

BOT_TOKEN = os.getenv("BOT_TOKEN")


# ------------------------------------------------------------
# Часовой пояс
# ------------------------------------------------------------

TIMEZONE = ZoneInfo("Europe/Tallinn")


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

# ------------------------------------------------------------
# ВАЖНО ДЛЯ BOTHOST
#
# База хранится в папке data.
#
# Bothost рекомендует использовать папку data
# для сохранения базы между обновлениями.
# ------------------------------------------------------------

DATA_DIR = Path("data")

# Если папки data нет — создаём её.
DATA_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

DB_FILE = DATA_DIR / "tasks_bot.sqlite3"


def check_database_directory():
    """
    Проверяет, что папка базы существует
    и доступна для записи.
    """

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not DATA_DIR.is_dir():
        raise RuntimeError(
            f"Не удалось создать папку базы данных: "
            f"{DATA_DIR.absolute()}"
        )

    try:
        test_file = DATA_DIR / ".write_test"

        test_file.touch(
            exist_ok=True
        )

        test_file.unlink()

    except Exception as error:

        raise RuntimeError(
            "Нет прав на запись в папку базы данных: "
            f"{DATA_DIR.absolute()}\n"
            f"Ошибка: {error}"
        )


def get_db():
    """
    Открывает SQLite-базу.
    """

    check_database_directory()

    db = sqlite3.connect(
        str(DB_FILE),
        timeout=30,
    )

    db.row_factory = sqlite3.Row

    # Немного повышаем устойчивость SQLite
    # к одновременным операциям.
    db.execute(
        "PRAGMA journal_mode=WAL"
    )

    db.execute(
        "PRAGMA foreign_keys=ON"
    )

    return db


# ============================================================
# БОНУСЫ
# ============================================================
#
# ЭТО МЕСТО ДЛЯ ИЗМЕНЕНИЯ БОНУСОВ.
# ============================================================


# Бонус за постановку любой задачи.
TASK_CREATION_BONUS = 10


# Бонус за выполнение задачи.
TASK_BONUSES = {

    # asap
    "asap": 10,

    # в течение дня
    "day": 20,

    # до конца недели
    "week": 50,

    # в течение месяца
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
    format=(
        "%(asctime)s - "
        "%(name)s - "
        "%(levelname)s - "
        "%(message)s"
    ),
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# ИНИЦИАЛИЗАЦИЯ БАЗЫ
# ============================================================

def init_db():
    """
    Создаёт все необходимые таблицы.
    """

    check_database_directory()

    logger.info(
        "Папка базы данных: %s",
        DATA_DIR.absolute(),
    )

    logger.info(
        "Файл базы данных: %s",
        DB_FILE.absolute(),
    )

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
    # Запросы на призы
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
    Текущее время в часовом поясе бота.
    """

    return datetime.now(TIMEZONE)


def dt_to_str(value: datetime):
    """
    datetime → строка.
    """

    return value.isoformat()


def str_to_dt(value: str):
    """
    Строка → datetime.
    """

    return datetime.fromisoformat(value)


# ============================================================
# ПОЛЬЗОВАТЕЛИ
# ============================================================

def ensure_user(update: Update):
    """
    Автоматически создаёт аккаунт пользователя.

    Никакого списка USERS нет.

    Любой участник чата может пользоваться ботом.
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
# ГЛАВНОЕ МЕНЮ
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


# ============================================================
# МЕНЮ СРОКОВ
# ============================================================

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


# ============================================================
# СРОКИ
# ============================================================

DEADLINE_NAMES = {

    "asap": "asap",

    "day": "в течение дня",

    "week": "до конца недели",

    "month": "в течение месяца",
}


DEADLINE_ORDER = {

    "asap": 1,

    "day": 2,

    "week": 3,

    "month": 4,
}


# ============================================================
# РАСЧЁТ СРОКА
# ============================================================

def calculate_expiration(
    created_at: datetime,
    deadline_type: str,
):
    """
    Рассчитывает момент сгорания задачи.
    """

    # --------------------------------------------------------
    # ASAP
    # --------------------------------------------------------

    if deadline_type == "asap":

        expiration = datetime.combine(
            created_at.date(),
            time(1, 0),
            tzinfo=TIMEZONE,
        )

        if expiration <= created_at:

            expiration += timedelta(
                days=1
            )

        return expiration

    # --------------------------------------------------------
    # В течение дня
    # --------------------------------------------------------

    if deadline_type == "day":

        expiration = datetime.combine(
            created_at.date(),
            time(1, 0),
            tzinfo=TIMEZONE,
        )

        if expiration <= created_at:

            expiration += timedelta(
                days=1
            )

        return expiration

    # --------------------------------------------------------
    # До конца недели
    # --------------------------------------------------------

    if deadline_type == "week":

        days_until_sunday = (
            6 - created_at.weekday()
        ) % 7

        sunday = (
            created_at
            + timedelta(
                days=days_until_sunday
            )
        )

        expiration = datetime.combine(
            sunday.date(),
            time(14, 0),
            tzinfo=TIMEZONE,
        )

        if expiration <= created_at:

            expiration += timedelta(
                days=7
            )

        return expiration

    # --------------------------------------------------------
    # В течение месяца
    # --------------------------------------------------------

    if deadline_type == "month":

        return (
            created_at
            + relativedelta(months=1)
        )

    raise ValueError(
        f"Неизвестный срок: {deadline_type}"
    )


# ============================================================
# ПЕРИОД НАПОМИНАНИЙ
# ============================================================

def in_reminder_period(dt: datetime):
    """
    Напоминания разрешены с 12:00 до 01:00.

    То есть:
    12:00–23:59
    00:00–00:59

    В 01:00–11:59 напоминания не отправляются.
    """

    return (
        dt.hour >= 12
        or dt.hour < 1
    )


def next_13_00(dt: datetime):
    """
    Ближайшие 13:00.
    """

    result = datetime.combine(
        dt.date(),
        time(13, 0),
        tzinfo=TIMEZONE,
    )

    if result <= dt:

        result += timedelta(
            days=1
        )

    return result


def reminder_time_allowed(dt: datetime):
    """
    Если время попадает в период уведомлений —
    возвращаем его.

    Если нет — переносим на 13:00.
    """

    if in_reminder_period(dt):

        return dt

    return next_13_00(dt)


# ============================================================
# СЛЕДУЮЩЕЕ НАПОМИНАНИЕ
# ============================================================

def calculate_next_reminder(task):

    current = now()

    expires = str_to_dt(
        task["expires_at"]
    )

    deadline_type = task["deadline_type"]

    # --------------------------------------------------------
    # ASAP
    # --------------------------------------------------------

    if deadline_type == "asap":

        candidate = (
            current
            + timedelta(minutes=10)
        )

    # --------------------------------------------------------
    # В течение дня
    # --------------------------------------------------------

    elif deadline_type == "day":

        remaining = expires - current

        if remaining > timedelta(hours=1):

            candidate = (
                current
                + timedelta(hours=1)
            )

        else:

            candidate = (
                current
                + timedelta(minutes=10)
            )

    # --------------------------------------------------------
    # До конца недели
    # --------------------------------------------------------

    elif deadline_type == "week":

        remaining = expires - current

        if remaining > timedelta(days=1):

            created = str_to_dt(
                task["created_at"]
            )

            candidate = (
                current
                + timedelta(days=1)
            )

            candidate = candidate.replace(
                hour=created.hour,
                minute=created.minute,
                second=0,
                microsecond=0,
            )

            # Если получилось время в прошлом,
            # оставляем следующий день.
            if candidate <= current:

                candidate += timedelta(
                    days=1
                )

        else:

            candidate = (
                current
                + timedelta(hours=1)
            )

    # --------------------------------------------------------
    # В течение месяца
    # --------------------------------------------------------

    elif deadline_type == "month":

        remaining = expires - current

        if remaining > timedelta(days=7):

            candidate = (
                current
                + timedelta(days=7)
            )

        else:

            candidate = (
                current
                + timedelta(days=1)
            )

    else:

        return None

    # --------------------------------------------------------
    # Не отправляем напоминание после срока.
    # --------------------------------------------------------

    if candidate >= expires:

        return None

    # --------------------------------------------------------
    # Ночью переносим на 13:00.
    # --------------------------------------------------------

    candidate = reminder_time_allowed(
        candidate
    )

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
                    callback_data=(
                        f"complete:{task_id}"
                    ),
                )
            ]
        ]
    )


# ============================================================
# ДОБАВИТЬ ЗАДАЧУ
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
# СОЗДАНИЕ ЗАДАЧИ
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
            "Не удалось определить задачу.\n"
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

    temporary_task = {
        "deadline_type": deadline_type,
        "created_at": dt_to_str(
            created_at
        ),
        "expires_at": dt_to_str(
            expires_at
        ),
    }

    next_reminder = (
        calculate_next_reminder(
            temporary_task
        )
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
    # Бонус за постановку задачи.
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

    # --------------------------------------------------------
    # Удаляем состояние пользователя.
    # --------------------------------------------------------

    db.execute(
        """
        DELETE FROM user_states
        WHERE telegram_id = ?
        """,
        (user.id,),
    )

    db.commit()
    db.close()

    await query.message.reply_text(
        f"Задача «{task_name}» добавлена.\n\n"
        f"Срок: "
        f"{DEADLINE_NAMES[deadline_type]}\n"
        f"Выполнить до: "
        f"{expires_at.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"За постановку задачи начислено "
        f"{TASK_CREATION_BONUS} бонусов.",
        reply_markup=main_menu(),
    )


# ============================================================
# ПОСМОТРЕТЬ ЗАДАЧИ
# ============================================================

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
        """
        ,
        (
            update.effective_chat.id,
        ),
    ).fetchall()

    db.close()

    # --------------------------------------------------------
    # Сортируем:
    # сначала более короткие сроки,
    # затем более раннее сгорание.
    # --------------------------------------------------------

    tasks = sorted(
        tasks,
        key=lambda task: (
            DEADLINE_ORDER[
                task["deadline_type"]
            ],
            task["expires_at"],
        ),
    )

    if not tasks:

        await query.message.reply_text(
            "Активных задач нет.",
            reply_markup=main_menu(),
        )

        return

    keyboard = []

    for task in tasks:

        title = task["title"]

        # Чтобы кнопка не была огромной.
        if len(title) > 45:

            title = title[:42] + "..."

        keyboard.append(
            [
                InlineKeyboardButton(
                    (
                        f"{title} — "
                        f"{DEADLINE_NAMES[task['deadline_type']]}"
                    ),
                    callback_data=(
                        f"view:{task['id']}"
                    ),
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
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Просьба выполнена",
                        callback_data=(
                            f"complete:{task_id}"
                        ),
                    )
                ]
            ]
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
            "Эта задача уже была выполнена "
            "или сгорела."
        )

        return

    # --------------------------------------------------------
    # Проверяем, было ли уже начисление.
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
            "Бонусы за эту задачу "
            "уже были начислены."
        )

        return

    bonus = TASK_BONUSES[
        task["deadline_type"]
    ]

    current = now()

    # --------------------------------------------------------
    # Записываем выполнение.
    # --------------------------------------------------------

    try:

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

    except sqlite3.IntegrityError:

        db.close()

        await query.message.reply_text(
            "Бонусы за эту задачу "
            "уже были начислены."
        )

        return

    # --------------------------------------------------------
    # Начисляем бонус.
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Статистика.
    # --------------------------------------------------------

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
        f"Спасибо\n"
        f"Зачислено {bonus} бонусов."
    )


# ============================================================
# СГОРЕВШАЯ ЗАДАЧА
# ============================================================

async def burn_expired_task(
    context: ContextTypes.DEFAULT_TYPE,
    task,
):

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

    # --------------------------------------------------------
    # Меняем статус.
    # --------------------------------------------------------

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
    # Защита от повторного нажатия.
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
            "Бонусы за эту задачу "
            "уже были начислены."
        )

        return

    bonus = TASK_BONUSES[
        task["deadline_type"]
    ]

    current = now()

    try:

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

    except sqlite3.IntegrityError:

        db.close()

        await query.message.reply_text(
            "Бонусы за эту задачу "
            "уже были начислены."
        )

        return

    # --------------------------------------------------------
    # Начисляем бонус.
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Статистика.
    # --------------------------------------------------------

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
        f"Бонусов {account['bonuses']}\n"
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
# ПРИЗЫ
# ============================================================

async def show_prizes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

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

    text = (
        "поцелуй: 10\n"
        "рандомная вкусняшка: 100\n"
        "плацинда: 1000\n\n"
        "Введите название приза:"
    )

    await query.message.reply_text(
        text
    )


# ============================================================
# ВЫБОР ПРИЗА
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
    # Записываем покупку.
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

    # --------------------------------------------------------
    # Удаляем состояние.
    # --------------------------------------------------------

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
        f"бонусы на приз "
        f"{selected_prize}"
    )


# ============================================================
# ОБРАБОТКА ТЕКСТА
# ============================================================

async def receive_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

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
# /MAIN
# ============================================================

async def main_menu_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Команда /main.

    Показывает главное меню.
    """

    await update.message.reply_text(
        "Главное меню:",
        reply_markup=main_menu(),
    )


# ============================================================
# НАЗАД В МЕНЮ
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
# ПЛАНИРОВЩИК ЗАДАЧ
# ============================================================

async def task_scheduler(
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Проверяет задачи каждую минуту.

    1. Сгорают просроченные задачи.
    2. Отправляются напоминания.
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
            # Срок закончился.
            # ------------------------------------------------

            if current >= expires:

                await burn_expired_task(
                    context,
                    task,
                )

                continue

            # ------------------------------------------------
            # Нет запланированного напоминания.
            # ------------------------------------------------

            if not task["next_reminder_at"]:

                continue

            next_reminder = str_to_dt(
                task["next_reminder_at"]
            )

            if current < next_reminder:

                continue

            # ------------------------------------------------
            # Если сейчас ночь — переносим на 13:00.
            # ------------------------------------------------

            if not in_reminder_period(current):

                new_time = next_13_00(
                    current
                )

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
            # Отправляем напоминание.
            # ------------------------------------------------

            await context.bot.send_message(
                chat_id=task["chat_id"],
                text=reminder_text(task),
                reply_markup=(
                    completion_keyboard(
                        task["id"]
                    )
                ),
            )

            # ------------------------------------------------
            # Рассчитываем следующее.
            # ------------------------------------------------

            next_time = (
                calculate_next_reminder(
                    task
                )
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

    # --------------------------------------------------------
    # Проверяем токен.
    # --------------------------------------------------------

    if (
        not BOT_TOKEN
        or BOT_TOKEN == "ВСТАВЬ_СЮДА_ТОКЕН"
    ):

        raise RuntimeError(
            "Не указан BOT_TOKEN."
        )

    # --------------------------------------------------------
    # Проверяем папку и создаём БД.
    # --------------------------------------------------------

    check_database_directory()

    init_db()

    # --------------------------------------------------------
    # Создаём Telegram Application.
    # --------------------------------------------------------

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # ========================================================
    # КОМАНДЫ
    # ========================================================

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

    # ========================================================
    # ГЛАВНОЕ МЕНЮ
    # ========================================================

    application.add_handler(
        CallbackQueryHandler(
            add_task_start,
            pattern=r"^menu_add$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            show_tasks,
            pattern=r"^menu_tasks$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            show_account,
            pattern=r"^menu_account$",
        )
    )

    # ========================================================
    # СРОКИ
    # ========================================================

    application.add_handler(
        CallbackQueryHandler(
            create_task,
            pattern=(
                r"^deadline_"
                r"(asap|day|week|month)$"
            ),
        )
    )

    # ========================================================
    # ПРОСМОТР ЗАДАЧИ
    # ========================================================

    application.add_handler(
        CallbackQueryHandler(
            view_task,
            pattern=r"^view:\d+$",
        )
    )

    # ========================================================
    # ВЫПОЛНЕНИЕ АКТИВНОЙ ЗАДАЧИ
    # ========================================================

    application.add_handler(
        CallbackQueryHandler(
            complete_task,
            pattern=r"^complete:\d+$",
        )
    )

    # ========================================================
    # ВЫПОЛНЕНИЕ СГОРЕВШЕЙ ЗАДАЧИ
    # ========================================================

    application.add_handler(
        CallbackQueryHandler(
            complete_burned_task,
            pattern=r"^burned_complete:\d+$",
        )
    )

    # ========================================================
    # ПОТРАТИТЬ БОНУСЫ
    # ========================================================

    application.add_handler(
        CallbackQueryHandler(
            show_prizes,
            pattern=r"^spend_bonus$",
        )
    )

    # ========================================================
    # НАЗАД
    # ========================================================

    application.add_handler(
        CallbackQueryHandler(
            back_to_menu,
            pattern=r"^back_menu$",
        )
    )

    # ========================================================
    # ОБЫЧНЫЙ ТЕКСТ
    # ========================================================

    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            receive_text,
        )
    )

    # ========================================================
    # ПЛАНИРОВЩИК
    # ========================================================

    application.job_queue.run_repeating(
        task_scheduler,
        interval=60,
        first=5,
    )

    logger.info(
        "========================================"
    )

    logger.info(
        "Бот запущен"
    )

    logger.info(
        "База данных: %s",
        DB_FILE.absolute(),
    )

    logger.info(
        "Часовой пояс: %s",
        TIMEZONE,
    )

    logger.info(
        "========================================"
    )

    # ========================================================
    # POLLING
    # ========================================================

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    main()
