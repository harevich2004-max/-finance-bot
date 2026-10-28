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
        
    add_btn_text = "➕ Добавить (кто должен)" if debt_type == "to_me" else "➕ Добавить (кому должен)"
    add_cb = "add_debt_to_me" if debt_type == "to_me" else "
