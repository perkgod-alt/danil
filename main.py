import telebot
from telebot import types
import datetime
import os
import sqlite3
import requests

TOKEN = ""

bot = telebot.TeleBot(TOKEN)

# ====================== БАЗА ДАННЫХ ======================
conn = sqlite3.connect('bot_data.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    text TEXT,
    date TEXT
)
''')
conn.commit()

user_states = {}

# ====================== START ======================
@bot.message_handler(commands=['start'])
def start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("⏰ Текущее время"),
        types.KeyboardButton("📝 Новая заметка"),
        types.KeyboardButton("🌤 Погода"),
        types.KeyboardButton("📋 Мои заметки"),
        types.KeyboardButton("💱 Конвертер валют"),
        types.KeyboardButton("ℹ️ О боте")
    )
    
    bot.send_message(message.chat.id, 
                     f"Привет, {message.from_user.first_name}!\n\n"
                     "Выбирай функцию 👇", 
                     reply_markup=markup)

# ====================== ОСНОВНАЯ ЛОГИКА ======================
@bot.message_handler(content_types=['text'])
def handle_text(message):
    user_id = message.chat.id
    text = message.text.lower().strip()

    if "время" in text or "⏰" in message.text:
        now = datetime.datetime.now().strftime("%H:%M:%S")
        bot.send_message(message.chat.id, f"Сейчас: **{now}**")

    elif "новая заметка" in text or "📝" in message.text:
        bot.send_message(message.chat.id, "Напиши текст заметки:")
        user_states[user_id] = "waiting_note"

    elif "мои заметки" in text or "📋" in message.text:
        show_notes(message)

    elif "погода" in text or "🌤" in message.text:
        bot.send_message(message.chat.id, "Напиши город:")
        user_states[user_id] = "waiting_weather"

    elif "конвертер" in text or "💱" in message.text:
        bot.send_message(message.chat.id, "Введи сумму в гривнах (UAH):")
        user_states[user_id] = "waiting_currency"

    elif "о боте" in text or "ℹ️" in message.text:
        bot.send_message(message.chat.id, "Бот на SQLite работает 🚀")

    # Обработка состояний
    elif user_states.get(user_id) == "waiting_note":
        save_note(message, user_id)
        user_states[user_id] = None

    elif user_states.get(user_id) == "waiting_weather":
        get_weather(message, message.text.strip())
        user_states[user_id] = None

    elif user_states.get(user_id) == "waiting_currency":
        try:
            amount = float(message.text.replace(",", "."))
            convert_currency(message, amount)
        except:
            bot.send_message(message.chat.id, "❌ Введи число")
        user_states[user_id] = None

    else:
        bot.send_message(message.chat.id, "Используй кнопки 👇")

# ====================== ФУНКЦИИ ======================
def save_note(message, user_id):
    cursor.execute("INSERT INTO notes (user_id, text, date) VALUES (?, ?, ?)",
                   (user_id, message.text, datetime.datetime.now().strftime("%d.%m %H:%M")))
    conn.commit()
    bot.send_message(message.chat.id, "✅ Заметка сохранена!")

def show_notes(message):
    cursor.execute("SELECT date, text FROM notes WHERE user_id = ? ORDER BY id DESC", (message.chat.id,))
    notes = cursor.fetchall()
    if notes:
        text = "📋 **Твои заметки:**\n\n"
        for date, note in notes:
            text += f"[{date}] {note}\n\n"
        bot.send_message(message.chat.id, text)
    else:
        bot.send_message(message.chat.id, "Пока заметок нет.")

def get_weather(message, city):
    try:
        # Геокодирование
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=ru"
        geo_response = requests.get(geo_url, timeout=5)
        geo = geo_response.json()
        
        if not geo.get("results"):
            bot.send_message(message.chat.id, f"Город '{city}' не найден.")
            return
            
        lat = geo["results"][0]["latitude"]
        lon = geo["results"][0]["longitude"]
        city_name = geo["results"][0]["name"]
        
        # Погода
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code&timezone=Europe/Moscow"
        weather_response = requests.get(weather_url, timeout=5)
        data = weather_response.json()
        
        temp = data["current"]["temperature_2m"]
        code = data["current"]["weather_code"]
        
        weather_desc = {
            0: "Ясно ☀️", 1: "В основном ясно 🌤️", 2: "Переменная облачность ⛅",
            3: "Пасмурно ☁️", 45: "Туман 🌫️", 51: "Лёгкий дождь 🌦️",
            61: "Дождь 🌧️", 71: "Снег ❄️", 95: "Гроза ⛈️"
        }.get(code, "Облачно")

        bot.send_message(message.chat.id, 
                        f"🌤 **{city_name}**\n"
                        f"Температура: **{temp}°C**\n"
                        f"Состояние: {weather_desc}")
        
    except Exception as e:
        bot.send_message(message.chat.id, "Не удалось получить погоду 😔 Попробуй позже.")

def convert_currency(message, amount):
    try:
        url = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/uah.min.json"
        data = requests.get(url).json()["uah"]
        
        usd = round(amount * data["usd"], 2)
        eur = round(amount * data["eur"], 2)
        rub = round(amount * data["rub"], 2)
        
        bot.send_message(message.chat.id, 
                        f"💱 **{amount} UAH** =\n\n"
                        f"💵 **{usd} USD**\n"
                        f"💶 **{eur} EUR**\n"
                        f"💰 **{rub} RUB**")
    except:
        bot.send_message(message.chat.id, "Не удалось получить курсы валют.")

print("Бот успешно запущен с SQLite!")
bot.infinity_polling()