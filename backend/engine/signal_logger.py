"""
Signal Logger - Logs buy/sell signals with timestamps and conditions
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def log_signal(
    strategy_name: str,
    signal: str,
    price: float,
    timestamp: Optional[datetime] = None,
    conditions: Optional[Dict[str, Any]] = None,
    symbol: str = "BTCUSDT",
):
    """
    Log a trading signal with timestamp and conditions.
    
    Args:
        strategy_name: Name of the strategy (e.g., 'strategy1')
        signal: Signal type ('long' or 'short')
        price: Current price when signal was generated
        timestamp: When the signal occurred (defaults to now)
        conditions: Dict of condition names and whether they were met
        symbol: Trading symbol
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    
    # Format timestamp
    ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    
    # Format signal type
    signal_str = signal.upper()
    signal_emoji = "🟢" if signal == "long" else "🔴"
    
    # Build conditions string
    conditions_str = ""
    if conditions:
        condition_parts = []
        for cond_name, cond_value in conditions.items():
            if isinstance(cond_value, dict):
                # Handle detailed condition dicts
                is_met = cond_value.get('met', False)
                check_mark = "✓" if is_met else "✗"
                condition_parts.append(f"{cond_name} {check_mark}")
            elif isinstance(cond_value, bool):
                check_mark = "✓" if cond_value else "✗"
                condition_parts.append(f"{cond_name} {check_mark}")
            else:
                condition_parts.append(f"{cond_name}: {cond_value}")
        conditions_str = " | ".join(condition_parts)
    
    # Build log message
    if conditions_str:
        log_msg = (
            f"\n{'='*80}\n"
            f"{signal_emoji} SIGNAL DETECTED | {ts_str} | {strategy_name} | {signal_str} | "
            f"Price: ${price:,.2f} | {symbol}\n"
            f"   Conditions: {conditions_str}\n"
            f"{'='*80}"
        )
    else:
        log_msg = (
            f"\n{'='*80}\n"
            f"{signal_emoji} SIGNAL DETECTED | {ts_str} | {strategy_name} | {signal_str} | "
            f"Price: ${price:,.2f} | {symbol}\n"
            f"{'='*80}"
        )
    
    # Log at INFO level so it's always visible
    logger.info(log_msg)
    
    # Also print to console for immediate visibility
    print(log_msg)


def log_signal_check(
    strategy_name: str,
    price: float,
    timestamp: Optional[datetime] = None,
    conditions: Optional[Dict[str, Any]] = None,
    signal: Optional[str] = None,
):
    """
    Log a signal check attempt (whether or not a signal was generated).
    Useful for debugging why signals didn't fire.
    
    Args:
        strategy_name: Name of the strategy
        price: Current price
        timestamp: When the check occurred
        conditions: Dict of condition names and their values
        signal: Signal generated (if any)
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    
    ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    
    if signal:
        # Signal was generated - use the main log_signal function
        log_signal(strategy_name, signal, price, timestamp, conditions)
        return
    
    # No signal - log why (only in debug mode)
    if conditions and logger.isEnabledFor(logging.DEBUG):
        condition_parts = []
        for cond_name, cond_value in conditions.items():
            if isinstance(cond_value, dict):
                is_met = cond_value.get('met', False)
                check_mark = "✓" if is_met else "✗"
                condition_parts.append(f"{cond_name} {check_mark}")
            elif isinstance(cond_value, bool):
                check_mark = "✓" if cond_value else "✗"
                condition_parts.append(f"{cond_name} {check_mark}")
            else:
                condition_parts.append(f"{cond_name}: {cond_value}")
        
        conditions_str = " | ".join(condition_parts)
        
        debug_msg = (
            f"🔍 Signal Check (NO SIGNAL) | {ts_str} | {strategy_name} | "
            f"Price: ${price:,.2f}\n"
            f"   Conditions: {conditions_str}"
        )
        logger.debug(debug_msg)
