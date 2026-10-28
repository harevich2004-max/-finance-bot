@app.get("/", response_class=HTMLResponse)
def read_root():
    db = SessionLocal()
    transactions = db.query(Transaction).order_by(Transaction.date.desc()).limit(20).all()
    db.close()
    
    rows = ""
    for tx in transactions:
        color = "green" if tx.amount > 0 else "red"
        date_str = tx.date.strftime('%Y-%m-%d %H:%M') if tx.date else ""
        rows += f"<tr><td>{date_str}</td><td>{tx.type}</td><td style='color:{color};'>{tx.amount}</td><td>{tx.comment or ''}</td></tr>"

    return f"""
    <html>
        <head>
            <title>Finance Dashboard</title>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; background: #f4f7f6; margin: 0; padding: 20px; }}
                .container {{ max-width: 800px; margin: auto; background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
                h2 {{ color: #333; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
                th, td {{ padding: 12px; border-bottom: 1px solid #ddd; text-align: left; }}
                th {{ background-color: #4CAF50; color: white; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>📊 Финансовый дашборд</h2>
                <p>Последние транзакции:</p>
                <table>
                    <tr><th>Дата</th><th>Тип</th><th>Сумма</th><th>Комментарий</th></tr>
                    {rows}
                </table>
            </div>
        </body>
    </html>
    """
