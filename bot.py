#!/usr/bin/env python3
import logging
import time
import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Updater, CommandHandler, CallbackQueryHandler,
                          MessageHandler, Filters, ConversationHandler, CallbackContext)

# --- Налаштування ---
BOT_TOKEN = "7259566463:AAGnKFTQQ_MfgpQ6GuZNtv95jASDIK0a62A"
SPREADSHEET_ID = "1ius3nORqf6RIlu15dlV6kgXIor4u2ayCjASxSGpwsm4"  # ID вашої Google Таблиці з даними
ALLOWED_USERS = {130476571}  # Telegram ID користувачів, яким дозволено запуск звітів

# Стани розмови
(CHOOSING_LOCATION, CHOOSING_VIEW, CHOOSING_PERIOD, ENTERING_CUSTOM_DATES) = range(4)

# Глобальний словник для зберігання стану користувачів (для простоти)
user_states = {}

# --- Налаштування логування ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Командні обробники ---
def start(update: Update, context: CallbackContext) -> int:
    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED_USERS:
        update.message.reply_text("Вибачте, у вас немає доступу до генерації звітів.")
        return ConversationHandler.END
    update.message.reply_text("Вітаємо! Використовуйте команду /report для генерації звіту про рух матеріалів.")
    return ConversationHandler.END

def report(update: Update, context: CallbackContext) -> int:
    chat_id = update.effective_chat.id
    if chat_id not in ALLOWED_USERS:
        update.message.reply_text("Вибачте, у вас немає доступу до генерації звітів.")
        return ConversationHandler.END
    keyboard = [
        [InlineKeyboardButton("Загальний", callback_data="choose_location:Загальний")],
        [InlineKeyboardButton("ІРПІНЬ", callback_data="choose_location:ІРПІНЬ")],
        [InlineKeyboardButton("ГОСТОМЕЛЬ", callback_data="choose_location:ГОСТОМЕЛЬ")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Оберіть точку для звіту:", reply_markup=reply_markup)
    user_states[chat_id] = {"stage": "choose_location"}
    logger.info(f"State for {chat_id} set to choose_location")
    return CHOOSING_LOCATION

# --- CallbackQuery обробники ---
def choose_location_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data  # Наприклад: "choose_location:Загальний"
    _, location = data.split(":", 1)
    chat_id = query.message.chat.id
    user_states[chat_id] = user_states.get(chat_id, {})
    user_states[chat_id]["location"] = location
    user_states[chat_id]["stage"] = "choose_view"
    logger.info(f"Location set to {location} for {chat_id}. Stage: choose_view")
    keyboard = [
        [InlineKeyboardButton("СТИСЛИЙ", callback_data="choose_view:СТИСЛИЙ")],
        [InlineKeyboardButton("РОЗГОРНУТИЙ", callback_data="choose_view:РОЗГОРНУТИЙ")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text("Оберіть режим звіту:", reply_markup=reply_markup)
    return CHOOSING_VIEW

def choose_view_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data  # Наприклад: "choose_view:СТИСЛИЙ"
    _, view_mode = data.split(":", 1)
    chat_id = query.message.chat.id
    user_states[chat_id]["viewMode"] = view_mode
    user_states[chat_id]["stage"] = "choose_period"
    logger.info(f"View mode set to {view_mode} for {chat_id}. Stage: choose_period")
    keyboard = [
        [InlineKeyboardButton("Сьогодні", callback_data="choose_period:Сьогодні")],
        [InlineKeyboardButton("Вчора", callback_data="choose_period:Вчора")],
        [InlineKeyboardButton("Минулий тиждень", callback_data="choose_period:Минулий тиждень")],
        [InlineKeyboardButton("Минулий місяць", callback_data="choose_period:Минулий місяць")],
        [InlineKeyboardButton("З - ПО", callback_data="choose_period:З - ПО")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text("Оберіть період звіту:", reply_markup=reply_markup)
    return CHOOSING_PERIOD

def choose_period_callback(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    data = query.data  # Наприклад: "choose_period:Сьогодні"
    _, period = data.split(":", 1)
    chat_id = query.message.chat.id
    user_states[chat_id]["periodType"] = period
    if period == "З - ПО":
        user_states[chat_id]["stage"] = "enter_custom_dates"
        query.edit_message_text("Будь ласка, введіть дату або діапазон дат у форматі dd.MM.yyyy або dd.MM.yyyy-dd.MM.yyyy:")
        logger.info(f"Stage set to enter_custom_dates for {chat_id}")
        return ENTERING_CUSTOM_DATES
    else:
        user_states[chat_id]["stage"] = "completed"
        computed = compute_standard_period(period)
        user_states[chat_id]["startDate"] = computed["start"]
        user_states[chat_id]["endDate"] = computed["end"]
        query.edit_message_text("Ваші параметри збережено. Звіт формується, будь ласка, очікуйте.")
        logger.info(f"Standard period computed for {chat_id}: {user_states[chat_id]}")
        generate_report_from_params(user_states[chat_id], chat_id, context)
        return ConversationHandler.END

def custom_dates(update: Update, context: CallbackContext) -> int:
    chat_id = update.effective_chat.id
    text = update.message.text
    parts = text.split("-")
    if len(parts) not in [1, 2]:
        update.message.reply_text("Невірний формат дат. Будь ласка, введіть у форматі dd.MM.yyyy або dd.MM.yyyy-dd.MM.yyyy")
        return ENTERING_CUSTOM_DATES
    user_states[chat_id]["periodType"] = "З - ПО"
    user_states[chat_id]["startDate"] = parts[0].strip()
    user_states[chat_id]["endDate"] = parts[1].strip() if len(parts) == 2 else parts[0].strip()
    user_states[chat_id]["stage"] = "completed"
    update.message.reply_text("Ваші параметри збережено. Звіт формується, будь ласка, очікуйте.")
    generate_report_from_params(user_states[chat_id], chat_id, context)
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("Операцію скасовано.")
    return ConversationHandler.END

# --- Допоміжні функції ---
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
    # Форматуємо дати як dd.MM.yyyy
    return {"start": start.strftime("%d.%m.%Y"), "end": end.strftime("%d.%m.%Y")}

# Dummy функція для генерації звіту. Замість цього інтегруйте власну логіку генерації PDF.
def generate_report_from_params(params: dict, chat_id: int, context: CallbackContext):
    logger.info(f"Generating report for chat {chat_id} with params: {params}")
    # Тут має бути ваша логіка роботи з Google Таблицею, генерації PDF тощо.
    time.sleep(2)  # Імітація затримки генерації звіту
    send_report_to_telegram("dummy_pdf_content", "Звіт (симуляція)", chat_id, context)

def send_report_to_telegram(pdf_file, report_title: str, chat_id: int, context: CallbackContext):
    # Ця функція імітує надсилання PDF звіту. Замість цього використовуйте власну логіку.
    context.bot.send_message(chat_id=chat_id, text=f"Report generated: {report_title}\n(Тут має бути PDF звіт)")
    logger.info(f"Sent report to {chat_id}: {report_title}")

# --- Основна функція ---
def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("report", report)],
        states={
            CHOOSING_LOCATION: [CallbackQueryHandler(choose_location_callback, pattern="^choose_location:")],
            CHOOSING_VIEW: [CallbackQueryHandler(choose_view_callback, pattern="^choose_view:")],
            CHOOSING_PERIOD: [CallbackQueryHandler(choose_period_callback, pattern="^choose_period:")],
            ENTERING_CUSTOM_DATES: [MessageHandler(Filters.text & ~Filters.command, custom_dates)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(conv_handler)

    updater.start_polling()
    logger.info("Bot started. Listening for commands...")
    updater.idle()

if __name__ == '__main__':
    main()
