import os
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime

# Если есть облачная база (из настроек Render) — берем ее, если нет — локальная SQLite для теста
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///finance.db")

# Исправление для Render (иногда URL начинается с postgres://, а SQLAlchemy требует postgresql://)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    type = Column(String)  # 'expense' или 'income'

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    amount = Column(Float)
    type = Column(String)
    category_id = Column(Integer, ForeignKey("categories.id"))
    comment = Column(String, nullable=True)
    date = Column(DateTime, default=datetime.utcnow)
    category = relationship("Category")

class Debt(Base):
    __tablename__ = "debts"
    id = Column(Integer, primary_key=True, index=True)
    person = Column(String)
    amount = Column(Float)  # > 0 (мне должны), < 0 (я должен)
    description = Column(String, nullable=True)

def init_db():
    Base.metadata.create_all(bind=engine)