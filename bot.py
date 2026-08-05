import logging
import os
import json
import re
import tempfile
import requests as req_lib
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
PHOTO_VISION_WAIT = 99  # waiting for photo for vision search

(ADD_NUMBER, ADD_BRAND, ADD_MODEL, ADD_DESCRIPTION,
 ADD_PRICE, ADD_QUANTITY, ADD_CONDITION, ADD_MARKET,
 ADD_SIDE, ADD_LAMP_TYPE, ADD_PHOTO, DUPLICATE_ACTION,
 EDIT_CHOOSE, EDIT_VALUE,
 QTY_CHANGE) = range(15)

db = Database()

SERPAPI_KEY       = os.environ.get("SERPAPI_KEY", "")
GOOGLE_VISION_KEY = os.environ.get("GOOGLE_VISION_KEY", "")

# Texts of persistent keyboard buttons (used to detect interruptions)
KB = {"🔍 Пошук по номеру", "🚗 Пошук по авто",
      "➕ Додати запчастину", "📋 Всі запчастини",
      "📷 Пошук по фото", "ℹ️ Допомога"}

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

    # Collect all photo ids
    all_photos = []
    if part.get('photo_id'):
        all_photos.append(part['photo_id'])
    if part.get('photo_ids'):
        try:
            extra = json.loads(part['photo_ids'])
            all_photos.extend(extra)
        except Exception:
            pass

    if not all_photos:
        await message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    elif len(all_photos) == 1:
        await message.reply_photo(photo=all_photos[0], caption=text,
                                  reply_markup=markup, parse_mode="Markdown")
    else:
        # Send as media group (album), then text card separately
        from telegram import InputMediaPhoto
        media = [InputMediaPhoto(pid) for pid in all_photos[:10]]
        await message.reply_media_group(media=media)
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
         [KeyboardButton("📷 Пошук по фото"), KeyboardButton("ℹ️ Допомога")]],
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
    if _is_kb(update.message.text) or not context.user_data.get('qty_part_id'):
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
        [btn("DP", "🔵")],
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
    kb = [[InlineKeyboardButton("✅ Готово / Пропустити", callback_data="skip_photo")]]
    await msg.reply_text(
        "Крок 11/11 — Надішліть *фото* запчастини\n"
        "_(можна надіслати кілька по черзі, потім натисніть Готово)_",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )
    return ADD_PHOTO


async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id   = update.message.photo[-1].file_id
    np        = context.user_data['new_part']
    all_ids   = np.get('_all_photo_ids', [])
    all_ids.append(file_id)
    np['_all_photo_ids'] = all_ids

    count = len(all_ids)
    kb    = [[InlineKeyboardButton(f"✅ Готово ({count} фото)", callback_data="skip_photo")]]
    await update.message.reply_text(
        f"📸 Фото {count} додано. Надішліть ще або натисніть Готово:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return ADD_PHOTO


async def skip_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    np      = context.user_data['new_part']
    all_ids = np.pop('_all_photo_ids', [])
    if all_ids:
        np['photo_id']  = all_ids[0]
        np['photo_ids'] = json.dumps(all_ids[1:]) if len(all_ids) > 1 else None
    else:
        np['photo_id']  = None
        np['photo_ids'] = None
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
        # Let add_conv entry_point handle this
        pass
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
        await q.message.reply_text(
            "📸 Надішліть фото _(можна кілька по черзі)_\n"
            "Потім натисніть /done або надішліть /done щоб зберегти:",
            parse_mode="Markdown"
        )
        context.user_data['edit_photos'] = []
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
        [ebtn("DP", "🔵")],
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
                    [ebtn("DP","🔵")],
                    [InlineKeyboardButton("✔️ Підтвердити", callback_data="econd_confirm")],
                ]
                await q.message.edit_reply_markup(InlineKeyboardMarkup(kb))
            except Exception:
                pass
            return EDIT_VALUE

        # Save multiple edit photos
        if q.data == "ephoto_done":
            photos = context.user_data.pop('edit_photos', [])
            if photos:
                db.update_field(pid, "photo_id", photos[0])
                db.update_field(pid, "photo_ids", json.dumps(photos[1:]) if len(photos) > 1 else None)
            part = db.get_by_id(pid)
            await q.message.reply_text("✅ Фото оновлено!")
            await send_part_card(q.message, part)
            context.user_data.clear()
            return ConversationHandler.END

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
            file_id = update.message.photo[-1].file_id
            photos  = context.user_data.get('edit_photos', [])
            photos.append(file_id)
            context.user_data['edit_photos'] = photos
            kb = [[InlineKeyboardButton(f"✅ Зберегти ({len(photos)} фото)", callback_data="ephoto_done")]]
            await update.message.reply_text(
                f"📸 Фото {len(photos)} додано. Надішліть ще або натисніть Зберегти:",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            return EDIT_VALUE
        elif field == "photo" and update.message.text and update.message.text.strip() == "/done":
            photos = context.user_data.pop('edit_photos', [])
            if photos:
                db.update_field(pid, "photo_id", photos[0])
                db.update_field(pid, "photo_ids", json.dumps(photos[1:]) if len(photos) > 1 else None)
            part = db.get_by_id(pid)
            await update.message.reply_text("✅ Фото оновлено!")
            await send_part_card(update.message, part)
            context.user_data.clear()
            return ConversationHandler.END
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



# ── PHOTO SEARCH ──────────────────────────────────────────────────────────────

# Mapping Ukrainian lamp types to DB values
LAMP_TYPE_MAP = {
    "передня": "Передня", "передній": "Передня", "фара": "Передня",
    "задня":   "Задня",   "задній":   "Задня",   "фонар": "Задня",
    "бленда":  "Бленда",
    "кузов":   "Кузов",
}
SIDE_MAP = {
    "права": "Права", "правий": "Права", "правa": "Права",
    "ліва":  "Ліва",  "лівий":  "Ліва",  "лівa":  "Ліва",
    "right": "Права", "left":   "Ліва",
}


def _extract_info_from_lens(lens_results: list) -> dict:
    """Parse Google Lens results to extract brand, model, lamp_type, side, part_number."""
    all_text = " ".join(lens_results).lower()

    info = {
        "brand": None, "model": None,
        "lamp_type": None, "side": None, "part_number": None
    }

    # Known brands
    brands = [
        "mercedes", "volkswagen", "skoda", "audi", "bmw", "toyota",
        "honda", "ford", "opel", "renault", "peugeot", "hyundai",
        "kia", "volvo", "seat", "porsche", "lexus", "nissan", "mazda",
    ]
    for b in brands:
        if b in all_text:
            info["brand"] = b.capitalize()
            break

    # Lamp type
    for kw, val in LAMP_TYPE_MAP.items():
        if kw in all_text:
            info["lamp_type"] = val
            break

    # Side
    for kw, val in SIDE_MAP.items():
        if kw in all_text:
            info["side"] = val
            break

    # Part number pattern (e.g. 57H945208, A2059060400, 63217296018)
    pn_match = re.search(r'\b[A-Z0-9]{6,15}\b', " ".join(lens_results))
    if pn_match:
        info["part_number"] = pn_match.group(0)

    return info


async def search_by_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo sent outside of add/edit conversation — SerpAPI Google Lens."""
    # Skip if inside a conversation or waiting for vision photo
    if (context.user_data.get('new_part') is not None or
            context.user_data.get('edit_id') is not None or
            context.user_data.get('awaiting') == 'vision_photo'):
        return

    if not SERPAPI_KEY:
        await update.message.reply_text(
            "⚠️ Пошук по фото не налаштовано.\n"
            "Додайте SERPAPI_KEY у .env файл."
        )
        return

    msg = await update.message.reply_text("🔍 Аналізую фото...")

    try:
        # Download photo from Telegram
        photo   = update.message.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            await tg_file.download_to_drive(tmp.name)
            tmp_path = tmp.name

        # Upload to SerpAPI Google Lens
        with open(tmp_path, "rb") as img_file:
            response = req_lib.post(
                "https://serpapi.com/search",
                params={
                    "engine":  "google_lens",
                    "api_key": SERPAPI_KEY,
                },
                files={"image_file": img_file},
                timeout=30
            )

        os.unlink(tmp_path)

        if response.status_code != 200:
            await msg.edit_text("❌ Помилка API. Спробуйте ще раз.")
            return

        data = response.json()

        # Extract text results from visual matches
        lens_texts = []
        for match in data.get("visual_matches", [])[:10]:
            lens_texts.append(match.get("title", ""))
            lens_texts.append(match.get("snippet", ""))

        # Also check knowledge graph
        kg = data.get("knowledge_graph", {})
        if kg:
            lens_texts.append(kg.get("title", ""))
            lens_texts.append(kg.get("description", ""))

        if not lens_texts:
            await msg.edit_text(
                "🤷 Не вдалось розпізнати деталь.\n"
                "Спробуйте сфотографувати з меншої відстані або під іншим кутом."
            )
            return

        # Extract structured info
        info = _extract_info_from_lens(lens_texts)

        # Build search summary
        summary_parts = []
        if info["part_number"]: summary_parts.append(f"Номер: `{info['part_number']}`")
        if info["brand"]:       summary_parts.append(f"Марка: {info['brand']}")
        if info["model"]:       summary_parts.append(f"Модель: {info['model']}")
        if info["lamp_type"]:   summary_parts.append(f"Тип: {info['lamp_type']}")
        if info["side"]:        summary_parts.append(f"Сторона: {info['side']}")

        if not any(info.values()):
            await msg.edit_text(
                "🤷 Не вдалось визначити деталь.\n"
                "Спробуйте надіслати чіткіше фото або введіть пошуковий запит вручну."
            )
            return

        await msg.edit_text(
            "🔍 *Розпізнано:*\n" + "\n".join(summary_parts) + "\n\n⏳ Шукаю в базі...",
            parse_mode="Markdown"
        )

        # Search DB — progressive fallback
        search_level = None
        parts = []

        # Level 1: part_number (найточніший)
        if info["part_number"]:
            parts = db.photo_search(part_number=info["part_number"])
            if parts: search_level = f"номером `{info['part_number']}`"

        # Level 2: brand + model + lamp_type + side
        if not parts and info["brand"] and info["model"] and info["lamp_type"] and info["side"]:
            parts = db.photo_search(
                brand=info["brand"], model=info["model"],
                lamp_type=info["lamp_type"], side=info["side"]
            )
            if parts: search_level = "маркою, моделлю, типом та стороною"

        # Level 3: brand + model
        if not parts and info["brand"] and info["model"]:
            parts = db.photo_search(brand=info["brand"], model=info["model"])
            if parts: search_level = "маркою та моделлю"

        if not parts:
            await update.message.reply_text(
                "❌ Збігів не знайдено.\n"
                "Спробуйте ввести пошуковий запит вручну або зробіть чіткіше фото."
            )
            return

        found_msg = f"✅ Знайдено *{len(parts)}* шт. за {search_level}."
        if len(parts) > 20:
            found_msg += "\n_Показую перші 20 результатів_"
            parts = parts[:20]

        await update.message.reply_text(found_msg, parse_mode="Markdown")
        for part in parts:
            await send_part_card(update.message, part)

    except Exception as e:
        logger.error(f"Photo search error: {e}")
        await msg.edit_text("❌ Помилка під час аналізу фото. Спробуйте ще раз.")



# ── GOOGLE VISION PHOTO SEARCH ────────────────────────────────────────────────

async def vision_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User pressed 📷 Пошук по фото button."""
    if not GOOGLE_VISION_KEY:
        await update.message.reply_text(
            "⚠️ Google Vision API не налаштовано.\n"
            "Додайте GOOGLE_VISION_KEY у .env файл."
        )
        return

    context.user_data['awaiting'] = 'vision_photo'
    await update.message.reply_text(
        "📷 *Пошук по фото*\n\n"
        "Надішліть фото запчастини — бот знайде схожі в базі.",
        parse_mode="Markdown"
    )


async def vision_search_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo for Google Vision similarity search — only when button was pressed."""
    if context.user_data.get('awaiting') != 'vision_photo':
        return  # Not waiting for vision photo — let search_by_photo handle it

    context.user_data.pop('awaiting', None)

    if not GOOGLE_VISION_KEY:
        return

    msg = await update.message.reply_text("🔍 Аналізую фото через Google Vision...")

    try:
        import base64

        # Download photo from Telegram
        photo   = update.message.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            await tg_file.download_to_drive(tmp.name)
            tmp_path = tmp.name

        with open(tmp_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")
        os.unlink(tmp_path)

        # Call Google Vision API - Web Detection (finds visually similar images)
        vision_url = (
            f"https://vision.googleapis.com/v1/images:annotate"
            f"?key={GOOGLE_VISION_KEY}"
        )
        payload = {
            "requests": [{
                "image": {"content": image_b64},
                "features": [
                    {"type": "WEB_DETECTION",  "maxResults": 10},
                    {"type": "TEXT_DETECTION",  "maxResults": 5},
                    {"type": "LABEL_DETECTION", "maxResults": 10},
                ]
            }]
        }

        resp = req_lib.post(vision_url, json=payload, timeout=30)
        if resp.status_code != 200:
            await msg.edit_text(f"❌ Помилка Google Vision API: {resp.status_code}")
            return

        result   = resp.json()["responses"][0]
        web_det  = result.get("webDetection", {})
        texts    = result.get("textAnnotations", [])
        labels   = result.get("labelAnnotations", [])

        # --- Extract part number from OCR text ---
        part_number = None
        full_text   = texts[0]["description"] if texts else ""
        pn_match    = re.search(r'\b[A-Z0-9]{6,15}\b', full_text)
        if pn_match:
            part_number = pn_match.group(0)

        # --- Extract brand/model/type from web entities and labels ---
        web_entities  = [e.get("description","") for e in web_det.get("webEntities", [])]
        label_descs   = [l.get("description","") for l in labels]
        all_text_data = " ".join(web_entities + label_descs + [full_text]).lower()

        brands = [
            "mercedes","volkswagen","skoda","audi","bmw","toyota",
            "honda","ford","opel","renault","peugeot","hyundai",
            "kia","volvo","seat","porsche","lexus","nissan","mazda",
        ]
        brand = next((b.capitalize() for b in brands if b in all_text_data), None)

        lamp_type = None
        for kw, val in LAMP_TYPE_MAP.items():
            if kw in all_text_data:
                lamp_type = val
                break

        side = None
        for kw, val in SIDE_MAP.items():
            if kw in all_text_data:
                side = val
                break

        # --- Also check visually similar images titles ---
        similar_titles = [
            p.get("pageTitle","") for p in web_det.get("pagesWithMatchingImages", [])
        ]
        similar_text = " ".join(similar_titles).lower()
        if not brand:
            brand = next((b.capitalize() for b in brands if b in similar_text), None)

        # Build summary
        found_info = []
        if part_number: found_info.append(f"Номер: `{part_number}`")
        if brand:       found_info.append(f"Марка: {brand}")
        if lamp_type:   found_info.append(f"Тип: {lamp_type}")
        if side:        found_info.append(f"Сторона: {side}")

        info_text = "\n".join(found_info) if found_info else "_нічого не розпізнано_"
        await msg.edit_text(
            f"🔍 *Розпізнано:*\n{info_text}\n\n⏳ Шукаю в базі...",
            parse_mode="Markdown"
        )

        # --- Progressive DB search ---
        parts        = []
        search_level = None

        # ── Vision progressive DB search ──
        # Level 1: part number
        if part_number:
            parts = db.photo_search(part_number=part_number)
            if parts: search_level = f"номером `{part_number}`"

        # Level 2: brand + model + lamp_type + side
        if not parts and brand and lamp_type and side:
            parts = db.photo_search(brand=brand, lamp_type=lamp_type, side=side)
            if parts: search_level = "маркою, типом та стороною"

        # Level 3: brand + model + lamp_type (без сторони)
        if not parts and brand and lamp_type:
            parts = db.photo_search(brand=brand, lamp_type=lamp_type)
            if parts: search_level = "маркою та типом"

        # Level 4: no match
        if not parts:
            await update.message.reply_text(
                "❌ Збігів не знайдено.\n"
                "Спробуйте ввести пошуковий запит вручну."
            )
            return

        found_msg = f"✅ Знайдено *{len(parts)}* шт. за {search_level}."
        if len(parts) > 20:
            found_msg += "\n_Показую перші 20_"
            parts = parts[:20]

        await update.message.reply_text(found_msg, parse_mode="Markdown")
        for part in parts:
            await send_part_card(update.message, part)

    except Exception as e:
        logger.error(f"Vision search error: {e}")
        await msg.edit_text("❌ Помилка під час аналізу. Спробуйте ще раз.")


# ── FREE TEXT ─────────────────────────────────────────────────────────────────
async def free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text    = update.message.text.strip()
    waiting = context.user_data.get('awaiting')

    # If user is inside a conversation dialog — ignore non-keyboard text
    in_add  = context.user_data.get('new_part') is not None
    in_edit = context.user_data.get('edit_id') is not None
    in_qty  = context.user_data.get('qty_part_id') is not None
    if (in_add or in_edit or in_qty) and not _is_kb(text):
        return

    if text == "📷 Пошук по фото":
        await vision_search_start(update, context)
        return
    if text == "🔍 Пошук по номеру":
        await update.message.reply_text("🔍 Введіть номер запчастини:")
        context.user_data['awaiting'] = 'search_num'
        return
    if text == "🚗 Пошук по авто":
        await update.message.reply_text("🚗 Введіть марку та/або модель авто:")
        context.user_data['awaiting'] = 'search_car'
        return
    if "Додати запчастину" in text:
        # Handled by add_conv entry_point - ignore here to prevent duplicate
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
                          CallbackQueryHandler(edit_value, pattern=r"^(econd_|emarket_|eside_|elamp_|ephoto_done)")],
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
    app.add_handler(MessageHandler(filters.PHOTO, vision_search_photo))
    app.add_handler(MessageHandler(filters.PHOTO, search_by_photo))

    print("🤖 Бот запущено!")
    app.run_polling()


if __name__ == "__main__":
    main()
