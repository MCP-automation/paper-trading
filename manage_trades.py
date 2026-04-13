#!/usr/bin/env python3
"""
Trade Management Script - Delete, View, and Manage Trades in SQLite Database
"""

import os
import sys
from datetime import datetime
from backend.models.database import get_engine, Trade, init_db

def get_db_session():
    """Initialize database session"""
    db_path = 'paper_trading.db'
    if not os.path.exists(db_path):
        print(f"❌ Database not found: {db_path}")
        return None
    
    engine = get_engine(db_path)
    return init_db(engine)

def count_trades():
    """Display trade counts"""
    db = get_db_session()
    if not db:
        return
    
    total = db.query(Trade).count()
    closed = db.query(Trade).filter_by(status='closed').count()
    open_trades = db.query(Trade).filter_by(status='open').count()
    
    print(f"📊 Trade Summary:")
    print(f"  Total Trades: {total}")
    print(f"  Closed Trades: {closed}")
    print(f"  Open Trades: {open_trades}")
    return total, closed, open_trades

def delete_all_trades():
    """Delete ALL trades from database"""
    db = get_db_session()
    if not db:
        return
    
    try:
        total_before = db.query(Trade).count()
        
        # Delete all trades
        db.query(Trade).delete()
        db.commit()
        
        total_after = db.query(Trade).count()
        
        print(f"✅ Deleted {total_before} trades")
        print(f"📊 Remaining trades: {total_after}")
        
    except Exception as e:
        print(f"❌ Error deleting trades: {e}")
        db.rollback()

def delete_oldest_trades(keep_count=48):
    """Delete oldest trades, keeping only the 'keep_count' most recent"""
    db = get_db_session()
    if not db:
        return
    
    try:
        total_before = db.query(Trade).count()
        
        if total_before <= keep_count:
            print(f"⚠️  Only {total_before} trades exist. Nothing to delete (keeping {keep_count}).")
            return
        
        # Get trades to keep (most recent)
        trades_to_keep = db.query(Trade).order_by(Trade.id.desc()).limit(keep_count).all()
        keep_ids = [t.id for t in trades_to_keep]
        
        # Delete all others
        deleted_count = db.query(Trade).filter(~Trade.id.in_(keep_ids)).delete()
        db.commit()
        
        total_after = db.query(Trade).count()
        
        print(f"✅ Deleted {deleted_count} oldest trades")
        print(f"✅ Kept {total_after} most recent trades")
        
    except Exception as e:
        print(f"❌ Error deleting trades: {e}")
        db.rollback()

def delete_by_strategy(strategy_name):
    """Delete all trades from a specific strategy"""
    db = get_db_session()
    if not db:
        return
    
    try:
        count = db.query(Trade).filter_by(strategy_name=strategy_name).delete()
        db.commit()
        
        print(f"✅ Deleted {count} trades from {strategy_name}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        db.rollback()

def delete_trades_with_zero_pnl():
    """Delete all trades with P&L = 0 (stale/reset trades)"""
    db = get_db_session()
    if not db:
        return
    
    try:
        total_before = db.query(Trade).count()
        
        # Delete trades with P&L = 0
        deleted_count = db.query(Trade).filter(Trade.pnl == 0).delete()
        db.commit()
        
        total_after = db.query(Trade).count()
        
        print(f"✅ Deleted {deleted_count} stale trades (P&L = $0)")
        print(f"📊 Remaining trades: {total_after}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        db.rollback()

def show_trades(limit=10):
    """Display recent trades"""
    db = get_db_session()
    if not db:
        return
    
    trades = db.query(Trade).order_by(Trade.id.desc()).limit(limit).all()
    
    print(f"\n📋 Latest {limit} Trades:")
    print("-" * 100)
    for t in trades:
        pnl_str = f"${t.pnl:.2f}" if t.pnl else "$0.00"
        print(f"ID={t.id:3d} | {t.strategy_name:10s} | {t.direction:5s} | Entry: ${t.entry_price:.2f} | Exit: ${t.exit_price:.2f} | P&L: {pnl_str:>10s}")
    print("-" * 100)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Manage trading database")
    parser.add_argument("--count", action="store_true", help="Show trade counts")
    parser.add_argument("--delete-all", action="store_true", help="Delete ALL trades")
    parser.add_argument("--delete-oldest", type=int, metavar="N", help="Delete oldest trades, keep N most recent")
    parser.add_argument("--delete-strategy", type=str, help="Delete all trades from strategy")
    parser.add_argument("--delete-zero-pnl", action="store_true", help="Delete stale trades (P&L = $0)")
    parser.add_argument("--show", type=int, default=10, metavar="N", help="Show latest N trades (default: 10)")
    
    args = parser.parse_args()
    
    if not any(vars(args).values()):
        parser.print_help()
        sys.exit(0)
    
    if args.count:
        count_trades()
    
    if args.delete_all:
        print("⚠️  WARNING: You are about to DELETE ALL TRADES!")
        confirm = input("Type 'YES' to confirm: ")
        if confirm == "YES":
            delete_all_trades()
        else:
            print("❌ Cancelled")
    
    if args.delete_oldest:
        print(f"⚠️  WARNING: Deleting all but {args.delete_oldest} most recent trades!")
        confirm = input("Type 'YES' to confirm: ")
        if confirm == "YES":
            delete_oldest_trades(args.delete_oldest)
        else:
            print("❌ Cancelled")
    
    if args.delete_strategy:
        print(f"⚠️  WARNING: Deleting all trades from {args.delete_strategy}!")
        confirm = input("Type 'YES' to confirm: ")
        if confirm == "YES":
            delete_by_strategy(args.delete_strategy)
        else:
            print("❌ Cancelled")
    
    if args.delete_zero_pnl:
        print("⚠️  WARNING: Deleting all stale trades (P&L = $0)!")
        confirm = input("Type 'YES' to confirm: ")
        if confirm == "YES":
            delete_trades_with_zero_pnl()
        else:
            print("❌ Cancelled")
    
    if args.show:
        show_trades(args.show)
    
    print("\n✅ Done")
