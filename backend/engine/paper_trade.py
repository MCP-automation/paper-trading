import json
import pandas as pd
import numpy as np
import time
import logging
import traceback
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from engine.strategies import (
    StrategyBase,
    Strategy1,
    Strategy2,
    Strategy3,
    Strategy4,
    Strategy5,
)
from engine.indicators import IndicatorEngine
from engine.signal_logger import log_signal, log_signal_check
from engine.trade_executor import (
    log_trade_attempt,
    log_trade_result,
    log_strategy_state_change,
    log_database_operation,
    log_trade_closure,
)
from models.database import Trade, EquitySnapshot, Signal, StrategyStatus
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class PaperTradeEngine:
    """Main paper trading engine that manages all strategies"""

    def __init__(self, config: Dict[str, Any], db_session: Session):
        self.config = config
        self.db = db_session
        self.strategies: Dict[str, StrategyBase] = {}
        self.active_trades: Dict[str, Trade] = {}
        self.equity_history: Dict[str, List[float]] = {}
        self.symbol = config["binance"]["symbol"]
        self.last_live_price: float = 0.0

        # Initialize strategies
        self._init_strategies()

        # Load open trades from DB
        self._load_open_trades()

    def _load_open_trades(self):
        """Load open trades from DB into active_trades dict"""
        open_trades = self.db.query(Trade).filter_by(status="open").all()

        if open_trades:
            logger.info(f"🔄 Resuming {len(open_trades)} open trades from previous run")
            for trade in open_trades:
                strat_name = trade.strategy_name
                strategy = self.strategies.get(strat_name)
                
                if strategy:
                    # Restore strategy state
                    strategy.in_trade = True
                    strategy.direction = trade.direction
                    strategy.entry_price = trade.entry_price
                    strategy.stop_loss = trade.stop_loss
                    strategy.take_profit = trade.take_profit
                    strategy.units = trade.units
                    
                    self.active_trades[strat_name] = trade
                    logger.info(f"✅ Resumed trade #{trade.id} for {strat_name}")
                else:
                    # If strategy no longer exists, close it
                    logger.warning(f"⚠️ Strategy {strat_name} not found, closing orphan trade #{trade.id}")
                    trade.status = "closed"
                    trade.exit_reason = "orphan_strategy"
                    trade.exit_price = trade.entry_price
                    trade.exit_time = datetime.now(timezone.utc)
                    trade.pnl = 0
            
            self.db.commit()

        # Restore each strategy's capital from its closed trade history
        for strat_name, strategy in self.strategies.items():
            closed_trades = (
                self.db.query(Trade)
                .filter_by(strategy_name=strat_name, status="closed")
                .all()
            )
            if closed_trades:
                realized_pnl = sum(t.pnl or 0 for t in closed_trades)
                strategy.capital = strategy.initial_capital + realized_pnl
                logger.info(
                    f"💼 [{strat_name}] Capital restored: ${strategy.capital:.2f} "
                    f"(initial ${strategy.initial_capital:.2f} + PnL ${realized_pnl:.2f})"
                )

    def _init_strategies(self):
        """Initialize all four strategies from config"""
        strat_cfg = self.config["strategies"]

        self.strategies["strategy1"] = Strategy1(strat_cfg["strategy1"])
        self.strategies["strategy2"] = Strategy2(strat_cfg["strategy2"])
        self.strategies["strategy3"] = Strategy3(strat_cfg["strategy3"])
        self.strategies["strategy4"] = Strategy4(strat_cfg["strategy4"])
        self.strategies["strategy5"] = Strategy5(strat_cfg["strategy5"])

        # Load enabled status from DB
        for name in self.strategies:
            db_status = (
                self.db.query(StrategyStatus).filter_by(strategy_name=name).first()
            )
            if db_status:
                self.strategies[name].enabled = db_status.enabled
            else:
                self.strategies[name].enabled = strat_cfg[name].get("enabled", True)
                self.db.add(
                    StrategyStatus(
                        strategy_name=name, enabled=self.strategies[name].enabled
                    )
                )

        self.db.commit()

    def process_new_bar(self, df: pd.DataFrame):
        """Process a new completed 1H bar - main entry point"""
        if len(df) < 200:  # Need enough data for EMA200
            return

        # Compute indicators
        for strat_name, strategy in self.strategies.items():
            if not strategy.enabled:
                continue

            # Get config for this strategy
            strat_config = self.config["strategies"][strat_name]

            # Compute indicators on the full DataFrame
            start = time.time()
            df_with_indicators = IndicatorEngine.compute_all_indicators(
                df.copy(), strat_config
            )
            indicator_duration = time.time() - start

            # Indicators computed successfully
            # (indicator_duration: {:.3f}s)
            logger.debug(
                f"[{strat_name}] Indicators computed in {indicator_duration:.3f}s"
            )

            # Get the last completed bar index
            current_idx = len(df_with_indicators) - 1

            # Manage existing trades first
            if strat_name in self.active_trades:
                self._manage_trade(strat_name, df_with_indicators, current_idx)

            # Check for new signals
            if not self.active_trades.get(strat_name):
                signal = strategy.generate_signal(df_with_indicators, current_idx)

                # Get the current bar's timestamp and price for logging
                row = df_with_indicators.iloc[current_idx]
                bar_timestamp = row.name if hasattr(row, 'name') else datetime.now(timezone.utc)
                current_price = row['close']
                
                # Log signal with detailed conditions
                if signal:
                    # Try to get debug info for conditions
                    try:
                        debug_info = strategy.get_signal_debug(df_with_indicators, current_idx)
                        conditions = debug_info.get('conditions', {}) if debug_info else None
                    except:
                        conditions = None
                    
                    log_signal(
                        strategy_name=strat_name,
                        signal=signal,
                        price=current_price,
                        timestamp=bar_timestamp if isinstance(bar_timestamp, datetime) else None,
                        conditions=conditions,
                        symbol=self.symbol
                    )
                else:
                    # Optional: log failed signal checks in debug mode
                    if getattr(strategy, 'debug_mode', False):
                        try:
                            debug_info = strategy.get_signal_debug(df_with_indicators, current_idx)
                            conditions = debug_info.get('conditions', {}) if debug_info else None
                            log_signal_check(
                                strategy_name=strat_name,
                                price=current_price,
                                timestamp=bar_timestamp if isinstance(bar_timestamp, datetime) else None,
                                conditions=conditions,
                                signal=None
                            )
                        except:
                            pass

                if signal:
                    self._open_trade(
                        strat_name,
                        signal,
                        df_with_indicators,
                        current_idx,
                        strat_config,
                    )

            # Record equity snapshot
            self._record_equity(strat_name)

    def update_live_price(self, live_price: float) -> list:
        """
        Update with a new live price from the ticker stream (~1s updates).
        Checks SL/TP on all active trades.
        Returns list of exited strategy names.
        """
        self.last_live_price = live_price
        exited = []

        for strat_name, trade in list(self.active_trades.items()):
            strategy = self.strategies[strat_name]

            exit_reason = None
            exit_price = live_price

            if strategy.direction == "long":
                if live_price <= strategy.stop_loss:
                    exit_reason = "stop_loss"
                elif live_price >= strategy.take_profit:
                    exit_reason = "take_profit"
            elif strategy.direction == "short":
                if live_price >= strategy.stop_loss:
                    exit_reason = "stop_loss"
                elif live_price <= strategy.take_profit:
                    exit_reason = "take_profit"
            else:
                logger.warning(f"⚠️ [{strat_name}] update_live_price: invalid direction={strategy.direction}, trade #{trade.id} may be stuck")
                # If direction is None/invalid, skip — no valid trade state

            if exit_reason:
                self._close_trade(strat_name, exit_price, exit_reason)
                exited.append(strat_name)

        return exited

    def process_live_price(self, live_price: float, df: pd.DataFrame):
        """Process live price updates for real-time paper trading (legacy method)"""
        if len(df) < 200:
            return
        for strat_name, strategy in self.strategies.items():
            if not strategy.enabled:
                continue
            if strat_name in self.active_trades:
                self._manage_live_trade(strat_name, live_price)
            if not self.active_trades.get(strat_name):
                signal = self._check_live_signal(strat_name, live_price, df)
                if signal:
                    strat_config = self.config["strategies"][strat_name]
                    self._open_live_trade(strat_name, signal, live_price, strat_config)
            self._record_equity(strat_name)

    def _manage_live_trade(self, strategy_name: str, live_price: float):
        """Manage active trades using live price data"""
        if strategy_name not in self.active_trades:
            return
        trade = self.active_trades[strategy_name]
        strategy = self.strategies[strategy_name]
        exit_reason = None
        exit_price = live_price
        if strategy.direction == "long":
            if live_price <= trade.stop_loss:
                exit_reason = "stop_loss"
            elif live_price >= trade.take_profit:
                exit_reason = "take_profit"
        else:
            if live_price >= trade.stop_loss:
                exit_reason = "stop_loss"
            elif live_price <= trade.take_profit:
                exit_reason = "take_profit"
        if exit_reason:
            self._close_trade(strategy_name, exit_price, exit_reason)

    def _close_trade(self, strategy_name: str, exit_price: float, exit_reason: str):
        """Close an active trade with detailed logging"""
        try:
            strategy = self.strategies[strategy_name]
            trade = self.active_trades[strategy_name]
            config = self.config["strategies"][strategy_name]

            capital_before = strategy.capital

            # Apply exit slippage (strategy3 and any config with slippage)
            slippage_val = 0.0
            if config.get("slippage"):
                slippage_val = config.get("slippage", 0.0003)
                if strategy.direction == "long":
                    exit_price *= 1 - slippage_val
                else:
                    exit_price *= 1 + slippage_val

            # Calculate PnL (guard against None direction)
            if strategy.direction == "long":
                pnl = (exit_price - strategy.entry_price) * strategy.units
            elif strategy.direction == "short":
                pnl = (strategy.entry_price - exit_price) * strategy.units
            else:
                logger.warning(f"⚠️ [{strategy_name}] Unknown direction: {strategy.direction}, defaulting to long PnL")
                pnl = (exit_price - strategy.entry_price) * strategy.units

            # Deduct fees
            fee = abs(exit_price * strategy.units) * config.get("fee", 0.0004)
            pnl -= fee

            # Funding cost (strategy3 or any config with funding_rate)
            funding_cost = 0.0
            if config.get("funding_rate") and trade.entry_time:
                # Estimate bars held (8h funding periods) from trade duration
                entry_dt = trade.entry_time
                # Handle both naive and aware datetimes from SQLite
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                hold_seconds = (datetime.now(timezone.utc) - entry_dt).total_seconds()
                bars_held = hold_seconds / 3600  # approximate hours held
                funding_cost = (
                    abs(strategy.entry_price * strategy.units)
                    * config.get("funding_rate", 0.0001)
                    * (bars_held / 8)
                )
                pnl -= funding_cost

            # Update strategy capital
            strategy.capital += pnl
            capital_after = strategy.capital

            logger.info(
                f"💰 [{strategy_name}] Trade closed: pnl=${pnl:.2f}, "
                f"capital: ${capital_before:.2f} → ${capital_after:.2f}"
            )
            strategy.in_trade = False

            # Update trade record
            trade.exit_price = exit_price
            trade.exit_time = datetime.now(timezone.utc)
            trade.pnl = pnl
            trade.exit_reason = exit_reason
            trade.fee = fee
            trade.slippage = slippage_val
            trade.funding_cost = funding_cost
            trade.status = "closed"

            logger.info(f"📝 [{strategy_name}] Committing trade closure: trade_id={trade.id}")
            self.db.commit()
            log_database_operation("COMMIT", True, f"Trade #{trade.id} closed")
            logger.info(f"✅ [{strategy_name}] Trade #{trade.id} committed to DB successfully")

            # Log trade closure with detailed P&L
            log_trade_closure(
                strategy_name=strategy_name,
                exit_price=exit_price,
                exit_reason=exit_reason,
                pnl=pnl,
                entry_price=strategy.entry_price,
                capital_before=capital_before,
                capital_after=capital_after,
                symbol=self.symbol,
            )

            # Remove from active trades
            del self.active_trades[strategy_name]

            # Log strategy state update
            log_strategy_state_change(
                strategy_name,
                {
                    'in_trade': (True, False),
                    'capital': (capital_before, capital_after),
                }
            )
            
        except Exception as e:
            logger.error(f"❌ [{strategy_name}] Failed to close trade: {e}")
            logger.error(traceback.format_exc())
            self.db.rollback()
            raise

    def _check_live_signal(
        self, strategy_name: str, live_price: float, df: pd.DataFrame
    ) -> Optional[str]:
        """Simplified live signal checking using current market conditions"""
        if len(df) < 2:
            return None

        strategy = self.strategies[strategy_name]
        strat_config = self.config["strategies"][strategy_name]

        # Get current indicators from latest completed candle
        df_with_indicators = IndicatorEngine.compute_all_indicators(
            df.copy(), strat_config
        )
        current_row = df_with_indicators.iloc[-1]

        # Simple price action signals for live trading
        prev_close = df.iloc[-2]["close"]

        # Bullish signal: price breaking above recent high with volume
        if (
            live_price > current_row.get("hh20", prev_close)
            and live_price > prev_close
            and current_row.get("volume", 0) > current_row.get("vol_ma20", 0)
        ):
            return "long"

        # Bearish signal: price breaking below recent low
        elif (
            live_price < current_row.get("ll20", prev_close) and live_price < prev_close
        ):
            return "short"

        return None

    def _open_live_trade(
        self, strategy_name: str, signal: str, live_price: float, config: Dict
    ):
        """Open a new live paper trade with detailed logging"""
        strategy = self.strategies[strategy_name]

        try:
            # Log strategy state before trade
            logger.info(f"🎯 Opening live trade for {strategy_name}: {signal.upper()}")
            logger.info(f"   Current state: capital=${strategy.capital:.2f}, in_trade={strategy.in_trade}")

            # Use ATR-based stop loss
            risk_pct = config.get("risk_pct", 0.01)
            risk_amt = strategy.capital * risk_pct

            # Use ATR from config defaults for stop distance (since we may not have
            # the latest bar's ATR in live mode — fallback to percentage)
            # Estimate ATR as ~1.5% of price for BTC (reasonable default)
            estimated_atr_pct = 0.015
            stop_dist = live_price * estimated_atr_pct * config.get("atr_sl_mult", 2.0)
            units = risk_amt / stop_dist

            # Apply slippage
            slippage = config.get("slippage", 0.0003)
            entry_price = live_price
            if signal == "long":
                entry_price *= 1 + slippage
            else:
                entry_price *= 1 - slippage

            # Calculate SL/TP using ATR-based distances
            if signal == "long":
                stop_loss = entry_price - stop_dist
                tp_mult = config.get("atr_tp_mult", config.get("reward_ratio", 2.0))
                take_profit = entry_price + tp_mult * stop_dist
            else:  # short
                stop_loss = entry_price + stop_dist
                tp_mult = config.get("atr_tp_mult", config.get("reward_ratio", 2.0))
                take_profit = entry_price - tp_mult * stop_dist

            # Log trade attempt
            log_trade_attempt(
                strategy_name=strategy_name,
                signal=signal,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                units=units,
                capital=strategy.capital,
                risk_pct=risk_pct,
                symbol=self.symbol,
                trade_type="live",
                extra_details={
                    "Slippage": slippage,
                    "Stop Distance": f"${stop_dist:.2f}",
                }
            )

            # Create trade record
            trade = Trade(
                strategy_name=strategy_name,
                symbol=self.symbol.upper(),
                direction=signal,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                units=units,
                entry_time=datetime.now(timezone.utc),
                status="open",
            )

            self.db.add(trade)
            self.db.commit()
            self.db.refresh(trade)
            
            log_database_operation("COMMIT", True, f"Trade #{trade.id} created")
            log_trade_result(
                strategy_name=strategy_name,
                success=True,
                trade_id=trade.id,
                message=f"Live trade opened: {signal.upper()} @ ${entry_price:,.2f}"
            )

            # Update strategy state
            old_state = {
                'in_trade': (strategy.in_trade, True),
                'entry_price': (strategy.entry_price, entry_price),
                'stop_loss': (strategy.stop_loss, stop_loss),
                'take_profit': (strategy.take_profit, take_profit),
                'units': (strategy.units, units),
                'direction': (strategy.direction, signal),
            }
            
            strategy.in_trade = True
            strategy.entry_price = entry_price
            strategy.stop_loss = stop_loss
            strategy.take_profit = take_profit
            strategy.units = units
            strategy.direction = signal

            self.active_trades[strategy_name] = trade
            
            # Log state changes
            log_strategy_state_change(strategy_name, old_state)

            # persist strategy status (enabled state only — in_trade tracked via Trade table)
            db_status = (
                self.db.query(StrategyStatus).filter_by(strategy_name=strategy_name).first()
            )
            if db_status:
                self.db.commit()
                log_database_operation("COMMIT", True, f"StrategyStatus verified for {strategy_name}")


            logger.info(
                f"✅ [{strategy_name}] Live trade opened: {signal.upper()} @ ${entry_price:,.2f} | "
                f"SL: ${stop_loss:,.2f} | TP: ${take_profit:,.2f} | Units: {units:.4f}"
            )
            
        except Exception as e:
            log_trade_result(
                strategy_name=strategy_name,
                success=False,
                error=e,
                message=f"Failed to open live trade: {str(e)}",
                stack_trace=traceback.format_exc()
            )
            self.db.rollback()
            logger.error(f"❌ [{strategy_name}] Failed to open live trade: {e}")
            raise

    def _open_trade(
        self, strategy_name: str, signal: str, df: pd.DataFrame, idx: int, config: Dict
    ):
        """Open a new paper trade with detailed logging"""
        strategy = self.strategies[strategy_name]
        
        try:
            row = df.iloc[idx]

            # Calculate stop distance
            stop_dist = config.get("atr_sl_mult", 1.5) * row["atr"]
            if stop_dist <= 0:
                logger.warning(f"[{strategy_name}] Invalid stop distance: {stop_dist}")
                log_trade_result(
                    strategy_name=strategy_name,
                    success=False,
                    message=f"Invalid stop distance: ${stop_dist:.2f}"
                )
                return

            # Risk amount
            risk_pct = config.get("risk_pct", 0.01)
            risk_amt = strategy.capital * risk_pct
            units = risk_amt / stop_dist

            # Entry price (with slippage for strategy3)
            entry_price = row["open"]
            slippage_cost = 0.0

            if strategy_name == "strategy3":
                slippage_cfg = config.get("slippage", 0.0003)
                if signal == "long":
                    entry_price *= 1 + slippage_cfg
                else:
                    entry_price *= 1 - slippage_cfg
                slippage_cost = entry_price * units * slippage_cfg

            # Calculate SL/TP
            if signal == "long":
                stop_loss = entry_price - stop_dist
                take_profit = (
                    entry_price
                    + config.get("reward_ratio", config.get("atr_tp_mult", 3.0)) * stop_dist
                )
            else:  # short
                stop_loss = entry_price + stop_dist
                take_profit = (
                    entry_price
                    - config.get("reward_ratio", config.get("atr_tp_mult", 3.0)) * stop_dist
                )

            # Log trade attempt
            log_trade_attempt(
                strategy_name=strategy_name,
                signal=signal,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                units=units,
                capital=strategy.capital,
                risk_pct=risk_pct,
                symbol=self.symbol,
                trade_type="historical",
                extra_details={
                    "Bar Index": idx,
                    "ATR": row["atr"],
                    "Stop Distance (ATR)": f"{config.get('atr_sl_mult', 1.5)}x",
                    "Slippage Cost": f"${slippage_cost:.2f}" if strategy_name == "strategy3" else "$0",
                }
            )

            # Create trade record
            trade = Trade(
                strategy_name=strategy_name,
                symbol=self.symbol.upper(),
                direction=signal,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                units=units,
                entry_time=datetime.now(timezone.utc),
                status="open",
            )

            self.db.add(trade)
            self.db.commit()
            self.db.refresh(trade)
            
            log_database_operation("COMMIT", True, f"Trade #{trade.id} created")
            log_trade_result(
                strategy_name=strategy_name,
                success=True,
                trade_id=trade.id,
                message=f"Historical trade opened: {signal.upper()} @ ${entry_price:,.2f}"
            )

            # Track active trade
            old_state = {
                'in_trade': (strategy.in_trade, True),
                'entry_price': (strategy.entry_price, entry_price),
                'entry_idx': (strategy.entry_idx, idx),
            }
            
            strategy.in_trade = True
            strategy.entry_price = entry_price
            strategy.stop_loss = stop_loss
            strategy.take_profit = take_profit
            strategy.units = units
            strategy.entry_idx = idx
            strategy.direction = signal

            self.active_trades[strategy_name] = trade
            
            # Log state changes
            log_strategy_state_change(strategy_name, old_state)

            logger.info(
                f"✅ [{strategy_name}] Trade opened: {signal.upper()} @ ${entry_price:,.2f} | "
                f"SL: ${stop_loss:,.2f} | TP: ${take_profit:,.2f} | Size: {units:.4f}"
            )
            
        except Exception as e:
            log_trade_result(
                strategy_name=strategy_name,
                success=False,
                error=e,
                message=f"Failed to open trade: {str(e)}",
                stack_trace=traceback.format_exc()
            )
            self.db.rollback()
            logger.error(f"❌ [{strategy_name}] Failed to open trade: {e}")
            raise

    def _manage_trade(self, strategy_name: str, df: pd.DataFrame, current_idx: int):
        """Manage an active trade - check for exits"""
        strategy = self.strategies[strategy_name]
        trade = self.active_trades[strategy_name]
        config = self.config["strategies"][strategy_name]

        row = df.iloc[current_idx]
        bars_held = current_idx - strategy.entry_idx

        exit_price = None
        exit_reason = None

        if strategy.direction == "long":
            # Check stop loss
            if row["low"] <= strategy.stop_loss:
                exit_price = strategy.stop_loss
                exit_reason = "sl_hit"
            # Check take profit
            elif row["high"] >= strategy.take_profit:
                exit_price = strategy.take_profit
                exit_reason = "tp_hit"
            # Check timeout (strategy1 only)
            elif strategy_name == "strategy1" and bars_held >= config.get(
                "max_hold_hours", 24
            ):
                exit_price = row["close"]
                exit_reason = "timeout"
        elif strategy.direction == "short":
            # Check stop loss
            if row["high"] >= strategy.stop_loss:
                exit_price = strategy.stop_loss
                exit_reason = "sl_hit"
            # Check take profit
            elif row["low"] <= strategy.take_profit:
                exit_price = strategy.take_profit
                exit_reason = "tp_hit"
        # If direction is None, skip — no valid trade state

        if exit_price:
            # Apply exit slippage (strategy3)
            slippage_val = 0.0
            if strategy_name == "strategy3":
                slippage_val = config.get("slippage", 0.0003)
                if strategy.direction == "long":
                    exit_price *= 1 - slippage_val
                else:
                    exit_price *= 1 + slippage_val

            # Calculate PnL (guard against None direction)
            if strategy.direction == "long":
                pnl = (exit_price - strategy.entry_price) * strategy.units
            elif strategy.direction == "short":
                pnl = (strategy.entry_price - exit_price) * strategy.units
            else:
                logger.warning(f"⚠️ [{strategy_name}] Unknown direction: {strategy.direction}")
                pnl = 0.0

            # Deduct fees
            fee = abs(exit_price * strategy.units) * config.get("fee", 0.0004)
            pnl -= fee

            # Funding cost (strategy3, 8h periods)
            funding_cost = 0.0
            if strategy_name == "strategy3":
                funding_cost = (
                    abs(strategy.entry_price * strategy.units)
                    * config.get("funding_rate", 0.0001)
                    * (bars_held / 8)
                )
                pnl -= funding_cost

            # Update strategy capital
            strategy.capital += pnl
            strategy.in_trade = False

            # Update trade record
            trade.exit_price = exit_price
            trade.exit_time = datetime.now(timezone.utc)
            trade.pnl = pnl
            trade.exit_reason = exit_reason
            trade.fee = fee
            trade.slippage = slippage_val
            trade.funding_cost = funding_cost
            trade.status = "closed"

            self.db.commit()

            # Remove from active trades
            del self.active_trades[strategy_name]

    def _record_equity(self, strategy_name: str):
        """Record equity snapshot"""
        strategy = self.strategies[strategy_name]

        snapshot = EquitySnapshot(
            strategy_name=strategy_name,
            equity=strategy.capital,
            timestamp=datetime.now(timezone.utc),
        )
        self.db.add(snapshot)
        self.db.commit()

    def get_performance_metrics(self) -> Dict[str, Any]:
        """Calculate performance metrics for all strategies"""
        metrics = {}

        for name, strategy in self.strategies.items():
            # Fetch equity history from DB
            snapshots = (
                self.db.query(EquitySnapshot)
                .filter_by(strategy_name=name)
                .order_by(EquitySnapshot.timestamp)
                .all()
            )

            if len(snapshots) < 2:
                metrics[name] = {
                    "total_return": 0,
                    "cagr": 0,
                    "sharpe": 0,
                    "max_drawdown": 0,
                    "trade_count": 0,
                    "current_equity": strategy.capital,
                    "initial_capital": strategy.initial_capital,
                }
                continue

            equity_curve = pd.Series([s.equity for s in snapshots])

            total_return = (strategy.capital / strategy.initial_capital - 1) * 100

            # CAGR
            hours = len(snapshots)
            years = hours / (24 * 365)
            cagr = (
                (strategy.capital / strategy.initial_capital) ** (1 / years) - 1
                if years > 0
                else 0
            )

            # Sharpe
            returns = equity_curve.pct_change().dropna()
            sharpe = (
                (returns.mean() / returns.std()) * np.sqrt(24 * 365)
                if returns.std() > 0
                else 0
            )

            # Max Drawdown
            peak = equity_curve.cummax()
            drawdown = (peak - equity_curve) / peak
            max_dd = drawdown.max() * 100

            # Trade count
            trades = (
                self.db.query(Trade)
                .filter_by(strategy_name=name, status="closed")
                .count()
            )

            metrics[name] = {
                "total_return": round(total_return, 2),
                "cagr": round(cagr * 100, 2),
                "sharpe": round(sharpe, 2),
                "max_drawdown": round(max_dd, 2),
                "trade_count": trades,
                "current_equity": round(strategy.capital, 2),
                "initial_capital": strategy.initial_capital,
            }

        return metrics

    def get_trade_analytics(self) -> Dict[str, Any]:
        """Get detailed trade analytics for all strategies"""
        analytics = {}

        for name, strategy in self.strategies.items():
            trades = (
                self.db.query(Trade)
                .filter_by(strategy_name=name, status="closed")
                .all()
            )

            if not trades:
                analytics[name] = {
                    "total_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate": 0,
                    "profit_factor": 0,
                    "avg_win": 0,
                    "avg_loss": 0,
                    "avg_trade_pnl": 0,
                    "best_trade": 0,
                    "worst_trade": 0,
                    "avg_hold_bars": 0,
                    "max_consecutive_losses": 0,
                    "long_trades": 0,
                    "short_trades": 0,
                    "sl_hits": 0,
                    "tp_hits": 0,
                    "timeouts": 0,
                    "total_fees": 0,
                    "total_slippage": 0,
                    "total_funding": 0,
                    "net_pnl": 0,
                    "gross_pnl": 0,
                }
                continue

            wins = [t for t in trades if t.pnl and t.pnl > 0]
            losses = [t for t in trades if t.pnl and t.pnl <= 0]
            win_rate = (len(wins) / len(trades)) * 100 if trades else 0

            gross_pnl = sum(t.pnl or 0 for t in trades)
            total_fees = sum(t.fee or 0 for t in trades)
            total_slippage = sum(t.slippage or 0 for t in trades)
            total_funding = sum(t.funding_cost or 0 for t in trades)

            avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
            avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0

            profit_factor = (
                abs(sum(t.pnl for t in wins) / sum(t.pnl for t in losses))
                if losses and sum(t.pnl for t in losses) != 0
                else (999 if wins else 0)
            )

            # Max consecutive losses
            max_consec = 0
            current_consec = 0
            for t in sorted(trades, key=lambda x: x.entry_time):
                if t.pnl and t.pnl <= 0:
                    current_consec += 1
                    max_consec = max(max_consec, current_consec)
                else:
                    current_consec = 0

            # Average hold bars (approximation via bars_held)
            hold_bars = []
            for t in trades:
                if t.entry_time and t.exit_time:
                    hours = (t.exit_time - t.entry_time).total_seconds() / 3600
                    hold_bars.append(max(1, int(hours)))
            avg_hold = sum(hold_bars) / len(hold_bars) if hold_bars else 0

            analytics[name] = {
                "total_trades": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(win_rate, 1),
                "profit_factor": round(profit_factor, 2),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "avg_trade_pnl": round(gross_pnl / len(trades), 2),
                "best_trade": round(max((t.pnl or 0) for t in trades), 2),
                "worst_trade": round(min((t.pnl or 0) for t in trades), 2),
                "avg_hold_bars": round(avg_hold, 1),
                "max_consecutive_losses": max_consec,
                "long_trades": len([t for t in trades if t.direction == "long"]),
                "short_trades": len([t for t in trades if t.direction == "short"]),
                "sl_hits": len([t for t in trades if t.exit_reason == "sl_hit"]),
                "tp_hits": len([t for t in trades if t.exit_reason == "tp_hit"]),
                "timeouts": len([t for t in trades if t.exit_reason == "timeout"]),
                "total_fees": round(total_fees, 2),
                "total_slippage": round(total_slippage, 2),
                "total_funding": round(total_funding, 2),
                "net_pnl": round(gross_pnl, 2),
                "gross_pnl": round(
                    gross_pnl + total_fees + total_slippage + total_funding, 2
                ),
            }

        return analytics

    def get_drawdown_series(self, limit: int = 200) -> Dict[str, List]:
        """Get drawdown time series for charting"""
        result = {}

        for name, strategy in self.strategies.items():
            snapshots = (
                self.db.query(EquitySnapshot)
                .filter_by(strategy_name=name)
                .order_by(EquitySnapshot.timestamp)
                .all()
            )

            if len(snapshots) < 2:
                result[name] = []
                continue

            equity_curve = pd.Series([s.equity for s in snapshots])
            peak = equity_curve.cummax()
            drawdown = ((peak - equity_curve) / peak * 100).tolist()

            timestamps = [s.timestamp.isoformat() for s in snapshots]
            result[name] = [
                {"timestamp": t, "drawdown": round(d, 2)}
                for t, d in zip(timestamps, drawdown)
            ]

        return result

    def get_recent_signals(self, limit: int = 50) -> List[Dict]:
        """Get recent signal data"""
        signals = (
            self.db.query(Signal).order_by(Signal.timestamp.desc()).limit(limit).all()
        )

        return [
            {
                "id": s.id,
                "strategy": s.strategy_name,
                "symbol": s.symbol,
                "signal_type": s.signal_type,
                "timestamp": s.timestamp.isoformat(),
                "indicators": s.indicators,
            }
            for s in signals
        ]

    def get_active_trades(self) -> List[Dict]:
        """Get all active trades with live P&L."""
        trades = []
        for name, trade in self.active_trades.items():
            strategy = self.strategies[name]
            live_price = self.last_live_price
            unrealized_pnl = 0.0
            if live_price > 0 and trade.entry_price > 0:
                if strategy.direction == "long":
                    unrealized_pnl = (live_price - trade.entry_price) * trade.units
                else:
                    unrealized_pnl = (trade.entry_price - live_price) * trade.units
            trades.append(
                {
                    "strategy": name,
                    "symbol": trade.symbol,
                    "direction": trade.direction,
                    "entry_price": trade.entry_price,
                    "live_price": live_price,
                    "stop_loss": trade.stop_loss,
                    "take_profit": trade.take_profit,
                    "units": trade.units,
                    "entry_time": trade.entry_time.isoformat()
                    if trade.entry_time
                    else None,
                    "unrealized_pnl": round(unrealized_pnl, 2),
                    "unrealized_pnl_pct": round(
                        (unrealized_pnl / (trade.entry_price * trade.units)) * 100, 2
                    )
                    if trade.entry_price > 0 and trade.units > 0
                    else 0,
                }
            )
        return trades

    def get_trade_history(
        self, strategy_name: str = None, limit: int = 48
    ) -> List[Dict]:
        """Get trade history"""
        query = self.db.query(Trade).filter_by(status="closed")
        if strategy_name:
            query = query.filter_by(strategy_name=strategy_name)

        trades = query.order_by(Trade.entry_time.desc()).limit(limit).all()

        return [
            {
                "id": t.id,
                "strategy": t.strategy_name,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "units": t.units,
                "pnl": t.pnl,
                "exit_reason": t.exit_reason,
                "fee": t.fee,
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
            }
            for t in trades
        ]

    def toggle_strategy(self, strategy_name: str, enabled: bool):
        """Enable/disable a strategy"""
        if strategy_name in self.strategies:
            self.strategies[strategy_name].enabled = enabled

            db_status = (
                self.db.query(StrategyStatus)
                .filter_by(strategy_name=strategy_name)
                .first()
            )

            if db_status:
                db_status.enabled = enabled
                db_status.last_updated = datetime.now(timezone.utc)
            else:
                self.db.add(
                    StrategyStatus(strategy_name=strategy_name, enabled=enabled)
                )

            self.db.commit()
            status = "ENABLED" if enabled else "DISABLED"

    def get_equity_curve(
        self, strategy_name: str = None, limit: int = 500
    ) -> Dict[str, List]:
        """Get equity curve data for charting"""
        if strategy_name:
            snapshots = (
                self.db.query(EquitySnapshot)
                .filter_by(strategy_name=strategy_name)
                .order_by(EquitySnapshot.timestamp.desc())
                .limit(limit)
                .all()
            )

            return {
                strategy_name: [
                    {"timestamp": s.timestamp.isoformat(), "equity": s.equity}
                    for s in reversed(snapshots)
                ]
            }
        else:
            result = {}
            for name in self.strategies:
                snapshots = (
                    self.db.query(EquitySnapshot)
                    .filter_by(strategy_name=name)
                    .order_by(EquitySnapshot.timestamp.desc())
                    .limit(limit)
                    .all()
                )

                result[name] = [
                    {"timestamp": s.timestamp.isoformat(), "equity": s.equity}
                    for s in reversed(snapshots)
                ]

            return result
