import asyncio
import logging
import sqlite3
import pytz
import os
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI  # Перенесите этот импорт сюда!
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from dotenv import load_dotenv

ACTIVE_COURIERS_FILE = 'active_couriers.txt'
TIME_ZONE = pytz.timezone('Europe/Moscow')

load_dotenv() 

API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
SECRET_PASSWORD = os.getenv('SECRET_PASSWORD')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

class Form(StatesGroup):
    previous_state = State()
    role = State()
    courier_password = State()
    restaurant_password = State()
    admin_password = State()
    tariff = State()
    restaurant_name = State()
    time = State()
    packages = State()
    distance = State()
    add_to_existing = State()
    courier_name = State() 

conn = sqlite3.connect('restaurants.db', check_same_thread=False)

conn.execute('''CREATE TABLE IF NOT EXISTS restaurants
             (user_id INTEGER PRIMARY KEY,
              name TEXT,
              tariff TEXT,
              registration_date TEXT)''')

conn.execute('''CREATE TABLE IF NOT EXISTS couriers
             (user_id INTEGER PRIMARY KEY,
              name TEXT,
              registration_date TEXT)''')

conn.execute('''CREATE TABLE IF NOT EXISTS blocked_couriers
             (user_id INTEGER PRIMARY KEY)''')
conn.commit()

pending_updates = {}

def get_blocked_couriers():
    try:
        return [int(line) for line in file_operation('blocked_couriers.txt', 'r').splitlines() if line]
    except:
        return []

def add_blocked_courier(courier_id: int):
    blocked = get_blocked_couriers()
    if courier_id not in blocked:
        file_operation('blocked_couriers.txt', 'a', courier_id)

def file_operation(filename: str, mode: str, data=None):
    try:
        Path(filename).touch(exist_ok=True)
        with open(filename, mode, encoding='utf-8') as f:
            if mode == 'r':
                return f.read().strip()
            if mode == 'a':
                f.write(f"{data}\n")
            if mode == 'w':
                f.seek(0)
                f.truncate()
                f.write(str(data))
        return ''
    except Exception as e:
        logger.error(f"File error ({filename}): {e}")
        return ''

def get_couriers():
    try:
        return [int(line) for line in file_operation('courier_id.txt', 'r').splitlines() if line]
    except:
        return []

def get_active_couriers():
    try:
        return [int(line) for line in file_operation(ACTIVE_COURIERS_FILE, 'r').splitlines() if line]
    except:
        return []

def add_active_courier(courier_id: int):
    active = get_active_couriers()
    if courier_id not in active:
        active.append(courier_id)
        file_operation(ACTIVE_COURIERS_FILE, 'w', '\n'.join(map(str, active)))
        file_operation('courier_index.txt', 'w', 0)

def remove_active_courier(courier_id: int):
    active = get_active_couriers()
    if courier_id in active:
        active.remove(courier_id)
        file_operation(ACTIVE_COURIERS_FILE, 'w', '\n'.join(map(str, active)))
        file_operation('courier_index.txt', 'w', 0)

def next_courier():
    couriers = get_active_couriers()
    if not couriers:
        return None
    try:
        idx = int(file_operation('courier_index.txt', 'r') or 0)
        idx = idx % len(couriers)
    except ValueError:
        idx = 0
    current = couriers[idx]
    new_idx = (idx + 1) % len(couriers)
    file_operation('courier_index.txt', 'w', new_idx)
    return current

def order_id_generator():
    try:
        counter = int(file_operation('order_counter.txt', 'r') or 0)
    except ValueError:
        counter = 0
    counter += 1
    file_operation('order_counter.txt', 'w', counter)
    return f"Заказ #{counter}"

async def save_order(order_data: dict):
    try:
        current_time = datetime.now(TIME_ZONE)
        with open('orders.txt', 'a', encoding='utf-8') as f:
            f.write(
                f"{order_data['id']}|{order_data['user_id']}|{order_data['restaurant']}|"
                f"{order_data['time']}|{order_data['packages']}|{', '.join(order_data['distances'])}|"
                f"{order_data['price']}|pending|"
                f"{current_time.strftime('%Y-%m-%d')}|"
                f"{current_time.strftime('%H:%M:%S')}|None\n"
            )
    except Exception as e:
        logger.error(f"Order save error: {e}")

async def update_order_status(order_id: str, new_status: str, courier_id: int = None):
    try:
        with open('orders.txt', 'r+', encoding='utf-8') as f:
            lines = []
            for line in f:
                if line.startswith(f"{order_id}|"):
                    parts = line.strip().split('|')
                    while len(parts) < 11:
                        parts.append('None')
                    parts[7] = new_status
                    if courier_id:
                        parts[10] = str(courier_id)
                    line = '|'.join(parts) + '\n'
                lines.append(line)
            f.seek(0)
            f.writelines(lines)
            f.truncate()
    except Exception as e:
        logger.error(f"Order update error: {e}")

async def send_to_courier(order: dict, keyboard: InlineKeyboardMarkup):
    courier_id = next_courier()
    if courier_id:
        try:
            # Получаем актуальное количество посылок из файла
            with open('orders.txt', 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith(f"{order['id']}|"):
                        parts = line.strip().split('|')
                        actual_packages = parts[4] if len(parts) > 4 else order['packages']
                        break
                else:
                    actual_packages = order['packages']

            await bot.send_message(
                courier_id,
                f"🚚 Новый заказ {order['id']}!\n"
                f"🏢 {order['restaurant']}\n"
                f"⏰ {order['time']}\n"
                f"📦 {actual_packages} посылок\n"  # Используем актуальное количество
                f"📍 {', '.join(order['distances'])}",
                reply_markup=keyboard
            )
            return True
        except Exception as e:
            logger.error(f"Error sending to courier {courier_id}: {e}")
            return await redirect_order(order['id'])
    return False

async def redirect_order(order_id: str):
    order_data = None
    with open('orders.txt', 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith(f"{order_id}|"):
                parts = line.strip().split('|')
                order_data = {
                    'id': parts[0],
                    'restaurant': parts[2],
                    'time': parts[3],
                    'packages': parts[4],
                    'distances': parts[5].split(', '),
                    'price': parts[6]
                }
                break
    if not order_data:
        logger.error(f"Order {order_id} not found for redirect")
        return
    courier_id = next_courier()
    if courier_id:
        try:
            await bot.send_message(
                courier_id,
                f"🔄 Перенаправленный заказ {order_data['id']}!\n"
                f"🏢 {order_data['restaurant']}\n"
                f"⏰ {order_data['time']}\n"
                f"📦 {order_data['packages']} посылок\n"
                f"📍 {', '.join(order_data['distances'])}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{order_data['id']}"),
                    InlineKeyboardButton(text="❌ Отказаться", callback_data=f"decline_{order_data['id']}")
                ]])
            )
        except Exception as e:
            logger.error(f"Error sending to courier {courier_id}: {e}")
            return await redirect_order(order_id)
    else:
        await bot.send_message(file_operation('admin_id.txt', 'r'), f"❗ Заказ {order_id} отклонен всеми!")

async def add_back_button(keyboard):
    if isinstance(keyboard, ReplyKeyboardMarkup):
        keyboard.keyboard.append([KeyboardButton(text="◀️ Назад")])
    return keyboard

@dp.message(lambda message: message.text == "◀️ Назад")
async def handle_back(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    data = await state.get_data()
    state_flow = {
        'Form:restaurant_name': Form.tariff,
        'Form:tariff': Form.restaurant_password,
        'Form:time': None,
        'Form:packages': Form.time,
        'Form:distance': Form.packages
    }
    if current_state in state_flow:
        if state_flow[current_state] is None:
            await state.clear()
            await show_restaurant_menu(message.from_user.id)
        else:
            await state.set_state(state_flow[current_state])
            await handle_state_return(message, state_flow[current_state])

async def handle_state_return(message: types.Message, return_state: State):
    if return_state == Form.tariff:
        await message.answer(
            "📋 Выберите тариф для вашего заведения:",
            reply_markup=await add_back_button(ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="5/5"), KeyboardButton(text="5/6")],
                    [KeyboardButton(text="6/6"), KeyboardButton(text="6/7")],
                    [KeyboardButton(text="7/7"), KeyboardButton(text="7/8")]
                ],
                resize_keyboard=True
            ))
        )
    elif return_state == Form.restaurant_password:
        await message.answer(
            "🔑 Введите пароль для заведения:",
            reply_markup=types.ReplyKeyboardRemove()
        )
    elif return_state == Form.time:
        await message.answer(
            "⏰ Выберите время:",
            reply_markup=await add_back_button(ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text=t) for t in ["15 мин", "20 мин", "30 мин", "45 мин", "1 час", "1.5 часа", "2 часа"]]
                ],
                resize_keyboard=True
            ))
        )
    elif return_state == Form.packages:
        await message.answer(
            "📦 Количество посылок (1-5):",
            reply_markup=await add_back_button(ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=str(i)) for i in range(1,6)]],
                resize_keyboard=True
            ))
        )

async def show_restaurant_menu(user_id: int):
    last_order = get_last_order_for_restaurant(user_id)
    buttons = []
    if last_order:
        buttons.append([KeyboardButton(text="Добавить к этому заказу")])
    buttons.extend([
        [KeyboardButton(text="Создать новый заказ")],
        [KeyboardButton(text="Получить отчёт")]
    ])
    await bot.send_message(
        user_id,
        "Выберите действие:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=buttons,
            resize_keyboard=True
        )
    )

def get_restaurant_tariff(user_id: int):
    try:
        row = conn.execute('SELECT tariff FROM restaurants WHERE user_id=?', (user_id,)).fetchone()
        return row[0] if row else '7/8'
    except:
        return '7/8'

@dp.message(Form.restaurant_password)
async def restaurant_auth(message: types.Message, state: FSMContext):
    if message.text == SECRET_PASSWORD:
        await state.set_state(Form.tariff)
        await message.answer(
            "📋 Выберите тариф для вашего заведения:",
            reply_markup=await add_back_button(ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="5/5"), KeyboardButton(text="5/6")],
                    [KeyboardButton(text="6/6"), KeyboardButton(text="6/7")],
                    [KeyboardButton(text="7/7"), KeyboardButton(text="7/8")]
                ],
                resize_keyboard=True
            ))
        )
    else:
        await message.answer("❌ Неверный пароль!")
        await state.clear()

@dp.message(Form.tariff)
async def set_tariff(message: types.Message, state: FSMContext):
    valid_tariffs = ["5/5", "5/6", "6/6", "6/7", "7/7", "7/8"]
    if message.text not in valid_tariffs:
        await message.answer("❗ Выберите предложенный тариф!")
        return
    await state.update_data(tariff=message.text)
    await state.set_state(Form.restaurant_name)
    await message.answer("🏢 Введите название вашего заведения:", reply_markup=types.ReplyKeyboardRemove())

@dp.message(Form.restaurant_name)
async def set_restaurant(message: types.Message, state: FSMContext):
    data = await state.get_data()
    tariff = data.get('tariff', '7/8')
    current_date = datetime.now(TIME_ZONE).strftime("%Y-%m-%d")
    conn.execute(
        'INSERT OR REPLACE INTO restaurants VALUES (?, ?, ?, ?)',
        (message.from_user.id, message.text, tariff, current_date)
    )
    conn.commit()
    await state.clear()
    await message.answer(f"✅ Регистрация завершена! Ваше заведение: {message.text}")
    await show_restaurant_menu(message.from_user.id)

@dp.message(lambda message: message.text == "Создать новый заказ")
async def restaurant_create_order(message: types.Message, state: FSMContext):
    restaurant = conn.execute('SELECT name FROM restaurants WHERE user_id=?', (message.from_user.id,)).fetchone()
    if not restaurant:
        await message.answer("❗ Вы не зарегистрированы как заведение!")
        return
    await create_order_flow(message, state)

async def create_order_flow(message: types.Message, state: FSMContext, main_order_id: str = None):
    if main_order_id:
        await state.update_data(main_order_id=main_order_id)
    await state.set_state(Form.time)
    await message.answer(
        "⏰ Выберите время:",
        reply_markup=await add_back_button(ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text=t) for t in ["15 мин", "20 мин", "30 мин", "45 мин", "1 час", "1.5 часа", "2 часа"]]
            ],
            resize_keyboard=True
        ))
    )

@dp.message(Form.time)
async def set_time(message: types.Message, state: FSMContext):
    if message.text in ["15 мин", "20 мин", "30 мин", "45 мин", "1 час", "1.5 часа", "2 часа"]:
        await state.update_data(time=message.text)
        await state.set_state(Form.packages)
        await message.answer(
            "📦 Количество посылок (1-5):",
            reply_markup=await add_back_button(ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=str(i)) for i in range(1,6)]],
                resize_keyboard=True
            ))
        )

@dp.message(Form.packages)
async def set_packages(message: types.Message, state: FSMContext):
    if message.text.isdigit() and 1 <= int(message.text) <= 5:
        await state.update_data(packages=int(message.text), current_package=1, distances=[])
        await ask_distance(message)
        await state.set_state(Form.distance)

async def ask_distance(message: types.Message):
    await message.answer(
        "📍 Укажите расстояние:",
        reply_markup=await add_back_button(ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Ближнее"), KeyboardButton(text="Дальнее")]],
            resize_keyboard=True
        ))
    )

@dp.message(Form.distance)
async def set_distance(message: types.Message, state: FSMContext):
    if message.text not in ["Ближнее", "Дальнее"]:
        await message.answer("❗ Выберите один из предложенных вариантов!")
        return

    data = await state.get_data()
    distances = data.get('distances', [])
    distances.append(message.text)
    current_package = data.get('current_package', 1)
    packages = data.get('packages', 0)

    if current_package < packages:
        await state.update_data(distances=distances, current_package=current_package + 1)
        await ask_distance(message)
    else:
        await state.update_data(distances=distances)
        await create_order(message, state)
        await state.clear()

async def create_order(message: types.Message, state: FSMContext):
    data = await state.get_data()
    main_order_id = data.get('main_order_id')
    
    restaurant = conn.execute('SELECT name FROM restaurants WHERE user_id=?', 
                            (message.from_user.id,)).fetchone()[0]
    
    if main_order_id:
        await update_existing_order(main_order_id, message.from_user.id, data)
        await message.answer("Дополнительные данные добавлены к заказу. Ожидайте подтверждения курьера.")
        await state.clear()
        await show_restaurant_menu(message.from_user.id)
        return

    tariff = get_restaurant_tariff(message.from_user.id)
    near_price, far_price = map(int, tariff.split('/'))
    PRICES = {"Ближнее": near_price, "Дальнее": far_price}

    order_data = {
        'id': order_id_generator(),
        'user_id': message.from_user.id,
        'restaurant': restaurant,
        'time': data['time'],
        'packages': data['packages'],
        'distances': data['distances'],
        'price': sum(PRICES[d] for d in data['distances'])
    }

    active_couriers = get_active_couriers()
    if not active_couriers:
        await message.answer("❌ Нет доступных курьеров! Заказ не будет создан.")
        await show_restaurant_menu(message.from_user.id)
        await state.clear()
        return

    await save_order(order_data)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{order_data['id']}"),
        InlineKeyboardButton(text="❌ Отказаться", callback_data=f"decline_{order_data['id']}")
    ]])

    if await send_to_courier(order_data, keyboard):
        await message.answer(
            f"✅ Заказ создан!\n"
            f"ID: {order_data['id']}\n"
            f"⏰ Время: {order_data['time']}\n"
            f"📦 Посылок: {order_data['packages']}\n"
            f"📍 Расстояния: {', '.join(order_data['distances'])}\n"
            f"💰 Стоимость: {order_data['price']} руб."
        )
    else:
        await message.answer("❌ Не удалось отправить заказ курьерам!") 
        await remove_order(order_data['id'])

    await show_restaurant_menu(message.from_user.id)

async def remove_order(order_id: str):
    """Удаляет заказ из системы"""
    try:
        with open('orders.txt', 'r', encoding='utf-8') as f:
            lines = [line for line in f if not line.startswith(f"{order_id}|")]
        
        with open('orders.txt', 'w', encoding='utf-8') as f:
            f.writelines(lines)
    except Exception as e:
        logger.error(f"Error removing order: {e}")

async def send_to_courier(order: dict, keyboard: InlineKeyboardMarkup):
    courier_id = next_courier()
    if courier_id:
        try:
            await bot.send_message(
                courier_id,
                f"🚚 Новый заказ {order['id']}!\n"
                f"🏢 {order['restaurant']}\n"
                f"⏰ {order['time']}\n"
                f"📦 {order['packages']} посылок\n"
                f"📍 {', '.join(order['distances'])}",
                reply_markup=keyboard
            )
            return True
        except Exception as e:
            logger.error(f"Error sending to courier {courier_id}: {e}")
            return await redirect_order(order['id'])
    return False
    
@dp.message(lambda message: message.text == "Добавить к этому заказу")
async def add_to_existing_order(message: types.Message, state: FSMContext):
    last_order = get_last_order_for_restaurant(message.from_user.id)
    if last_order:
        await create_order_flow(message, state, last_order['id'])
    else:
        await message.answer("❌ Нет активных заказов для добавления")
        await show_restaurant_menu(message.from_user.id)


@dp.message(lambda message: message.text == "ПОЛУЧИТЬ ОТЧЁТ")
async def send_report(message: types.Message):
    admin_id = file_operation('admin_id.txt', 'r')
    if str(message.from_user.id) == str(admin_id):
        try:
            report = await generate_report()
            await message.answer(report)
        except Exception as e:
            logger.error(f"Admin report error: {e}")
            await message.answer("❌ Ошибка формирования отчёта")
    else:
        await send_restaurant_report(message)


@dp.message(lambda message: message.text == "Получить отчёт")
async def send_restaurant_report(message: types.Message):
    try:
        user_id = message.from_user.id
        restaurant = conn.execute('SELECT name FROM restaurants WHERE user_id=?', (user_id,)).fetchone()
        if not restaurant:
            await message.answer("❗ Вы не зарегистрированы как заведение!")
            return
        
        report = await generate_restaurant_report(user_id)
        await message.answer(report)
        await show_restaurant_menu(user_id)
    except Exception as e:
        logger.error(f"Restaurant report error: {e}")
        await message.answer("❌ Ошибка формирования отчёта")


@dp.message(lambda message: message.text == "Управление курьерами")
async def manage_couriers(message: types.Message):
    if str(message.from_user.id) != file_operation('admin_id.txt', 'r'):
        return

    # Получаем только активных курьеров (не заблокированных)
    couriers = conn.execute('''
        SELECT c.user_id, c.name 
        FROM couriers c
        WHERE c.user_id NOT IN (
            SELECT user_id FROM blocked_couriers
        )
        AND c.user_id IN (
            SELECT user_id FROM couriers
            EXCEPT
            SELECT user_id FROM blocked_couriers
        )
    ''').fetchall()

    keyboard = []
    for courier_id, name in couriers:
        keyboard.append([KeyboardButton(text=f"❌ Удалить курьера {name} (ID: {courier_id})")])

    if not couriers:
        keyboard.append([KeyboardButton(text="Нет активных курьеров")])

    keyboard.append([KeyboardButton(text="◀️ Назад в меню")])
    
    await message.answer(
        "👥 Управление курьерами:",
        reply_markup=ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    )
    
@dp.message(lambda message: message.text.startswith("❌ Удалить курьера"))
async def delete_courier(message: types.Message):
    if str(message.from_user.id) != file_operation('admin_id.txt', 'r'):
        return
    
    try:
        courier_id = int(message.text.split("(ID:")[1].strip(")").strip())
        
        with open('orders.txt', 'r+', encoding='utf-8') as f:
            lines = f.readlines()
            f.seek(0)
            for line in lines:
                parts = line.strip().split('|')
                if len(parts) > 10 and parts[10] == str(courier_id) and parts[7] == 'accepted':
                    restaurant_id = parts[1]
                    order_id = parts[0]
                    await bot.send_message(
                        restaurant_id,
                        f"❌ Заказ {order_id} отменён, так как курьер был удалён"
                    )
                    line = line.replace('|accepted|', '|declined|')
                f.write(line)
            f.truncate()
        
        remove_active_courier(courier_id)
        add_blocked_courier(courier_id)
        
        name = conn.execute('SELECT name FROM couriers WHERE user_id=?', (courier_id,)).fetchone()[0]
        
        await message.answer(
            f"✅ Курьер {name} успешно удален",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="ПОЛУЧИТЬ ОТЧЁТ")],
                    [KeyboardButton(text="Управление курьерами")]
                ],
                resize_keyboard=True
            )
        )
        
        try:
            await bot.send_message(
                courier_id,
                "❌ Ваш аккаунт был деактивирован администратором"
            )
        except:
            pass
    except Exception as e:
        logger.error(f"Error deleting courier: {e}")
        await message.answer("❌ Ошибка при удалении курьера")
        
@dp.message(lambda message: message.text == "◀️ Назад")
async def admin_back(message: types.Message):
    admin_id = file_operation('admin_id.txt', 'r')
    if str(message.from_user.id) == str(admin_id):
        try:
            await message.delete()
        except:
            pass
            
        await message.answer(
            "Главное меню администратора:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="ПОЛУЧИТЬ ОТЧЁТ")],
                    [KeyboardButton(text="Управление курьерами")]
                ],
                resize_keyboard=True
            )
        )
    else:
        await message.answer("Доступ запрещен")
        

@dp.message(lambda message: message.text == "◀️ Назад в меню")
async def back_from_manage_couriers(message: types.Message):
    admin_id = file_operation('admin_id.txt', 'r')
    if str(message.from_user.id) == str(admin_id):
        try:
            await message.delete()
        except:
            pass
            
        await message.answer(
            "Главное меню администратора:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="ПОЛУЧИТЬ ОТЧЁТ")],
                    [KeyboardButton(text="Управление курьерами")]
                ],
                resize_keyboard=True
            )
        )
    else:
        await message.answer("Доступ запрещен")
        
@dp.message(lambda message: message.text == "ПОЛУЧИТЬ ОТЧЁТ")
async def send_admin_report(message: types.Message):
    admin_id = file_operation('admin_id.txt', 'r')
    if str(message.from_user.id) == str(admin_id):
        try:
            # Отчёт за всю историю
            full_report = await generate_full_history_report()
            await message.answer(full_report)
            
            # Отчёт за текущую неделю
            weekly_report = await generate_report()
            await message.answer(f"📊 Отчёт за текущую неделю:\n\n{weekly_report}")
            
        except Exception as e:
            logger.error(f"Admin report error: {e}")
            await message.answer("❌ Ошибка формирования отчёта")
    else:
        await message.answer("Доступ запрещён")

@dp.message(Form.courier_password)
async def courier_auth(message: types.Message, state: FSMContext):
    if message.from_user.id in get_blocked_couriers():
        await message.answer("❌ Ваш аккаунт заблокирован и не может быть зарегистрирован.")
        await state.clear()
        return

    if message.text == SECRET_PASSWORD:
        couriers = get_couriers()
        
        if message.from_user.id not in couriers:
            await state.set_state(Form.courier_name)
            await message.answer("🔤 Придумайте себе имя (например: Курьер Женя):")
        else:
            reply_markup = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Начать смену")]],
                resize_keyboard=True
            )
            await message.answer("✅ Вы уже зарегистрированы!", reply_markup=reply_markup)
            await state.clear()
    else:
        await message.answer("❌ Неверный пароль!")
        await state.clear()

@dp.message(Form.courier_name)
async def set_courier_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2 or len(name) > 30:
        await message.answer("❗ Имя должно быть от 2 до 30 символов. Попробуйте еще раз:")
        return
    
    current_date = datetime.now(TIME_ZONE).strftime("%Y-%m-%d")
    conn.execute(
        'INSERT OR REPLACE INTO couriers VALUES (?, ?, ?)',
        (message.from_user.id, name, current_date)
    )
    conn.commit()
    
    file_operation('courier_id.txt', 'a', message.from_user.id)
    await message.answer(
        f"✅ Регистрация завершена! Ваше имя: {name}",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Начать смену")]],
            resize_keyboard=True
        )
    )
    await state.clear()


async def generate_report():
    try:
        end_date = datetime.now(TIME_ZONE)
        start_date = end_date - timedelta(days=7)
        
        report_data = {
            'restaurants': {},
            'couriers': {},
            'total_packages': 0,  # Изменили с total_orders на total_packages
            'total_price': 0,
            'delivered_packages': 0  # Изменили с delivered_orders на delivered_packages
        }

        with open('orders.txt', 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('|')
                if len(parts) < 11:
                    continue
                
                try:
                    order_date = datetime.strptime(parts[8], "%Y-%m-%d").replace(tzinfo=TIME_ZONE)
                except:
                    continue
                
                if start_date <= order_date <= end_date:
                    restaurant = parts[2]
                    courier_id = parts[10] if len(parts) > 10 else None
                    status = parts[7]
                    packages = int(parts[4]) if parts[4].isdigit() else 0
                    price = int(parts[6]) if parts[6].isdigit() else 0
                    
                    if restaurant not in report_data['restaurants']:
                        report_data['restaurants'][restaurant] = {
                            'packages': 0,  # Изменили с orders на packages
                            'price': 0,
                            'delivered': 0
                        }
                    
                    report_data['restaurants'][restaurant]['packages'] += packages
                    report_data['restaurants'][restaurant]['price'] += price
                    if status == 'delivered':
                        report_data['restaurants'][restaurant]['delivered'] += packages
                    
                    if courier_id and courier_id != 'None':
                        courier_name = conn.execute(
                            'SELECT name FROM couriers WHERE user_id=?', 
                            (courier_id,)
                        ).fetchone()
                        courier_name = courier_name[0] if courier_name else f"Курьер {courier_id}"
                        
                        if courier_name not in report_data['couriers']:
                            report_data['couriers'][courier_name] = {
                                'packages': 0,  # Изменили с orders на packages
                                'price': 0
                            }
                        
                        report_data['couriers'][courier_name]['packages'] += packages
                        report_data['couriers'][courier_name]['price'] += price
                    
                    report_data['total_packages'] += packages
                    report_data['total_price'] += price
                    if status == 'delivered':
                        report_data['delivered_packages'] += packages

        report_text = (
            f"📊 ОТЧЁТ АДМИНИСТРАТОРА\n"
            f"Период: {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}\n"
            f"Всего посылок: {report_data['total_packages']}\n"  # Изменили вывод
            f"Доставлено посылок: {report_data['delivered_packages']}\n"  # Изменили вывод
            f"Общая сумма: {report_data['total_price']} руб.\n\n"
        )
        
        report_text += "🏢 ЗАВЕДЕНИЯ:\n"
        for restaurant, data in report_data['restaurants'].items():
            report_text += (
                f"▫️ {restaurant}:\n"
                f"   - Посылок: {data['packages']}\n"  # Изменили вывод
                f"   - Доставлено: {data['delivered']}\n"
                f"   - Сумма к оплате: {data['price']} руб.\n"
            )
        
        report_text += "\n🚚 КУРЬЕРЫ:\n"
        for courier, data in report_data['couriers'].items():
            report_text += (
                f"▫️ {courier}:\n"
                f"   - Посылок доставлено: {data['packages']}\n"  # Изменили вывод
                f"   - Заработано: {data['price']} руб.\n"
            )
        
        return report_text if report_data['total_packages'] > 0 else "❗ За неделю посылок не было"
    
    except Exception as e:
        logger.error(f"Ошибка формирования отчёта: {e}")
        return "❌ Не удалось сформировать отчёт"
        
async def generate_full_history_report():
    try:
        total_packages = 0  # Изменили с total_orders
        total_price = 0
        restaurants = {}
        couriers = {}
        
        with open('orders.txt', 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('|')
                if len(parts) < 11:
                    continue
                
                restaurant = parts[2]
                courier_id = parts[10] if len(parts) > 10 else None
                packages = int(parts[4]) if parts[4].isdigit() else 0  # Добавили подсчет посылок
                price = int(parts[6]) if parts[6].isdigit() else 0
                
                if restaurant not in restaurants:
                    restaurants[restaurant] = {
                        'packages': 0,
                        'price': 0
                    }
                restaurants[restaurant]['packages'] += packages
                restaurants[restaurant]['price'] += price
                
                if courier_id and courier_id != 'None':
                    courier_name = conn.execute(
                        'SELECT name FROM couriers WHERE user_id=?', 
                        (courier_id,)
                    ).fetchone()
                    courier_name = courier_name[0] if courier_name else f"Курьер {courier_id}"
                    
                    if courier_name not in couriers:
                        couriers[courier_name] = {
                            'packages': 0,
                            'price': 0
                        }
                    couriers[courier_name]['packages'] += packages
                    couriers[courier_name]['price'] += price
                
                total_packages += packages
                total_price += price
        
        report_text = (
            f"📊 ПОЛНЫЙ ОТЧЁТ (ВСЯ ИСТОРИЯ)\n"
            f"Всего посылок: {total_packages}\n"  # Изменили вывод
            f"Общая сумма: {total_price} руб.\n\n"
        )
        
        report_text += "🏢 ЗАВЕДЕНИЯ:\n"
        for restaurant, data in restaurants.items():
            report_text += (
                f"▫️ {restaurant}:\n"
                f"   - Посылок: {data['packages']}\n"  # Изменили вывод
                f"   - Сумма к оплате: {data['price']} руб.\n"
            )
        
        report_text += "\n🚚 КУРЬЕРЫ:\n"
        for courier, data in couriers.items():
            report_text += (
                f"▫️ {courier}:\n"
                f"   - Посылок доставлено: {data['packages']}\n"  # Изменили вывод
                f"   - Заработано: {data['price']} руб.\n"
            )
        
        return report_text if total_packages > 0 else "❗ Посылок ещё не было"
    
    except Exception as e:
        logger.error(f"Ошибка формирования полного отчёта: {e}")
        return "❌ Не удалось сформировать полный отчёт"
        
        

async def generate_restaurant_report(user_id: int):
    try:
        report_data = {
            'packages': 0,
            'price': 0,
            'accepted_packages': 0,  # Добавили
            'declined_packages': 0,  # Добавили
            'pending_packages': 0,   # Добавили
            'delivered_packages': 0
        }

        today = datetime.now(TIME_ZONE).strftime("%Y-%m-%d")
        restaurant_name = conn.execute('SELECT name FROM restaurants WHERE user_id=?', (user_id,)).fetchone()[0]
        
        with open('orders.txt', 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('|')
                if len(parts) < 11:
                    continue
                if int(parts[1]) == user_id and parts[8] == today:
                    packages = int(parts[4]) if parts[4].isdigit() else 0
                    price = int(parts[6]) if parts[6].isdigit() else 0
                    status = parts[7]
                    
                    report_data['packages'] += packages
                    report_data['price'] += price
                    
                    # Добавляем подсчет по статусам с учетом количества посылок
                    if status == 'accepted':
                        report_data['accepted_packages'] += packages
                    elif status == 'declined':
                        report_data['declined_packages'] += packages
                    elif status == 'pending':
                        report_data['pending_packages'] += packages
                    elif status == 'delivered':
                        report_data['delivered_packages'] += packages
        
        report_text = (
            f"📋 Отчёт заведения {restaurant_name} за {datetime.now(TIME_ZONE).strftime('%d.%m.%Y')}:\n\n"
            f"• Всего посылок: {report_data['packages']}\n"
            f"• Общая сумма: {report_data['price']} руб.\n"
            f"• Принято курьерами: {report_data['accepted_packages']} посылок\n"
            f"• Отклонено курьерами: {report_data['declined_packages']} посылок\n"
            f"• Ожидает принятия: {report_data['pending_packages']} посылок\n"
            f"• Успешно доставлено: {report_data['delivered_packages']} посылок\n"
        )
        
        return report_text if report_data['packages'] > 0 else "❗ За сегодня посылок не было"
    except Exception as e:
        logger.error(f"Restaurant report generation error: {e}")
        return "Не удалось сформировать отчёт"

def get_last_order_for_restaurant(user_id: int):
    try:
        if not os.path.exists('orders.txt'):
            return None
        with open('orders.txt', 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for line in reversed(lines):
                parts = line.strip().split('|')
                if len(parts) >= 11 and int(parts[1]) == user_id and parts[7] == 'accepted':
                    return {'id': parts[0], 'courier_id': parts[10]}
        return None
    except Exception as e:
        logger.error(f"Error getting last order: {e}")
        return None


def get_courier_for_order(order_id: str):
    try:
        with open('orders.txt', 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith(f"{order_id}|"):
                    parts = line.strip().split('|')
                    return int(parts[10]) if parts[10] != 'None' else None
    except Exception:
        return None


async def check_order_status(order_id: str) -> str:
    with open('orders.txt', 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith(f"{order_id}|"):
                parts = line.strip().split('|')
                return parts[7]
    return 'not_found'


async def remind_courier(order_id: str):
    await asyncio.sleep(9999)
    if await check_order_status(order_id) == 'pending':
        await redirect_order(order_id)
        if admin_id := file_operation('admin_id.txt', 'r'):
            await bot.send_message(
                admin_id,
                f"❗ Заказ {order_id} не был принят вовремя! Перенаправлен следующему курьеру."
            )


async def update_existing_order(order_id: str, user_id: int, data: dict):
    try:
        tariff = get_restaurant_tariff(user_id)
        near_price, far_price = map(int, tariff.split('/'))
        PRICES = {"Ближнее": near_price, "Дальнее": far_price}

        pending_updates[order_id] = {
            'added_packages': data['packages'],
            'added_distances': data['distances'],
            'added_price': sum(PRICES[d] for d in data['distances'])
        }
        
        courier_id = get_courier_for_order(order_id)
        if courier_id:
            try:
                await bot.send_message(
                    courier_id,
                    f"➕ К заказу {order_id} добавлено:\n"
                    f"Посылки: {data['packages']}\n"
                    f"Расстояния: {', '.join(data['distances'])}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{order_id}"),
                        InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_{order_id}")
                    ]])
                )
            except Exception as e:
                logger.error(f"Error sending to courier: {e}")
                await bot.send_message(
                    user_id,
                    "❌ Не удалось уведомить курьера. Попробуйте создать новый заказ."
                )
    except Exception as e:
        logger.error(f"Error in update_existing_order: {e}")
        
@dp.callback_query(lambda c: c.data.startswith(('confirm_', 'cancel_')))
async def handle_additional_packages(callback: types.CallbackQuery):
    action, order_id = callback.data.split('_', 1)

    if order_id not in pending_updates:
        await callback.answer("❗ Изменения уже обработаны")
        return

    update_data = pending_updates.pop(order_id)

    try:
        with open('orders.txt', 'r', encoding='utf-8') as f:
            parts = None
            for line in f:
                if line.startswith(f"{order_id}|"):
                    parts = line.strip().split('|')
                    break
            
            if not parts:
                raise Exception("Order not found")
            
            restaurant_id = int(parts[1])
        
        if action == 'confirm':
            new_packages = int(parts[4]) + update_data['added_packages']
            new_distances = parts[5] + ', ' + ', '.join(update_data['added_distances'])
            new_price = int(parts[6]) + update_data['added_price']
            
            with open('orders.txt', 'r+', encoding='utf-8') as f:
                lines = []
                for line in f:
                    if line.startswith(f"{order_id}|"):
                        new_line = (
                            f"{parts[0]}|{parts[1]}|{parts[2]}|{parts[3]}|"
                            f"{new_packages}|{new_distances}|{new_price}|"
                            f"{parts[7]}|{parts[8]}|{parts[9]}|{parts[10]}"
                        )
                        lines.append(new_line + '\n')
                    else:
                        lines.append(line)
                
                f.seek(0)
                f.writelines(lines)
                f.truncate()
            
            await callback.answer("Дополнительные посылки подтверждены!")
            await bot.send_message(
                restaurant_id,
                f"✅ Курьер подтвердил добавление к заказу {order_id}"
            )
        else:
            await callback.answer("Дополнительные посылки отменены!")
            await bot.send_message(
                restaurant_id,
                f"❌ Курьер отклонил добавление к заказу {order_id}"
            )
        
        await callback.message.delete()
    except Exception as e:
        logger.error(f"Error processing additional packages: {e}")
        await callback.answer("❗ Произошла ошибка при обработке запроса")
        
@dp.callback_query(lambda c: c.data.startswith('accept_'))
async def handle_order_accept(callback: types.CallbackQuery):
    order_id = callback.data.split('_', 1)[1]
    courier_id = callback.from_user.id

    await update_order_status(order_id, 'accepted', courier_id)

    try:
        with open('orders.txt', 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith(f"{order_id}|"):
                    parts = line.strip().split('|')
                    restaurant_id = int(parts[1])
                    break
        
        await bot.send_message(
            restaurant_id,
            f"✅ Заказ {order_id} принят курьером @{callback.from_user.username}"
        )
        
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🚚 Заказ доставлен", callback_data=f"delivered_{order_id}")
            ]])
        )
        
        await callback.answer(f"Вы приняли заказ {order_id}")
    except Exception as e:
        logger.error(f"Error updating message: {e}")
        await callback.answer("Ошибка обновления статуса")
@dp.callback_query(lambda c: c.data.startswith('decline_'))
async def handle_order_decline(callback: types.CallbackQuery):
    order_id = callback.data.split('_', 1)[1]

    await update_order_status(order_id, 'declined')
    await callback.message.delete()
    await callback.answer(f"Заказ {order_id} отклонен")
    await redirect_order(order_id)


@dp.callback_query(lambda c: c.data.startswith('delivered_'))
async def handle_delivery_confirmation(callback: types.CallbackQuery):
    order_id = callback.data.split('_', 1)[1]

    await update_order_status(order_id, 'delivered')

    try:
        with open('orders.txt', 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith(f"{order_id}|"):
                    parts = line.strip().split('|')
                    restaurant_id = int(parts[1])
                    break
        
        await bot.send_message(
            restaurant_id,
            f"🚚 Заказ {order_id} был доставлен!"
        )
        
        await callback.message.delete()
        await callback.answer(f"Заказ {order_id} отмечен как доставленный")
    except Exception as e:
        logger.error(f"Delivery confirmation error: {e}")
        await callback.answer("Ошибка обновления статуса")


@dp.message(lambda message: message.text == "Начать смену")
async def start_shift(message: types.Message):
    if message.from_user.id in get_blocked_couriers():
        await message.answer("❌ Ваш аккаунт заблокирован и не может работать курьером.")
        return

    add_active_courier(message.from_user.id)

    await message.answer(
        "✅ Смена начата! Теперь вы будете получать заказы.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Закрыть смену")]],
            resize_keyboard=True
        )
    )


@dp.message(lambda message: message.text == "Закрыть смену")
async def end_shift(message: types.Message):
    remove_active_courier(message.from_user.id)

    await message.answer(
        "✅ Смена закрыта! Заказы больше не будут поступать.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Начать смену")]],
            resize_keyboard=True
        )
    )


@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    admin_id = file_operation('admin_id.txt', 'r')
    if str(message.from_user.id) == str(admin_id):
        await message.answer(
            "👋 Добро пожаловать, администратор!",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="ПОЛУЧИТЬ ОТЧЁТ")],
                    [KeyboardButton(text="Управление курьерами")]
                ],
                resize_keyboard=True
            )
        )
        return

    couriers = get_couriers()
    if message.from_user.id in couriers:
        if message.from_user.id in get_active_couriers():
            markup = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Закрыть смену")]],
                resize_keyboard=True
            )
        else:
            markup = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Начать смену")]],
                resize_keyboard=True
            )
        await message.answer("👋 Добро пожаловать назад!", reply_markup=markup)
        return

    restaurant = conn.execute('SELECT name FROM restaurants WHERE user_id=?', (message.from_user.id,)).fetchone()
    if restaurant:
        await show_restaurant_menu(message.from_user.id)
        return

    await state.set_state(Form.role)
    await message.answer(
        "👋 Добро пожаловать! Выберите тип регистрации:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Регистрация курьера")],
                [KeyboardButton(text="Регистрация заведения")],
                [KeyboardButton(text="Администратор")]
            ],
            resize_keyboard=True
        )
    )

@dp.message(Form.role)
async def process_role(message: types.Message, state: FSMContext):
    if message.text not in ["Регистрация курьера", "Регистрация заведения", "Администратор"]:
        await message.answer("❗ Пожалуйста, выберите вариант из меню")
        return

    if message.text == "Регистрация курьера":
        await state.set_state(Form.courier_password)
        await message.answer(
            "🔑 Введите пароль для регистрации курьера:",
            reply_markup=types.ReplyKeyboardRemove()
        )
    elif message.text == "Регистрация заведения":
        await state.set_state(Form.restaurant_password)
        await message.answer(
            "🔑 Введите пароль для регистрации заведения:",
            reply_markup=types.ReplyKeyboardRemove()
        )
    elif message.text == "Администратор":
        await state.set_state(Form.admin_password)
        await message.answer(
            "🔑 Введите пароль администратора:",
            reply_markup=types.ReplyKeyboardRemove()
        )

@dp.message(Form.courier_password)
async def courier_auth(message: types.Message, state: FSMContext):
    if message.text == SECRET_PASSWORD:
        await state.set_state(Form.courier_name)
        await message.answer("🔤 Придумайте себе имя (например: Курьер Женя):")
    else:
        await message.answer("❌ Неверный пароль!")
        await state.clear()

@dp.message(Form.restaurant_password)
async def restaurant_auth(message: types.Message, state: FSMContext):
    if message.text == SECRET_PASSWORD:
        await state.set_state(Form.restaurant_name)
        await message.answer("🏢 Введите название вашего заведения:")
    else:
        await message.answer("❌ Неверный пароль!")
        await state.clear()
        
@dp.message(Form.admin_password)
async def admin_auth(message: types.Message, state: FSMContext):
    if message.text == ADMIN_PASSWORD:
        file_operation('admin_id.txt', 'w', message.from_user.id)
        await message.answer(
            "👋 Добро пожаловать, администратор!",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="ПОЛУЧИТЬ ОТЧЁТ")],
                    [KeyboardButton(text="Управление курьерами")]
                ],
                resize_keyboard=True
            )
        )
        await state.clear()
    else:
        await message.answer("❌ Неверный пароль!")
        await state.clear()


async def schedule_cleanup():
    while True:
        now = datetime.now(TIME_ZONE)
        if now.hour == 23 and now.minute == 59:
            with open('orders.txt', 'w') as f:
                f.truncate()
            logger.info("Daily orders cleanup performed")
        await asyncio.sleep(60)


async def send_weekly_report():
    while True:
        now = datetime.now(TIME_ZONE)
        
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0 and now.hour >= 9:
            days_until_monday = 7  
        
        target_time = now.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=days_until_monday)
        wait_seconds = (target_time - now).total_seconds()
        
        await asyncio.sleep(wait_seconds)
        
        try:
            report = await generate_report()
            
            admin_id = file_operation('admin_id.txt', 'r')
            if admin_id:
                await bot.send_message(admin_id, f"📅 Еженедельный отчёт:\n\n{report}")
            
            restaurants = conn.execute('SELECT user_id, name FROM restaurants').fetchall()
            for user_id, name in restaurants:
                try:
                    restaurant_report = await generate_restaurant_report(user_id)
                    await bot.send_message(user_id, f"📅 Ваша недельная статистика:\n\n{restaurant_report}")
                except Exception as e:
                    logger.error(f"Ошибка отправки отчёта заведению {name}: {e}")
                    
        except Exception as e:
            logger.error(f"Ошибка отправки еженедельного отчёта: {e}")


@dp.message()
async def handle_unprocessed(message: types.Message):
    await message.answer("❗ Пожалуйста, используйте кнопки меню для взаимодействия с ботом")


async def on_startup():
    asyncio.create_task(schedule_cleanup())
    asyncio.create_task(send_weekly_report())

from fastapi import FastAPI
import uvicorn
import multiprocessing
import logging

app = FastAPI()

@app.get("/")
async def wakeup():
    return {"status": "Bot is alive"}

def run_bot():
    """Запуск бота в отдельном процессе"""
    from aiogram import Dispatcher
    import asyncio
    
    async def start_polling():
        await dp.start_polling(bot)
    
    asyncio.run(start_polling())

def run_http():
    """Запуск HTTP сервера"""
    uvicorn.run(app, host="0.0.0.0", port=10000)

if __name__ == '__main__':
    # Настройка логирования
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    # Запускаем бота в отдельном процессе
    bot_process = multiprocessing.Process(target=run_bot)
    bot_process.start()
    
    # Запускаем HTTP сервер в основном процессе
    try:
        run_http()
    except KeyboardInterrupt:
        logger.info("Остановка сервера...")
    finally:
        bot_process.terminate()
        bot_process.join()