import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)
from database import Database

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
(ADD_NUMBER, ADD_BRAND, ADD_MODEL, ADD_DESCRIPTION,
 ADD_PRICE, ADD_QUANTITY, ADD_CONDITION, ADD_MARKET, ADD_SIDE, ADD_LAMP_TYPE, ADD_PHOTO,
 DUPLICATE_ACTION,
 EDIT_CHOOSE_FIELD, EDIT_VALUE,
 QTY_CHANGE) = range(15)

db = Database()

KEYBOARD_BUTTONS = {
    "🔍 Пошук по номеру", "🚗 Пошук по авто",
    "➕ Додати запчастину", "📋 Всі запчастини", "ℹ️ Допомога"
}

def is_keyboard_button(text: str) -> bool:
    return text.strip() in KEYBOARD_BUTTONS or "Додати запчастину" in text



# ─────────────────────────────────────────────
# /start  /help
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Persistent bottom keyboard
    reply_keyboard = ReplyKeyboardMarkup(
        [
            [KeyboardButton("🔍 Пошук по номеру"), KeyboardButton("🚗 Пошук по авто")],
            [KeyboardButton("➕ Додати запчастину"), KeyboardButton("📋 Всі запчастини")],
            [KeyboardButton("ℹ️ Допомога")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
    await update.message.reply_text(
        "🔧 *Склад автозапчастин*\n\nОберіть дію:",
        reply_markup=reply_keyboard,
        parse_mode="Markdown"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Команди бота:*\n\n"
        "/start — головне меню\n"
        "/add — додати запчастину\n"
        "/search `<номер>` — пошук по номеру\n"
        "/car `<марка>` `<модель>` — пошук по авто\n"
        "/list — всі запчастини\n"
        "/help — ця довідка\n\n"
        "Також можна просто надіслати номер запчастини — бот знайде її автоматично."
    )
    msg = update.message or update.callback_query.message
    await msg.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────
# SEARCH
# ─────────────────────────────────────────────
async def search_by_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.message.reply_text("🔍 Введіть номер запчастини:")
        context.user_data['awaiting'] = 'search_num'
        return

    # direct command: /search 57H945208
    if context.args:
        number = " ".join(context.args)
        await _show_search_results_by_number(update.message, number)
    else:
        await update.message.reply_text("🔍 Введіть номер запчастини:")
        context.user_data['awaiting'] = 'search_num'


async def search_by_car(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.message.reply_text(
            "🚗 Введіть марку та модель авто через пробіл:\n"
            "Наприклад: `Skoda Kodiaq` або просто `Skoda`",
            parse_mode="Markdown"
        )
        context.user_data['awaiting'] = 'search_car'
        return

    if context.args:
        brand = context.args[0]
        model = context.args[1] if len(context.args) > 1 else None
        await _show_search_results_by_car(update.message, brand, model)
    else:
        await update.message.reply_text("🚗 Введіть: /car Skoda Kodiaq")


async def _show_search_results_by_number(message, number: str):
    parts = db.search_by_number(number)
    if not parts:
        await message.reply_text(f"❌ Запчастину *{number}* не знайдено.", parse_mode="Markdown")
        return
    for part in parts:
        await send_part_card(message, part)


async def _show_search_results_by_car(message, brand: str, model: str = None):
    parts = db.search_by_car(brand, model)
    if not parts:
        query = f"{brand} {model}" if model else brand
        await message.reply_text(f"❌ Запчастин для *{query}* не знайдено.", parse_mode="Markdown")
        return
    label = f"{brand} {model}" if model else brand
    await message.reply_text(
        f"🚗 Знайдено запчастин для *{label}*: {len(parts)} шт.",
        parse_mode="Markdown"
    )
    for part in parts:
        await send_part_card(message, part)


async def list_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    msg = query.message if query else update.message
    if query:
        await query.answer()

    parts = db.get_all()
    if not parts:
        await msg.reply_text("📦 Склад порожній.")
        return
    await msg.reply_text(f"📋 Всього на складі: *{len(parts)}* позицій", parse_mode="Markdown")
    for part in parts:
        await send_part_card(msg, part)


async def send_part_card(message, part: dict):
    """Send one part as a card with action buttons."""
    qty_emoji = "✅" if part['quantity'] > 0 else "❌"
    market_str = ("\n🌍 Ринок: " + str(part['market'])) if part.get('market') else ""
    side_str = ("\n↔️ Сторона: " + str(part['side'])) if part.get('side') else ""
    lamp_str = ("\n💡 Тип фари: " + str(part['lamp_type'])) if part.get('lamp_type') else ""
    text = (
        f"🔩 *{part['part_number']}*\n"
        f"🚗 {part['car_brand']} {part['car_model'] or ''}\n"
        f"📝 {part['description'] or '—'}\n"
        f"💰 {part['price'] or '—'} грн\n"
        f"{qty_emoji} Кількість: *{part['quantity']}* шт.\n"
        f"📦 Стан: {part['condition'] or '—'}"
        f"{market_str}{side_str}{lamp_str}"
    )
    keyboard = [
        [
            InlineKeyboardButton("➕1", callback_data=f"qty_plus_{part['id']}"),
            InlineKeyboardButton("➖1", callback_data=f"qty_minus_{part['id']}"),
            InlineKeyboardButton("✏️ Кількість", callback_data=f"qty_set_{part['id']}"),
        ],
        [
            InlineKeyboardButton("✏️ Редагувати", callback_data=f"edit_{part['id']}"),
            InlineKeyboardButton("🗑 Видалити", callback_data=f"delete_{part['id']}"),
        ],
    ]
    markup = InlineKeyboardMarkup(keyboard)

    if part.get('photo_id'):
        await message.reply_photo(
            photo=part['photo_id'],
            caption=text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    else:
        await message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


# ─────────────────────────────────────────────
# QUANTITY QUICK CHANGE
# ─────────────────────────────────────────────
async def qty_plus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    part_id = int(query.data.split("_")[2])
    new_qty = db.change_quantity(part_id, +1)
    await query.answer(f"✅ Кількість: {new_qty} шт.", show_alert=False)
    await _refresh_card(query, part_id)


async def qty_minus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    part_id = int(query.data.split("_")[2])
    part = db.get_by_id(part_id)
    if part['quantity'] <= 0:
        await query.answer("⚠️ Кількість вже 0!", show_alert=True)
        return
    new_qty = db.change_quantity(part_id, -1)
    await query.answer(f"✅ Кількість: {new_qty} шт.", show_alert=False)
    await _refresh_card(query, part_id)


async def qty_set_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    part_id = int(query.data.split("_")[2])
    context.user_data['qty_part_id'] = part_id
    await query.message.reply_text("✏️ Введіть нову кількість:")
    return QTY_CHANGE


async def qty_set_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = int(update.message.text.strip())
        if qty < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введіть ціле невід'ємне число:")
        return QTY_CHANGE

    part_id = context.user_data.get('qty_part_id')
    db.set_quantity(part_id, qty)
    part = db.get_by_id(part_id)
    await update.message.reply_text(f"✅ Кількість оновлено: *{qty}* шт.", parse_mode="Markdown")
    await send_part_card(update.message, part)
    return ConversationHandler.END


async def _refresh_card(query, part_id: int):
    part = db.get_by_id(part_id)
    qty_emoji = "✅" if part['quantity'] > 0 else "❌"
    market_str = ("\n🌍 Ринок: " + str(part['market'])) if part.get('market') else ""
    side_str = ("\n↔️ Сторона: " + str(part['side'])) if part.get('side') else ""
    lamp_str = ("\n💡 Тип фари: " + str(part['lamp_type'])) if part.get('lamp_type') else ""
    text = (
        f"🔩 *{part['part_number']}*\n"
        f"🚗 {part['car_brand']} {part['car_model'] or ''}\n"
        f"📝 {part['description'] or '—'}\n"
        f"💰 {part['price'] or '—'} грн\n"
        f"{qty_emoji} Кількість: *{part['quantity']}* шт.\n"
        f"📦 Стан: {part['condition'] or '—'}"
        f"{market_str}{side_str}{lamp_str}"
    )
    keyboard = [
        [
            InlineKeyboardButton("➕1", callback_data=f"qty_plus_{part['id']}"),
            InlineKeyboardButton("➖1", callback_data=f"qty_minus_{part['id']}"),
            InlineKeyboardButton("✏️ Кількість", callback_data=f"qty_set_{part['id']}"),
        ],
        [
            InlineKeyboardButton("✏️ Редагувати", callback_data=f"edit_{part['id']}"),
            InlineKeyboardButton("🗑 Видалити", callback_data=f"delete_{part['id']}"),
        ],
    ]
    try:
        if query.message.photo:
            await query.edit_message_caption(
                caption=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
    except Exception:
        pass  # message not modified


# ─────────────────────────────────────────────
# ADD PART — ConversationHandler
# ─────────────────────────────────────────────
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    msg = query.message if query else update.message
    if query:
        await query.answer()
    context.user_data['new_part'] = {}
    await msg.reply_text(
        "➕ *Додавання запчастини*\n\n"
        "Крок 1/11 — Введіть номер запчастини:\n_(наприклад: 57H945208)_",
        parse_mode="Markdown"
    )
    return ADD_NUMBER


async def add_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_keyboard_button(update.message.text): return ConversationHandler.END
    context.user_data['new_part']['part_number'] = update.message.text.strip().upper()
    await update.message.reply_text(
        "Крок 2/11 — Введіть *марку авто*:\n_(наприклад: Skoda)_",
        parse_mode="Markdown"
    )
    return ADD_BRAND


async def add_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_keyboard_button(update.message.text): return ConversationHandler.END
    context.user_data['new_part']['car_brand'] = update.message.text.strip()
    keyboard = [[InlineKeyboardButton("⏭ Пропустити", callback_data="skip_model")]]
    await update.message.reply_text(
        "Крок 3/11 — Введіть *модель авто*:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ADD_MODEL


async def add_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_keyboard_button(update.message.text): return ConversationHandler.END
    context.user_data['new_part']['car_model'] = update.message.text.strip()
    return await _ask_description(update.message)


async def skip_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data['new_part']['car_model'] = None
    return await _ask_description(update.callback_query.message)


async def _ask_description(message):
    keyboard = [[InlineKeyboardButton("⏭ Пропустити", callback_data="skip_description")]]
    await message.reply_text(
        "Крок 4/11 — Введіть *опис* запчастини:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ADD_DESCRIPTION


async def add_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_keyboard_button(update.message.text): return ConversationHandler.END
    context.user_data['new_part']['description'] = update.message.text.strip()
    return await _ask_price(update.message)


async def skip_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data['new_part']['description'] = None
    return await _ask_price(update.callback_query.message)


async def _ask_price(message):
    keyboard = [[InlineKeyboardButton("⏭ Пропустити", callback_data="skip_price")]]
    await message.reply_text(
        "Крок 5/11 — Введіть *ціну* (грн):",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ADD_PRICE


async def add_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_keyboard_button(update.message.text): return ConversationHandler.END
    try:
        price = float(update.message.text.strip().replace(",", "."))
        context.user_data['new_part']['price'] = price
    except ValueError:
        await update.message.reply_text("❌ Введіть число (наприклад: 1500 або 1500.50):")
        return ADD_PRICE
    return await _ask_quantity(update.message)


async def skip_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data['new_part']['price'] = None
    return await _ask_quantity(update.callback_query.message)


async def _ask_quantity(message):
    await message.reply_text("Крок 6/11 — Введіть *кількість* на складі:", parse_mode="Markdown")
    return ADD_QUANTITY


async def add_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_keyboard_button(update.message.text): return ConversationHandler.END
    try:
        qty = int(update.message.text.strip())
        if qty < 0:
            raise ValueError
        context.user_data['new_part']['quantity'] = qty
    except ValueError:
        await update.message.reply_text("❌ Введіть ціле невід'ємне число:")
        return ADD_QUANTITY
    return await _ask_condition(update.message)


async def _ask_condition(message, selected: list = None):
    selected = selected or []
    def btn(label, emoji):
        check = "✅ " if label in selected else ""
        return InlineKeyboardButton(f"{check}{emoji} {label}", callback_data=f"cond_{label}")
    keyboard = [
        [btn("Нова", "🆕"), btn("Б/В", "🔄")],
        [btn("До роботи", "🔧"), btn("Битий", "💥")],
        [InlineKeyboardButton("✔️ Підтвердити", callback_data="cond_confirm")],
        [InlineKeyboardButton("⏭ Пропустити", callback_data="skip_condition")],
    ]
    text = (
        "Крок 7/11 — Оберіть *стан* запчастини:\n_(можна обрати кілька, потім натисніть Підтвердити)_"
    )
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADD_CONDITION


async def add_condition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected = context.user_data['new_part'].get('condition_list', [])

    if query.data == "skip_condition":
        context.user_data['new_part']['condition'] = None
        context.user_data['new_part'].pop('condition_list', None)
        return await _ask_market(query.message)

    if query.data == "cond_confirm":
        context.user_data['new_part']['condition'] = ", ".join(selected) if selected else None
        context.user_data['new_part'].pop('condition_list', None)
        return await _ask_market(query.message)

    label = query.data.replace("cond_", "")
    if label in selected:
        selected.remove(label)
    else:
        selected.append(label)
    context.user_data['new_part']['condition_list'] = selected
    try:
        await query.message.delete()
    except Exception:
        pass
    return await _ask_condition(query.message, selected)


async def _ask_market(message):
    keyboard = [
        [InlineKeyboardButton("🇪🇺 EU", callback_data="market_EU"),
         InlineKeyboardButton("🇺🇸 USA", callback_data="market_USA")],
        [InlineKeyboardButton("⏭ Пропустити", callback_data="skip_market")],
    ]
    await message.reply_text(
        "Крок 8/11 — Оберіть *ринок* запчастини:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ADD_MARKET


async def add_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "skip_market":
        context.user_data['new_part']['market'] = None
    else:
        context.user_data['new_part']['market'] = query.data.replace("market_", "")
    return await _ask_side(query.message)


async def _ask_side(message):
    keyboard = [
        [InlineKeyboardButton("⬅️ Ліва", callback_data="side_Ліва"),
         InlineKeyboardButton("➡️ Права", callback_data="side_Права")],
        [InlineKeyboardButton("⏭ Пропустити", callback_data="skip_side")],
    ]
    await message.reply_text(
        "Крок 9/11 — Оберіть *сторону* запчастини:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ADD_SIDE


async def add_side(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "skip_side":
        context.user_data['new_part']['side'] = None
    else:
        context.user_data['new_part']['side'] = query.data.replace("side_", "")
    return await _ask_lamp_type(query.message)


async def _ask_lamp_type(message):
    keyboard = [
        [InlineKeyboardButton("🔦 Передня", callback_data="lamp_Передня"),
         InlineKeyboardButton("🔴 Задня", callback_data="lamp_Задня")],
        [InlineKeyboardButton("🪟 Бленда", callback_data="lamp_Бленда"),
         InlineKeyboardButton("🚗 Кузов", callback_data="lamp_Кузов")],
        [InlineKeyboardButton("⏭ Пропустити", callback_data="skip_lamp")],
    ]
    await message.reply_text(
        "Крок 10/11 — Оберіть *тип* запчастини:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ADD_LAMP_TYPE


async def add_lamp_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "skip_lamp":
        context.user_data['new_part']['lamp_type'] = None
    else:
        context.user_data['new_part']['lamp_type'] = query.data.replace("lamp_", "")
    return await _ask_photo(query.message)


async def _ask_photo(message):
    keyboard = [[InlineKeyboardButton("⏭ Пропустити", callback_data="skip_photo")]]
    await message.reply_text(
        "Крок 11/11 — Надішліть *фото* запчастини:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ADD_PHOTO


async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]  # highest resolution
    context.user_data['new_part']['photo_id'] = photo.file_id
    return await _save_part(update.message, context)


async def skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data['new_part']['photo_id'] = None
    return await _save_part(update.callback_query.message, context)


async def _save_part(message, context: ContextTypes.DEFAULT_TYPE):
    part_data = context.user_data['new_part']

    # Check for duplicate
    existing = db.find_duplicate(
        part_data.get('part_number', ''),
        part_data.get('car_brand', '')
    )

    if existing:
        context.user_data['duplicate_id'] = existing['id']
        qty_emoji = "✅" if existing['quantity'] > 0 else "❌"
        text = (
            "⚠️ *Така запчастина вже є на складі:*\n\n"
            f"🔩 *{existing['part_number']}*\n"
            f"🚗 {existing['car_brand']} {existing['car_model'] or ''}\n"
            f"{qty_emoji} Кількість: *{existing['quantity']}* шт.\n"
            f"📦 Стан: {existing['condition'] or '—'}\n\n"
            "Що зробити?"
        )
        keyboard = [
            [InlineKeyboardButton("➕ Додати кількість і заповнити порожні поля", callback_data="dup_merge")],
            [InlineKeyboardButton("📝 Створити окремий запис", callback_data="dup_new")],
            [InlineKeyboardButton("❌ Скасувати", callback_data="dup_cancel")],
        ]
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return DUPLICATE_ACTION

    part_id = db.add_part(part_data)
    part = db.get_by_id(part_id)
    await message.reply_text("✅ *Запчастину додано!*", parse_mode="Markdown")
    await send_part_card(message, part)
    context.user_data.clear()
    return ConversationHandler.END


async def handle_duplicate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    part_data   = context.user_data.get('new_part', {})
    existing_id = context.user_data.get('duplicate_id')

    if query.data == "dup_merge":
        db.merge_part(existing_id, part_data)
        part = db.get_by_id(existing_id)
        await query.message.reply_text("✅ *Запис оновлено!*", parse_mode="Markdown")
        await send_part_card(query.message, part)

    elif query.data == "dup_new":
        part_id = db.add_part(part_data)
        part = db.get_by_id(part_id)
        await query.message.reply_text("✅ *Новий запис створено!*", parse_mode="Markdown")
        await send_part_card(query.message, part)

    else:  # dup_cancel
        await query.message.reply_text("❌ Скасовано.")

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Скасовано.")
    return ConversationHandler.END


# ─────────────────────────────────────────────
# EDIT PART
# ─────────────────────────────────────────────
async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    part_id = int(query.data.split("_")[1])
    context.user_data['edit_id'] = part_id
    part = db.get_by_id(part_id)

    keyboard = [
        [InlineKeyboardButton("🔢 Номер", callback_data="ef_part_number"),
         InlineKeyboardButton("🚗 Марка", callback_data="ef_car_brand")],
        [InlineKeyboardButton("🚙 Модель", callback_data="ef_car_model"),
         InlineKeyboardButton("📝 Опис", callback_data="ef_description")],
        [InlineKeyboardButton("💰 Ціна", callback_data="ef_price"),
         InlineKeyboardButton("📦 Кількість", callback_data="ef_quantity")],
        [InlineKeyboardButton("🔧 Стан", callback_data="ef_condition"),
         InlineKeyboardButton("🌍 EU/USA", callback_data="ef_market")],
        [InlineKeyboardButton("↔️ Сторона", callback_data="ef_side"),
         InlineKeyboardButton("💡 Тип", callback_data="ef_lamp_type")],
        [InlineKeyboardButton("🖼 Фото", callback_data="ef_photo")],
        [InlineKeyboardButton("❌ Скасувати", callback_data="edit_cancel")],
    ]
    await query.message.reply_text(
        f"✏️ Редагування *{part['part_number']}*\nОберіть поле:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return EDIT_CHOOSE_FIELD


async def edit_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "edit_cancel":
        context.user_data.clear()
        await query.message.reply_text("❌ Скасовано.")
        return ConversationHandler.END

    field = query.data.replace("ef_", "")
    context.user_data['edit_field'] = field

    if field == "condition":
        part_id = context.user_data.get('edit_id')
        part = db.get_by_id(part_id)
        current = [c.strip() for c in (part.get('condition') or '').split(',') if c.strip()]
        context.user_data['edit_cond_selected'] = current
        def ebtn(label, emoji):
            check = "✅ " if label in current else ""
            return InlineKeyboardButton(f"{check}{emoji} {label}", callback_data=f"econd_{label}")
        keyboard = [
            [ebtn("Нова", "🆕"), ebtn("Б/В", "🔄")],
            [ebtn("До роботи", "🔧"), ebtn("Битий", "💥")],
            [InlineKeyboardButton("✔️ Підтвердити", callback_data="econd_confirm")],
        ]
        await query.message.reply_text(
            "Оберіть стан _(можна кілька)_:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
        return EDIT_VALUE

    if field == "market":
        keyboard = [
            [InlineKeyboardButton("🇪🇺 EU", callback_data="emarket_EU"),
             InlineKeyboardButton("🇺🇸 USA", callback_data="emarket_USA")],
            [InlineKeyboardButton("❌ Прибрати", callback_data="emarket_None")],
        ]
        await query.message.reply_text(
            "Оберіть ринок:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return EDIT_VALUE

    if field == "side":
        keyboard = [
            [InlineKeyboardButton("➡️ Права", callback_data="eside_Права"),
             InlineKeyboardButton("⬅️ Ліва", callback_data="eside_Ліва")],
            [InlineKeyboardButton("❌ Прибрати", callback_data="eside_None")],
        ]
        await query.message.reply_text(
            "Оберіть сторону:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return EDIT_VALUE

    if field == "lamp_type":
        keyboard = [
            [InlineKeyboardButton("🔦 Передня", callback_data="elamp_Передня"),
             InlineKeyboardButton("🔴 Задня", callback_data="elamp_Задня")],
            [InlineKeyboardButton("🪟 Бленда", callback_data="elamp_Бленда"),
             InlineKeyboardButton("🚗 Кузов", callback_data="elamp_Кузов")],
            [InlineKeyboardButton("❌ Прибрати", callback_data="elamp_None")],
        ]
        await query.message.reply_text(
            "Оберіть тип:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return EDIT_VALUE

    if field == "photo":
        await query.message.reply_text("📸 Надішліть нове фото:")
        return EDIT_VALUE

    labels = {
        "part_number": "новий номер запчастини",
        "car_brand": "нову марку авто",
        "car_model": "нову модель авто",
        "description": "новий опис",
        "price": "нову ціну (грн)",
        "quantity": "нову кількість",
        "market": "ринок (EU або USA)",
    }
    await query.message.reply_text(f"✏️ Введіть {labels.get(field, field)}:")
    return EDIT_VALUE


async def edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    field = context.user_data.get('edit_field')
    part_id = context.user_data.get('edit_id')

    if query:
        await query.answer()
        if query.data.startswith("econd_"):
            label = query.data.replace("econd_", "")
            selected = context.user_data.get('edit_cond_selected', [])
            if label == "confirm":
                value = ", ".join(selected) if selected else None
                db.update_field(part_id, "condition", value)
                part = db.get_by_id(part_id)
                await query.message.reply_text("✅ Стан оновлено!")
                await send_part_card(query.message, part)
                context.user_data.clear()
                return ConversationHandler.END
            else:
                if label in selected:
                    selected.remove(label)
                else:
                    selected.append(label)
                context.user_data['edit_cond_selected'] = selected
                def ebtn(lbl, emoji):
                    check = "✅ " if lbl in selected else ""
                    return InlineKeyboardButton(f"{check}{emoji} {lbl}", callback_data=f"econd_{lbl}")
                keyboard = [
                    [ebtn("Нова", "🆕"), ebtn("Б/В", "🔄")],
                    [ebtn("До роботи", "🔧"), ebtn("Битий", "💥")],
                    [InlineKeyboardButton("✔️ Підтвердити", callback_data="econd_confirm")],
                ]
                try:
                    await query.message.edit_reply_markup(InlineKeyboardMarkup(keyboard))
                except Exception:
                    pass
                return EDIT_VALUE
        if query.data.startswith("emarket_"):
            raw = query.data.replace("emarket_", "")
            value = None if raw == "None" else raw
            db.update_field(part_id, "market", value)
            part = db.get_by_id(part_id)
            await query.message.reply_text("✅ Ринок оновлено!")
            await send_part_card(query.message, part)
            context.user_data.clear()
            return ConversationHandler.END
        if query.data.startswith("eside_"):
            raw = query.data.replace("eside_", "")
            value = None if raw == "None" else raw
            db.update_field(part_id, "side", value)
            part = db.get_by_id(part_id)
            await query.message.reply_text("✅ Сторону оновлено!")
            await send_part_card(query.message, part)
            context.user_data.clear()
            return ConversationHandler.END
        if query.data.startswith("elamp_"):
            raw = query.data.replace("elamp_", "")
            value = None if raw == "None" else raw
            db.update_field(part_id, "lamp_type", value)
            part = db.get_by_id(part_id)
            await query.message.reply_text("✅ Тип оновлено!")
            await send_part_card(query.message, part)
            context.user_data.clear()
            return ConversationHandler.END
    else:
        # text or photo
        if field == "photo" and update.message.photo:
            value = update.message.photo[-1].file_id
            field = "photo_id"  # DB column name
        elif field == "price":
            try:
                value = float(update.message.text.strip().replace(",", "."))
            except ValueError:
                await update.message.reply_text("❌ Введіть число:")
                return EDIT_VALUE
        elif field == "quantity":
            try:
                value = int(update.message.text.strip())
                if value < 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Введіть ціле невід'ємне число:")
                return EDIT_VALUE
        else:
            value = update.message.text.strip()

        db.update_field(part_id, field, value)
        part = db.get_by_id(part_id)
        await update.message.reply_text("✅ Оновлено!")
        await send_part_card(update.message, part)
        context.user_data.clear()
        return ConversationHandler.END


# ─────────────────────────────────────────────
# DELETE
# ─────────────────────────────────────────────
async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    part_id = int(query.data.split("_")[1])
    part = db.get_by_id(part_id)
    keyboard = [
        [InlineKeyboardButton("✅ Так, видалити", callback_data=f"del_yes_{part_id}"),
         InlineKeyboardButton("❌ Ні", callback_data="del_no")],
    ]
    await query.message.reply_text(
        f"🗑 Видалити *{part['part_number']}*?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def delete_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "del_no":
        await query.message.edit_text("❌ Видалення скасовано.")
        return
    part_id = int(query.data.split("_")[2])
    db.delete_part(part_id)
    await query.message.edit_text("✅ Запчастину видалено.")


# ─────────────────────────────────────────────
# FREE TEXT — auto-search
# ─────────────────────────────────────────────
async def free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    awaiting = context.user_data.get('awaiting')
    text = update.message.text.strip()

    # Handle persistent keyboard buttons
    if text == "🔍 Пошук по номеру":
        await update.message.reply_text("🔍 Введіть номер запчастини:")
        context.user_data['awaiting'] = 'search_num'
        return
    if text == "🚗 Пошук по авто":
        await update.message.reply_text(
            "🚗 Введіть марку та/або модель авто:\nНаприклад: Skoda або Skoda Kodiaq"
        )
        context.user_data['awaiting'] = 'search_car'
        return
    if "Додати запчастину" in text:
        context.user_data.clear()
        await add_start(update, context)
        return
    if text == "📋 Всі запчастини":
        await list_all(update, context)
        return
    if text == "ℹ️ Допомога":
        await help_cmd(update, context)
        return

    if awaiting == 'search_num':
        context.user_data.pop('awaiting', None)
        await _smart_search(update.message, text)
        return

    if awaiting == 'search_car':
        context.user_data.pop('awaiting', None)
        await _smart_search(update.message, text)
        return

    # Default: universal smart search
    await _smart_search(update.message, text)


async def _smart_search(message, query: str):
    """Universal search: partial number, brand, model or any mix."""
    parts = db.smart_search(query)

    if not parts:
        await message.reply_text(
            f"❌ За запитом *{query}* нічого не знайдено.\n\n"
            "Спробуйте:\n"
            "• Частину номера: `57H945`\n"
            "• Марку авто: `Skoda`\n"
            "• Марку і модель: `Skoda Kodiaq`",
            parse_mode="Markdown"
        )
        return

    if len(parts) > 30:
        await message.reply_text(
            f"🔍 Знайдено *{len(parts)}* результатів за *{query}* — "
            f"забагато для відображення. Уточніть запит.",
            parse_mode="Markdown"
        )
        return

    await message.reply_text(
        f"🔍 Знайдено: *{len(parts)}* шт. за *{query}*",
        parse_mode="Markdown"
    )
    for part in parts:
        await send_part_card(message, part)


# ─────────────────────────────────────────────
# CALLBACK ROUTER for menu buttons
# ─────────────────────────────────────────────
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data == "cmd_search_num":
        await search_by_number(update, context)
    elif data == "cmd_search_car":
        await search_by_car(update, context)
    elif data == "cmd_list":
        await list_all(update, context)
    elif data == "cmd_help":
        await help_cmd(update, context)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("❌ Встановіть змінну BOT_TOKEN!")
        return

    app = Application.builder().token(token).build()

    # Add part conversation
    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            CallbackQueryHandler(add_start, pattern="^cmd_add$"),
            MessageHandler(filters.Text(["➕ Додати запчастину", "+ Додати запчастину"]), add_start),
        ],
        states={
            ADD_NUMBER:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_number)],
            ADD_BRAND:       [MessageHandler(filters.TEXT & ~filters.COMMAND, add_brand)],
            ADD_MODEL:       [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_model),
                CallbackQueryHandler(skip_model, pattern="^skip_model$"),
            ],
            ADD_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_description),
                CallbackQueryHandler(skip_description, pattern="^skip_description$"),
            ],
            ADD_PRICE:       [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_price),
                CallbackQueryHandler(skip_price, pattern="^skip_price$"),
            ],
            ADD_QUANTITY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_quantity)],
            ADD_CONDITION:   [CallbackQueryHandler(add_condition, pattern="^(cond_|skip_condition$)")],
            ADD_MARKET:      [CallbackQueryHandler(add_market, pattern="^(market_(EU|USA)|skip_market)$")],
            ADD_SIDE:        [CallbackQueryHandler(add_side, pattern="^(side_(Ліва|Права)|skip_side)$")],
            ADD_LAMP_TYPE:   [CallbackQueryHandler(add_lamp_type, pattern="^(lamp_(Передня|Задня|Бленда|Кузов)|skip_lamp)$")],
            ADD_PHOTO:       [
                MessageHandler(filters.PHOTO, add_photo),
                CallbackQueryHandler(skip_photo, pattern="^skip_photo$"),
            ],
            DUPLICATE_ACTION: [
                CallbackQueryHandler(handle_duplicate, pattern="^dup_(merge|new|cancel)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Edit conversation
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_start, pattern=r"^edit_\d+$")],
        states={
            EDIT_CHOOSE_FIELD: [CallbackQueryHandler(edit_choose_field, pattern="^(ef_|edit_cancel)")],
            EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value),
                MessageHandler(filters.PHOTO, edit_value),
                CallbackQueryHandler(edit_value, pattern="^econd_"),
                CallbackQueryHandler(edit_value, pattern="^emarket_"),
                CallbackQueryHandler(edit_value, pattern="^eside_"),
                CallbackQueryHandler(edit_value, pattern="^elamp_"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Quantity set conversation
    qty_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(qty_set_start, pattern=r"^qty_set_\d+$")],
        states={
            QTY_CHANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, qty_set_finish)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_by_number))
    app.add_handler(CommandHandler("car", search_by_car))
    app.add_handler(CommandHandler("list", list_all))
    app.add_handler(add_conv)
    app.add_handler(edit_conv)
    app.add_handler(qty_conv)
    app.add_handler(CallbackQueryHandler(qty_plus,       pattern=r"^qty_plus_\d+$"))
    app.add_handler(CallbackQueryHandler(qty_minus,      pattern=r"^qty_minus_\d+$"))
    app.add_handler(CallbackQueryHandler(delete_confirm, pattern=r"^delete_\d+$"))
    app.add_handler(CallbackQueryHandler(delete_execute, pattern=r"^del_(yes_\d+|no)$"))
    app.add_handler(CallbackQueryHandler(menu_callback,  pattern="^cmd_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))

    print("🤖 Бот запущено! Натисніть Ctrl+C для зупинки.")
    app.run_polling()


if __name__ == "__main__":
    main()
