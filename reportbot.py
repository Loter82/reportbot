#!/usr/bin/env python3
import os
import json
import time
import datetime
import calendar
import logging

import gspread
from google.oauth2.service_account import Credentials

from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Updater, CommandHandler, CallbackQueryHandler,
                          MessageHandler, Filters, ConversationHandler, CallbackContext)

# Налаштування логування
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Змінні оточення
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON")

if not SERVICE_ACCOUNT_JSON:
    logger.error("SERVICE_ACCOUNT_JSON is not set in environment variables!")
    raise ValueError("SERVICE_ACCOUNT_JSON environment variable is missing.")

# Розмовні стани
(CHOOSING_LOCATION, CHOOSING_VIEW, CHOOSING_PERIOD, ENTERING_CUSTOM_DATES) = range(4)

# Глобальний словник для зберігання стану користувачів
user_states = {}

# --- Функції для роботи з Google Таблицею ---
def get_spreadsheet():
    scopes = ['https://www.googleapis.com/auth/spreadsheets',
              'https://www.googleapis.com/auth/drive']
    try:
        service_account_info = json.loads(SERVICE_ACCOUNT_JSON)
    except Exception as e:
        logger.error("Error parsing SERVICE_ACCOUNT_JSON: " + str(e))
        raise
    creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID)

def is_user_allowed(chat_id):
    try:
        ss = get_spreadsheet()
        users_sheet = ss.worksheet("USERS")
        data = users_sheet.get_all_values()
        for row in data[1:]:
            user_id = row[2].strip() if row[2] else ""
            permission = row[6].strip().upper() if row[6] else ""
            logger.info(f"USERS row: user_id={user_id}, permission={permission}")
            if user_id == str(chat_id) and permission == "REPORT":
                logger.info(f"User {chat_id} is allowed.")
                return True
        logger.info(f"User {chat_id} is not allowed.")
    except Exception as e:
        logger.error("Error in is_user_allowed: " + str(e))
    return False

def set_state(chat_id, state):
    user_states[str(chat_id)] = state
    logger.info(f"State saved for {chat_id}: {state}")

def get_state(chat_id):
    state = user_states.get(str(chat_id))
    logger.info(f"State retrieved for {chat_id}: {state}")
    return state

def compute_standard_period(period: str):
    today = datetime.date.today()
    if period == "Сьогодні":
        start = today
        end = today
    elif period == "Вчора":
        start = today - datetime.timedelta(days=1)
        end = start
    elif period == "Минулий тиждень":
        start = today - datetime.timedelta(days=today.weekday() + 7)
        end = start + datetime.timedelta(days=6)
    elif period == "Минулий місяць":
        first_day_this_month = today.replace(day=1)
        last_day_last_month = first_day_this_month - datetime.timedelta(days=1)
        start = last_day_last_month.replace(day=1)
        end = last_day_last_month
    else:
        start = today
        end = today
    return {"start": start.strftime("%d.%m.%Y"), "end": end.strftime("%d.%m.%Y")}

# --- Функції для генерації звіту ---

def get_material_mapping():
    """Отримує мапу матеріалів (тип сировини -> вид) з аркуша 'МАТЕРІАЛИ'."""
    try:
        ss = get_spreadsheet()
        mat_sheet = ss.worksheet("МАТЕРІАЛИ")
        data = mat_sheet.get_all_values()
        mapping = {}
        for row in data[1:]:
            if row and row[0]:
                material = row[0].strip()
                kind = row[2].strip() if len(row) > 2 and row[2] else "Інше"
                mapping[material] = kind
        return mapping
    except Exception as e:
        logger.error("Error in get_material_mapping: " + str(e))
        return {}

def process_journal(operation_type: str, start_date: datetime.date, end_date: datetime.date, selected_location: str):
    """
    Обробляє дані з аркуша 'ЖУРНАЛ' за заданим типом операції,
    діапазоном дат та (опціонально) локацією.
    """
    try:
        ss = get_spreadsheet()
        journal_sheet = ss.worksheet("JOURNAL")
        data = journal_sheet.get_all_values()
        result = {}
        # Припускаємо, що:
        # - Дата знаходиться у стовпці B (індекс 1)
        # - Тип операції – у стовпці E (індекс 4)
        # - Локація – у стовпці K (індекс 10)
        # - Матеріал – у стовпці D (індекс 3)
        # - Вага – у стовпці F (індекс 5)
        # - Сума – у стовпці J (індекс 9)
        for row in data[1:]:
            try:
                # Спробуємо розпізнати дату з різних форматів
                row_date = None
                for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                    try:
                        row_date = datetime.datetime.strptime(row[1].strip(), fmt).date()
                        break
                    except Exception:
                        continue
                if not row_date:
                    continue
                if not (start_date <= row_date <= end_date):
                    continue
                if row[4].strip() != operation_type:
                    continue
                if selected_location and row[10].strip() != selected_location:
                    continue
                material = row[3].strip()
                weight = float(row[5].replace(",", ".").strip()) if row[5] else 0
                sum_val = float(row[9].replace(",", ".").strip()) if row[9] else 0
                if material not in result:
                    result[material] = {"weight": 0, "sum": 0}
                result[material]["weight"] += weight
                result[material]["sum"] += sum_val
            except Exception as e:
                logger.error("Error processing row in JOURNAL: " + str(e))
                continue
        return result
    except Exception as e:
        logger.error("Error in process_journal: " + str(e))
        return {}

def generate_brief_table_data(aggregated_data: dict, material_mapping: dict):
    """
    Формує дані таблиці для стислого режиму: групування за видом із підсумковими значеннями.
    """
    grouped = {}
    for material, values in aggregated_data.items():
        kind = material_mapping.get(material, "Інше")
        if kind not in grouped:
            grouped[kind] = {"weight": 0, "sum": 0}
        grouped[kind]["weight"] += values["weight"]
        grouped[kind]["sum"] += values["sum"]
    table_data = [["Вид", "Вага (кг)", "Сума"]]
    overall_weight = 0
    overall_sum = 0
    sorted_kinds = sorted(grouped.items(), key=lambda x: x[1]["sum"], reverse=True)
    for kind, vals in sorted_kinds:
        overall_weight += vals["weight"]
        overall_sum += vals["sum"]
        table_data.append([kind, f"{vals['weight']:.2f}", f"{vals['sum']:.2f}"])
    table_data.append(["**Загальний підсумок:**", f"{overall_weight:.2f}", f"{overall_sum:.2f}"])
    return table_data

def generate_detailed_table_data(aggregated_data: dict, material_mapping: dict):
    """
    Формує дані таблиці для розгорнутого режиму: детальний перелік по кожному виду
    з матеріалами, їхніми показниками та підсумками.
    """
    grouped = {}
    for material, values in aggregated_data.items():
        kind = material_mapping.get(material, "Інше")
        if kind not in grouped:
            grouped[kind] = {}
        grouped[kind][material] = values
    table_data = [["Тип сировини", "Вага (кг)", "Сума", "Середня ціна за кг"]]
    overall_weight = 0
    overall_sum = 0
    kind_subtotals = {}
    for kind, materials in grouped.items():
        subtotal_weight = sum(v["weight"] for v in materials.values())
        subtotal_sum = sum(v["sum"] for v in materials.values())
        kind_subtotals[kind] = {"weight": subtotal_weight, "sum": subtotal_sum}
    sorted_kinds = sorted(kind_subtotals.items(), key=lambda x: x[1]["sum"], reverse=True)
    for kind, subtotal in sorted_kinds:
        table_data.append([f"🎯 {kind}", "", "", ""])
        materials = grouped[kind]
        sorted_materials = sorted(materials.items(), key=lambda x: x[1]["sum"], reverse=True)
        for material, vals in sorted_materials:
            weight = vals["weight"]
            sum_val = vals["sum"]
            avg = sum_val / weight if weight != 0 else 0
            table_data.append([material, f"{weight:.2f}", f"{sum_val:.2f}", f"{avg:.2f}"])
        table_data.append([f"   └─ Підсумок ({kind}):", f"{subtotal['weight']:.2f}", f"{subtotal['sum']:.2f}", ""])
        overall_weight += subtotal["weight"]
        overall_sum += subtotal["sum"]
    table_data.append(["**Загальний підсумок:**", f"{overall_weight:.2f}", f"{overall_sum:.2f}", ""])
    return table_data

def generate_pdf_report(params: dict) -> bytes:
    """
    Генерує PDF‑звіт за параметрами, отриманими від користувача через бота.
    Дані беруться з аркушів "ЖУРНАЛ" та "МАТЕРІАЛИ" із застосуванням логіки,
    аналогічної до Google Apps Script.
    """
    # Парсинг дат із параметрів (формат dd.MM.yyyy)
    try:
        start_date = datetime.datetime.strptime(params["startDate"], "%d.%m.%Y").date()
        end_date = datetime.datetime.strptime(params["endDate"], "%d.%m.%Y").date()
    except Exception as e:
        logger.error("Error parsing dates: " + str(e))
        start_date = end_date = datetime.date.today()

    # Перевірка, чи є період повним календарним місяцем
    full_month = False
    if start_date.day == 1:
        last_day = calendar.monthrange(start_date.year, start_date.month)[1]
        if end_date.day == last_day and start_date.month == end_date.month and start_date.year == end_date.year:
            full_month = True

    locationText = params.get("location") if params.get("location") else "Загальний"
    if full_month:
        monthNames = ["січень", "лютий", "березень", "квітень", "травень", "червень",
                      "липень", "серпень", "вересень", "жовтень", "листопад", "грудень"]
        monthName = monthNames[start_date.month - 1]
        docTitle = f"Звіт про закупівлі та продажі: {locationText} за {monthName} {start_date.year} року"
    else:
        startString = start_date.strftime("%Y-%m-%d")
        endString = end_date.strftime("%Y-%m-%d")
        docTitle = f"📊 Звіт: {locationText} | {startString} - {endString}"

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    
    # Заголовок звіту
    title_paragraph = Paragraph(docTitle, styles["Title"])
    story.append(title_paragraph)
    story.append(Spacer(1, 12))
    
    # Операційні типи: "КУПІВЛЯ", "ПРОДАЖ", "ВІДВАНТАЖЕННЯ"
    op_types = [("КУПІВЛЯ", "Куплені матеріали"),
                ("ПРОДАЖ", "Продані матеріали"),
                ("ВІДВАНТАЖЕННЯ", "Відвантажені матеріали")]
    
    # Отримання мапи матеріалів
    material_mapping = get_material_mapping()
    
    for op_code, op_title in op_types:
        story.append(Paragraph(f"✏️ {op_title}", styles["Heading2"]))
        story.append(Spacer(1, 6))
        
        aggregated_data = process_journal(op_code, start_date, end_date, params.get("location"))
        if not aggregated_data:
            story.append(Paragraph(f"❌ Немає даних для {op_title}", styles["Normal"]))
            story.append(Spacer(1, 12))
            continue
        
        if params.get("viewMode") == "СТИСЛИЙ":
            table_data = generate_brief_table_data(aggregated_data, material_mapping)
        else:
            table_data = generate_detailed_table_data(aggregated_data, material_mapping)
        
        table = Table(table_data, hAlign="LEFT")
        table_style = TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ])
        table.setStyle(table_style)
        story.append(table)
        story.append(Spacer(1, 12))
    
    doc.build(story)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf

def send_report_to_telegram(pdf_file, report_title: str, chat_id: int, context: CallbackContext):
    context.bot.send_document(chat_id=chat_id, document=pdf_file, caption=f"📄 {report_title}")
    logger.info(f"Sent report to {chat_id}: {report_title}")

def generate_report_from_params(params: dict, chat_id: int, context: CallbackContext):
    logger.info(f"Generating report for chat {chat_id} with params: {json.dumps(params)}")
    pdf = generate_pdf_report(params)
    send_report_to_telegram(pdf, "Звіт про рух матеріалів", chat_id, context)

# --- Обробники команд Telegram ---
def start_command(update: Update, context: CallbackContext) -> int:
    chat_id = update.effective_chat.id
    update.message.reply_text("Вітаємо! Використовуйте команду /report для генерації звіту про рух матеріалів.")
    return ConversationHandler.END

def report_command(update: Update, context: CallbackContext) -> int:
    chat_id = update.effective_chat.id
    if not is_user_allowed(chat_id):
        update.message.reply_text("Вибачте, у вас немає доступу до генерації звітів.")
        return ConversationHandler.END
    keyboard = [
        [InlineKeyboardButton("Загальний", callback_data="choose_location:Загальний")],
        [InlineKeyboardButton("ІРПІНЬ", callback_data="choose_location:ІРПІНЬ")],
        [InlineKeyboardButton("ГОСТОМЕЛЬ", callback_data="choose_location:ГОСТОМЕЛЬ")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Оберіть точку для звіту:", reply_markup=reply_markup)
    set_state(chat_id, {"stage": "choose_location"})
    return CHOOSING_LOCATION

def choose_location_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    _, location = query.data.split(":", 1)
    chat_id = query.message.chat.id
    state = get_state(chat_id) or {}
    state["location"] = location
    state["stage"] = "choose_view"
    set_state(chat_id, state)
    keyboard = [
        [InlineKeyboardButton("СТИСЛИЙ", callback_data="choose_view:СТИСЛИЙ")],
        [InlineKeyboardButton("РОЗГОРНУТИЙ", callback_data="choose_view:РОЗГОРНУТИЙ")]
    ]
    query.edit_message_text(text="Оберіть режим звіту:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_VIEW

def choose_view_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    _, view_mode = query.data.split(":", 1)
    chat_id = query.message.chat.id
    state = get_state(chat_id) or {}
    state["viewMode"] = view_mode
    state["stage"] = "choose_period"
    set_state(chat_id, state)
    keyboard = [
        [InlineKeyboardButton("Сьогодні", callback_data="choose_period:Сьогодні")],
        [InlineKeyboardButton("Вчора", callback_data="choose_period:Вчора")],
        [InlineKeyboardButton("Минулий тиждень", callback_data="choose_period:Минулий тиждень")],
        [InlineKeyboardButton("Минулий місяць", callback_data="choose_period:Минулий місяць")],
        [InlineKeyboardButton("З - ПО", callback_data="choose_period:З - ПО")]
    ]
    query.edit_message_text(text="Оберіть період звіту:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_PERIOD

def choose_period_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    _, period = query.data.split(":", 1)
    chat_id = query.message.chat.id
    state = get_state(chat_id) or {}
    state["periodType"] = period
    if period == "З - ПО":
        state["stage"] = "enter_custom_dates"
        set_state(chat_id, state)
        query.edit_message_text(text="Будь ласка, введіть дату або діапазон дат у форматі dd.MM.yyyy або dd.MM.yyyy-dd.MM.yyyy:")
        return ENTERING_CUSTOM_DATES
    else:
        state["stage"] = "completed"
        computed = compute_standard_period(period)
        state["startDate"] = computed["start"]
        state["endDate"] = computed["end"]
        set_state(chat_id, state)
        query.edit_message_text(text="Ваші параметри збережено. Звіт формується, будь ласка, очікуйте.")
        generate_report_from_params(state, chat_id, context)
        return ConversationHandler.END

def custom_dates(update: Update, context: CallbackContext) -> int:
    chat_id = update.effective_chat.id
    text = update.message.text
    parts = text.split("-")
    if len(parts) not in [1, 2]:
        update.message.reply_text("Невірний формат дат. Будь ласка, введіть у форматі dd.MM.yyyy або dd.MM.yyyy-dd.MM.yyyy")
        return ENTERING_CUSTOM_DATES
    state = get_state(chat_id) or {}
    state["periodType"] = "З - ПО"
    state["startDate"] = parts[0].strip()
    state["endDate"] = parts[1].strip() if len(parts) == 2 else parts[0].strip()
    state["stage"] = "completed"
    set_state(chat_id, state)
    update.message.reply_text("Ваші параметри збережено. Звіт формується, будь ласка, очікуйте.")
    generate_report_from_params(state, chat_id, context)
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("Операцію скасовано.")
    return ConversationHandler.END

def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("report", report_command)],
        states={
            CHOOSING_LOCATION: [CallbackQueryHandler(choose_location_callback, pattern="^choose_location:")],
            CHOOSING_VIEW: [CallbackQueryHandler(choose_view_callback, pattern="^choose_view:")],
            CHOOSING_PERIOD: [CallbackQueryHandler(choose_period_callback, pattern="^choose_period:")],
            ENTERING_CUSTOM_DATES: [MessageHandler(Filters.text & ~Filters.command, custom_dates)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    dp.add_handler(CommandHandler("start", start_command))
    dp.add_handler(conv_handler)

    updater.start_polling()
    logger.info("Bot started. Listening for commands...")
    updater.idle()

if __name__ == '__main__':
    main()
