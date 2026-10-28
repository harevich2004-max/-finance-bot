import os
import logging
import threading
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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

# --- МОДЕЛИ БАЗЫ ДАННЫХ ---
class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    amount = Column(Float)
    type = Column(String)  # 'income', 'expense', 'transfer'
    account = Column(String)  # 'cash' или 'card'
    comment = Column(String, nullable=True)
    date = Column(DateTime, default=datetime.utcnow)

class Debt(Base):
    __tablename__ = "debts"
    id = Column(Integer, primary_key=True, index=True)
    person = Column(String)
    amount = Column(Float)
    debt_type = Column(String)  # 'to_me' (мне должны) или 'i_owe' (я должен)
    status = Column(String, default="active") # 'active' или 'closed'
    date = Column(DateTime, default=datetime.utcnow)

def init_db():
    Base.metadata.create_all(bind=engine)

# --- СОСТОЯНИЯ (FSM) ---
class FinanceStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_account = State()
    waiting_for_debt_amount = State()
    waiting_for_replenish = State()

# --- КЛАВИАТУРЫ ---
def get_main_keyboard():
    dashboard_url = "https://finance-bot-qshy.onrender.com"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Доход", callback_data="add_income"),
            InlineKeyboardButton(text="➖ Расход", callback_data="add_expense")
        ],
        [
            InlineKeyboardButton(text="💳 Пополнить карту", callback_data="replenish_card")
        ],
        [
            InlineKeyboardButton(text="🟢 Мне должны", callback_data="debts_to_me"),
            InlineKeyboardButton(text="🔴 Я должен", callback_data="debts_i_owe")
        ],
        [
            InlineKeyboardButton(text="📊 Открыть дашборд", url=dashboard_url)
        ]
    ])

# Команда /start
@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я твой продвинутый финансовый бот.\n"
        "Выбери нужный раздел с помощью кнопок:",
        reply_markup=get_main_keyboard()
    )

# --- ДОХОДЫ И РАСХОДЫ ---
@router.callback_query(F.data.in_(["add_income", "add_expense"]))
async def cb_start_transaction(callback: CallbackQuery, state: FSMContext):
    t_type = "income" if callback.data == "add_income" else "expense"
    await state.update_data(type=t_type)
    
    action_name = "дохода" if t_type == "income" else "расхода"
    await callback.message.answer(f"📥 Введи сумму и описание {action_name} (например: `500 Зарплата` или `200 Обед`):")
    await state.set_state(FinanceStates.waiting_for_amount)
    await callback.answer()

@router.message(FinanceStates.waiting_for_amount)
async def process_tx_amount(message: Message, state: FSMContext):
    try:
        parts = message.text.strip().split(maxsplit=1)
        amount = float(parts[0])
        comment = parts[1] if len(parts) > 1 else ""
        
        await state.update_data(amount=amount, comment=comment)
        
        data = await state.get_data()
        if data.get("type") == "expense":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="💵 Наличные", callback_data="acc_cash"),
                    InlineKeyboardButton(text="💳 Карта", callback_data="acc_card")
                ]
            ])
            await message.answer("С какого счета списать расход?", reply_markup=kb)
            await state.set_state(FinanceStates.waiting_for_account)
        else:
            save_transaction(amount, "income", "card", comment)
            await state.clear()
            await message.answer(f"✅ Доход +{amount} ({comment}) успешно записан на карту!", reply_markup=get_main_keyboard())
    except Exception:
        await message.answer("⚠️ Неверный формат. Введи сумму и описание через пробел, например: `350 Кофе`")

@router.callback_query(FinanceStates.waiting_for_account, F.data.in_(["acc_cash", "acc_card"]))
async def process_account_choice(callback: CallbackQuery, state: FSMContext):
    account = "cash" if callback.data == "acc_cash" else "card"
    data = await state.get_data()
    
    amount = -abs(data["amount"])
    save_transaction(amount, "expense", account, data["comment"])
    
    await state.clear()
    acc_name = "Наличные" if account == "cash" else "Карта"
    await callback.message.answer(f"✅ Расход {amount} ({data['comment']}) успешно списан с баланса: <b>{acc_name}</b>!", reply_markup=get_main_keyboard())
    await callback.answer()

# --- ПОПОЛНЕНИЕ КАРТЫ ---
@router.callback_query(F.data == "replenish_card")
async def cb_replenish(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("💳 Введи сумму, на которую хочешь пополнить карту (деньги спишутся с наличных):")
    await state.set_state(FinanceStates.waiting_for_replenish)
    await callback.answer()

@router.message(FinanceStates.waiting_for_replenish)
async def process_replenish(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        save_transaction(amount, "transfer", "card", "Пополнение карты с наличных")
        await state.clear()
        await message.answer(f"✅ Карта успешно пополнена на <b>+{amount}</b>!", reply_markup=get_main_keyboard())
    except Exception:
        await message.answer("⚠️ Введи корректную сумму числом, например: `1500`")

# --- УЧЕТ ДОЛГОВ ---
@router.callback_query(F.data.in_(["debts_to_me", "debts_i_owe"]))
async def cb_show_debts(callback: CallbackQuery):
    debt_type = "to_me" if callback.data == "debts_to_me" else "i_owe"
    title = "🟢 Мне должны:" if debt_type == "to_me" else "🔴 Я должен:"
    
    db = SessionLocal()
    debts = db.query(Debt).filter(Debt.debt_type == debt_type, Debt.status == "active").all()
    db.close()
    
    keyboard_buttons = []
    text = f"<b>{title}</b>\n\n"
    
    if debts:
        for d in debts:
            text += f"• <b>{d.person}</b>: {d.amount}\n"
            keyboard_buttons.append([InlineKeyboardButton(text=f"🔄 Вернули: {d.person} ({d.amount})", callback_data=f"pay_debt_{d.id}")])
    else:
        text += "Список пуст 🎉\n"
        
    if debt_type == "to_me":
        add_btn_text = "➕ Добавить (кто должен)"
        add_cb = "add_debt_to_me"
    else:
        add_btn_text = "➕ Добавить (кому должен)"
        add_cb = "add_debt_i_owe"
    
    keyboard_buttons.append([InlineKeyboardButton(text=add_btn_text, callback_data=add_cb)])
    keyboard_buttons.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="back_to_menu")])
    
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons))
    await callback.answer()

@router.callback_query(F.data.in_(["add_debt_to_me", "add_debt_i_owe"]))
async def cb_add_debt_start(callback: CallbackQuery, state: FSMContext):
    dt = "to_me" if callback.data == "add_debt_to_me" else "i_owe"
    await state.update_data(debt_type=dt)
    await callback.message.answer("✍️ Напиши в формате: `Имя Сумма` (например: `Андрей 500`):")
    await state.set_state(FinanceStates.waiting_for_debt_amount)
    await callback.answer()

@router.message(FinanceStates.waiting_for_debt_amount)
async def process_debt_save(message: Message, state: FSMContext):
    try:
        parts = message.text.strip().split(maxsplit=1)
        person = parts[0]
        amount = float(parts[1])
        
        data = await state.get_data()
        db = SessionLocal()
        new_debt = Debt(person=person, amount=amount, debt_type=data["debt_type"], status="active")
        db.add(new_debt)
        db.commit()
        db.close()
        
        await state.clear()
        await message.answer("✅ Долг успешно сохранен!", reply_markup=get_main_keyboard())
    except Exception:
        await message.answer("⚠️ Ошибка формата. Напиши так: `Имя Сумма` (например: `Максим 1000`)")

@router.callback_query(F.data.startswith("pay_debt_"))
async def cb_close_debt(callback: CallbackQuery):
    debt_id = int(callback.data.split("_")[2])
    db = SessionLocal()
    debt = db.query(Debt).filter(Debt.id == debt_id).first()
    if debt:
        debt.status = "closed"
        db.commit()
    db.close()
    
    await callback.message.answer("✅ Долг отмечен как возвращенный!")
    await callback.answer()

@router.callback_query(F.data == "back_to_menu")
async def cb_back(callback: CallbackQuery):
    await callback.message.edit_text("🏠 Главное меню:", reply_markup=get_main_keyboard())
    await callback.answer()

def save_transaction(amount, t_type, account, comment):
    db = SessionLocal()
    tx = Transaction(amount=amount, type=t_type, account=account, comment=comment)
    db.add(tx)
    db.commit()
    db.close()

dp.include_router(router)

# --- FASTAPI ВЕБ-ДАШБОРД ---
app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def read_root():
    db = SessionLocal()
    transactions = db.query(Transaction).order_by(Transaction.date.desc()).limit(20).all()
    debts = db.query(Debt).filter(Debt.status == "active").all()
    
    cash_balance = sum(t.amount for t in db.query(Transaction).filter(Transaction.account == 'cash').all())
    card_balance = sum(t.amount for t in db.query(Transaction).filter(Transaction.account == 'card').all())
    db.close()
    
    rows = ""
    for tx in transactions:
        color = "green" if tx.amount > 0 else "red"
        date_str = tx.date.strftime('%Y-%m-%d %H:%M') if tx.date else ""
        rows += f"<tr><td>{date_str}</td><td>{tx.type}</td><td>{tx.account}</td><td style='color:{color};'>{tx.amount}</td><td>{tx.comment or ''}</td></tr>"

    debt_rows = ""
    for d in debts:
        dt_name = "Мне должны" if d.debt_type == "to_me" else "Я должен"
        debt_rows += f"<tr><td><b>{d.person}</b></td><td>{dt_name}</td><td>{d.amount}</td></tr>"

    return f"""
    <html>
        <head>
            <title>Finance Dashboard</title>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; background: #f4f7f6; margin: 0; padding: 20px; }}
                .container {{ max-width: 900px; margin: auto; background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
                .balance-box {{ display: flex; gap: 20px; margin-bottom: 20px; }}
                .card {{ background: #4CAF50; color: white; padding: 15px; border-radius: 8px; flex: 1; text-align: center; }}
                .card.cash {{ background: #2196F3; }}
                h2, h3 {{ color: #333; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 30px; }}
                th, td {{ padding: 10px; border-bottom: 1px solid #ddd; text-align: left; }}
                th {{ background-color: #333; color: white; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>📊 Финансовый дашборд</h2>
                
                <div class="balance-box">
                    <div class="card">
                        <h3>💳 Баланс карты</h3>
                        <p style="font-size: 24px; margin: 0;">{card_balance}</p>
                    </div>
                    <div class="card cash">
                        <h3>💵 Наличные</h3>
                        <p style="font-size: 24px; margin: 0;">{cash_balance}</p>
                    </div>
                </div>

                <h3>Активные долги</h3>
                <table>
                    <tr><th>Кто / Кому</th><th>Тип</th><th>Сумма</th></tr>
                    {debt_rows if debt_rows else "<tr><td colspan='3'>Нет активных долгов</td></tr>"}
                </table>

                <h3>Последние транзакции</h3>
                <table>
                    <tr><th>Дата</th><th>Тип</th><th>Счет</th><th>Сумма</th><th>Комментарий</th></tr>
                    {rows}
                </table>
            </div>
        </body>
    </html>
    """

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
