import os
import asyncio
import sqlite3
import random
import logging
import json
from dotenv import load_dotenv
from openpyxl import Workbook

from vkbottle import Bot, Keyboard, OpenLink, Callback, DocMessagesUploader
from vkbottle.bot import Message
from vkbottle_types import GroupEventType, GroupTypes

# ==================== НАСТРОЙКА ЛОГГИРОВАНИЯ ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_TOKEN = os.getenv("GROUP_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID"))
LOG_CHAT_ID = int(os.getenv("LOG_CHAT_ID"))
PDF_PATH = os.getenv("PDF_PATH", "/app/pamphlet.pdf")

bot = Bot(token=BOT_TOKEN)


# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect("bot.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT DEFAULT 'Пользователь',
            last_name TEXT DEFAULT '',
            step INTEGER DEFAULT 0,
            is_subscribed INTEGER DEFAULT 0,
            topic TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")


def get_user(user_id: int) -> sqlite3.Row:
    conn = sqlite3.connect("bot.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result


def upsert_user(user_id: int, first_name: str, last_name: str, step: int = 1):
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, first_name, last_name, step)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            step = excluded.step
    """, (user_id, first_name, last_name, step))
    conn.commit()
    conn.close()
    logger.info(f"Пользователь {user_id} обновлен: {first_name}, step={step}")


def set_step(user_id: int, step: int):
    conn = sqlite3.connect("bot.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET step = ? WHERE user_id = ?", (step, user_id))
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


# ==================== КЛАВИАТУРЫ (INLINE) ====================
def get_start_keyboard() -> Keyboard:
    kb = Keyboard(one_time=False, inline=True)
    kb.add(OpenLink("https://vk.ru/comandaldpr", "ВСТУПИТЬ В СООБЩЕСТВО"))
    kb.row()
    kb.add(Callback("✅ Я ВСТУПИЛ(А), ПРОДОЛЖИТЬ", {"cmd": "check_sub"}))
    return kb


def get_resubscribe_keyboard() -> Keyboard:
    kb = Keyboard(one_time=False, inline=True)
    kb.add(OpenLink("https://vk.ru/comandaldpr", "ВСТУПИТЬ В СООБЩЕСТВО"))
    kb.row()
    kb.add(Callback("🔄 ПРОВЕРИТЬ ЕЩЁ РАЗ", {"cmd": "check_sub"}))
    return kb


def get_topics_keyboard() -> Keyboard:
    kb = Keyboard(one_time=False, inline=True)
    topics = [
        "Пенсия и надбавки", "Льготы на ЖКУ", "Налоги",
        "Лекарства и лечение", "Стаж и пенсионные баллы",
        "Другая тема", "Пока вопросов нет"
    ]
    for idx, topic in enumerate(topics):
        kb.add(Callback(topic, {"cmd": "topic", "topic": topic}))
        if idx == 4:
            kb.row()
        if idx % 2 == 1:
            kb.row()

    return kb


def get_persistent_keyboard(first_time: bool = False) -> Keyboard:
    kb = Keyboard(one_time=False, inline=True)
    kb.add(OpenLink(
        "https://comanda-products.hb.ru-msk.vkcloud-storage.ru/others/LDPR.pdf",
        "СКАЧАТЬ ПАМЯТКУ")
    )
    kb.row()
    if not first_time:
        kb.add(OpenLink("https://vk.me/join/1OduOEjaYgO4M1LNGDRALl/piNhVvRZQpdc=", "ВСТУПИТЬ В ЧАТ"))
    return kb


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def send_message(ctx_api, peer_id: int, text: str, keyboard: Keyboard = None, attachment: str = None):
    """Унифицированная отправка сообщений через ctx_api, как в примере"""
    data = {"peer_id": peer_id, "message": text, "random_id": 0}
    if keyboard:
        data["keyboard"] = keyboard.get_json()
    if attachment:
        data["attachment"] = attachment
    await ctx_api.messages.send(**data)


async def answer_callback(ctx_api, event_id: int, user_id: int, peer_id: int):
    """Обязательный ответ на callback, чтобы убрать загрузку с кнопки"""
    try:
        await ctx_api.messages.send_message_event_answer(
            event_id=event_id, user_id=user_id, peer_id=peer_id
        )
    except Exception as e:
        logger.error(f"Ошибка ответа на callback: {e}")


def get_payload(event: GroupTypes.MessageEvent) -> dict:
    """Безопасное извлечение payload из события"""
    try:
        payload = event.object.payload
        return json.loads(payload) if isinstance(payload, str) else payload
    except Exception:
        return {}


async def get_pdf_attachment(peer_id: int) -> str:
    """Загрузка документа через DocMessagesUploader с указанием peer_id"""
    if not os.path.exists(PDF_PATH):
        logger.warning(f"PDF файл не найден: {PDF_PATH}")
        return ""
    try:
        uploader = DocMessagesUploader(bot.api)
        doc = await uploader.upload(PDF_PATH, peer_id=peer_id)
        return doc
    except Exception as e:
        logger.error(f"Ошибка загрузки PDF: {e}")
        return ""


# ==================== ЛОГИКА ШАГОВ ====================
async def process_step_1(ctx_api, user_id: int, peer_id: int):
    try:
        # Явное указание user_ids для корректной работы API
        user_info = await ctx_api.users.get(user_ids=user_id)
        first_name = user_info[0].first_name if user_info else "Пользователь"
        last_name = user_info[0].last_name if user_info else ""
    except Exception as e:
        logger.error(f"Ошибка получения данных пользователя {user_id}: {e}")
        first_name, last_name = "Пользователь", ""

    upsert_user(user_id, first_name, last_name, step=1)

    text = (
        f"Здравствуйте, {first_name}!\n\n"
        "Большая команда ЛДПР подготовила памятку о выплатах и льготах для пенсионеров на 2026 "
        "год.\n\nВнутри вы сможете проверить:\n\n"
        "🔹 надбавки к пенсии;\n🔹 льготы на ЖКУ и налоги;\n🔹 доплаты за стаж и иждивенцев;\n🔹 льготные лекарства и санаторное лечение;\n🔹 куда обращаться и какие документы подготовить.\n\n"
        "Чтобы получить памятку, вступите в сообщество «Большая команда ЛДПР»."
    )
    await send_message(ctx_api, peer_id, text, keyboard=get_start_keyboard())


async def process_check_sub(ctx_api, user_id: int, peer_id: int, event_id: int):
    await answer_callback(ctx_api, event_id, user_id, peer_id)

    user = get_user(user_id)
    if user and user["is_subscribed"] == 1:
        set_step(user_id, 2)
        await process_step_4(ctx_api, user_id, peer_id)
        return

    try:
        is_member = await ctx_api.groups.is_member(group_id=GROUP_ID, user_id=user_id)
        if is_member == 1:
            set_subscribed(user_id)
            set_step(user_id, 2)
            await process_step_4(ctx_api, user_id, peer_id)
        else:
            await send_message(
                ctx_api, peer_id,
                "Пока подписка на сообщество не подтверждена. Вступите в «Большую команду ЛДПР», затем нажмите кнопку проверки ещё раз.",
                keyboard=get_resubscribe_keyboard()
            )
    except Exception as e:
        logger.error(f"Ошибка проверки подписки для {user_id}: {e}")
        # Fallback
        set_subscribed(user_id)
        set_step(user_id, 2)
        await process_step_4(ctx_api, user_id, peer_id)


async def process_step_4(ctx_api, user_id: int, peer_id: int):
    attachment = await get_pdf_attachment(peer_id)
    text1 = (
        "Готово! Ваша памятка:\n📘 «Выплаты и льготы пенсионерам. Памятка 2026»\n\n"
        "В ней собраны основные выплаты, надбавки и льготы. Сохраните её и отправьте родным."
    )

    if attachment:
        await send_message(ctx_api, peer_id, text1, keyboard=get_persistent_keyboard(True),
                           attachment=attachment)
    else:
        await send_message(ctx_api, peer_id, text1 +
                           "\n\nhttps://disk.yandex.ru/i/BrsJSevE_AsnFA", keyboard=get_persistent_keyboard(True))

    await send_message(ctx_api, peer_id, "С какой темой вы хотели бы разобраться?", keyboard=get_topics_keyboard())


async def process_get_pdf(ctx_api, user_id: int, peer_id: int, event_id: int):
    await answer_callback(ctx_api, event_id, user_id, peer_id)
    attachment = await get_pdf_attachment(peer_id)
    if attachment:
        await send_message(ctx_api, peer_id, "📘 Ваша памятка:", attachment=attachment)
    else:
        await send_message(
            ctx_api, peer_id, "📘 Ваша памятка:\nhttps://disk.yandex.ru/i/BrsJSevE_AsnFA"
        )


async def process_topic(ctx_api, user_id: int, peer_id: int, topic: str, event_id: int):
    await answer_callback(ctx_api, event_id, user_id, peer_id)

    user = get_user(user_id)
    if user and user["topic"] is not None:
        await send_message(ctx_api, peer_id, "Вы уже выбрали тему ранее. Мы учтём это в работе!",
                           keyboard=get_persistent_keyboard())
        return

    set_topic(user_id, topic)
    set_step(user_id, 3) # Регистрация завершена
    first_name = user["first_name"] if user["first_name"] else "Пользователь"
    await send_message(
        ctx_api, peer_id,
        f'{first_name}, выбрали тему "{topic}". Мы учтём это в работе!\n\n'
    )
    first_name = user["first_name"] if user and user["first_name"] else "Пользователь"

    # 2. Логирование
    try:
        await ctx_api.messages.send(
            peer_id=LOG_CHAT_ID,
            message=f"📊 Пользователь [id{user_id}|{first_name}] выбрал тему: {topic}",
            random_id=0
        )
        logger.info(f"Лог: пользователь {user_id}, тема: {topic}")
    except Exception as e:
        logger.error(f"Ошибка отправки в лог: {e}")

    # 3. Задержка и приглашение в чат (строго последовательно, как просили)
    delay = random.randint(5, 10)
    logger.info(f"Ожидание {delay} сек. перед приглашением пользователя {user_id} в чат")
    await asyncio.sleep(delay)

    kb = Keyboard(one_time=False, inline=True)
    kb.add(OpenLink("https://vk.me/join/1OduOEjaYgO4M1LNGDRALl/piNhVvRZQpdc=", "ВСТУПИТЬ В ЧАТ"))

    text_invite = (
        "Мы открываем чат «ЛДПР пенсионеры России». Здесь пенсионеры и их близкие смогут:\n\n"
        "🔹 рассказывать о проблемах с выплатами, льготами, медициной и ЖКХ;\n"
        "🔹 задавать вопросы;\n🔹 делиться личным опытом;\n🔹 предлагать решения;\n"
        "🔹 узнавать о новых инициативах Большой команды ЛДПР.\n\n"
        "Истории участников помогут нам увидеть, с какими трудностями люди сталкиваются в разных регионах, и добиваться конкретных решений."
    )

    await send_message(ctx_api, peer_id, text_invite, keyboard=kb)
    logger.info(f"Приглашение в чат отправлено пользователю {user_id}")


# ==================== ОБРАБОТЧИКИ СОБЫТИЙ ====================
@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, GroupTypes.MessageEvent)
async def handle_callbacks(event: GroupTypes.MessageEvent):
    """Централизованная обработка всех inline кнопок (Callback)"""
    payload = get_payload(event)
    cmd = payload.get("cmd")
    peer_id = event.object.peer_id
    user_id = event.object.user_id

    if cmd == "check_sub":
        await process_check_sub(event.ctx_api, user_id, peer_id, event.object.event_id)
    elif cmd == "get_pdf":
        await process_get_pdf(event.ctx_api, user_id, peer_id, event.object.event_id)
    elif cmd == "topic":
        topic = payload.get("topic")
        if topic:
            await process_topic(event.ctx_api, user_id, peer_id, topic, event.object.event_id)


@bot.on.message(text="/export")
async def export_handler(message: Message):
    if message.from_id != ADMIN_ID:
        return

    logger.info(f"Админ {message.from_id} запросил экспорт данных")
    await send_message(message.ctx_api, message.peer_id, "Формирую отчёт, подождите...")

    conn = sqlite3.connect("bot.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, first_name, last_name, is_subscribed, topic, created_at FROM users")
    rows = cursor.fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Пользователи"
    ws.append(["ID", "Имя", "Фамилия", "Подписан", "Тема", "Дата"])

    for row in rows:
        ws.append([
            row["user_id"],
            row["first_name"] or "",
            row["last_name"] or "",
            "Да" if row["is_subscribed"] == 1 else "Нет",
            row["topic"] or "Не выбрана",
            row["created_at"]
        ])

    export_filename = "users_export.xlsx"
    wb.save(export_filename)

    try:
        uploader = DocMessagesUploader(bot.api)
        doc = await uploader.upload(export_filename, peer_id=message.peer_id)
        await send_message(message.ctx_api, message.peer_id, "Отчёт успешно сформирован:",
                           attachment=doc)
        if os.path.exists(export_filename):
            os.remove(export_filename)
        logger.info("Экспорт завершён")
    except Exception as e:
        logger.error(f"Ошибка экспорта: {e}")
        await send_message(message.ctx_api, message.peer_id, f"Ошибка: {e}")


@bot.on.message()
async def fallback_handler(message: Message):
    """
    Гарантирует, что бот ВСЕГДА ответит, если пользователь написал текст,
    исходя из его текущего шага (step) в БД.
    """
    if message.from_id < 0:
        return  # Игнорируем беседы

    if message.from_id == ADMIN_ID and message.text == "/export":
        return  # Уже обработан выше

    user = get_user(message.from_id)

    if not user or user["step"] == 0:
        logger.info(f"Новый пользователь или сброс: {message.from_id}")
        await process_step_1(message.ctx_api, message.from_id, message.peer_id)

    elif user["step"] == 1:
        logger.info(f"Пользователь {message.from_id} на шаге 1. Напоминаем о подписке.")
        await send_message(
            message.ctx_api, message.peer_id,
            "Чтобы получить памятку, пожалуйста, подтвердите подписку на сообщество, нажав кнопку ниже.",
            keyboard=get_start_keyboard()
        )

    elif user["step"] == 2:
        logger.info(f"Пользователь {message.from_id} на шаге 2. Напоминаем о выборе темы.")
        await send_message(
            message.ctx_api, message.peer_id,
            "Вы уже получили памятку! Пожалуйста, выберите тему, которая вас интересует, чтобы мы могли помочь вам точнее:",
            keyboard=get_topics_keyboard()
        )

    elif user["step"] == 3:
        first_name = user["first_name"] if user["first_name"] else "Пользователь"
        await send_message(
            message.ctx_api, message.peer_id,
            f"{first_name}, выбрали тему. Мы учтём это в работе!\n\n"
        )


# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    logger.info("Бот запущен. Используется ctx_api, DocMessagesUploader и корректная обработка Callback событий.")
    bot.run()