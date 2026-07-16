import os
import asyncio
import sqlite3
import random
from dotenv import load_dotenv

from vkbottle import Bot, Keyboard, OpenLink, Callback
from vkbottle.bot import Message
from vkbottle.tools import DocUploader
from openpyxl import Workbook

# Загрузка переменных окружения
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_TOKEN = os.getenv("GROUP_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID"))
LOG_CHAT_ID = int(os.getenv("LOG_CHAT_ID"))
PDF_PATH = os.getenv("PDF_PATH", "/app/pamphlet.pdf")

# Инициализация API
bot = Bot(token=BOT_TOKEN)
group_api = Bot(token=GROUP_TOKEN)  # Отдельный инстанс для проверки подписки


# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            is_subscribed INTEGER DEFAULT 0,
            topic TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def get_user(user_id: int) -> tuple:
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result


def upsert_user(user_id: int, first_name: str, last_name: str):
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, first_name, last_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            first_name = excluded.first_name,
            last_name = excluded.last_name
    """, (user_id, first_name, last_name))
    conn.commit()
    conn.close()


def set_subscribed(user_id: int):
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_subscribed = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def set_topic(user_id: int, topic: str):
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET topic = ? WHERE user_id = ?", (topic, user_id))
    conn.commit()
    conn.close()


init_db()


# ==================== КЛАВИАТУРЫ ====================
def get_start_keyboard() -> Keyboard:
    kb = Keyboard(one_time=False, inline=False)
    kb.add(OpenLink("https://vk.ru/comandaldpr", "ВСТУПИТЬ В СООБЩЕСТВО"))
    kb.row()
    kb.add(Callback("Я ВСТУПИЛ(А), ПРОДОЛЖИТЬ", {"cmd": "check_sub"}))
    return kb


def get_resubscribe_keyboard() -> Keyboard:
    kb = Keyboard(one_time=False, inline=False)
    kb.add(OpenLink("https://vk.ru/comandaldpr", "ВСТУПИТЬ В СООБЩЕСТВО"))
    kb.row()
    kb.add(Callback("ПРОВЕРИТЬ ЕЩЁ РАЗ", {"cmd": "check_sub"}))
    return kb


def get_topics_keyboard() -> Keyboard:
    kb = Keyboard(one_time=False, inline=True)
    topics = [
        "Пенсия и надбавки",
        "Льготы на ЖКУ",
        "Налоги",
        "Лекарства и лечение",
        "Стаж и пенсионные баллы",
        "Другая тема",
        "Пока вопросов нет"
    ]
    for topic in topics:
        kb.add(Callback(topic, {"cmd": "topic", "topic": topic}))
        kb.row()
    return kb


def get_persistent_keyboard() -> Keyboard:
    kb = Keyboard(one_time=False, inline=False)
    kb.add(Callback("📥 СКАЧАТЬ ПАМЯТКУ", {"cmd": "get_pdf"}))
    kb.row()
    kb.add(OpenLink("https://vk.me/join/1OduOEjaYgO4M1LNGDRALl/piNhVvRZQpdc=", "ВСТУПИТЬ В ЧАТ"))
    return kb


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def get_pdf_attachment() -> str:
    """Загружает PDF в документы ВК и возвращает строку attachment"""
    if not os.path.exists(PDF_PATH):
        return ""
    try:
        uploader = DocUploader(bot.api)
        doc = await uploader.upload(PDF_PATH)
        return doc
    except Exception as e:
        print(f"Ошибка загрузки PDF: {e}")
        return ""


async def log_to_chat(user_id: int, topic: str, first_name: str):
    """Отправляет сообщение в чат логов"""
    try:
        await bot.api.messages.send(
            peer_id=LOG_CHAT_ID,
            message=f"📊 Пользователь [id{user_id}|{first_name}] выбрал тему: {topic}",
            random_id=0
        )
    except Exception as e:
        print(f"Ошибка отправки в лог: {e}")


async def delayed_chat_invite(user_id: int):
    """Задержка 5-10 секунд перед приглашением в чат"""
    delay = random.randint(5, 10)
    await asyncio.sleep(delay)

    kb = Keyboard(one_time=False, inline=False)
    kb.add(OpenLink("https://vk.me/join/1OduOEjaYgO4M1LNGDRALl/piNhVvRZQpdc=", "ВСТУПИТЬ В ЧАТ"))

    text = (
        "Мы открываем чат «ЛДПР пенсионеры России». Здесь пенсионеры и их близкие смогут:\n\n"
        "🔹 рассказывать о проблемах с выплатами, льготами, медициной и ЖКХ;\n"
        "🔹 задавать вопросы;\n"
        "🔹 делиться личным опытом;\n"
        "🔹 предлагать решения;\n"
        "🔹 узнавать о новых инициативах Большой команды ЛДПР.\n\n"
        "Истории участников помогут нам увидеть, с какими трудностями люди сталкиваются в разных регионах, и добиваться конкретных решений."
    )
    try:
        await bot.api.messages.send(
            user_id=user_id,
            message=text,
            keyboard=kb.get_json(),
            random_id=0
        )
    except Exception as e:
        print(f"Ошибка отправки приглашения в чат: {e}")


# ==================== ОБРАБОТЧИКИ ====================

@bot.on.message(text="/start")
@bot.on.message(payload={"cmd": "start"})
async def start_handler(message: Message):
    # Получаем данные пользователя
    user_info = await bot.api.users.get(message.from_id)
    first_name = user_info[0].first_name
    last_name = user_info[0].last_name

    upsert_user(message.from_id, first_name, last_name)

    text = (
        f"Здравствуйте, {first_name}!\n\n"
        "Большая команда ЛДПР подготовила памятку о выплатах и льготах для пенсионеров на 2026 год. Внутри вы сможете проверить:\n\n"
        "🔹 надбавки к пенсии;\n"
        "🔹 льготы на ЖКУ и налоги;\n"
        "🔹 доплаты за стаж и иждивенцев;\n"
        "🔹 льготные лекарства и санаторное лечение;\n"
        "🔹 куда обращаться и какие документы подготовить.\n\n"
        "Чтобы получить памятку, вступите в сообщество «Большая команда ЛДПР»."
    )

    await message.answer(text, keyboard=get_start_keyboard().get_json())


@bot.on.message(payload={"cmd": "check_sub"})
async def check_sub_handler(message: Message):
    user_id = message.from_id
    user_data = get_user(user_id)

    # Если уже подписан по нашей БД, сразу переходим к выдаче
    if user_data and user_data[3] == 1:
        await send_step_4(message)
        return

    # Проверка через API группы (отдельный токен)
    try:
        is_member = await group_api.api.groups.is_member(group_id=GROUP_ID, user_id=user_id)
        # is_member возвращает: 0 - не состоит, 1 - состоит, 2 - отказался, 3 - ожидает
        if is_member == 1:
            set_subscribed(user_id)
            await send_step_4(message)
        else:
            await message.answer(
                "Пока подписка на сообщество не подтверждена. Вступите в «Большую команду ЛДПР», затем нажмите кнопку проверки ещё раз.\n\n"
                "В сообществе мы рассказываем о мерах поддержки, инициативах ЛДПР и возможностях обратиться за помощью.",
                keyboard=get_resubscribe_keyboard().get_json()
            )
    except Exception as e:
        print(f"Ошибка проверки подписки: {e}")
        # Fallback: если платформа не позволяет проверить, ведём дальше (как указано в ТЗ)
        set_subscribed(user_id)
        await send_step_4(message)


async def send_step_4(message: Message):
    attachment = await get_pdf_attachment()

    text1 = (
        "Готово! Ваша памятка:\n"
        "📘 «Выплаты и льготы пенсионерам. Памятка 2026»\n\n"
        "В ней собраны основные выплаты, надбавки и льготы, которые стоит проверить. Сохраните памятку и отправьте её родным и знакомым — эта информация может помочь им получить положенную поддержку."
    )

    if attachment:
        await message.answer(text1, attachment=attachment,
                             keyboard=get_persistent_keyboard().get_json())
    else:
        await message.answer(text1 + "\n\n(Файл временно недоступен, обратитесь к администратору)",
                             keyboard=get_persistent_keyboard().get_json())

    # Короткое сообщение сразу после выдачи
    text2 = "С какой темой вы хотели бы разобраться?"
    await message.answer(text2, keyboard=get_topics_keyboard().get_json())


@bot.on.message(payload={"cmd": "get_pdf"})
async def get_pdf_handler(message: Message):
    attachment = await get_pdf_attachment()
    if attachment:
        await message.answer("📘 Ваша памятка:", attachment=attachment)
    else:
        await message.answer("Файл временно недоступен, обратитесь к администратору.")


@bot.on.message(payload={"cmd": "topic", "topic": str})
async def topic_handler(message: Message):
    user_id = message.from_id
    topic = message.payload["topic"]
    user_data = get_user(user_id)

    # Проверка: на вопросы можно ответить только один раз
    if user_data and user_data[4] is not None:
        await message.answer(
            "Вы уже выбрали тему ранее. Мы учтём это при работе с вашими обращениями. Вы всегда можете вступить в наш чат для обсуждения.",
            keyboard=get_persistent_keyboard().get_json())
        return

    # Сохраняем тему
    set_topic(user_id, topic)

    # Логируем
    first_name = user_data[1] if user_data and user_data[1] else "Пользователь"
    await asyncio.create_task(log_to_chat(user_id, topic, first_name))

    await message.answer(
        f"Спасибо! Вы выбрали тему: «{topic}». Мы уже готовим для вас полезную информацию. А пока вы можете вступить в наш чат или скачать памятку.",
        keyboard=get_persistent_keyboard().get_json())

    # Запускаем отложенное приглашение в чат (Шаг 5)
    await asyncio.create_task(delayed_chat_invite(user_id))


@bot.on.message(text="/export")
async def export_handler(message: Message):
    if message.from_id != ADMIN_ID:
        return  # Бот не реагирует на текст от не-админов (и вообще на любой другой текст)

    await message.answer("Формирую отчёт, подождите...")

    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id, first_name, last_name, is_subscribed, topic, created_at FROM users")
    rows = cursor.fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Пользователи"
    ws.append(["ID пользователя", "Имя", "Фамилия", "Подписан на группу", "Выбранная тема",
               "Дата регистрации"])

    for row in rows:
        user_id, first_name, last_name, is_sub, topic, created_at = row
        ws.append([
            user_id,
            first_name or "",
            last_name or "",
            "Да" if is_sub == 1 else "Нет",
            topic or "Не выбрана",
            created_at
        ])

    export_filename = "users_export.xlsx"
    wb.save(export_filename)

    try:
        uploader = DocUploader(bot.api)
        doc = await uploader.upload(export_filename)
        await message.answer("Отчёт успешно сформирован:", attachment=doc)

        # Удаляем локальный файл после отправки
        if os.path.exists(export_filename):
            os.remove(export_filename)
    except Exception as e:
        await message.answer(f"Ошибка при создании отчёта: {e}")


# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    print("Бот запущен. Игнорирует обычные текстовые сообщения, реагирует только на /start, /export и кнопки.")
    bot.run_forever()
