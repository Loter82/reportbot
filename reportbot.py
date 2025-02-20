#!/usr/bin/env python3
import os
import json
import datetime
import calendar
import logging

import gspread
from google.oauth2.service_account import Credentials

from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (SimpleDocTemplate, Table, Paragraph, Spacer,
                                Image as RLImage)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus.tables import TableStyle

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, Update,
                      Chat, BotCommand, BotCommandScopeChat)
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

# -------------------------------------------------------
# 1. Функції для роботи з Google Таблицею
# -------------------------------------------------------

def get_spreadsheet():
    scopes = ['https://www.googleapis.com/auth/spreadsheets',
              'https://www.googleapis.com/auth/drive']
    service_account_info = json.loads(SERVICE_ACCOUNT_JSON)
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
            if user_id == str(chat_id) and permission == "REPORT":
                return True
    except Exception as e:
        logger.error("Error in is_user_allowed: " + str(e))
    return False

def set_state(chat_id, state):
    user_states[str(chat_id)] = state

def get_state(chat_id):
    return user_states.get(str(chat_id))

def compute_standard_period(period: str):
    """
    Повертає start та end у форматі dd.MM.yyyy
    """
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

def get_locations():
    """
    Зчитує локації з аркуша SHOPS
    """
    try:
        ss = get_spreadsheet()
        shops_sheet = ss.worksheet("SHOPS")
        values = shops_sheet.col_values(1)
        return values[1:] if len(values) > 1 else []
    except Exception as e:
        logger.error("Error in get_locations: " + str(e))
        return []

# -------------------------------------------------------
# 2. Логіка формування звіту
# -------------------------------------------------------

def format_number(num: float) -> str:
    """
    Форматує число з двома знаками після коми і розділяє тисячі пробілами.
    1234.56 -> "1 234.56"
    """
    s = "{:,.2f}".format(num)  # -> "1,234.56"
    return s.replace(",", " ")  # -> "1 234.56"

def get_material_mapping():
    try:
        ss = get_spreadsheet()
        mat_sheet = ss.worksheet("МАТЕРІАЛИ")
        data = mat_sheet.get_all_values()
        mapping = {}
        for row in data[1:]:
            if row and row[0]:
                material = row[0].strip()
                if len(row) > 2 and row[2]:
                    kind = row[2].replace('\xa0', '').strip()
                else:
                    kind = "Інше"
                mapping[material] = kind
        return mapping
    except Exception as e:
        logger.error("Error in get_material_mapping: " + str(e))
        return {}

def process_journal(operation_type: str, start_date: datetime.date, end_date: datetime.date, selected_location: str):
    """
    Збирає дані з JOURNAL за заданим типом операції, періодом і локацією.
    """
    try:
        ss = get_spreadsheet()
        journal_sheet = ss.worksheet("JOURNAL")
        data = journal_sheet.get_all_values()
        result = {}
        for row in data[1:]:
            try:
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
                weight_str = row[5].replace('\xa0', '').replace(",", ".").strip() if row[5] else "0"
                sum_str = row[9].replace('\xa0', '').replace(",", ".").strip() if row[9] else "0"
                weight = float(weight_str)
                sum_val = float(sum_str)

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
        table_data.append([
            kind,
            format_number(vals["weight"]),
            format_number(vals["sum"])
        ])
    table_data.append([
        "**Загальний підсумок:**",
        format_number(overall_weight),
        format_number(overall_sum)
    ])
    return table_data

def generate_detailed_table_data(aggregated_data: dict, material_mapping: dict):
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
        table_data.append([f"Вид: {kind}", "", "", ""])
        materials = grouped[kind]
        sorted_materials = sorted(materials.items(), key=lambda x: x[1]["sum"], reverse=True)
        for material, vals in sorted_materials:
            weight = vals["weight"]
            sum_val = vals["sum"]
            avg = sum_val / weight if weight != 0 else 0
            table_data.append([
                material,
                format_number(weight),
                format_number(sum_val),
                format_number(avg)
            ])
        table_data.append([
            f"   Підсумок ({kind}):",
            format_number(subtotal["weight"]),
            format_number(subtotal["sum"]),
            ""
        ])
        overall_weight += subtotal["weight"]
        overall_sum += subtotal["sum"]

    table_data.append([
        "**Загальний підсумок:**",
        format_number(overall_weight),
        format_number(overall_sum),
        ""
    ])
    return table_data

def build_table_style(table_data):
    style_cmds = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTNAME', (0, 0), (-1, -1), 'NotoSans'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
    ]
    # Жирний шрифт для підсумкових рядків
    for i, row in enumerate(table_data):
        if row[0].startswith("**Загальний підсумок:") or row[0].strip().startswith("Підсумок"):
            style_cmds.append(('FONTNAME', (0, i), (-1, i), 'NotoSans-Bold'))
    return TableStyle(style_cmds)

def generate_pdf_report(params: dict) -> bytes:
    """
    Генерує PDF-звіт, враховуючи випадок, коли start_date == end_date.
    Якщо це одна дата, використовуємо формат: "Звіт за 19 лютого 2025 року".
    """

    # Реєструємо шрифти
    pdfmetrics.registerFont(TTFont("NotoSans", "fonts/NotoSans-Regular.ttf"))
    pdfmetrics.registerFont(TTFont("NotoSans-Bold", "fonts/NotoSans-Bold.ttf"))

    # Парсимо дати
    try:
        start_date = datetime.datetime.strptime(params["startDate"], "%d.%m.%Y").date()
        end_date = datetime.datetime.strptime(params["endDate"], "%d.%m.%Y").date()
    except Exception as e:
        logger.error("Error parsing dates: " + str(e))
        start_date = end_date = datetime.date.today()

    # Перевірка, чи весь місяць
    full_month = False
    if start_date.day == 1:
        last_day = calendar.monthrange(start_date.year, start_date.month)[1]
        if end_date.day == last_day and start_date.month == end_date.month and start_date.year == end_date.year:
            full_month = True

    # Формуємо заголовок
    locationText = params.get("location") if params.get("location") else "Загальний"
    # Якщо однакова дата
    if start_date == end_date and not full_month:
        # "Звіт за 19 лютого 2025 року"
        monthNames = ["січня", "лютого", "березня", "квітня", "травня", "червня",
                      "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
        day = start_date.day
        monthName = monthNames[start_date.month - 1]
        year = start_date.year
        docTitle = f"Звіт за {day} {monthName} {year} року ({locationText})"
    else:
        # Якщо повний місяць
        if full_month:
            monthNames = ["січень", "лютий", "березень", "квітень", "травень", "червень",
                          "липень", "серпень", "вересень", "жовтень", "листопад", "грудень"]
            monthName = monthNames[start_date.month - 1]
            docTitle = f"Звіт про закупівлі та продажі: {locationText} за {monthName} {start_date.year} року"
        else:
            startString = start_date.strftime("%Y-%m-%d")
            endString = end_date.strftime("%Y-%m-%d")
            docTitle = f"Звіт: {locationText} | {startString} - {endString}"

    # Налаштовуємо документ із невеликими полями
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            topMargin=20, leftMargin=20, rightMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()

    styles["Normal"].fontName = "NotoSans"
    styles["Title"].fontName = "NotoSans"
    styles["Heading1"].fontName = "NotoSans"
    styles["Heading2"].fontName = "NotoSans"

    # Малий стиль для інфо-рядка
    small_style = ParagraphStyle('Small', parent=styles['Normal'], fontSize=8)

    story = []

    # --- Додаємо логотип ---
    # Припустимо, він у папці "images/" і хочемо зберегти співвідношення сторін.
    # Якщо встановити лише width=150, а height=None, ReportLab сам спробує зберегти пропорції.
    try:
        logo = RLImage("images/logo_black_metal.png", width=200)  # height=None => пропорції
        logo.hAlign = 'LEFT'
        story.append(logo)
        story.append(Spacer(1, 4))
    except Exception as e:
        logger.error(f"Cannot load logo image: {e}")

    # --- Інфо-рядок (хто сформував звіт і коли) ---
    info_text = f"Звіт сформовано користувачем: {params.get('generated_by', 'Невідомий')} | " \
                f"{datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
    story.append(Paragraph(info_text, small_style))
    story.append(Spacer(1, 8))

    # --- Заголовок ---
    title_paragraph = Paragraph(docTitle, styles["Title"])
    story.append(title_paragraph)
    story.append(Spacer(1, 12))

    # --- Дані звіту ---
    op_types = [
        ("КУПІВЛЯ", "Куплені матеріали"),
        ("ПРОДАЖ", "Продані матеріали (Роздріб)"),
        ("ВІДВАНТАЖЕННЯ", "Відвантажені матеріали")
    ]
    material_mapping = get_material_mapping()

    for op_code, op_title in op_types:
        story.append(Paragraph(op_title, styles["Heading2"]))
        story.append(Spacer(1, 6))

        aggregated_data = process_journal(op_code, start_date, end_date, params.get("location"))
        if not aggregated_data:
            story.append(Paragraph(f"Немає даних для {op_title}", styles["Normal"]))
            story.append(Spacer(1, 12))
            continue

        if params.get("viewMode") == "СТИСЛИЙ":
            table_data = generate_brief_table_data(aggregated_data, material_mapping)
        else:
            table_data = generate_detailed_table_data(aggregated_data, material_mapping)

        table_style = build_table_style(table_data)
        table = Table(table_data, hAlign="LEFT")
        table.setStyle(table_style)

        story.append(table)
        story.append(Spacer(1, 12))

    doc.build(story)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf

# -------------------------------------------------------
# 3. Відправка PDF у Telegram
# -------------------------------------------------------

def send_report_to_telegram(pdf_file, report_title: str, chat_id: int, context: CallbackContext):
    pdf_buffer = BytesIO(pdf_file)
    pdf_buffer.name = "report.pdf"
    context.bot.send_document(chat_id=chat_id, document=pdf_buffer, caption=report_title)

def generate_report_from_params(params: dict, chat_id: int, context: CallbackContext):
    pdf = generate_pdf_report(params)
    send_report_to_telegram(pdf, "Звіт про рух матеріалів", chat_id, context)

# -------------------------------------------------------
# 4. Телеграм-логіка (обробники команд, розмови)
# -------------------------------------------------------

def start_command(update: Update, context: CallbackContext) -> int:
    """
    Звичайна команда /start. Якщо хочемо обробляти deep-linking (наприклад "?start=report"),
    можна тут перевірити context.args або update.message.text.
    """
    update.message.reply_text("Вітаємо! Використовуйте команду /report для генерації звіту про рух матеріалів.")
    return ConversationHandler.END

def report_command(update: Update, context: CallbackContext) -> int:
    chat_id = update.effective_chat.id

    # Якщо це група і хтось виконав /report, треба перевірити is_user_allowed
    if update.effective_chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
        # Можна перевірити, чи бот має достатні права, і чи користувач у списку
        pass

    if not is_user_allowed(chat_id):
        update.message.reply_text("⛔️Вибачте, у вас немає доступу до генерації звітів.")
        return ConversationHandler.END

    user_full_name = update.effective_user.full_name if update.effective_user.full_name else update.effective_user.username
    set_state(chat_id, {"stage": "choose_location", "generated_by": user_full_name})

    locations = get_locations()
    keyboard = []
    if locations:
        for loc in locations:
            keyboard.append([InlineKeyboardButton(loc, callback_data=f"choose_location:{loc}")])
    keyboard.append([InlineKeyboardButton("ЗАГАЛЬНИЙ ЗВІТ", callback_data="choose_location:ЗАГАЛЬНИЙ ЗВІТ")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("📍Оберіть точку для звіту:", reply_markup=reply_markup)
    return CHOOSING_LOCATION

def choose_location_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    _, location = query.data.split(":", 1)
    chat_id = query.message.chat.id
    state = get_state(chat_id) or {}
    state["location"] = "" if location == "ЗАГАЛЬНИЙ ЗВІТ" else location
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
    query.edit_message_text(text="🗓Оберіть період звіту:", reply_markup=InlineKeyboardMarkup(keyboard))
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
        query.edit_message_text(text="🗓Будь ласка, введіть дату або діапазон дат у форматі dd.MM.yyyy або dd.MM.yyyy-dd.MM.yyyy:")
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

# -------------------------------------------------------
# 5. Кнопка "ЗВІТИ" у групі -> відкриття приватного чату
# -------------------------------------------------------

def group_reports_button(update: Update, context: CallbackContext):
    """
    Ця функція надсилає в групу повідомлення з кнопкою "ЗВІТИ",
    яка веде у приватний чат із ботом (deep-link).
    Користувач мусить натиснути, щоб перейти в приватний чат.
    """
    if update.effective_chat.type not in [Chat.GROUP, Chat.SUPERGROUP]:
        update.message.reply_text("Ця команда призначена для групи.")
        return

    bot_username = context.bot.username  # Наприклад, "MyReportBot"
    # Deep-link (користувач відкриє приватний чат із ботом із параметром start=reports)
    deep_link_url = f"https://t.me/{bot_username}?start=reports"

    keyboard = [[InlineKeyboardButton("ЗВІТИ", url=deep_link_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        "Натисніть «ЗВІТИ», щоб відкрити приватний чат з ботом і сформувати звіт:",
        reply_markup=reply_markup
    )

def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Створимо команду /groupreports, яку можна викликати в групі,
    # щоб бот надіслав кнопку "ЗВІТИ"
    dp.add_handler(CommandHandler("groupreports", group_reports_button))

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
    updater.idle()

if __name__ == '__main__':
    main()
