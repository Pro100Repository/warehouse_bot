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

# ── States ────────────────────────────────────────────────────────────────────
(ADD_NUMBER, ADD_BRAND, ADD_MODEL, ADD_DESCRIPTION,
 ADD_PRICE, ADD_QUANTITY, ADD_CONDITION, ADD_MARKET,
 ADD_SIDE, ADD_LAMP_TYPE, ADD_PHOTO, DUPLICATE_ACTION,
 EDIT_CHOOSE, EDIT_VALUE,
 QTY_CHANGE) = range(15)

db = Database()

# Texts of persistent keyboard buttons (used to detect interruptions)
KB = {"🔍 Пошук по номеру", "🚗 Пошук по авто",
      "➕ Додати запчастину", "📋 Всі запчастини", "ℹ️ Допомога"}

def _is_kb(text: str) -> bool:
    return text.strip() in KB


# ── Helpers ───────────────────────────────────────────────────────────────────
def _part_text(part: dict) -> str:
    qty_emoji = "✅" if part['quantity'] > 0 else "❌"
    extras = ""
    if part.get('market'):    extras += f"\n🌍 Ринок: {part['market']}"
    if part.get('side'):      extras += f"\n↔️ Сторона: {part['side']}"
    if part.get('lamp_type'): extras += f"\n💡 Тип: {part['lamp_type']}"
    return (
        f"🔩 *{part['part_number']}*\n"
        f"🚗 {part['car_brand']} {part['car_model'] or ''}\n"
        f"📝 {part['description'] or '—'}\n"
        f"💰 {part['price'] or '—'} грн\n"
        f"{qty_emoji} Кількість: *{part['quantity']}* шт.\n"
        f"📦 Стан: {part['condition'] or '—'}"
        f"{extras}"
    )


def _part_keyboard(part: dict) -> InlineKeyboardMarkup:
    pid = part['id']
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕1", callback_data=f"qty_plus_{pid}"),
         InlineKeyboardButton("➖1", callback_data=f"qty_minus_{pid}"),
         InlineKeyboardButton("✏️ Кількість", callback_data=f"qty_set_{pid}")],
        [InlineKeyboardButton("✏️ Редагувати", callback_data=f"edit_{pid}"),
         InlineKeyboardButton("🗑 Видалити", callback_data=f"delete_{pid}")],
    ])


async def send_part_card(message, part: dict):
    text   = _part_text(part)
    markup = _part_keyboard(part)
    if part.get('photo_id'):
        await message.reply_photo(photo=part['photo_id'], caption=text,
                                  reply_markup=markup, parse_mode="Markdown")
    else:
        await message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


async def _smart_search(message, query: str):
    parts = db.smart_search(query)
    if not parts:
        await message.reply_text(
            f"❌ За запитом *{query}* нічого не знайдено.\n\n"
            "Спробуйте частину номера, марку або модель авто.",
            parse_mode="Markdown"
        )
        return
    if len(parts) > 30:
        await message.reply_text(
            f"🔍 Знайдено *{len(parts)}* результатів — забагато. Уточніть запит.",
            parse_mode="Markdown"
        )
        return
    await message.reply_text(
        f"🔍 Знайдено: *{len(parts)}* шт. за *{query}*", parse_mode="Markdown"
    )
    for part in parts:
        await send_part_card(message, part)


# ── /start  /help ─────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("🔍 Пошук по номеру"), KeyboardButton("🚗 Пошук по авто")],
         [KeyboardButton("➕ Додати запчастину"), KeyboardButton("📋 Всі запчастини")],
         [KeyboardButton("ℹ️ Допомога")]],
        resize_keyboard=True, is_persistent=True,
    )
    await update.message.reply_text(
        "🔧 *Склад автозапчастин*\n\nОберіть дію:",
        reply_markup=kb, parse_mode="Markdown"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    await msg.reply_text(
        "📖 *Команди:*\n\n"
        "/start — головне меню\n"
        "/add — додати запчастину\n"
        "/list — всі запчастини\n"
        "/cancel — скасувати поточну дію\n\n"
        "Просто напишіть номер, марку або модель — бот знайде автоматично.",
        parse_mode="Markdown"
    )


async def list_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    msg = q.message if q else update.message
    if q: await q.answer()
    parts = db.get_all()
    if not parts:
        await msg.reply_text("📦 Склад порожній.")
        return
    await msg.reply_text(f"📋 На складі: *{len(parts)}* позицій", parse_mode="Markdown")
    for part in parts:
        await send_part_card(msg, part)


# ── Quantity ──────────────────────────────────────────────────────────────────
async def qty_plus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split("_")[2])
    new = db.change_quantity(pid, +1)
    await q.answer(f"✅ Кількість: {new} шт.", show_alert=False)
    await _refresh_card(q, pid)


async def qty_minus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pid  = int(q.data.split("_")[2])
    part = db.get_by_id(pid)
    if part['quantity'] <= 0:
        await q.answer("⚠️ Кількість вже 0!", show_alert=True)
        return
    db.change_quantity(pid, -1)
    await _refresh_card(q, pid)


async def qty_set_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split("_")[2])
    context.user_data['qty_part_id'] = pid
    await q.message.reply_text("✏️ Введіть нову кількість:")
    return QTY_CHANGE


async def qty_set_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_kb(update.message.text):
        context.user_data.clear()
        return ConversationHandler.END
    try:
        qty = int(update.message.text.strip())
        if qty < 0: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введіть ціле невід'ємне число:")
        return QTY_CHANGE
    pid  = context.user_data.get('qty_part_id')
    db.set_quantity(pid, qty)
    part = db.get_by_id(pid)
    await update.message.reply_text(f"✅ Кількість: *{qty}* шт.", parse_mode="Markdown")
    await send_part_card(update.message, part)
    context.user_data.clear()
    return ConversationHandler.END


async def _refresh_card(q, pid: int):
    part   = db.get_by_id(pid)
    text   = _part_text(part)
    markup = _part_keyboard(part)
    try:
        if q.message.photo:
            await q.edit_message_caption(caption=text, reply_markup=markup, parse_mode="Markdown")
        else:
            await q.edit_message_text(text=text, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        pass


# ── ADD PART ──────────────────────────────────────────────────────────────────
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    msg = q.message if q else update.message
    if q: await q.answer()
    context.user_data.clear()
    context.user_data['new_part'] = {}
    await msg.reply_text(
        "➕ *Додавання запчастини*\n\n"
        "Крок 1/11 — Введіть *номер* запчастини:\n_(наприклад: 57H945208)_\n\n"
        "_/cancel — скасувати_",
        parse_mode="Markdown"
    )
    return ADD_NUMBER


async def add_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_kb(update.message.text):
        return await _kb_interrupt(update, context)
    context.user_data['new_part']['part_number'] = update.message.text.strip().upper()
    await update.message.reply_text(
        "Крок 2/11 — Введіть *марку авто*:\n_(наприклад: Skoda)_",
        parse_mode="Markdown"
    )
    return ADD_BRAND


async def add_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_kb(update.message.text):
        return await _kb_interrupt(update, context)
    context.user_data['new_part']['car_brand'] = update.message.text.strip()
    await update.message.reply_text(
        "Крок 3/11 — Введіть *модель авто*:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустити", callback_data="skip_model")]]),
        parse_mode="Markdown"
    )
    return ADD_MODEL


async def add_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_kb(update.message.text):
        return await _kb_interrupt(update, context)
    context.user_data['new_part']['car_model'] = update.message.text.strip()
    return await _ask_description(update.message)


async def skip_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data['new_part']['car_model'] = None
    return await _ask_description(update.callback_query.message)


async def _ask_description(msg):
    await msg.reply_text(
        "Крок 4/11 — Введіть *опис* запчастини:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустити", callback_data="skip_description")]]),
        parse_mode="Markdown"
    )
    return ADD_DESCRIPTION


async def add_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_kb(update.message.text):
        return await _kb_interrupt(update, context)
    context.user_data['new_part']['description'] = update.message.text.strip()
    return await _ask_price(update.message)


async def skip_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data['new_part']['description'] = None
    return await _ask_price(update.callback_query.message)


async def _ask_price(msg):
    await msg.reply_text(
        "Крок 5/11 — Введіть *ціну* (грн):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустити", callback_data="skip_price")]]),
        parse_mode="Markdown"
    )
    return ADD_PRICE


async def add_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_kb(update.message.text):
        return await _kb_interrupt(update, context)
    try:
        context.user_data['new_part']['price'] = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Введіть число (наприклад: 1500):")
        return ADD_PRICE
    return await _ask_quantity(update.message)


async def skip_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data['new_part']['price'] = None
    return await _ask_quantity(update.callback_query.message)


async def _ask_quantity(msg):
    await msg.reply_text("Крок 6/11 — Введіть *кількість* на складі:", parse_mode="Markdown")
    return ADD_QUANTITY


async def add_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_kb(update.message.text):
        return await _kb_interrupt(update, context)
    try:
        qty = int(update.message.text.strip())
        if qty < 0: raise ValueError
        context.user_data['new_part']['quantity'] = qty
    except ValueError:
        await update.message.reply_text("❌ Введіть ціле невід'ємне число:")
        return ADD_QUANTITY
    return await _ask_condition(update.message)


async def _ask_condition(msg, selected=None):
    selected = selected or []
    def btn(label, emoji):
        check = "✅ " if label in selected else ""
        return InlineKeyboardButton(f"{check}{emoji} {label}", callback_data=f"cond_{label}")
    kb = [
        [btn("Нова", "🆕"), btn("Б/В", "🔄")],
        [btn("До роботи", "🔧"), btn("Битий", "💥")],
        [InlineKeyboardButton("✔️ Підтвердити", callback_data="cond_confirm")],
        [InlineKeyboardButton("⏭ Пропустити", callback_data="skip_condition")],
    ]
    await msg.reply_text(
        "Крок 7/11 — Оберіть *стан* _(можна кілька, потім Підтвердити)_:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )
    return ADD_CONDITION


async def add_condition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    selected = context.user_data['new_part'].get('condition_list', [])

    if q.data == "skip_condition":
        context.user_data['new_part']['condition'] = None
        context.user_data['new_part'].pop('condition_list', None)
        return await _ask_market(q.message)

    if q.data == "cond_confirm":
        context.user_data['new_part']['condition'] = ", ".join(selected) if selected else None
        context.user_data['new_part'].pop('condition_list', None)
        return await _ask_market(q.message)

    label = q.data.replace("cond_", "")
    if label in selected: selected.remove(label)
    else: selected.append(label)
    context.user_data['new_part']['condition_list'] = selected
    try: await q.message.delete()
    except Exception: pass
    return await _ask_condition(q.message, selected)


async def _ask_market(msg):
    kb = [
        [InlineKeyboardButton("🇪🇺 EU", callback_data="market_EU"),
         InlineKeyboardButton("🇺🇸 USA", callback_data="market_USA")],
        [InlineKeyboardButton("⏭ Пропустити", callback_data="skip_market")],
    ]
    await msg.reply_text(
        "Крок 8/11 — Оберіть *ринок*:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )
    return ADD_MARKET


async def add_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data['new_part']['market'] = None if q.data == "skip_market" else q.data.replace("market_", "")
    return await _ask_side(q.message)


async def _ask_side(msg):
    kb = [
        [InlineKeyboardButton("⬅️ Ліва", callback_data="side_Ліва"),
         InlineKeyboardButton("➡️ Права", callback_data="side_Права")],
        [InlineKeyboardButton("⏭ Пропустити", callback_data="skip_side")],
    ]
    await msg.reply_text(
        "Крок 9/11 — Оберіть *сторону*:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )
    return ADD_SIDE


async def add_side(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data['new_part']['side'] = None if q.data == "skip_side" else q.data.replace("side_", "")
    return await _ask_lamp_type(q.message)


async def _ask_lamp_type(msg):
    kb = [
        [InlineKeyboardButton("🔦 Передня", callback_data="lamp_Передня"),
         InlineKeyboardButton("🔴 Задня", callback_data="lamp_Задня")],
        [InlineKeyboardButton("🪟 Бленда", callback_data="lamp_Бленда"),
         InlineKeyboardButton("🚗 Кузов", callback_data="lamp_Кузов")],
        [InlineKeyboardButton("⏭ Пропустити", callback_data="skip_lamp")],
    ]
    await msg.reply_text(
        "Крок 10/11 — Оберіть *тип*:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )
    return ADD_LAMP_TYPE


async def add_lamp_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data['new_part']['lamp_type'] = None if q.data == "skip_lamp" else q.data.replace("lamp_", "")
    return await _ask_photo(q.message)


async def _ask_photo(msg):
    kb = [[InlineKeyboardButton("⏭ Пропустити", callback_data="skip_photo")]]
    await msg.reply_text(
        "Крок 11/11 — Надішліть *фото*:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )
    return ADD_PHOTO


async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_part']['photo_id'] = update.message.photo[-1].file_id
    return await _save_part(update.message, context)


async def skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data['new_part']['photo_id'] = None
    return await _save_part(update.callback_query.message, context)


async def _save_part(message, context: ContextTypes.DEFAULT_TYPE):
    data     = context.user_data['new_part']
    existing = db.find_duplicate(data.get('part_number', ''), data.get('car_brand', ''))

    if existing:
        context.user_data['duplicate_id'] = existing['id']
        qty_e = "✅" if existing['quantity'] > 0 else "❌"
        text  = (
            "⚠️ *Така запчастина вже є:*\n\n"
            f"🔩 *{existing['part_number']}*\n"
            f"🚗 {existing['car_brand']} {existing['car_model'] or ''}\n"
            f"{qty_e} Кількість: *{existing['quantity']}* шт.\n"
            f"📦 Стан: {existing['condition'] or '—'}\n\nЩо зробити?"
        )
        kb = [
            [InlineKeyboardButton("➕ Додати до існуючого", callback_data="dup_merge")],
            [InlineKeyboardButton("📝 Новий окремий запис", callback_data="dup_new")],
            [InlineKeyboardButton("❌ Скасувати", callback_data="dup_cancel")],
        ]
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return DUPLICATE_ACTION

    part_id = db.add_part(data)
    part    = db.get_by_id(part_id)
    await message.reply_text("✅ *Запчастину додано!*", parse_mode="Markdown")
    await send_part_card(message, part)
    context.user_data.clear()
    return ConversationHandler.END


async def handle_duplicate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data    = context.user_data.get('new_part', {})
    eid     = context.user_data.get('duplicate_id')

    if q.data == "dup_merge":
        db.merge_part(eid, data)
        part = db.get_by_id(eid)
        await q.message.reply_text("✅ *Запис оновлено!*", parse_mode="Markdown")
        await send_part_card(q.message, part)
    elif q.data == "dup_new":
        pid  = db.add_part(data)
        part = db.get_by_id(pid)
        await q.message.reply_text("✅ *Новий запис створено!*", parse_mode="Markdown")
        await send_part_card(q.message, part)
    else:
        await q.message.reply_text("❌ Скасовано.")

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Скасовано.")
    return ConversationHandler.END


async def _kb_interrupt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called when keyboard button pressed during conversation. Routes correctly."""
    text = update.message.text.strip()
    context.user_data.clear()
    if "Пошук по номеру" in text:
        await update.message.reply_text("🔍 Введіть номер запчастини:")
        context.user_data['awaiting'] = 'search_num'
    elif "Пошук по авто" in text:
        await update.message.reply_text("🚗 Введіть марку та/або модель авто:")
        context.user_data['awaiting'] = 'search_car'
    elif "Всі запчастини" in text:
        await list_all(update, context)
    elif "Допомога" in text:
        await help_cmd(update, context)
    elif "Додати запчастину" in text:
        return await add_start(update, context)
    return ConversationHandler.END


# ── EDIT PART ─────────────────────────────────────────────────────────────────
async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    await q.answer()
    pid = int(q.data.split("_")[1])
    context.user_data['edit_id'] = pid
    part = db.get_by_id(pid)
    kb = [
        [InlineKeyboardButton("🔢 Номер",    callback_data="ef_part_number"),
         InlineKeyboardButton("🚗 Марка",    callback_data="ef_car_brand")],
        [InlineKeyboardButton("🚙 Модель",   callback_data="ef_car_model"),
         InlineKeyboardButton("📝 Опис",     callback_data="ef_description")],
        [InlineKeyboardButton("💰 Ціна",     callback_data="ef_price"),
         InlineKeyboardButton("📦 Кількість",callback_data="ef_quantity")],
        [InlineKeyboardButton("🔧 Стан",     callback_data="ef_condition"),
         InlineKeyboardButton("🌍 EU/USA",   callback_data="ef_market")],
        [InlineKeyboardButton("↔️ Сторона",  callback_data="ef_side"),
         InlineKeyboardButton("💡 Тип",      callback_data="ef_lamp_type")],
        [InlineKeyboardButton("🖼 Фото",     callback_data="ef_photo")],
        [InlineKeyboardButton("❌ Скасувати", callback_data="edit_cancel")],
    ]
    await q.message.reply_text(
        f"✏️ Редагування *{part['part_number']}*\nОберіть поле:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )
    return EDIT_CHOOSE


async def edit_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "edit_cancel":
        context.user_data.clear()
        await q.message.reply_text("❌ Скасовано.")
        return ConversationHandler.END

    field = q.data.replace("ef_", "")
    context.user_data['edit_field'] = field
    pid   = context.user_data.get('edit_id')

    if field == "condition":
        part    = db.get_by_id(pid)
        current = [c.strip() for c in (part.get('condition') or '').split(',') if c.strip()]
        context.user_data['edit_cond'] = current
        return await _edit_condition_kb(q.message, current)

    if field == "market":
        kb = [[InlineKeyboardButton("🇪🇺 EU", callback_data="emarket_EU"),
               InlineKeyboardButton("🇺🇸 USA", callback_data="emarket_USA")],
              [InlineKeyboardButton("❌ Прибрати", callback_data="emarket_None")]]
        await q.message.reply_text("Оберіть ринок:", reply_markup=InlineKeyboardMarkup(kb))
        return EDIT_VALUE

    if field == "side":
        kb = [[InlineKeyboardButton("⬅️ Ліва", callback_data="eside_Ліва"),
               InlineKeyboardButton("➡️ Права", callback_data="eside_Права")],
              [InlineKeyboardButton("❌ Прибрати", callback_data="eside_None")]]
        await q.message.reply_text("Оберіть сторону:", reply_markup=InlineKeyboardMarkup(kb))
        return EDIT_VALUE

    if field == "lamp_type":
        kb = [[InlineKeyboardButton("🔦 Передня", callback_data="elamp_Передня"),
               InlineKeyboardButton("🔴 Задня",   callback_data="elamp_Задня")],
              [InlineKeyboardButton("🪟 Бленда",  callback_data="elamp_Бленда"),
               InlineKeyboardButton("🚗 Кузов",   callback_data="elamp_Кузов")],
              [InlineKeyboardButton("❌ Прибрати", callback_data="elamp_None")]]
        await q.message.reply_text("Оберіть тип:", reply_markup=InlineKeyboardMarkup(kb))
        return EDIT_VALUE

    if field == "photo":
        await q.message.reply_text("📸 Надішліть нове фото:")
        return EDIT_VALUE

    labels = {
        "part_number": "новий номер",
        "car_brand":   "нову марку авто",
        "car_model":   "нову модель авто",
        "description": "новий опис",
        "price":       "нову ціну (грн)",
        "quantity":    "нову кількість",
    }
    await q.message.reply_text(f"✏️ Введіть {labels.get(field, field)}:")
    return EDIT_VALUE


async def _edit_condition_kb(msg, selected):
    def ebtn(label, emoji):
        check = "✅ " if label in selected else ""
        return InlineKeyboardButton(f"{check}{emoji} {label}", callback_data=f"econd_{label}")
    kb = [
        [ebtn("Нова", "🆕"), ebtn("Б/В", "🔄")],
        [ebtn("До роботи", "🔧"), ebtn("Битий", "💥")],
        [InlineKeyboardButton("✔️ Підтвердити", callback_data="econd_confirm")],
    ]
    await msg.reply_text(
        "Оберіть стан _(можна кілька)_:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )
    return EDIT_VALUE


async def edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q     = update.callback_query
    field = context.user_data.get('edit_field')
    pid   = context.user_data.get('edit_id')

    if q:
        await q.answer()

        # Condition multi-select
        if q.data.startswith("econd_"):
            label    = q.data.replace("econd_", "")
            selected = context.user_data.get('edit_cond', [])
            if label == "confirm":
                value = ", ".join(selected) if selected else None
                db.update_field(pid, "condition", value)
                part  = db.get_by_id(pid)
                await q.message.reply_text("✅ Стан оновлено!")
                await send_part_card(q.message, part)
                context.user_data.clear()
                return ConversationHandler.END
            if label in selected: selected.remove(label)
            else: selected.append(label)
            context.user_data['edit_cond'] = selected
            try:
                def ebtn(lbl, emoji):
                    check = "✅ " if lbl in selected else ""
                    return InlineKeyboardButton(f"{check}{emoji} {lbl}", callback_data=f"econd_{lbl}")
                kb = [
                    [ebtn("Нова","🆕"), ebtn("Б/В","🔄")],
                    [ebtn("До роботи","🔧"), ebtn("Битий","💥")],
                    [InlineKeyboardButton("✔️ Підтвердити", callback_data="econd_confirm")],
                ]
                await q.message.edit_reply_markup(InlineKeyboardMarkup(kb))
            except Exception:
                pass
            return EDIT_VALUE

        # Single-choice callbacks
        for prefix, db_field in [("emarket_","market"), ("eside_","side"), ("elamp_","lamp_type")]:
            if q.data.startswith(prefix):
                raw   = q.data.replace(prefix, "")
                value = None if raw == "None" else raw
                db.update_field(pid, db_field, value)
                part  = db.get_by_id(pid)
                await q.message.reply_text("✅ Оновлено!")
                await send_part_card(q.message, part)
                context.user_data.clear()
                return ConversationHandler.END

    else:
        # Text or photo input
        if field == "photo" and update.message.photo:
            value = update.message.photo[-1].file_id
            field = "photo_id"
        elif field == "price":
            try:
                value = float(update.message.text.strip().replace(",", "."))
            except ValueError:
                await update.message.reply_text("❌ Введіть число:")
                return EDIT_VALUE
        elif field == "quantity":
            try:
                value = int(update.message.text.strip())
                if value < 0: raise ValueError
            except ValueError:
                await update.message.reply_text("❌ Введіть невід'ємне ціле число:")
                return EDIT_VALUE
        else:
            value = update.message.text.strip()

        db.update_field(pid, field, value)
        part = db.get_by_id(pid)
        await update.message.reply_text("✅ Оновлено!")
        await send_part_card(update.message, part)
        context.user_data.clear()
        return ConversationHandler.END


# ── DELETE ────────────────────────────────────────────────────────────────────
async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    await q.answer()
    pid  = int(q.data.split("_")[1])
    part = db.get_by_id(pid)
    kb   = [[InlineKeyboardButton("✅ Так, видалити", callback_data=f"del_yes_{pid}"),
             InlineKeyboardButton("❌ Ні", callback_data="del_no")]]
    await q.message.reply_text(
        f"🗑 Видалити *{part['part_number']}*?",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )


async def delete_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "del_no":
        await q.message.edit_text("❌ Скасовано.")
        return
    pid = int(q.data.split("_")[2])
    db.delete_part(pid)
    await q.message.edit_text("✅ Видалено.")


# ── FREE TEXT ─────────────────────────────────────────────────────────────────
async def free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text    = update.message.text.strip()
    waiting = context.user_data.get('awaiting')

    if text == "🔍 Пошук по номеру":
        await update.message.reply_text("🔍 Введіть номер запчастини:")
        context.user_data['awaiting'] = 'search_num'
        return
    if text == "🚗 Пошук по авто":
        await update.message.reply_text("🚗 Введіть марку та/або модель авто:")
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

    if waiting in ('search_num', 'search_car'):
        context.user_data.pop('awaiting', None)

    await _smart_search(update.message, text)


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("❌ Встановіть змінну BOT_TOKEN!")
        return

    app = Application.builder().token(token).build()

    # ConversationHandlers registered with group=-1 (higher priority than free_text)
    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            CallbackQueryHandler(add_start, pattern="^cmd_add$"),
            MessageHandler(filters.Regex(r"^[➕+]\s*Додати запчастину$"), add_start),
        ],
        states={
            ADD_NUMBER:       [MessageHandler(filters.TEXT & ~filters.COMMAND, add_number)],
            ADD_BRAND:        [MessageHandler(filters.TEXT & ~filters.COMMAND, add_brand)],
            ADD_MODEL:        [MessageHandler(filters.TEXT & ~filters.COMMAND, add_model),
                               CallbackQueryHandler(skip_model, pattern="^skip_model$")],
            ADD_DESCRIPTION:  [MessageHandler(filters.TEXT & ~filters.COMMAND, add_description),
                               CallbackQueryHandler(skip_description, pattern="^skip_description$")],
            ADD_PRICE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, add_price),
                               CallbackQueryHandler(skip_price, pattern="^skip_price$")],
            ADD_QUANTITY:     [MessageHandler(filters.TEXT & ~filters.COMMAND, add_quantity)],
            ADD_CONDITION:    [CallbackQueryHandler(add_condition, pattern=r"^(cond_|skip_condition)")],
            ADD_MARKET:       [CallbackQueryHandler(add_market, pattern=r"^(market_|skip_market)")],
            ADD_SIDE:         [CallbackQueryHandler(add_side, pattern=r"^(side_|skip_side)")],
            ADD_LAMP_TYPE:    [CallbackQueryHandler(add_lamp_type, pattern=r"^(lamp_|skip_lamp)")],
            ADD_PHOTO:        [MessageHandler(filters.PHOTO, add_photo),
                               CallbackQueryHandler(skip_photo, pattern="^skip_photo$")],
            DUPLICATE_ACTION: [CallbackQueryHandler(handle_duplicate, pattern=r"^dup_")],
        },
        fallbacks=[CommandHandler("cancel", cancel),
                   CommandHandler("start", start)],
    )

    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_start, pattern=r"^edit_\d+$")],
        states={
            EDIT_CHOOSE: [CallbackQueryHandler(edit_choose, pattern=r"^(ef_|edit_cancel)")],
            EDIT_VALUE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value),
                          MessageHandler(filters.PHOTO, edit_value),
                          CallbackQueryHandler(edit_value, pattern=r"^(econd_|emarket_|eside_|elamp_)")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    qty_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(qty_set_start, pattern=r"^qty_set_\d+$")],
        states={
            QTY_CHANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, qty_set_finish)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(add_conv,  group=-1)
    app.add_handler(edit_conv, group=-1)
    app.add_handler(qty_conv,  group=-1)

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("help",   help_cmd))
    app.add_handler(CommandHandler("list",   list_all))
    app.add_handler(CallbackQueryHandler(qty_plus,       pattern=r"^qty_plus_\d+$"))
    app.add_handler(CallbackQueryHandler(qty_minus,      pattern=r"^qty_minus_\d+$"))
    app.add_handler(CallbackQueryHandler(delete_confirm, pattern=r"^delete_\d+$"))
    app.add_handler(CallbackQueryHandler(delete_execute, pattern=r"^del_(yes_\d+|no)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))

    print("🤖 Бот запущено!")
    app.run_polling()


if __name__ == "__main__":
    main()
