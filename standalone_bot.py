#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import sqlite3
from datetime import datetime, timedelta
import subprocess
import asyncio
import random

# === Автоустановка зависимостей ===
try:
    from aiogram import Bot, Dispatcher, executor, types
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from aiogram.utils.callback_data import CallbackData
    import aiohttp
    from dotenv import load_dotenv
except ImportError:
    print("📦 Устанавливаю зависимости... (1–2 минуты)")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except:
        import urllib.request
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", "get-pip.py")
        subprocess.check_call([sys.executable, "get-pip.py"])
        os.remove("get-pip.py")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "aiogram==2.25.1", "python-dotenv", "aiohttp"])
    from aiogram import Bot, Dispatcher, executor, types
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from aiogram.utils.callback_data import CallbackData
    import aiohttp
    from dotenv import load_dotenv

# === Конфигурация ===
TOKEN_FILE = "bot_token.txt"
DB_FILE = "farm_data.db"

menu_cb = CallbackData("menu", "action")
zone_cb = CallbackData("zone", "name")

# Зоны фарма (увеличен доход до 500 ₽/день)
ZONES = {
    "mine": {"name": "Шахта", "currency": "TON", "rate": 0.5},   # 0.5 TON за 2 часа = ~35 ₽
    "lab": {"name": "Лаборатория", "currency": "NOT", "rate": 100},
    "space": {"name": "Космос", "currency": "USDT", "rate": 0.2}
}

RATES = {"TON": 70.0, "NOT": 0.12, "USDT": 92.0}

# Рулетка удачи (без проигрыша)
WHEEL_PRIZES = [
    {"type": "ton", "amount": 0.1, "desc": "0.1 TON"},
    {"type": "ton", "amount": 0.2, "desc": "0.2 TON"},
    {"type": "ton", "amount": 0.3, "desc": "0.3 TON"},
    {"type": "ton", "amount": 0.5, "desc": "0.5 TON"},
    {"type": "usdt", "amount": 10, "desc": "10 USDT"},
    {"type": "usdt", "amount": 20, "desc": "20 USDT"},
    {"type": "usdt", "amount": 50, "desc": "50 USDT"},
    {"type": "booster", "multiplier": 1.2, "hours": 24, "desc": "Буст ×1.2 на 24ч"},
    {"type": "booster", "multiplier": 1.5, "hours": 12, "desc": "Буст ×1.5 на 12ч"},
    {"type": "booster", "multiplier": 2.0, "hours": 6, "desc": "Буст ×2.0 на 6ч"}
]

# === База данных ===
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            ton REAL DEFAULT 0,
            not_tokens INTEGER DEFAULT 0,
            usdt REAL DEFAULT 0,
            last_farm TEXT,
            booster_multiplier REAL DEFAULT 1.0,
            current_zone TEXT DEFAULT 'mine',
            daily_claimed DATE,
            last_wheel TEXT,
            active_booster_multiplier REAL DEFAULT 1.0,
            booster_expire TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT id, ton, not_tokens, usdt, last_farm, booster_multiplier, current_zone,
               daily_claimed, last_wheel, active_booster_multiplier, booster_expire
        FROM users WHERE id = ?
    """, (user_id,))
    row = c.fetchone()
    if not row:
        now = datetime.utcnow().isoformat()
        c.execute("""
            INSERT INTO users (id, last_farm) 
            VALUES (?, ?)
        """, (user_id, now))
        conn.commit()
        row = (user_id, 0.0, 0, 0.0, now, 1.0, 'mine', None, None, 1.0, None)
    conn.close()
    return {
        "id": row[0],
        "ton": row[1],
        "not_tokens": row[2],
        "usdt": row[3],
        "last_farm": row[4],
        "booster_multiplier": row[5] or 1.0,
        "current_zone": row[6],
        "daily_claimed": row[7],
        "last_wheel": row[8],
        "active_booster_multiplier": row[9] or 1.0,
        "booster_expire": row[10]
    }

def update_user(user_id, **kwargs):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    fields = ", ".join([f"{k} = ?" for k in kwargs])
    values = list(kwargs.values()) + [user_id]
    c.execute(f"UPDATE users SET {fields} WHERE id = ?", values)
    conn.commit()
    conn.close()

# === Расчёт пассивного дохода ===
def calculate_passive(user):
    now = datetime.utcnow()
    last_str = user["last_farm"]
    if not last_str:
        return {"TON": 0, "NOT": 0, "USDT": 0}
    try:
        last = datetime.fromisoformat(last_str)
    except:
        return {"TON": 0, "NOT": 0, "USDT": 0}
    hours = (now - last).total_seconds() / 3600
    cycles = int(hours // 2)
    
    # Проверяем активный буст из рулетки
    active_boost = user["active_booster_multiplier"]
    expire_str = user["booster_expire"]
    if expire_str:
        try:
            expire = datetime.fromisoformat(expire_str)
            if now > expire:
                active_boost = 1.0
                update_user(user["id"], active_booster_multiplier=1.0, booster_expire=None)
        except:
            pass
    
    total_multiplier = active_boost * user["booster_multiplier"]
    
    zone = user["current_zone"]
    rate = ZONES[zone]["rate"]
    currency = ZONES[zone]["currency"]
    
    result = {"TON": 0, "NOT": 0, "USDT": 0}
    if currency == "TON":
        result["TON"] = rate * cycles * total_multiplier
    elif currency == "NOT":
        result["NOT"] = int(rate * cycles * total_multiplier)
    elif currency == "USDT":
        result["USDT"] = rate * cycles * total_multiplier
    
    return result

# === Рулетка удачи ===
def can_spin_wheel(last_wheel_str):
    if not last_wheel_str:
        return True
    try:
        last = datetime.fromisoformat(last_wheel_str)
        return datetime.utcnow() >= last + timedelta(hours=48)
    except:
        return True

def spin_wheel(user_id):
    prize = random.choice(WHEEL_PRIZES)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    if prize["type"] == "ton":
        c.execute("UPDATE users SET ton = ton + ? WHERE id = ?", (prize["amount"], user_id))
        message = f"🎉 Ты выиграл {prize['desc']}!"
    elif prize["type"] == "usdt":
        c.execute("UPDATE users SET usdt = usdt + ? WHERE id = ?", (prize["amount"], user_id))
        message = f"🎉 Ты выиграл {prize['desc']}!"
    elif prize["type"] == "booster":
        expire_time = datetime.utcnow() + timedelta(hours=prize["hours"])
        c.execute("""
            UPDATE users 
            SET active_booster_multiplier = ?, booster_expire = ?
            WHERE id = ?
        """, (prize["multiplier"], expire_time.isoformat(), user_id))
        message = f"🚀 Активирован {prize['desc']}!"
    
    c.execute("UPDATE users SET last_wheel = ? WHERE id = ?", (datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()
    return message

# === Вывод через Tonconsole ===
async def tonconsole_withdraw(amount_rub, card_number, user_id):
    tonconsole_key = os.getenv("TON_CONSOLE_API_KEY")
    if not tonconsole_key:
        return {"error": "TON_CONSOLE_API_KEY не задан. Создайте файл .env рядом с .exe"}
    
    url = "https://api.tonconsole.com/v1/payouts/sbp"
    headers = {
        "Authorization": f"Bearer {tonconsole_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "amount": amount_rub,
        "currency": "RUB",
        "destination": card_number,
        "description": f"Withdrawal for user {user_id}",
        "external_id": f"pf_{user_id}_{int(datetime.utcnow().timestamp())}"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 201:
                    return {"success": True}
                else:
                    error_text = await resp.text()
                    return {"error": f"Tonconsole error: {error_text}"}
    except Exception as e:
        return {"error": str(e)}

# === Получение токена ===
def get_bot_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token
    print("\n" + "="*50)
    print("🤖 Добро пожаловать в Pixel Farm!")
    print("Чтобы начать, тебе нужен BOT_TOKEN от @BotFather.")
    print("="*50)
    token = input("\nВставь сюда свой BOT_TOKEN: ").strip()
    if not token or len(token) < 10:
        print("❌ Неверный токен.")
        sys.exit(1)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    print("✅ Токен сохранён!\n")
    return token

# === Вспомогательная функция: таймер ===
def get_time_until_next_farm(last_farm_str):
    if not last_farm_str:
        return "Готово!"
    try:
        last = datetime.fromisoformat(last_farm_str)
        next_farm = last + timedelta(hours=2)
        now = datetime.utcnow()
        if now >= next_farm:
            return "Готово!"
        delta = next_farm - now
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes = remainder // 60
        return f"{hours} ч {minutes} мин"
    except:
        return "00:00"

# === Основной бот ===
if __name__ == "__main__":
    if os.path.exists(".env"):
        load_dotenv(".env")
    
    BOT_TOKEN = get_bot_token()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(bot)
    init_db()

    @dp.message_handler(commands=['start'])
    async def start(message: types.Message):
        kb = InlineKeyboardMarkup(row_width=2).add(
            InlineKeyboardButton("📊 Баланс", callback_data=menu_cb.new(action="balance")),
            InlineKeyboardButton("🎡 Рулетка", callback_data=menu_cb.new(action="wheel")),
            InlineKeyboardButton("🌍 Зоны", callback_data=menu_cb.new(action="zones")),
            InlineKeyboardButton("📈 Курс", callback_data=menu_cb.new(action="rates")),
            InlineKeyboardButton("📤 Вывести", callback_data=menu_cb.new(action="withdraw"))
        )
        await message.answer(
            "🎮 Pixel Farm запущен!\n\n"
            "🔥 Зарабатывай до 500 ₽/день без вложений!\n"
            "• Фарми каждые 2 часа\n"
            "• Играй в рулетку раз в 2 дня\n"
            "• Выводи на карту от 500 ₽",
            reply_markup=kb
        )

    @dp.callback_query_handler(menu_cb.filter(action="balance"))
    async def balance(callback: types.CallbackQuery):
        user = get_user(callback.from_user.id)
        passive = calculate_passive(user)
        total_ton = user["ton"] + passive["TON"]
        total_not = user["not_tokens"] + passive["NOT"]
        total_usdt = user["usdt"] + passive["USDT"]
        rub_ton = total_ton * RATES["TON"]
        rub_not = total_not * RATES["NOT"]
        rub_usdt = total_usdt * RATES["USDT"]
        total_rub = rub_ton + rub_not + rub_usdt
        time_left = get_time_until_next_farm(user["last_farm"])
        await callback.message.edit_text(
            f"💰 Баланс:\n"
            f"⚡ TON: {total_ton:.4f} (~{int(rub_ton)} ₽)\n"
            f"💎 NOT: {total_not} (~{int(rub_not)} ₽)\n"
            f"🪙 USDT: {total_usdt:.4f} (~{int(rub_usdt)} ₽)\n\n"
            f"Итого: ~{int(total_rub)} ₽\n"
            f"⏳ До следующего фарма: {time_left}",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⛏️ Добыть", callback_data=menu_cb.new(action="mine")),
                InlineKeyboardButton("⬅️ Назад", callback_data=menu_cb.new(action="back"))
            )
        )

    @dp.callback_query_handler(menu_cb.filter(action="mine"))
    async def mine(callback: types.CallbackQuery):
        user = get_user(callback.from_user.id)
        passive = calculate_passive(user)
        if sum(passive.values()) == 0:
            await callback.answer("⏳ Подождите до следующего цикла!", show_alert=True)
        else:
            update_user(callback.from_user.id,
                       ton=user["ton"] + passive["TON"],
                       not_tokens=user["not_tokens"] + passive["NOT"],
                       usdt=user["usdt"] + passive["USDT"],
                       last_farm=datetime.utcnow().isoformat())
            await callback.answer(f"✅ Добыто!\nTON: {passive['TON']:.4f}\nNOT: {passive['NOT']}\nUSDT: {passive['USDT']:.4f}")
        await balance(callback)

    @dp.callback_query_handler(menu_cb.filter(action="rates"))
    async def rates(callback: types.CallbackQuery):
        await callback.message.edit_text(
            f"📈 Курсы:\nTON: {RATES['TON']} ₽\nNOT: {RATES['NOT']} ₽\nUSDT: {RATES['USDT']} ₽",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️ Назад", callback_data=menu_cb.new(action="back"))
            )
        )

    @dp.message_handler(commands=['withdraw'])
    async def withdraw_cmd(message: types.Message):
        args = message.text.split()
        if len(args) != 3:
            await message.answer("Использование: /withdraw <сумма> <карта>\nПример: /withdraw 500 1234567890123456")
            return
        try:
            amount = int(args[1])
            card = args[2]
            if amount < 500:
                await message.answer("Минимум 500 ₽")
                return
            user = get_user(message.from_user.id)
            passive = calculate_passive(user)
            total_ton = user["ton"] + passive["TON"]
            rub_balance = int(total_ton * RATES["TON"])
            if rub_balance < amount:
                await message.answer(f"Недостаточно средств. Баланс: {rub_balance} ₽")
                return
            new_ton = total_ton - (amount / RATES["TON"])
            update_user(message.from_user.id, ton=new_ton)
            result = await tonconsole_withdraw(amount, card, message.from_user.id)
            if result.get("success"):
                await message.answer(f"✅ Запрос на вывод {amount} ₽ отправлен!")
            else:
                await message.answer(f"❌ Ошибка: {result.get('error')}")
        except ValueError:
            await message.answer("Сумма должна быть числом")

    @dp.callback_query_handler(menu_cb.filter(action="withdraw"))
    async def withdraw_start(callback: types.CallbackQuery):
        await callback.message.edit_text(
            "📤 Используй команду:\n/withdraw <сумма> <карта>\nПример: /withdraw 500 1234567890123456",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️ Назад", callback_data=menu_cb.new(action="back"))
            )
        )

    @dp.callback_query_handler(menu_cb.filter(action="zones"))
    async def zones(callback: types.CallbackQuery):
        user = get_user(callback.from_user.id)
        kb = InlineKeyboardMarkup()
        for key, zone in ZONES.items():
            mark = " ✅" if key == user["current_zone"] else ""
            kb.add(InlineKeyboardButton(f"{zone['name']} ({zone['currency']}){mark}", 
                                       callback_data=zone_cb.new(name=key)))
        kb.add(InlineKeyboardButton("⬅️ Назад", callback_data=menu_cb.new(action="back")))
        await callback.message.edit_text("🌍 Выберите зону:", reply_markup=kb)

    # 🔥 ИСПРАВЛЕНО: правильное объявление параметра
    @dp.callback_query_handler(zone_cb.filter())
    async def select_zone(callback: types.CallbackQuery, callback_ dict):  # ← ЭТО ПРАВИЛЬНО!
        zone_name = callback_data['name']
        user = get_user(callback.from_user.id)
        update_user(callback.from_user.id, current_zone=zone_name)
        await callback.answer(f"✅ Зона: {ZONES[zone_name]['name']}")
        await zones(callback)

    @dp.callback_query_handler(menu_cb.filter(action="wheel"))
    async def wheel(callback: types.CallbackQuery):
        user = get_user(callback.from_user.id)
        if can_spin_wheel(user["last_wheel"]):
            message = spin_wheel(callback.from_user.id)
            await callback.answer(message, show_alert=True)
        else:
            await callback.answer("⏳ Рулетка доступна раз в 48 часов!", show_alert=True)
        await balance(callback)

    @dp.callback_query_handler(menu_cb.filter(action="back"))
    async def back(callback: types.CallbackQuery):
        await callback.message.edit_text(
            "🎮 Главное меню",
            reply_markup=InlineKeyboardMarkup(row_width=2).add(
                InlineKeyboardButton("📊 Баланс", callback_data=menu_cb.new(action="balance")),
                InlineKeyboardButton("🎡 Рулетка", callback_data=menu_cb.new(action="wheel")),
                InlineKeyboardButton("🌍 Зоны", callback_data=menu_cb.new(action="zones")),
                InlineKeyboardButton("📈 Курс", callback_data=menu_cb.new(action="rates")),
                InlineKeyboardButton("📤 Вывести", callback_data=menu_cb.new(action="withdraw"))
            )
        )

    print("✅ Pixel Farm запущен! Доход до 500 ₽/день без вложений.")
    executor.start_polling(dp, skip_updates=True)
