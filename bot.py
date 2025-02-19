#!/usr/bin/env python3
import os
import json
import time
import datetime
import logging

import gspread
from google.oauth2.service_account import Credentials

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Updater, CommandHandler, CallbackQueryHandler,
                          MessageHandler, Filters, ConversationHandler, CallbackContext)

# Налаштування логування
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Змінні середовища
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # Наприклад, "7259566463:..."
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")  # ID вашої Google таблиці
SERVICE_ACCOUNT_FILE = os.environ.get("SERVICE_ACCOUNT_FILE")  # Шлях до файлу credentials.json

# Розмовні стани
CHOOSING_LOCATION, CHOOSING_VIEW, CHOOSING_PERIOD, ENTERING_CUSTOM_DATES = range(4)

# Глобальний словник для зберігання стану користувача (для простоти)
user_states = {}

# --- Функції для роботи з Google Таблицею ---
def get_spreadsheet():
    scopes = ['https://www.googleapis.com/auth/spreadsheets',
              'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID)

def is_user_allowed(chat_id):
    try:
        ss = get_spreadsheet()
        users_sheet = ss.worksheet("USERS")
        data = users_sheet.get_all_values()
        # Припускаємо, що перший рядок – заголовок. Telegram ID у колонці C (індекс 2), дозвіл "REPORT" у колонці G (індекс 6)
        for row in data[1:]:
            if row[2].strip() == str(chat_id) and row[6].strip().upper() == "REPORT":
                logger.info(f"User {chat_id} is allowed.")
                return True
    except Exception as e:
        logger.error("Error in is_user_allowed: " + str(e))
    logger.info(f"User {chat_id} is not allowed.")
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
    # Запит вибору точки
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

# Dummy функція генерації звіту. Замість цього блоку інтегруйте свою логіку читання даних та генерації PDF.
def generate_report_from_params(params: dict, chat_id: int, context: CallbackContext):
    logger.info(f"Generating report for chat {chat_id} with params: {json.dumps(params)}")
    time.sleep(2)  # Імітація затримки генерації звіту
    dummy_pdf = b"Dummy PDF content"  # Замість цього використовуйте реальний PDF (байти файлу)
    send_report_to_telegram(dummy_pdf, "Звіт (симуляція)", chat_id, context)

def send_report_to_telegram(pdf_file, report_title: str, chat_id: int, context: CallbackContext):
    context.bot.send_document(chat_id=chat_id, document=pdf_file, caption=f"📄 {report_title}")
    logger.info(f"Sent report to {chat_id}: {report_title}")

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
