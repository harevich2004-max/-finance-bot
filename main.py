import os
import logging
import threading
from fastapi import FastAPI
import uvicorn
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

# --- НАСТРОЙКИ БАЗЫ ДАННЫХ И БОТА ---
TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Модель транзакции под существующую структуру Supabase
class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    amount = Column(Float)
    type = Column(String)  # 'income' или 'expense'
    category_id = Column(Integer, nullable=True)
    comment = Column(String, nullable=True)
    date = Column(DateTime, default=datetime.utcnow)

def init_db():
    Base.metadata.create_all(bind=engine)

# --- ИНТЕРАКТИВНЫЕ КНОПКИ ---
def get_main_keyboard():
    dashboard_url = "https://finance-bot-qshy.onrender.com"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Доход", callback_data="add_income"),
            InlineKeyboardButton(text="➖ Расход", callback_data="add_expense")
        ],
        [
            InlineKeyboardButton(text="👥 Долги (Мне / Я должен)", callback_data="manage_debts")
        ],
        [
            InlineKeyboardButton(text="📊 Открыть дашборд", url=dashboard_url)
        ]
    ])

# Команда /start
@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я твой финансовый бот.\n"
        "Выбери нужное действие с помощью кнопок ниже:",
        reply_markup=get_main_keyboard()
    )

@router.callback_query(F.data == "add_income")
async def cb_add_income(callback: CallbackQuery):
    await callback.message.answer(
        "📥 **Добавление дохода**\n"
        "Отправь сумму и описание сообщением, например:\n"
        "`+5000 Зарплата`"
    )
    await callback.answer()

@router.callback_query(F.data == "add_expense")
async def cb_add_expense(callback: CallbackQuery):
    await callback.message.answer(
        "📤 **Добавление расхода**\n"
        "Отправь сумму и описание сообщением, например:\n"
        "`-350 Обед`"
    )
    await callback.answer()

@router.callback_query(F.data == "manage_debts")
async def cb_manage_debts(callback: CallbackQuery):
    await callback.message.answer(
        "👥 **Учет долгов**\n"
        "Напиши запись в формате:\n"
        "• `долг мне Имя Сумма`\n"
        "• `я должен Имя Сумма`"
    )
    await callback.answer()

# --- ОБРАБОТКА ТЕКСТОВЫХ ЗАПИСЕЙ ---
@router.message(F.text.regexp(r"^([+-]?\d+(?:\.\d+)?)\s+(.+)"))
async def process_transaction(message: Message):
    text = message.text.strip()
    try:
        parts = text.split(maxsplit=1)
        amount = float(parts[0])
        comment = parts[1]
        
        t_type = "income" if amount > 0 else "expense"
        
        db = SessionLocal()
        new_tx = Transaction(
            amount=amount,
            type=t_type,
            comment=comment
        )
        db.add(new_tx)
        db.commit()
        db.close()
        
        sign = "+" if amount > 0 else ""
        await message.answer(f"✅ Успешно записано: <b>{sign}{amount}</b> ({comment})")
    except Exception as e:
        await message.answer("⚠️ Ошибка при сохранении. Проверь формат записи.")

dp.include_router(router)

# --- FASTAPI ДЕШБОРД ---
app = FastAPI()

@app.get("/")
def read_root():
    db = SessionLocal()
    transactions = db.query(Transaction).order_by(Transaction.date.desc()).limit(20).all()
    db.close()
    
    html = """
    <html>
        <head>
            <title>Finance Dashboard</title>
            <meta charset="utf-8">
            <style>
                body { font-family: Arial, sans-serif; background: #f4f7f6; margin: 0; padding: 20px; }
                .container { max-width: 800px; margin: auto; background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
                h2 { color: #333; }
                table { width: 100%; border-collapse: collapse; margin-top: 20px; }
                th, td { padding: 12px; border-bottom: 1px solid #ddd; text-align: left; }
                th { background-color: #4CAF50; color: white; }
            </style>
        </head>
        <body>
            <div class="container">
                <h2>📊 Финансовый дашборд</h2>
                <p>Последние транзакции:</p>
                <table>
                    <tr><th>Дата</th><th>Тип</th><th>Сумма</th><th>Комментарий</th></tr>
    """
    for tx in transactions:
        color = "green" if tx.amount > 0 else "red"
        date_str = tx.date.strftime('%Y-%m-%d %H:%M') if tx.date else ""
        html += f"<tr><td>{date_str}</td><td>{tx.type}</td><td style='color:{color};'>{tx.amount}</td><td>{tx.comment}</td></tr>"
    
    html += """
                </table>
            </div>
        </body>
    </html>
    """
    return html

async def run_bot():
    init_db()
    await dp.start_polling(bot)

def run_web():
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    
    asyncio_run = __import__("asyncio")
    asyncio_run.run(run_bot())
