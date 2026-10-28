import asyncio
import logging
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn
import threading

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("Не задан BOT_TOKEN в переменных окружения!")

from database import SessionLocal, Transaction, Category, Debt, init_db

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- TELEGRAM БОТ ЛОГИКА ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 **Привет! Твой облачный фин-бот запущен.**\n\n"
        "🔹 **Учет трат/доходов:** `-350 Кафе` или `+15000 Зарплата`\n"
        "🔹 **Долги:** пиши `/debts` или добавляй: `долг Иван 500 за обед`\n"
        "🔹 **Дашборд:** открывай веб-панель по ссылке твоего сервера."
    )

@dp.message(Command("debts"))
async def cmd_debts(message: types.Message):
    db = SessionLocal()
    debts = db.query(Debt).all()
    db.close()
    
    if not debts:
        await message.answer("📌 Долгов пока нет.\nДобавить: `долг [Имя] [Сумма] [описание]` (минус — если должен ты).")
        return
        
    text = "📌 **Твои долги:**\n\n"
    for d in debts:
        status = "должен тебе" if d.amount > 0 else "ты должен"
        text += f"• **{d.person}**: {abs(d.amount)} грн ({status}) — *{d.description or ''}*\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text.regexp(r"^долг\s+(?P<person>\S+)\s+(?P<amount>[+\-]?\d+(?:\.\d+)?)(?:\s+(?P<desc>.+))?"))
async def add_debt_handler(message: types.Message):
    parts = message.text.split(maxsplit=3)
    if len(parts) < 3:
        await message.reply("Формат: `долг Иван 500 за обед`")
        return
    
    person = parts[1]
    amount = float(parts[2])
    desc = parts[3] if len(parts) > 3 else ""
    
    db = SessionLocal()
    debt = Debt(person=person, amount=amount, description=desc)
    db.add(debt)
    db.commit()
    db.close()
    
    await message.reply(f"✅ Долг сохранен: **{person}** на сумму **{amount} грн**.", parse_mode="Markdown")

@dp.message(F.text.regexp(r"^([+\-]?\d+(?:\.\d+)?)\s+(.+)"))
async def process_transaction(message: types.Message):
    text = message.text.strip()
    parts = text.split(maxsplit=1)
    amount = float(parts[0])
    cat_name = parts[1].strip()
    
    t_type = "income" if amount > 0 else "expense"
    
    db = SessionLocal()
    category = db.query(Category).filter(Category.name == cat_name).first()
    if not category:
        category = Category(name=cat_name, type=t_type)
        db.add(category)
        db.commit()
        db.refresh(category)
        
    tx = Transaction(amount=abs(amount), type=t_type, category_id=category.id, comment=cat_name)
    db.add(tx)
    db.commit()
    db.close()
    
    await message.reply(f"✅ Записано: **{amount} грн**\n📂 Категория: *{cat_name}*", parse_mode="Markdown")

# --- ВЕБ-ДАШБОРД (FastAPI) ---
app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    db = SessionLocal()
    total_expenses = db.query(Transaction).filter(Transaction.type == 'expense').all()
    total_income = db.query(Transaction).filter(Transaction.type == 'income').all()
    debts = db.query(Debt).all()
    recent_txs = db.query(Transaction).order_by(Transaction.id.desc()).limit(15).all()
    db.close()
    
    sum_exp = sum(t.amount for t in total_expenses)
    sum_inc = sum(t.amount for t in total_income)
    
    html = f"""
    <!DOCTYPE html>
    <html lang="uk">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Финансовый Дашборд</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-950 text-slate-100 p-6 font-sans">
        <div class="max-w-3xl mx-auto">
            <h1 class="text-3xl font-bold mb-6 flex items-center gap-2">📊 Учет финансов</h1>
            
            <div class="grid grid-cols-2 gap-4 mb-8">
                <div class="bg-slate-900 border border-slate-800 p-5 rounded-2xl shadow-lg">
                    <p class="text-slate-400 text-sm">Доходы</p>
                    <p class="text-3xl font-bold text-emerald-400 mt-1">+{sum_inc:,.0f} грн</p>
                </div>
                <div class="bg-slate-900 border border-slate-800 p-5 rounded-2xl shadow-lg">
                    <p class="text-slate-400 text-sm">Расходы</p>
                    <p class="text-3xl font-bold text-rose-400 mt-1">-{sum_exp:,.0f} грн</p>
                </div>
            </div>

            <div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl shadow-lg mb-6">
                <h2 class="text-xl font-semibold mb-4 text-slate-200">📌 Долги</h2>
                <div class="space-y-2">
    """
    if debts:
        for d in debts:
            color = "text-emerald-400" if d.amount > 0 else "text-rose-400"
            status = "должен тебе" if d.amount > 0 else "ты должен"
            html += f"""
                <div class="flex justify-between items-center bg-slate-800/50 p-3 rounded-xl border border-slate-700/50">
                    <div>
                        <span class="font-medium">{d.person}</span>
                        <span class="text-xs text-slate-400 block">{d.description or ''}</span>
                    </div>
                    <span class="{color} font-semibold">{abs(d.amount)} грн ({status})</span>
                </div>
            """
    else:
        html += '<p class="text-slate-400 text-sm">Долгов пока нет.</p>'

    html += """
                </div>
            </div>
            
            <div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl shadow-lg">
                <h2 class="text-xl font-semibold mb-4 text-slate-200">📜 Последние транзакции</h2>
                <div class="space-y-2">
    """
    if recent_txs:
        for t in recent_txs:
            color = "text-emerald-400" if t.type == 'income' else "text-rose-400"
            sign = "+" if t.type == 'income' else "-"
            html += f"""
                <div class="flex justify-between items-center bg-slate-800/50 p-3 rounded-xl border border-slate-700/50">
                    <span class="font-medium">{t.comment}</span>
                    <span class="{color} font-semibold">{sign}{t.amount} грн</span>
                </div>
            """
    else:
        html += '<p class="text-slate-400 text-sm">Транзакций пока нет.</p>'

    html += """
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

# --- ЗАПУСК ДЛЯ ОБЛАКА ---
async def run_bot():
    init_db()
    await dp.start_polling(bot)

def run_web():
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Запускаем FastAPI в отдельном потоке, чтобы хостинг видел веб-сервер
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    
    # Запускаем бота
    asyncio.run(run_bot())