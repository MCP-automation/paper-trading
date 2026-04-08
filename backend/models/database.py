from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

Base = declarative_base()

class Trade(Base):
    __tablename__ = 'trades'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String(50), nullable=False)
    symbol = Column(String(20), nullable=False)
    direction = Column(String(10), nullable=False)  # 'long' or 'short'
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(Float, nullable=False)
    units = Column(Float, nullable=False)
    entry_time = Column(DateTime, nullable=False, default=datetime.utcnow)
    exit_price = Column(Float, nullable=True)
    exit_time = Column(DateTime, nullable=True)
    pnl = Column(Float, nullable=True)
    exit_reason = Column(String(50), nullable=True)  # 'sl_hit', 'tp_hit', 'timeout'
    fee = Column(Float, default=0.0)
    slippage = Column(Float, default=0.0)
    funding_cost = Column(Float, default=0.0)
    status = Column(String(20), default='open')  # 'open' or 'closed'

class EquitySnapshot(Base):
    __tablename__ = 'equity_snapshots'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String(50), nullable=False)
    equity = Column(Float, nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)

class Signal(Base):
    __tablename__ = 'signals'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String(50), nullable=False)
    symbol = Column(String(20), nullable=False)
    signal_type = Column(String(20), nullable=False)  # 'long', 'short', 'none'
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    indicators = Column(Text, nullable=True)  # JSON string of indicator values

class StrategyStatus(Base):
    __tablename__ = 'strategy_status'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String(50), nullable=False, unique=True)
    enabled = Column(Boolean, default=True)
    last_updated = Column(DateTime, nullable=False, default=datetime.utcnow)

def get_engine(db_path):
    abs_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), db_path)
    return create_engine(f'sqlite:///{abs_path}')

def init_db(engine):
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()
