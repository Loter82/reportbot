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

# Змінні оточення
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON")

if not SERVICE_ACCOUNT_JSON:
    logger.error("SERVICE_ACCOUNT_JSON is not set in environment variables!")
    raise ValueError("SERVICE_ACCOUNT_JSON environment variable is missing.")

# Перевірка коректності JSON сервісного акаунта
def get_credentials():
    try:
        service_account_info = json.loads(SERVICE_ACCOUNT_JSON)
        scopes = ['https://www.googleapis.com/auth/spreadsheets',
                  'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
        return creds
    except Exception as e:
        logger.error("Error parsing SERVICE_ACCOUNT_JSON: " + str(e))
        raise

# Функція для отримання доступу до Google Таблиці
def get_spreadsheet():
    try:
        creds = get_credentials()
        gc = gspread.authorize(creds)
        ss = gc.open_by_key(SPREADSHEET_ID)
        logger.info(f"Successfully accessed spreadsheet: {ss.title}")
        return ss
    except Exception as e:
        logger.error("Error accessing Google Spreadsheet: " + str(e))
        raise

# Перевірка доступу до Google Таблиці
def test_google_sheets_connection():
    try:
        ss = get_spreadsheet()
        sheet_list = ss.worksheets()
        logger.info(f"Worksheets found: {[sheet.title for sheet in sheet_list]}")
        return True
    except Exception as e:
        logger.error("Google Sheets connection test failed: " + str(e))
        return False

# Команда для тестування доступу
def test_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    if test_google_sheets_connection():
        update.message.reply_text("✅ Успішно підключено до Google Таблиці!")
    else:
        update.message.reply_text("❌ Помилка підключення до Google Таблиці. Перевірте логи.")

# Основна функція запуску бота
def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    
    dp.add_handler(CommandHandler("test", test_command))
    
    updater.start_polling()
    logger.info("Bot started. Listening for commands...")
    updater.idle()

if __name__ == '__main__':
    main()
