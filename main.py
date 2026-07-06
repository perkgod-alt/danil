import os
import logging
import sqlite3
import threading
from datetime import datetime
from contextlib import contextmanager

import requests
import telebot
from telebot import types

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise SystemExit("Set BOT_TOKEN before running")

DB_PATH = os.getenv("BOT_DB", "bot_data.db")
MAX_NOTE_LEN = 4000
PER_PAGE = 5
TIMEOUT = 8
RATES_TTL = 600

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
lock = threading.Lock()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with lock:
            yield conn
            conn.commit()
    finally:
        conn.close()


with db() as conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS notes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER NOT NULL, "
        "text TEXT NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_user ON notes(user_id)")


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def has(text, *words):
    if not text:
        return False
    low = text.lower()
    return any(w in low for w in words)


def menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("⏰ Time"),
        types.KeyboardButton("📝 New note"),
        types.KeyboardButton("📋 My notes"),
        types.KeyboardButton("🌤 Weather"),
        types.KeyboardButton("💱 Converter"),
        types.KeyboardButton("ℹ️ About"),
    )
    return kb


@bot.message_handler(commands=["start"])
def start(m):
    bot.clear_step_handler_by_chat_id(m.chat.id)
    bot.send_message(
        m.chat.id,
        f"Hi, {esc(m.from_user.first_name)}!\n\nPick an option below.",
        reply_markup=menu(),
    )


@bot.message_handler(commands=["cancel"])
def cancel(m):
    bot.clear_step_handler_by_chat_id(m.chat.id)
    bot.send_message(m.chat.id, "Cancelled.", reply_markup=menu())


@bot.message_handler(func=lambda m: has(m.text, "time", "⏰"))
def show_time(m):
    now = datetime.now()
    bot.send_message(m.chat.id, f"🕐 {now:%H:%M:%S}\n📅 {now:%d.%m.%Y}")


@bot.message_handler(func=lambda m: has(m.text, "new note", "📝"))
def ask_note(m):
    msg = bot.send_message(m.chat.id, "Type your note (or /cancel):")
    bot.register_next_step_handler(msg, save_note)


def save_note(m):
    if (m.text or "").startswith("/"):
        return
    text = (m.text or "").strip()
    if not text:
        bot.send_message(m.chat.id, "Empty note, nothing saved.")
        return
    with db() as conn:
        conn.execute(
            "INSERT INTO notes (user_id, text, created_at) VALUES (?, ?, ?)",
            (m.chat.id, text[:MAX_NOTE_LEN], datetime.now().strftime("%d.%m.%Y %H:%M")),
        )
    bot.send_message(m.chat.id, "Saved.", reply_markup=menu())


def notes_page(user_id, page):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, text, created_at FROM notes WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()

    if not rows:
        return "You have no notes yet.", None

    pages = (len(rows) + PER_PAGE - 1) // PER_PAGE
    page = max(0, min(page, pages - 1))
    rows = rows[page * PER_PAGE:(page + 1) * PER_PAGE]

    text = f"📋 Your notes ({page + 1}/{pages})\n\n"
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        text += f"<i>{r['created_at']}</i>\n{esc(r['text'])}\n\n"
        kb.add(types.InlineKeyboardButton(f"Delete ({r['created_at']})", callback_data=f"del:{r['id']}"))

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("◀️", callback_data=f"pg:{page - 1}"))
    if page < pages - 1:
        nav.append(types.InlineKeyboardButton("▶️", callback_data=f"pg:{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.add(types.InlineKeyboardButton("Clear all", callback_data="clear"))
    return text, kb


@bot.message_handler(func=lambda m: has(m.text, "my notes", "📋"))
def list_notes(m):
    text, kb = notes_page(m.chat.id, 0)
    bot.send_message(m.chat.id, text, reply_markup=kb)


def refresh(chat_id, message_id, text, kb=None):
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=kb)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("pg:"))
def page_cb(c):
    text, kb = notes_page(c.message.chat.id, int(c.data[3:]))
    refresh(c.message.chat.id, c.message.message_id, text, kb)
    bot.answer_callback_query(c.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("del:"))
def delete_cb(c):
    with db() as conn:
        deleted = conn.execute(
            "DELETE FROM notes WHERE id = ? AND user_id = ?",
            (int(c.data[4:]), c.message.chat.id),
        ).rowcount
    bot.answer_callback_query(c.id, "Deleted" if deleted else "Not found")
    text, kb = notes_page(c.message.chat.id, 0)
    refresh(c.message.chat.id, c.message.message_id, text, kb)


@bot.callback_query_handler(func=lambda c: c.data == "clear")
def clear_cb(c):
    with db() as conn:
        conn.execute("DELETE FROM notes WHERE user_id = ?", (c.message.chat.id,))
    bot.answer_callback_query(c.id, "All notes deleted")
    refresh(c.message.chat.id, c.message.message_id, "All notes deleted.")


WEATHER = {
    0: "Clear ☀️", 1: "Mostly clear 🌤", 2: "Partly cloudy ⛅", 3: "Overcast ☁️",
    45: "Fog 🌫", 48: "Rime fog 🌫", 51: "Light drizzle 🌦", 53: "Drizzle 🌦",
    55: "Heavy drizzle 🌧", 61: "Light rain 🌦", 63: "Rain 🌧", 65: "Heavy rain 🌧",
    71: "Light snow 🌨", 73: "Snow ❄️", 75: "Heavy snow ❄️", 77: "Snow grains 🌨",
    80: "Showers 🌦", 81: "Showers 🌧", 82: "Heavy showers ⛈", 85: "Snow showers 🌨",
    86: "Heavy snow showers ❄️", 95: "Thunderstorm ⛈", 96: "Thunderstorm, hail ⛈",
    99: "Severe thunderstorm ⛈",
}


def weather(city):
    geo = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1},
        timeout=TIMEOUT,
    ).json()
    if not geo.get("results"):
        return f"City '{esc(city)}' not found."

    loc = geo["results"][0]
    data = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": loc["latitude"],
            "longitude": loc["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
            "timezone": "auto",
        },
        timeout=TIMEOUT,
    ).json()["current"]

    place = esc(loc["name"])
    if loc.get("country"):
        place += f", {esc(loc['country'])}"

    return (
        f"🌤 <b>{place}</b>\n"
        f"Temperature: <b>{data['temperature_2m']}°C</b> (feels like {data['apparent_temperature']}°C)\n"
        f"{WEATHER.get(data['weather_code'], 'Cloudy')}\n"
        f"Humidity: {data['relative_humidity_2m']}%\n"
        f"Wind: {data['wind_speed_10m']} km/h"
    )


@bot.message_handler(func=lambda m: has(m.text, "weather", "🌤"))
def ask_weather(m):
    msg = bot.send_message(m.chat.id, "Enter a city (or /cancel):")
    bot.register_next_step_handler(msg, send_weather)


def send_weather(m):
    if (m.text or "").startswith("/"):
        return
    city = (m.text or "").strip()
    if not city:
        bot.send_message(m.chat.id, "City name is empty.")
        return
    bot.send_chat_action(m.chat.id, "typing")
    try:
        bot.send_message(m.chat.id, weather(city))
    except requests.RequestException:
        bot.send_message(m.chat.id, "Weather service is down, try again later.")
    except (KeyError, ValueError):
        bot.send_message(m.chat.id, "Couldn't read the weather response.")


rates_cache = {"time": 0, "data": None}


def uah_rates():
    now = datetime.now().timestamp()
    if rates_cache["data"] and now - rates_cache["time"] < RATES_TTL:
        return rates_cache["data"]
    urls = [
        "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/uah.min.json",
        "https://latest.currency-api.pages.dev/v1/currencies/uah.min.json",
    ]
    for url in urls:
        try:
            data = requests.get(url, timeout=TIMEOUT).json()["uah"]
            rates_cache.update(time=now, data=data)
            return data
        except (requests.RequestException, KeyError, ValueError):
            continue
    raise RuntimeError("no rates")


@bot.message_handler(func=lambda m: has(m.text, "converter", "💱"))
def ask_amount(m):
    msg = bot.send_message(m.chat.id, "Enter amount in UAH:")
    bot.register_next_step_handler(msg, convert)


def convert(m):
    if (m.text or "").startswith("/"):
        return
    try:
        amount = float((m.text or "").replace(",", ".").strip())
    except ValueError:
        bot.send_message(m.chat.id, "That's not a number.")
        return
    if amount <= 0:
        bot.send_message(m.chat.id, "Amount must be positive.")
        return
    bot.send_chat_action(m.chat.id, "typing")
    try:
        rates = uah_rates()
    except RuntimeError:
        bot.send_message(m.chat.id, "Couldn't fetch exchange rates.")
        return
    lines = [f"💱 <b>{amount:g} UAH</b> ="]
    for code, sign in (("usd", "💵"), ("eur", "💶"), ("pln", "🇵🇱"), ("rub", "💰")):
        if code in rates:
            lines.append(f"{sign} <b>{amount * rates[code]:.2f} {code.upper()}</b>")
    bot.send_message(m.chat.id, "\n".join(lines))


@bot.message_handler(func=lambda m: has(m.text, "about", "ℹ️"))
def about(m):
    bot.send_message(
        m.chat.id,
        "Notes, weather and currency converter in one bot.\nCommands: /start, /cancel",
    )


@bot.message_handler(func=lambda m: True, content_types=["text"])
def fallback(m):
    bot.send_message(m.chat.id, "Use the buttons below.", reply_markup=menu())


if __name__ == "__main__":
    log.info("Bot started")
    bot.infinity_polling(skip_pending=True)
