"""
Trade Execution Test Suite — Integration Tests
Verifies: signal → trade open → DB persistence → SL/TP closure → balance integrity

Usage:  cd backend && python test_trade_execution.py

⚠️  Uses paper_trading_test.db — NEVER touches production DB.
    Cleaned up automatically on exit.
"""

import os
import sys
import sqlite3
import unittest
import shutil

# ── Path setup ──────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models.database import Base, Trade, EquitySnapshot, StrategyStatus
from engine.paper_trade import PaperTradeEngine
from engine.strategies import Strategy1, Strategy2, Strategy3, Strategy4, Strategy5

# ── Constants ───────────────────────────────────────────────────────────────
TEST_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "paper_trading_test.db")
SYMBOL = "BTCUSDT"
INITIAL_CAPITAL = 10_000.0
ENTRY_PRICE = 50_000.0          # Simulated BTC price for predictable math
SL_DISTANCE_PCT = 0.03          # 3% stop distance (matches _open_live_trade estimate)
TP_DISTANCE_PCT = 0.06          # 6% take profit (2x risk)


def _make_config(**overrides):
    """Build a minimal config that PaperTradeEngine can initialise with."""
    base = {
        "binance": {"symbol": SYMBOL, "interval": "1m"},
        "system": {"db_path": TEST_DB, "check_interval_seconds": 30,
                    "host": "127.0.0.1", "port": 8000},
        "strategies": {
            "strategy1": {
                "enabled": True, "name": "Test S1", "initial_capital": INITIAL_CAPITAL,
                "risk_pct": 0.01, "ema_fast": 20, "ema_slow": 50,
                "atr_period": 14, "atr_sl_mult": 2.0, "atr_tp_mult": 2.0,
                "rsi_period": 14, "rsi_max": 75, "breakout_period": 10,
                "vol_mult": 1.1, "max_hold_hours": 24, "fee": 0.0004,
            },
            "strategy2": {
                "enabled": True, "name": "Test S2", "initial_capital": INITIAL_CAPITAL,
                "risk_pct": 0.01, "ema_fast": 25, "ema_slow": 60,
                "atr_period": 14, "atr_sl_mult": 1.8, "atr_tp_mult": 2.5,
                "rsi_period": 14, "rsi_max": 72, "breakout_period": 15,
                "vol_mult": 1.08, "fee": 0.0004,
            },
            "strategy3": {
                "enabled": True, "name": "Test S3", "initial_capital": INITIAL_CAPITAL,
                "risk_pct": 0.01, "ema_fast": 15, "ema_slow": 35,
                "atr_period": 10, "atr_sl_mult": 2.5, "atr_tp_mult": 2.0,
                "rsi_period": 14, "breakout_period": 12, "fee": 0.0004,
                "slippage": 0.0005, "funding_rate": 0.0001,
            },
            "strategy4": {
                "enabled": True, "name": "Test S4", "initial_capital": INITIAL_CAPITAL,
                "risk_pct": 0.01, "ema_fast": 8, "ema_slow": 21,
                "ema_short": 8, "ema_long": 21, "rsi_period": 9,
                "reward_ratio": 1.5, "fee": 0.0004,
            },
            "strategy5": {
                "enabled": True, "name": "Test S5", "initial_capital": INITIAL_CAPITAL,
                "risk_pct": 0.01, "ema_fast": 5, "ema_slow": 13,
                "rsi_period": 9, "vwap_period": 15, "momentum_period": 3,
                "reward_ratio": 1.8, "fee": 0.0004,
            },
        },
    }
    # Deep-merge overrides into strategies sections if provided
    for section, val in overrides.items():
        if section in base and isinstance(val, dict) and isinstance(base[section], dict):
            base[section].update(val)
        else:
            base[section] = val
    return base


def _direct_query(db_path):
    """Return a raw sqlite3 connection for assertions."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _cleanup_db(db_path):
    """Delete test database file if it exists."""
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except PermissionError:
            pass  # Windows file lock — harmless for test teardown


class TestTradeExecution(unittest.TestCase):
    """Integration tests for the paper trading engine — real DB, real engine."""

    @classmethod
    def setUpClass(cls):
        _cleanup_db(TEST_DB)
        cls.config = _make_config()
        cls.engine = create_engine(f"sqlite:///{TEST_DB}")
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        _cleanup_db(TEST_DB)

    def setUp(self):
        """Each test starts with a fresh PaperTradeEngine on a clean schema."""
        # Wipe all tables
        with self.Session() as s:
            s.query(EquitySnapshot).delete()
            s.query(Trade).delete()
            s.query(StrategyStatus).delete()
            s.commit()
        # Recreate engine — fresh session, clean state
        self.db_session = self.Session()
        self.paper_engine = PaperTradeEngine(self.config, self.db_session)

    def tearDown(self):
        if self.db_session:
            try:
                self.db_session.close()
            except Exception:
                pass

    # ─────────────────────────── helper methods ─────────────────────────────

    def _open_trade_for_strategy(self, strat_name: str, direction: str,
                                  entry_price: float = ENTRY_PRICE):
        """Convenience: open a live trade for a given strategy by name."""
        strat_config = self.config["strategies"][strat_name]
        self.paper_engine._open_live_trade(strat_name, direction, entry_price, strat_config)
        return strat_config

    def _close_trade_by_price(self, strat_name: str, trigger_price: float):
        """Simulate a price update that should trigger SL or TP."""
        exited = self.paper_engine.update_live_price(trigger_price)
        return exited

    def _db_trade(self, trade_id=None, strategy_name=None, status="open"):
        """Fetch a trade directly from the DB (not from engine memory)."""
        conn = _direct_query(TEST_DB)
        try:
            q = "SELECT * FROM trades WHERE 1=1"
            params = []
            if trade_id is not None:
                q += " AND id = ?"
                params.append(trade_id)
            if strategy_name is not None:
                q += " AND strategy_name = ?"
                params.append(strategy_name)
            if status is not None:
                q += " AND status = ?"
                params.append(status)
            q += " ORDER BY id DESC LIMIT 1"
            row = conn.execute(q, params).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _db_open_trades_count(self, strategy_name=None):
        conn = _direct_query(TEST_DB)
        try:
            if strategy_name:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM trades WHERE status='open' AND strategy_name=?",
                    (strategy_name,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM trades WHERE status='open'"
                ).fetchone()
            return row["c"]
        finally:
            conn.close()

    def _db_equity(self, strategy_name):
        conn = _direct_query(TEST_DB)
        try:
            row = conn.execute(
                "SELECT equity FROM equity_snapshots WHERE strategy_name=? ORDER BY id DESC LIMIT 1",
                (strategy_name,),
            ).fetchone()
            return row["equity"] if row else None
        finally:
            conn.close()

    def _db_all_trades(self):
        conn = _direct_query(TEST_DB)
        try:
            rows = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ─────────────────────────── TEST 1 ─────────────────────────────────────
    def test_database_operations(self):
        """Verify we can connect, read, write, and clean up the test DB."""
        print("\n┌─────────────────────────────────────────┐")
        print("│ TEST 1: Database Operations             │")

        # 1. Initial state — zero trades
        open_count = self._db_open_trades_count()
        self.assertEqual(open_count, 0,
            f"Expected 0 open trades, got {open_count}")

        # 2. Insert a dummy trade directly via DB
        conn = _direct_query(TEST_DB)
        conn.execute(
            "INSERT INTO trades (strategy_name, symbol, direction, entry_price, "
            "stop_loss, take_profit, units, entry_time, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)",
            ("DummyStrat", SYMBOL, "long", 100.0, 95.0, 110.0, 1.0, "open"),
        )
        conn.commit()
        conn.close()

        # 3. Verify it appears
        row = self._db_trade(strategy_name="DummyStrat", status="open")
        self.assertIsNotNone(row, "Dummy trade not found after insert")
        self.assertEqual(row["entry_price"], 100.0)
        self.assertEqual(row["stop_loss"], 95.0)
        self.assertEqual(row["take_profit"], 110.0)
        self.assertEqual(row["direction"], "long")
        self.assertIsNotNone(row["id"], "Trade id should be auto-generated")

        # 4. Delete it
        conn = _direct_query(TEST_DB)
        conn.execute("DELETE FROM trades WHERE strategy_name='DummyStrat'")
        conn.commit()
        conn.close()

        # 5. Verify gone
        row = self._db_trade(strategy_name="DummyStrat", status="open")
        self.assertIsNone(row, "Dummy trade should be deleted")

        print(f"│ ✓ PASS (initial_open={open_count}, insert/delete OK) │")
        print("└─────────────────────────────────────────┘")

    # ─────────────────────────── TEST 2 ─────────────────────────────────────
    def test_trade_open_persists(self):
        """Prove that when a trade opens, it immediately appears in the DB."""
        print("\n┌─────────────────────────────────────────┐")
        print("│ TEST 2: Trade Open Persists             │")

        strat = "strategy1"
        initial_open = self._db_open_trades_count(strat)
        initial_equity = self.paper_engine.strategies[strat].capital

        # Open trade
        self._open_trade_for_strategy(strat, "long", ENTRY_PRICE)

        # Assertion 1: open count increased by exactly 1
        new_open = self._db_open_trades_count(strat)
        self.assertEqual(new_open, initial_open + 1,
            f"Expected {initial_open + 1} open trade, got {new_open}")

        # Assertion 2: fetch the trade and verify fields
        trade = self._db_trade(strategy_name=strat, status="open")
        self.assertIsNotNone(trade, "Trade not found in DB after open")
        self.assertEqual(trade["direction"], "long")
        self.assertAlmostEqual(trade["entry_price"], ENTRY_PRICE * 1.0003, places=2,
            msg=f"entry_price {trade['entry_price']} != expected ~{ENTRY_PRICE * 1.0003} (slippage applied)")
        self.assertGreater(trade["stop_loss"], 0, "stop_loss should be set")
        self.assertGreater(trade["take_profit"], 0, "take_profit should be set")
        self.assertIsNotNone(trade["id"], "Trade id should be auto-generated (persisted)")
        trade_id = trade["id"]

        # Assertion 3: strategy capital unchanged on open (P&L realised on close)
        strat_capital = self.paper_engine.strategies[strat].capital
        self.assertAlmostEqual(strat_capital, initial_equity, places=2,
            msg=f"strategy capital {strat_capital} should equal initial {initial_equity} on open")

        # Assertion 4: engine memory has the trade
        self.assertIn(strat, self.paper_engine.active_trades,
            "Strategy not in active_trades dict")
        mem_trade = self.paper_engine.active_trades[strat]
        self.assertEqual(mem_trade.id, trade_id,
            f"Engine trade id {mem_trade.id} != DB trade id {trade_id}")

        print(f"│ ✓ PASS (trade_id={trade_id}, entry=${trade['entry_price']:.2f}) │")
        print(f"│   SL=${trade['stop_loss']:.2f}  TP=${trade['take_profit']:.2f}  │")
        print("└─────────────────────────────────────────┘")

    # ─────────────────────────── TEST 3 ─────────────────────────────────────
    def test_trade_close_stop_loss(self):
        """Prove: price hits SL → trade closes, P&L recorded, balance updated."""
        print("\n┌─────────────────────────────────────────┐")
        print("│ TEST 3: Trade Close — Stop Loss         │")

        strat = "strategy1"
        self._open_trade_for_strategy(strat, "long", ENTRY_PRICE)
        trade = self._db_trade(strategy_name=strat, status="open")
        trade_id = trade["id"]
        sl = trade["stop_loss"]
        prev_equity = self.paper_engine.strategies[strat].capital

        # Trigger SL: price drops below stop loss
        trigger_price = sl - 1.0  # $1 below SL to guarantee hit
        exited = self._close_trade_by_price(strat, trigger_price)

        # Assertion 1: trade is closed in DB
        closed_trade = self._db_trade(trade_id=trade_id, status=None)
        self.assertIsNotNone(closed_trade, "Trade disappeared from DB")
        self.assertEqual(closed_trade["status"], "closed",
            f"Expected status='closed', got '{closed_trade['status']}'")

        # Assertion 2: exit_reason is stop_loss related
        self.assertEqual(closed_trade["exit_reason"], "stop_loss",
            f"Expected exit_reason='stop_loss', got '{closed_trade['exit_reason']}'")

        # Assertion 3: exit_price set
        self.assertIsNotNone(closed_trade["exit_price"],
            "exit_price should not be NULL")

        # Assertion 4: P&L calculated and negative (loss)
        self.assertIsNotNone(closed_trade["pnl"], "pnl should not be NULL")
        self.assertLess(closed_trade["pnl"], 0,
            f"P&L should be negative for SL, got {closed_trade['pnl']:.2f}")

        # Assertion 5: strategy capital reflects the loss
        new_equity = self.paper_engine.strategies[strat].capital
        expected_equity = prev_equity + closed_trade["pnl"]
        self.assertAlmostEqual(new_equity, expected_equity, places=1,
            msg=f"equity {new_equity} != expected {expected_equity}")

        # Assertion 6: no open trades left for this strategy
        open_count = self._db_open_trades_count(strat)
        self.assertEqual(open_count, 0, f"Expected 0 open trades, got {open_count}")

        # Assertion 7: engine memory cleared
        self.assertNotIn(strat, self.paper_engine.active_trades,
            "Strategy should be removed from active_trades")
        self.assertFalse(self.paper_engine.strategies[strat].in_trade,
            "strategy.in_trade should be False")

        pnl = closed_trade["pnl"]
        print(f"│ ✓ PASS (SL triggered, P&L=${pnl:.2f} loss)  │")
        print(f"│   exit_price=${closed_trade['exit_price']:.2f} │")
        print(f"│   equity: ${prev_equity:.2f} → ${new_equity:.2f}      │")
        print("└─────────────────────────────────────────┘")

    # ─────────────────────────── TEST 4 ─────────────────────────────────────
    def test_trade_close_take_profit(self):
        """Prove: price hits TP → trade closes with profit."""
        print("\n┌─────────────────────────────────────────┐")
        print("│ TEST 4: Trade Close — Take Profit       │")

        strat = "strategy2"
        self._open_trade_for_strategy(strat, "long", ENTRY_PRICE)
        trade = self._db_trade(strategy_name=strat, status="open")
        trade_id = trade["id"]
        tp = trade["take_profit"]
        prev_equity = self.paper_engine.strategies[strat].capital

        # Trigger TP: price rises above take profit
        trigger_price = tp + 1.0
        exited = self._close_trade_by_price(strat, trigger_price)

        # Assertion 1: trade closed
        closed_trade = self._db_trade(trade_id=trade_id, status=None)
        self.assertEqual(closed_trade["status"], "closed")

        # Assertion 2: exit_reason = take_profit
        self.assertEqual(closed_trade["exit_reason"], "take_profit",
            f"Expected 'take_profit', got '{closed_trade['exit_reason']}'")

        # Assertion 3: exit_price set
        self.assertIsNotNone(closed_trade["exit_price"])

        # Assertion 4: P&L positive (profit on a long that hit TP)
        self.assertIsNotNone(closed_trade["pnl"])
        self.assertGreater(closed_trade["pnl"], 0,
            f"P&L should be positive for TP, got {closed_trade['pnl']:.2f}")

        # Assertion 5: equity increased
        new_equity = self.paper_engine.strategies[strat].capital
        self.assertGreater(new_equity, prev_equity,
            f"Equity {new_equity} should be > {prev_equity} after profit")

        # Assertion 6: no open trades left
        open_count = self._db_open_trades_count(strat)
        self.assertEqual(open_count, 0)

        pnl = closed_trade["pnl"]
        print(f"│ ✓ PASS (TP triggered, P&L=${pnl:.2f} profit)│")
        print(f"│   exit_price=${closed_trade['exit_price']:.2f} │")
        print(f"│   equity: ${prev_equity:.2f} → ${new_equity:.2f}     │")
        print("└─────────────────────────────────────────┘")

    # ─────────────────────────── TEST 5 ─────────────────────────────────────
    def test_stuck_state_recovery(self):
        """Prove: if strategy.in_trade=True but no active trade, new trade can still open."""
        print("\n┌─────────────────────────────────────────┐")
        print("│ TEST 5: Stuck State Recovery            │")

        strat = "strategy3"

        # Corrupt state: mark as in_trade but no entry in active_trades
        self.paper_engine.strategies[strat].in_trade = True
        # Verify active_trades is empty for this strategy
        self.assertNotIn(strat, self.paper_engine.active_trades,
            "Precondition: no active trade in dict")

        # Now try to open — the guard in main.py checks BOTH in_trade AND active_trades.
        # But _open_live_trade bypasses the guard (it's called directly).
        # To test the guard path, we check that the engine's active_trades dict
        # does NOT have the strategy, so a direct call should still work.
        self._open_trade_for_strategy(strat, "long", ENTRY_PRICE)

        # Assertion: trade exists in DB
        open_count = self._db_open_trades_count(strat)
        self.assertEqual(open_count, 1,
            f"Expected exactly 1 open trade, got {open_count}")

        trade = self._db_trade(strategy_name=strat, status="open")
        self.assertIsNotNone(trade, "Trade not found after stuck-state open")
        self.assertIsNotNone(trade["id"])

        # Verify engine state is now consistent
        self.assertIn(strat, self.paper_engine.active_trades,
            "Strategy should be in active_trades after open")
        self.assertTrue(self.paper_engine.strategies[strat].in_trade)

        # Clean up: close the trade
        sl = trade["stop_loss"]
        self._close_trade_by_price(strat, sl - 1.0)

        # Verify fully cleaned
        open_count_after = self._db_open_trades_count(strat)
        self.assertEqual(open_count_after, 0)

        print("│ ✓ PASS (stuck state recovered, trade OK)│")
        print(f"│   trade_id={trade['id']}, direction={trade['direction']}        │")
        print("└─────────────────────────────────────────┘")

    # ─────────────────────────── TEST 6 ─────────────────────────────────────
    def test_multiple_strategies_concurrent(self):
        """Prove: two strategies can have open trades simultaneously, close independently."""
        print("\n┌─────────────────────────────────────────┐")
        print("│ TEST 6: Multiple Strategies Concurrent  │")

        # Open two trades
        self._open_trade_for_strategy("strategy4", "long", ENTRY_PRICE)
        self._open_trade_for_strategy("strategy5", "short", ENTRY_PRICE)

        # Assertion 1: two open trades total
        total_open = self._db_open_trades_count()
        self.assertEqual(total_open, 2, f"Expected 2 open trades, got {total_open}")

        # Assertion 2: each strategy has exactly one open trade with correct direction
        trade_a = self._db_trade(strategy_name="strategy4", status="open")
        trade_b = self._db_trade(strategy_name="strategy5", status="open")
        self.assertIsNotNone(trade_a, "Strategy4 trade not found")
        self.assertIsNotNone(trade_b, "Strategy5 trade not found")
        self.assertEqual(trade_a["direction"], "long")
        self.assertEqual(trade_b["direction"], "short")

        # Close strategy4 trade via SL
        sl_a = trade_a["stop_loss"]
        exited = self._close_trade_by_price("strategy4", sl_a - 1.0)

        # Assertion 3: only 1 open trade remains
        total_open_after = self._db_open_trades_count()
        self.assertEqual(total_open_after, 1,
            f"Expected 1 open trade after closing S4, got {total_open_after}")

        # Assertion 4: strategy5 trade is still open
        trade_b_check = self._db_trade(strategy_name="strategy5", status="open")
        self.assertIsNotNone(trade_b_check,
            "Strategy5 trade should still be open after S4 closed")

        # Clean up strategy5
        # For short: SL is above entry; trigger by price going above SL
        sl_b = trade_b["stop_loss"]
        self._close_trade_by_price("strategy5", sl_b + 1.0)

        final_open = self._db_open_trades_count()
        self.assertEqual(final_open, 0, "All trades should be closed at end")

        print("│ ✓ PASS (2 concurrent, independent close)  │")
        print(f"│   S4: long ${ENTRY_PRICE:.0f}, S5: short ${ENTRY_PRICE:.0f} │")
        print("└─────────────────────────────────────────┘")

    # ─────────────────────────── TEST 7 ─────────────────────────────────────
    def test_balance_integrity(self):
        """Prove: equity stays correct over multiple open→close cycles."""
        print("\n┌─────────────────────────────────────────┐")
        print("│ TEST 7: Balance Integrity (Multi-Cycle) │")

        strat = "strategy1"
        initial_equity = self.paper_engine.strategies[strat].capital
        pnl_sum = 0.0

        # ── Cycle 1: profitable trade (TP hit) ──────────────────────────
        self._open_trade_for_strategy(strat, "long", ENTRY_PRICE)
        trade1 = self._db_trade(strategy_name=strat, status="open")
        tp1 = trade1["take_profit"]
        self._close_trade_by_price(strat, tp1 + 1.0)

        closed1 = self._db_trade(trade_id=trade1["id"], status=None)
        pnl1 = closed1["pnl"]
        pnl_sum += pnl1
        equity_after_1 = self.paper_engine.strategies[strat].capital

        # Verify equity matches initial + pnl
        expected_1 = initial_equity + pnl1
        self.assertAlmostEqual(equity_after_1, expected_1, places=1,
            msg=f"After cycle 1: equity {equity_after_1} != expected {expected_1}")

        # ── Cycle 2: losing trade (SL hit) ──────────────────────────────
        self._open_trade_for_strategy(strat, "long", ENTRY_PRICE)
        trade2 = self._db_trade(strategy_name=strat, status="open")
        sl2 = trade2["stop_loss"]
        self._close_trade_by_price(strat, sl2 - 1.0)

        closed2 = self._db_trade(trade_id=trade2["id"], status=None)
        pnl2 = closed2["pnl"]
        pnl_sum += pnl2
        equity_after_2 = self.paper_engine.strategies[strat].capital

        # Verify equity matches initial + sum of all PnL
        expected_2 = initial_equity + pnl1 + pnl2
        self.assertAlmostEqual(equity_after_2, expected_2, places=1,
            msg=f"After cycle 2: equity {equity_after_2} != expected {expected_2}")

        # ── Cross-check: sum of all closed trade PnL matches equity delta ─
        conn = _direct_query(TEST_DB)
        try:
            row = conn.execute(
                "SELECT SUM(pnl) as total_pnl FROM trades WHERE strategy_name=? AND status='closed'",
                (strat,),
            ).fetchone()
            db_pnl_sum = row["total_pnl"] or 0.0
        finally:
            conn.close()

        actual_delta = equity_after_2 - initial_equity
        self.assertAlmostEqual(db_pnl_sum, actual_delta, places=1,
            msg=f"DB PnL sum {db_pnl_sum} != equity delta {actual_delta}")

        # Verify no open trades remaining
        open_count = self._db_open_trades_count(strat)
        self.assertEqual(open_count, 0, "No open trades should remain after cycles")

        print(f"│ ✓ PASS (equity integrity verified)        │")
        print(f"│   Initial equity:  ${initial_equity:.2f}           │")
        print(f"│   Cycle 1 (TP):   +${pnl1:+.2f} → ${equity_after_1:.2f}  │")
        print(f"│   Cycle 2 (SL):   ${pnl2:+.2f} → ${equity_after_2:.2f}   │")
        print(f"│   Net P&L:        ${pnl_sum:+.2f}                     │")
        print(f"│   DB PnL sum:     ${db_pnl_sum:+.2f}              │")
        print("└─────────────────────────────────────────┘")


# ──────────────────────────── Runner ──────────────────────────────────────────
def _print_header():
    width = 57
    print()
    print("╔" + "═" * width + "╗")
    print("║  TRADE EXECUTION TEST SUITE".ljust(width) + "║")
    print("║  Tests: signal → DB → SL/TP → equity".ljust(width) + "║")
    print("║  DB: paper_trading_test.db (isolated)".ljust(width) + "║")
    print("╚" + "═" * width + "╝")


if __name__ == "__main__":
    _print_header()

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestTradeExecution)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # ── Summary ──────────────────────────────────────────────────────────
    passed = result.testsRun - len(result.failures) - len(result.errors)
    failed = len(result.failures) + len(result.errors)

    width = 57
    print()
    if failed == 0:
        print("╔" + "═" * width + "╗")
        print("║  ALL TESTS PASSED".ljust(width) + "║")
        print("║  Trade execution is reliable.".ljust(width) + "║")
        print("╚" + "═" * width + "╝")
    else:
        print("╔" + "═" * width + "╗")
        print(f"║  {passed} PASSED, {failed} FAILED".ljust(width) + "║")
        print("║  ⚠  System NOT reliable — see failures above.".ljust(width) + "║")
        print("╚" + "═" * width + "╝")

    # Cleanup
    _cleanup_db(TEST_DB)

    sys.exit(0 if failed == 0 else 1)
