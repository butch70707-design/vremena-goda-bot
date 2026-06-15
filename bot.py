"""
Telegram-бот «Времена Года» — помощник для гостей загородных домов.
Поддерживает голосовые сообщения (OpenAI Whisper), напоминания о выезде и запрос отзывов.

Требования:
    pip install python-telegram-bot anthropic python-dotenv openai

Запуск:
    python bot.py
"""

import os
import json
import logging
import tempfile
import asyncio
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
import anthropic
from openai import OpenAI

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
OWNER_ID        = os.getenv("OWNER_TELEGRAM_ID")   # числовой ID хозяина (не @username)

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
openai_client    = OpenAI(api_key=OPENAI_API_KEY)

# Файл для хранения гостей между перезапусками
GUESTS_FILE = "guests.json"

# Состояния диалога /newguest
ASK_HOUSE, ASK_USERNAME, ASK_DATE = range(3)

# Ссылки на отзывы по домам
REVIEW_LINKS = {
    "10":  "https://yandex.ru/maps/org/vremena_goda/212845641563",
    "73":  "https://yandex.ru/maps/org/vremena_goda/125103399564",
    "74":  "https://yandex.ru/maps/org/vremena_goda/125103399564",
    "54":  "https://yandex.ru/maps/org/vremena_goda/70428628450",
}

# ─────────────────────────────────────────────
# Хранилище гостей
# ─────────────────────────────────────────────

def load_guests() -> list:
    if os.path.exists(GUESTS_FILE):
        with open(GUESTS_FILE) as f:
            return json.load(f)
    return []

def save_guests(guests: list):
    with open(GUESTS_FILE, "w") as f:
        json.dump(guests, f, ensure_ascii=False, indent=2)

def add_guest(house: str, username: str, checkout_date: str):
    guests = load_guests()
    guests.append({
        "house": house,
        "username": username,
        "checkout_date": checkout_date,
        "reminded": False,
        "reviewed": False,
    })
    save_guests(guests)

# ─────────────────────────────────────────────
# Системный промпт
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """
Ты — дружелюбный помощник-консьерж загородных домов «Времена Года» (Ступинский район МО).
Отвечай тепло, с эмодзи, по-русски. Будь краток и конкретен.
Если вопрос выходит за рамки базы знаний — предложи написать хозяину напрямую.

═══════════════════════════════════════════════
🏠 ДОМА И ДЕТАЛИ
═══════════════════════════════════════════════

## Дом 73 — КП Семёновское (до 6 гостей, сауна)
- Адрес: МО, Ступинский р-н, КП ТСН Семёновское, д.73
- Карты: https://yandex.ru/maps/?text=55.073800%2C37.752027
- Въезд: ворота автоматические — звонить хозяину, откроет; пульт на ключах
- Навигация: после въезда налево на второй поворот, 500м прямо, дом 73 справа
- Парковка: 5–6 машин вдоль дома и перед входом
- Сейф: слева от двери, шторка → код → ключ
- Wi-Fi: SNR-CPE-E281 / Пароль: Nw551TfXdMwm
- Сауна: 5 000₽/сеанс 3–4ч, включается удалённо после оплаты; русская баня: 300–400мл воды в чашу печки
- Шашлычный набор: 3 000₽ (уголь 5кг, решётка, розжиг, шампуры 6шт, горелка, опахало, фонарь)
- Дрова: 500₽/сетка (5шт берёза)
- Залог: возвращается в день после выезда
- Тишина: 22:00–10:00 и 13:00–15:00
- Промокод: «ВременаГода» — друзьям скидка от 2 000₽

## Дом 10 — КП Семёновское (баня + джакузи + сибирский чан)
- Адрес: МО, Ступинский р-н, КП ТСН Семёновское, д.10
- Карты: https://yandex.ru/maps/org/vremena_goda/212845641563
- Въезд: ворота автоматические — звонить хозяину; пропуск Гринвуд и пульт в доме (утрата 5 000₽, оставить при выезде)
- Навигация: после въезда вниз до конца, налево — дом 10 справа (графит с красной полосой)
- Парковка: 5–6 машин
- Сейф: у двери, шторка → код → ключ; ручки вверх для закрытия
- Wi-Fi: MyHome10 / Пароль: DVB4218CN
- Окна: открывать только на «зимнее» проветривание (ручка 180°)
- Баня/сауна: 5 000₽/сеанс 3–4ч, звонить за 4 часа до; русская баня: 300–400мл воды в чашу
- Сибирский чан: 7 000₽/топка (40–45°); баня + чан 2+ сеанса — скидка 30%
- Бассейн (лето): 5 000₽/сутки
- Шашлычный набор: 3 000₽
- Дрова: 500₽/сетка
- Залог: 15 000₽, возвращается в день после выезда
- Тишина: 22:00–10:00 и 13:00–15:00
- Промокод: «Времена года» — друзьям скидка 30% на баню/чан, хозяину 2 500₽ бонусов

## Дом 54 — КП Бекетово-парк (сауна + сибирский чан)
- Адрес: МО, Ступинский р-н, КП «Бекетово-парк», А54
- Карты: https://yandex.ru/maps/?text=55.029082,37.774101
- Въезд: прислать марку и номер авто заранее для пропуска; пропуск Гринвуд в доме (утрата 5 000₽)
- Навигация: после въезда направо, сразу налево, прямо до первого поворота направо, до конца улицы — дом А54 справа
- Парковка: 2–3 машины или вдоль дороги у забора
- Сейф: слева от ворот → код → ключ → пульт ворот → дверь дома
- Wi-Fi: Vremena_Goda_A54 / Пароль: 123456789
- Сибирский чан: 7 000₽/топка
- Дрова: 500₽/сетка
- Мусор: зелёный бак у забора (вывозят утром)
- Залог: возвращается в день после выезда
- Взять с собой: уголь и розжиг, тапочки, детское постельное бельё

## Дом 74 — КП Семёновское (сауна)
- Адрес: МО, Ступинский р-н, КП ТСН Семёновское, д.74
- Карты: https://yandex.ru/maps/?text=55.073800%2C37.752027
- Въезд: ворота — звонить хозяину, откроет; вывеска «Семёновское»
- Навигация: после въезда налево на второй поворот, 500м прямо, дом 74 справа
- Парковка: 5–6 машин
- Сейф: слева от двери, шторка → код → ключ
- Wi-Fi: MyHome74-5G / Пароль: Nw551TfXdMwm
- Сауна: 5 000₽/сеанс 3–4ч, включается удалённо после оплаты
- Дрова: 500₽/сетка
- Залог: возвращается в день после выезда
- В наличии: соль, перец, масло, сахар, чай, кофе ✅
- Взять с собой: уголь и розжиг, тапочки, детское постельное бельё, молотый кофе для кофемашины
- Промокод: «MyHome» — друзьям скидка 30% на баню/сауну

═══════════════════════════════════════════════
⏰ ОБЩЕЕ ДЛЯ ВСЕХ ДОМОВ
═══════════════════════════════════════════════

Заезд: с 16:00 | Выезд: до 12:00
🟢 Ранний заезд (с 12:00): +3 000₽ (при наличии свободного дома)
🔴 Поздний выезд до 18:00: +3 000₽

Заселение: самостоятельное через мини-сейф; код высылается в день заезда (не раньше!)
При выезде: ключ вернуть в сейф, «спрятать» код.

═══════════════════════════════════════════════
❌ ПРАВИЛА
═══════════════════════════════════════════════
- Нельзя: курить внутри (сигареты, вейп, кальян, айкос), ходить в уличной обуви, пиротехника
- Нельзя: гостей больше чем в бронировании (камеры на входе)
- Нельзя: мыть машины, накрывать конвекторы, оставлять мусор на участке
- Мангал — только в мангальной зоне, не на террасе
- В унитаз — ничего постороннего (штраф за септик 5 000–22 000₽)
- Штраф за каждое нарушение: 10 000₽
- Животные: по согласованию, +1 500₽/сутки
- Тишина: 22:00–10:00 и 13:00–15:00 (все дома)

═══════════════════════════════════════════════
💰 ЦЕНЫ НА ДОПУСЛУГИ
═══════════════════════════════════════════════
- Баня/сауна (3–4ч): 5 000₽
- Сибирский чан (1 топка): 7 000₽
- Баня + чан (2+ сеанса): скидка 30%
- Бассейн (лето, д.10): 5 000₽/сутки
- Шашлычный набор: 3 000₽
- Дрова (сетка 5шт): 500₽
- Веник/запарка для чана: 1 000₽/шт
- Утрата пропуска/пульта: 5 000₽
- Дрова из-под дома (без заказа): 1 000₽ из залога
Вопросы: @butch707

═══════════════════════════════════════════════
💳 ОПЛАТА
═══════════════════════════════════════════════
По номеру телефона (Сбер/Тинькофф):
  Сергей Алексеевич Б., +79257146859 (попросить прислать чек)

По реквизитам ИП (Альфа-банк, крупные суммы):
  ИП Басов Сергей Алексеевич
  ИНН 773472969084 / Р/с 40802810901300019371
  АО «АЛЬФА-БАНК», БИК 044525593
  Назначение: «Посуточная аренда дома»

Наличные — возможны, если хозяин будет на месте.

═══════════════════════════════════════════════
🔧 ТЕХНИЧЕСКИЕ НЕПОЛАДКИ
═══════════════════════════════════════════════
- Свет выбило: автоматы на верхнем щитке; причина — перегрузка (фен + сауна + кондиционер одновременно)
- Вода не уходит: открыть воду на кухне → проверить; открыть техничку (код 2222) → сфотографировать щит → проверить кнопку насоса (мигает красная точка)
- Телевизор: кнопка включения под подсветкой телевизора
- Алиса не отвечает: вытащить из розетки на 5 мин или держать кнопку паузы 5 сек
- Холодно: тёплый пол греется 3–5 часов; есть жёлтая тепловая пушка под террасой (при пушке — выключить кондиционер)
- Сауна: греется ~3 часа до 78–80°С — включать заблаговременно
- Горячей воды нет: написать хозяину — должна быть

═══════════════════════════════════════════════
📍 ДОСТОПРИМЕЧАТЕЛЬНОСТИ
═══════════════════════════════════════════════
- Лесная прогулка к роднику: https://yandex.ru/navi/?whatshere[point]=37.757253,55.081716
- Лесное озеро: https://yandex.ru/navi/?whatshere[point]=37.770354,55.083829
- Ресторан, конюшня, кафе, детская площадка: https://yandex.ru/navi/?whatshere[point]=37.744501,55.082321
- Детская площадка с верёвочным парком: https://yandex.ru/navi/?whatshere[point]=37.755915,55.075154
- Рыбалка (Русская рыбалка, Чехов): https://yandex.ru/maps/org/sportivny_klub_russkaya_rybalka/161302815921

🗑️ МУСОР:
- Дома 73, 74, 10: баки ТБО на выезде из посёлка
- Дом 54: зелёный бак у забора

═══════════════════════════════════════════════
ВАЖНО: Коды от сейфов высылаются в день заезда — НИКОГДА раньше!
Если вопрос сложный или нестандартный — предложи написать хозяину лично: +79257146859 или Telegram @butch707.
"""

user_conversations: dict[int, list] = {}
MAX_HISTORY = 10

# ─────────────────────────────────────────────
# Меню /start
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🏠 Выбрать дом", callback_data="choose_house"),
            InlineKeyboardButton("⏰ Заезд/выезд", callback_data="checkin"),
        ],
        [
            InlineKeyboardButton("🔥 Баня / чан", callback_data="banya"),
            InlineKeyboardButton("🌐 Wi-Fi", callback_data="wifi"),
        ],
        [
            InlineKeyboardButton("💰 Цены", callback_data="prices"),
            InlineKeyboardButton("❌ Правила", callback_data="rules"),
        ],
        [
            InlineKeyboardButton("📍 Что посмотреть", callback_data="places"),
            InlineKeyboardButton("🆘 Проблема в доме", callback_data="problem"),
        ],
    ]
    await update.message.reply_text(
        "Добро пожаловать в «Времена Года»! 🏡\n\n"
        "Я помогу ответить на любые вопросы о вашем отдыхе.\n"
        "Выберите тему или просто напишите (или надиктуйте) вопрос 👇",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💬 Напишите или надиктуйте вопрос голосовым — я отвечу!\n\n"
        "Например:\n"
        "• «Какой пароль от Wi-Fi в доме 73?»\n"
        "• «Как включить сауну?»\n"
        "• «Когда вернут залог?»\n\n"
        "Или нажмите /start чтобы увидеть меню 🏠"
    )

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_conversations[user_id] = []
    await update.message.reply_text("🔄 История очищена. Начнём заново!")

# ─────────────────────────────────────────────
# Кнопки меню
# ─────────────────────────────────────────────
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quick_answers = {
        "choose_house": "У нас 4 дома:\n\n🏠 *Дом 10* — баня + джакузи + сибирский чан (КП Семёновское)\n🏠 *Дом 73* — сауна, до 6 гостей (КП Семёновское)\n🏠 *Дом 74* — сауна (КП Семёновское)\n🏠 *Дом 54* — сауна + сибирский чан (КП Бекетово-парк)\n\nПро какой дом хотите узнать подробнее?",
        "checkin": "⏰ *Время заезда/выезда:*\n\n✅ Заезд: с 16:00\n✅ Выезд: до 12:00\n\n🟢 Ранний заезд (с 12:00): +3 000₽\n🔴 Поздний выезд до 18:00: +3 000₽\n\nЗаселение самостоятельное — код от сейфа пришлю ко времени заезда 🔐",
        "banya": "🔥 *Баня и сибирский чан:*\n\nСауна/баня (3–4ч): *5 000₽*\nСибирский чан (1 топка): *7 000₽*\nБаня + чан (2+ сеанса): скидка *30%*\nВеник / запарка для чана: 1 000₽/шт\n\n⏳ Сауна греется ~3 часа — включаем удалённо после оплаты!\nСибирский чан — позвонить хозяину за 4 часа.",
        "wifi": "🌐 *Wi-Fi по домам:*\n\n🏠 Дом 10: `MyHome10` / `DVB4218CN`\n🏠 Дом 73: `SNR-CPE-E281` / `Nw551TfXdMwm`\n🏠 Дом 74: `MyHome74-5G` / `Nw551TfXdMwm`\n🏠 Дом 54: `Vremena_Goda_A54` / `123456789`",
        "prices": "💰 *Дополнительные услуги:*\n\nБаня/сауна (3–4ч): 5 000₽\nСибирский чан: 7 000₽\nБаня + чан (2+ раза): скидка 30%\nБассейн (лето, д.10): 5 000₽/сутки\nШашлычный набор: 3 000₽\nДрова (сетка 5шт берёза): 500₽\nВеник / запарка: 1 000₽\nЖивотное: +1 500₽/сутки\n\nОплата: Сбер/Тинькофф на +7 925 714‑68‑59\nВопросы: @butch707",
        "rules": "❌ *Правила дома:*\n\n• Нельзя курить внутри (сигареты, вейп, кальян, айкос)\n• Ходить в уличной обуви — нельзя\n• Гостей больше, чем в бронировании — нельзя (камеры)\n• Пиротехника — запрещена\n• Мангал — только в мангальной зоне\n• В унитаз — ничего постороннего\n• Тишина: 22:00–10:00 и 13:00–15:00\n\n⚠️ Штраф за каждое нарушение: 10 000₽",
        "places": "📍 *Что посмотреть рядом:*\n\n🌿 [Лесная прогулка к роднику](https://yandex.ru/navi/?whatshere[point]=37.757253,55.081716)\n🏞 [Лесное озеро](https://yandex.ru/navi/?whatshere[point]=37.770354,55.083829)\n🍽 [Ресторан, конюшня, кафе](https://yandex.ru/navi/?whatshere[point]=37.744501,55.082321)\n🎪 [Детская площадка с верёвочным парком](https://yandex.ru/navi/?whatshere[point]=37.755915,55.075154)\n🎣 [Рыбалка — Русская рыбалка, Чехов](https://yandex.ru/maps/org/sportivny_klub_russkaya_rybalka/161302815921)",
        "problem": "🆘 *Проблема в доме?*\n\nОпишите подробнее, что случилось — я помогу разобраться!\n\nИли сразу напишите хозяину:\n📞 +7 925 714‑68‑59\n✈️ @butch707\n\nЧастые решения:\n• Выбило свет → автоматы на верхнем щитке\n• Нет интернета → аварийное отключение провайдера\n• Телевизор → кнопка под подсветкой\n• Алиса не отвечает → вытащить из розетки на 5 мин",
    }

    if query.data in quick_answers:
        await query.edit_message_text(
            quick_answers[query.data],
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    else:
        await query.edit_message_text("Напишите ваш вопрос, и я отвечу! 😊")

# ─────────────────────────────────────────────
# Claude
# ─────────────────────────────────────────────
async def ask_claude(user_id: int, user_text: str) -> str:
    if user_id not in user_conversations:
        user_conversations[user_id] = []
    user_conversations[user_id].append({"role": "user", "content": user_text})
    if len(user_conversations[user_id]) > MAX_HISTORY:
        user_conversations[user_id] = user_conversations[user_id][-MAX_HISTORY:]
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=user_conversations[user_id],
    )
    reply = response.content[0].text
    user_conversations[user_id].append({"role": "assistant", "content": reply})
    return reply

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        reply = await ask_claude(user_id, update.message.text)
        await update.message.reply_text(reply, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text("⚠️ Временная ошибка. Напишите хозяину:\n📞 +7 925 714‑68‑59\n✈️ @butch707")

# ─────────────────────────────────────────────
# Голосовые
# ─────────────────────────────────────────────
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        await voice_file.download_to_drive(tmp_path)

        text = await asyncio.get_event_loop().run_in_executor(
            None, lambda: openai_client.audio.transcriptions.create(
                model="whisper-1", file=open(tmp_path, "rb"), language="ru"
            ).text
        )
        os.unlink(tmp_path)

        if not text.strip():
            await update.message.reply_text("🎤 Не смог разобрать. Попробуйте ещё раз или напишите текстом.")
            return

        await update.message.reply_text(f"🎤 _{text}_", parse_mode="Markdown")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        reply = await ask_claude(user_id, text)
        await update.message.reply_text(reply, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("⚠️ Не смог обработать голосовое. Напишите текстом или хозяину:\n📞 +7 925 714‑68‑59\n✈️ @butch707")

# ─────────────────────────────────────────────
# /newguest — регистрация гостя (только для хозяина)
# ─────────────────────────────────────────────
async def newguest_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏠 Дом 10", callback_data="ng_10"),
         InlineKeyboardButton("🏠 Дом 73", callback_data="ng_73")],
        [InlineKeyboardButton("🏠 Дом 74", callback_data="ng_74"),
         InlineKeyboardButton("🏠 Дом 54", callback_data="ng_54")],
    ]
    await update.message.reply_text(
        "🏠 Выберите дом для нового гостя:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ASK_HOUSE

async def newguest_house(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    house = query.data.replace("ng_", "")
    context.user_data["ng_house"] = house
    await query.edit_message_text(f"✅ Дом {house} выбран.\n\n📱 Напишите @username гостя в Telegram\n(например: @ivanov или просто ivanov):")
    return ASK_USERNAME

async def newguest_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lstrip("@")
    context.user_data["ng_username"] = username
    await update.message.reply_text(
        f"✅ Гость: @{username}\n\n📅 Укажите дату выезда в формате ДД.ММ.ГГГГ\n(например: 16.06.2026):"
    )
    return ASK_DATE

async def newguest_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        checkout = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Введите дату как ДД.ММ.ГГГГ, например: 16.06.2026")
        return ASK_DATE

    house    = context.user_data["ng_house"]
    username = context.user_data["ng_username"]
    add_guest(house, username, checkout.isoformat())

    await update.message.reply_text(
        f"✅ Гость зарегистрирован!\n\n"
        f"🏠 Дом: {house}\n"
        f"👤 @{username}\n"
        f"📅 Выезд: {checkout.strftime('%d.%m.%Y')}\n\n"
        f"Бот напомнит гостю в 9:00 и попросит отзыв после 13:00 в день выезда 🎯"
    )
    return ConversationHandler.END

async def newguest_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Регистрация отменена.")
    return ConversationHandler.END

# ─────────────────────────────────────────────
# /guests — список активных гостей
# ─────────────────────────────────────────────
async def list_guests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    guests = load_guests()
    today = date.today()
    active = [g for g in guests if date.fromisoformat(g["checkout_date"]) >= today]
    if not active:
        await update.message.reply_text("📋 Нет активных гостей.")
        return
    lines = ["📋 *Активные гости:*\n"]
    for g in active:
        d = datetime.strptime(g["checkout_date"], "%Y-%m-%d").strftime("%d.%m.%Y")
        reminded = "✅" if g["reminded"] else "⏳"
        reviewed = "⭐" if g["reviewed"] else "—"
        lines.append(f"🏠 Дом {g['house']} | @{g['username']} | выезд {d} | напомнил {reminded} | отзыв {reviewed}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ─────────────────────────────────────────────
# Автоматические напоминания (Job Queue)
# ─────────────────────────────────────────────
async def send_checkout_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет напоминание о выезде в 9:00 в день выезда"""
    guests = load_guests()
    today  = date.today().isoformat()
    changed = False

    for g in guests:
        if g["checkout_date"] == today and not g["reminded"]:
            house = g["house"]
            username = g["username"]
            # Определяем куда выбрасывать мусор
            trash = "зелёный бак у забора 🗑️" if house == "54" else "баки ТБО на выезде из посёлка 🗑️"

            msg = (
                f"🌅 Доброе утро!\n\n"
                f"Напоминаем, что сегодня день выезда из дома {house}.\n\n"
                f"📋 *Памятка при выезде:*\n"
                f"• Выезд до 12:00\n"
                f"• Ключ вернуть в сейф и «спрятать» код\n"
                f"• Мусор: {trash}\n"
                f"• Окна и двери закрыть\n\n"
                f"Спасибо, что выбрали «Времена Года»! 🏡\n"
                f"Будем рады видеть вас снова 😊"
            )
            try:
                await context.bot.send_message(
                    chat_id=f"@{username}",
                    text=msg,
                    parse_mode="Markdown"
                )
                g["reminded"] = True
                changed = True
                logger.info(f"Напоминание отправлено @{username} (дом {house})")
            except Exception as e:
                logger.error(f"Не смог отправить напоминание @{username}: {e}")

    if changed:
        save_guests(guests)


async def send_review_request(context: ContextTypes.DEFAULT_TYPE):
    """Запрашивает отзыв после 13:00 в день выезда"""
    guests = load_guests()
    today  = date.today().isoformat()
    changed = False

    for g in guests:
        if g["checkout_date"] == today and g["reminded"] and not g["reviewed"]:
            house    = g["house"]
            username = g["username"]
            link     = REVIEW_LINKS.get(house, REVIEW_LINKS["73"])

            msg = (
                f"⭐ Надеемся, вам понравилось в доме {house}!\n\n"
                f"Будем очень благодарны за отзыв — это занимает буквально минуту 🙏\n\n"
                f"{link}\n\n"
                f"Ждём вас снова! 🏡"
            )
            try:
                await context.bot.send_message(
                    chat_id=f"@{username}",
                    text=msg,
                    disable_web_page_preview=True
                )
                g["reviewed"] = True
                changed = True
                logger.info(f"Запрос отзыва отправлен @{username} (дом {house})")
            except Exception as e:
                logger.error(f"Не смог отправить запрос отзыва @{username}: {e}")

    if changed:
        save_guests(guests)

# ─────────────────────────────────────────────
# main
# ─────────────────────────────────────────────
def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN не задан в .env!")
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY не задан в .env!")
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY не задан в .env!")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # ConversationHandler для /newguest
    conv = ConversationHandler(
        entry_points=[CommandHandler("newguest", newguest_start)],
        states={
            ASK_HOUSE:    [CallbackQueryHandler(newguest_house, pattern="^ng_")],
            ASK_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, newguest_username)],
            ASK_DATE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, newguest_date)],
        },
        fallbacks=[CommandHandler("cancel", newguest_cancel)],
    )

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("help",   help_command))
    app.add_handler(CommandHandler("reset",  reset_command))
    app.add_handler(CommandHandler("guests", list_guests))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Расписание: напоминание в 9:00, запрос отзыва в 13:05
    job_queue = app.job_queue
    job_queue.run_daily(send_checkout_reminder, time=datetime.strptime("09:00", "%H:%M").time())
    job_queue.run_daily(send_review_request,    time=datetime.strptime("13:05", "%H:%M").time())

    logger.info("Бот «Времена Года» запущен ✅ (голос 🎤 + напоминания 🌅 + отзывы ⭐)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
