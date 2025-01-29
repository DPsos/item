from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.utils import executor
import sqlite3
from PIL import Image, ImageDraw
import requests
import asyncio
from flask import Flask, render_template
import re
import threading
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
from datetime import datetime
import secrets
import base64
import os

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация Flask для веб-интерфейса
app = Flask(__name__)

# Инициализация бота
API_TOKEN = '7565784723:AAHyDRF6C-ExQ10Mrf4QxOh2_EuJAWwL9dE'
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# Подключение к базе данных
conn = sqlite3.connect('space_world.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблиц
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE,
    wallet_address TEXT,
    username TEXT,
    created_at TEXT,
    payment_comment TEXT UNIQUE  -- Добавляем поле для уникального комментария
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS planets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    planet_data TEXT,
    created_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
)
''')
conn.commit()

# Стоимость создания планеты (0.01 TON)
PLANET_PRICE_TON = 0.01 * 1e9  # Преобразуем в наноTON

# Ваш кошелек для приема платежей
YOUR_WALLET_ADDRESS = "UQDCwmyuPjSJg8XmIHbEo1d2gLoRXq0iYyEshPGosPF_6JG1"

# Валидация адреса кошелька TON
def validate_wallet_address(address):
    return re.match(r"^[a-zA-Z0-9_-]{48}$", address) is not None

# Генерация уникального комментария
def generate_unique_comment():
    return secrets.token_hex(8)

# Формирование ссылки на оплату
def generate_payment_link(payment_comment):
    amount = PLANET_PRICE_TON  # 0.01 TON в наноTON
    url = f"https://pay.ton.org/?amount={amount}&to={YOUR_WALLET_ADDRESS}&text={payment_comment}"
    return url

# Команда /start
@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.reply("Добро пожаловать в космический мир! Используйте /create_planet, чтобы создать свою планету.")

# Команда /create_planet
@dp.message_handler(commands=['create_planet'])
async def create_planet(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    if user:
        await message.reply("Вы уже зарегистрированы. Используйте /draw_planet, чтобы нарисовать планету.")
    else:
        # Генерируем уникальный комментарий
        payment_comment = generate_unique_comment()
        
        # Формируем ссылку на оплату
        payment_link = generate_payment_link(payment_comment)
        
        # Отправляем ссылку на оплату
        await message.reply(
            f"Для создания планеты перейдите по ссылке и оплатите {PLANET_PRICE_TON / 1e9} TON:\n\n"
            f"[Оплатить]({payment_link})\n\n"
            "После оплаты введите адрес вашего кошелька TON:",
            parse_mode="Markdown"
        )
        # Ожидаем ввода адреса кошелька пользователя
        @dp.message_handler(lambda msg: msg.from_user.id == user_id and not msg.text.startswith('/'))
        async def process_wallet(message: types.Message):
            wallet_address = message.text.strip()
            try:
                if validate_wallet_address(wallet_address):
                    username = message.from_user.username or "Anonymous"
                    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    cursor.execute(
                        "INSERT INTO users (user_id, wallet_address, username, created_at, payment_comment) VALUES (?, ?, ?, ?, ?)",
                        (user_id, wallet_address, username, created_at, payment_comment)
                    )
                    conn.commit()
                    await message.reply(f"Кошелек {wallet_address} сохранен. Теперь вы можете использовать /draw_planet для создания планеты.")
                else:
                    await message.reply("Неверный адрес кошелька. Пожалуйста, введите корректный адрес TON.")
            except sqlite3.Error as e:
                logger.error(f"Ошибка базы данных: {e}")
                await message.reply(f"Произошла ошибка при обработке вашего запроса: {e}")

# Асинхронная функция для проверки платежа
async def check_payment(user_id):
    cursor.execute("SELECT wallet_address, payment_comment FROM users WHERE user_id = ?", (user_id,))
    user_data = cursor.fetchone()
    if not user_data:
        return False
    user_wallet, payment_comment = user_data
    
    url = f"https://toncenter.com/api/v2/getTransactions?address={YOUR_WALLET_ADDRESS}&limit=10"
    try:
        logger.info(f"Отправляем запрос к {url}")
        response = requests.get(url)
        logger.info(f"Получен ответ: {response.status_code}, {response.text}")
        if response.status_code != 200:
            logger.error(f"Ошибка получения транзакций: {response.status_code}, Response: {response.text}")
            return False
        
        transactions = response.json().get('result', [])
        logger.info(f"Полученные транзакции: {transactions}")
        
        for tx in transactions:
            if tx['in_msg']:
                msg = tx['in_msg']
                if msg['source'] == user_wallet and int(msg['value']) >= PLANET_PRICE_TON:
                    # Проверяем наличие комментария в теле сообщения
                    comment = None
                    if 'msg_data' in msg:
                        if msg['msg_data']['@type'] == 'msg.dataText':
                            comment = msg['msg_data'].get('text', '')
                        elif msg['msg_data']['@type'] == 'msg.dataRaw':
                            body = msg['msg_data'].get('body', '')
                            try:
                                decoded_body = bytes.fromhex(body).decode('utf-8').strip('\x00')
                                comment = base64.b64decode(decoded_body).decode('utf-8')
                            except Exception as e:
                                logger.error(f"Ошибка декодирования сообщения: {e}")
                    
                    if comment and comment == payment_comment:
                        logger.info(f"Транзакция найдена: source={msg['source']}, value={msg['value']}, comment={comment}")
                        return True
                    else:
                        logger.info(f"Комментарий не совпадает: expected={payment_comment}, got={comment}")
        logger.info(f"Не найдено подходящих транзакций для кошелька: {user_wallet}")
        return False
    except Exception as e:
        logger.error(f"Исключение при получении транзакций: {e}")
        return False

# Функция для создания редактора планеты
def create_planet_editor(user_id):
    img = Image.new('RGB', (32, 32), color='black')
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 24, 24), fill='blue')
    img_path = f'static/planets/editor_{user_id}.png'
    img.save(img_path)
    return img_path

# Инлайн кнопки для выбора цвета
def get_color_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=3)
    buttons = [
        InlineKeyboardButton("🟥 Красный", callback_data="color_red"),
        InlineKeyboardButton("🟩 Зеленый", callback_data="color_green"),
        InlineKeyboardButton("🟦 Синий", callback_data="color_blue"),
    ]
    keyboard.add(*buttons)
    return keyboard

# Команда /draw_planet
@dp.message_handler(commands=['draw_planet'])
async def draw_planet(message: types.Message):
    user_id = message.from_user.id
    # Задержка перед проверкой транзакции
    await asyncio.sleep(10)  # Подождите 10 секунд перед проверкой
    
    if await check_payment(user_id):
        editor_path = create_planet_editor(user_id)
        with open(editor_path, 'rb') as photo:
            await message.reply_photo(photo, caption="Редактор планеты. Используйте команду /pixel X Y COLOR для изменения пикселей.")
        
        await message.reply("Выберите цвет:", reply_markup=get_color_keyboard())
    else:
        await message.reply("Оплата не подтверждена. Пожалуйста, отправьте 0.01 TON с указанным комментарием.")

# Обработка выбора цвета
@dp.callback_query_handler(lambda query: query.data.startswith("color_"))
async def process_color(query: types.CallbackQuery):
    color = query.data.split("_")[1]
    await query.message.reply(f"Выбран цвет: {color}. Используйте команду /pixel X Y для изменения пикселя.")

# Команда /pixel для изменения пикселя
@dp.message_handler(commands=['pixel'])
async def set_pixel(message: types.Message):
    user_id = message.from_user.id
    try:
        args = message.get_args().split()
        x, y = int(args[0]), int(args[1])
        color = args[2] if len(args) > 2 else "red"
        editor_path = f'static/planets/editor_{user_id}.png'
        editor_image = Image.open(editor_path).convert("RGBA")
        draw = ImageDraw.Draw(editor_image)
        if color == "red":
            draw.point((x, y), fill=(255, 0, 0, 255))
        elif color == "green":
            draw.point((x, y), fill=(0, 255, 0, 255))
        elif color == "blue":
            draw.point((x, y), fill=(0, 0, 255, 255))
        editor_image.save(editor_path)
        with open(editor_path, 'rb') as photo:
            await message.reply_photo(photo, caption=f"Пиксель ({x}, {y}) изменен на {color}.")
    except Exception as e:
        logger.error(f"Ошибка при изменении пикселя: {e}")
        await message.reply(f"Ошибка: {e}. Используйте команду /pixel X Y COLOR.")

# Команда /save_planet для сохранения планеты
@dp.message_handler(commands=['save_planet'])
async def save_planet(message: types.Message):
    user_id = message.from_user.id
    editor_path = f'static/planets/editor_{user_id}.png'
    planet_path = f'static/planets/planet_{user_id}.png'
    Image.open(editor_path).save(planet_path)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO planets (user_id, planet_data, created_at) VALUES (?, ?, ?)", (user_id, planet_path, created_at))
    conn.commit()
    await message.reply("Ваша планета сохранена и выпущена в космос!")

# Веб-интерфейс для отображения общего космического мира
@app.route('/')
def show_space():
    cursor.execute("SELECT planets.*, users.username FROM planets JOIN users ON planets.user_id = users.user_id")
    planets = cursor.fetchall()
    return render_template('index.html', planets=planets)

# Запуск Flask
def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 80)))

if __name__ == '__main__':
    webhook_url = os.getenv('WEBHOOK_URL', 'https://plannet.space/webhook')
    webhook_path = '/webhook'

    async def on_startup(dispatcher):
        await bot.set_webhook(webhook_url + webhook_path)

    async def on_shutdown(dispatcher):
        await bot.delete_webhook()

    # Запуск Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    # Запуск бота с вебхуком
    executor.start_webhook(
        dispatcher=dp,
        webhook_path=webhook_path,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        skip_updates=True,
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 80))
    )