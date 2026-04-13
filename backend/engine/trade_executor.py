"""
Trade Executor - Comprehensive trade execution with detailed logging
Logs every step: signal validation, risk calculations, DB operations, state changes
"""

import logging
import traceback
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class TradeExecutionResult:
    """Container for trade execution result"""
    def __init__(self):
        self.success = False
        self.trade_id = None
        self.message = ""
        self.details = {}
        self.error = None
        self.stack_trace = None


def log_trade_attempt(
    strategy_name: str,
    signal: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    units: float,
    capital: float,
    risk_pct: float,
    symbol: str = "BTCUSDT",
    trade_type: str = "live",  # "live" or "historical"
    extra_details: Optional[Dict[str, Any]] = None,
):
    """
    Log a complete trade attempt with all details.
    
    Returns formatted log string for visibility.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    # Calculate risk metrics
    risk_amount = capital * risk_pct
    if signal == "long":
        risk_distance = entry_price - stop_loss
        reward_distance = take_profit - entry_price
    else:  # short
        risk_distance = stop_loss - entry_price
        reward_distance = entry_price - take_profit
    
    rr_ratio = reward_distance / risk_distance if risk_distance > 0 else 0
    
    # Build log message
    log_lines = [
        "=" * 80,
        f"🎯 TRADE ATTEMPT | {timestamp} | {strategy_name} | {trade_type.upper()}",
        f"📊 Signal: {signal.upper()} | Price: ${entry_price:,.2f} | Symbol: {symbol}",
        f"📊 Strategy State: capital=${capital:,.2f} | risk={risk_pct*100:.2f}% | units={units:.4f}",
        "💰 Risk Calculation:",
        f"   - Risk Amount: ${risk_amount:.2f} ({risk_pct*100:.2f}% of capital)",
        f"   - Stop Distance: ${risk_distance:.2f}",
        f"   - Position Size: {units:.4f} units",
        "📝 Trade Details:",
        f"   - Entry: ${entry_price:,.2f}",
        f"   - Stop Loss: ${stop_loss:,.2f}",
        f"   - Take Profit: ${take_profit:,.2f}",
        f"   - R:R Ratio: {rr_ratio:.2f}",
    ]
    
    if extra_details:
        log_lines.append("🔍 Additional Details:")
        for key, value in extra_details.items():
            log_lines.append(f"   - {key}: {value}")
    
    log_lines.append("=" * 80)
    
    log_message = "\n".join(log_lines)
    
    # Log at INFO level
    logger.info(log_message)
    
    # Also print to console for immediate visibility
    print(log_message)
    
    return log_message


def log_trade_result(
    strategy_name: str,
    success: bool,
    trade_id: Optional[int] = None,
    message: str = "",
    error: Optional[Exception] = None,
    stack_trace: Optional[str] = None,
):
    """
    Log the result of a trade execution attempt.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    if success:
        log_lines = [
            "=" * 80,
            f"✅ TRADE OPENED | {timestamp} | {strategy_name}",
            f"📝 Trade ID: #{trade_id}",
            f"💾 Database: Commit successful",
            f"✅ {message if message else 'Trade opened successfully'}",
            "=" * 80,
        ]
        log_message = "\n".join(log_lines)
        logger.info(log_message)
        print(log_message)
    else:
        log_lines = [
            "=" * 80,
            f"❌ TRADE FAILED | {timestamp} | {strategy_name}",
            f"💾 Database: Commit failed or error occurred",
            f"❌ Error: {message if message else 'Unknown error'}",
        ]
        
        if error:
            log_lines.append(f"🔍 Exception Type: {type(error).__name__}")
            log_lines.append(f"🔍 Exception Message: {str(error)}")
        
        if stack_trace:
            log_lines.append("📚 Stack Trace:")
            log_lines.append(stack_trace)
        
        log_lines.append("=" * 80)
        
        log_message = "\n".join(log_lines)
        logger.error(log_message)
        print(log_message)
    
    return log_message


def log_strategy_state_change(
    strategy_name: str,
    changes: Dict[str, tuple],
):
    """
    Log strategy state changes when opening/closing trades.
    
    Args:
        strategy_name: Strategy name
        changes: Dict of {param_name: (old_value, new_value)}
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    log_lines = [
        f"🔄 Strategy State Update | {timestamp} | {strategy_name}",
    ]
    
    for param, (old_val, new_val) in changes.items():
        log_lines.append(f"   - {param}: {old_val} → {new_val}")
    
    log_message = " | ".join(log_lines)
    logger.info(log_message)
    print(log_message)
    
    return log_message


def log_database_operation(
    operation: str,
    success: bool,
    message: str = "",
    error: Optional[Exception] = None,
):
    """
    Log database operations (commit, refresh, etc.)
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    if success:
        log_message = f"💾 Database | {timestamp} | {operation}: ✅ {message}"
        logger.info(log_message)
    else:
        log_message = f"💾 Database | {timestamp} | {operation}: ❌ {message}"
        if error:
            log_message += f" | Error: {str(error)}"
        logger.error(log_message)
    
    print(log_message)
    return log_message


def log_trade_closure(
    strategy_name: str,
    exit_price: float,
    exit_reason: str,
    pnl: float,
    entry_price: float,
    capital_before: float,
    capital_after: float,
    symbol: str = "BTCUSDT",
):
    """
    Log trade closure with P&L details.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    pnl_pct = ((capital_after - capital_before) / capital_before * 100) if capital_before > 0 else 0
    
    log_lines = [
        "=" * 80,
        f"🔴 TRADE CLOSED | {timestamp} | {strategy_name}",
        f"📊 Signal: {exit_reason.upper()} | Exit Price: ${exit_price:,.2f} | Symbol: {symbol}",
        f"📊 Trade Details:",
        f"   - Entry: ${entry_price:,.2f}",
        f"   - Exit: ${exit_price:,.2f}",
        f"   - Exit Reason: {exit_reason}",
        f"💰 P&L:",
        f"   - Trade P&L: ${pnl:+,.2f}",
        f"   - Capital: ${capital_before:,.2f} → ${capital_after:,.2f} ({pnl_pct:+.2f}%)",
        "=" * 80,
    ]
    
    log_message = "\n".join(log_lines)
    logger.info(log_message)
    print(log_message)
    
    return log_message


def safe_execute_trade(func, strategy_name: str, *args, **kwargs) -> TradeExecutionResult:
    """
    Safely execute a trade operation with comprehensive error handling and logging.
    
    Args:
        func: The function to execute (e.g., _open_trade)
        strategy_name: Strategy name for logging
        *args, **kwargs: Arguments to pass to the function
    
    Returns:
        TradeExecutionResult with success/failure details
    """
    result = TradeExecutionResult()
    
    try:
        # Log attempt
        logger.info(f"🎯 Executing trade for {strategy_name}...")
        
        # Execute the trade
        trade_result = func(*args, **kwargs)
        
        # Success
        result.success = True
        result.message = "Trade executed successfully"
        
        return result
        
    except Exception as e:
        # Capture error details
        result.success = False
        result.error = e
        result.message = str(e)
        result.stack_trace = traceback.format_exc()
        
        # Log error
        log_trade_result(
            strategy_name=strategy_name,
            success=False,
            error=e,
            message=str(e),
            stack_trace=result.stack_trace,
        )
        
        logger.error(f"❌ Trade execution failed for {strategy_name}: {e}")
        logger.error(traceback.format_exc())
        
        return result
