"""
Real-time monitoring and logging system for paper trading operations
Tracks all backend activities: API calls, signal generation, trade execution, etc.
"""

import json
from datetime import datetime
from typing import List, Dict, Any
from collections import deque
from enum import Enum
import threading
import numpy as np


def convert_numpy_types(obj):
    """Recursively convert numpy types to native Python types for JSON serialization"""
    if isinstance(obj, (np.integer, np.floating, np.bool_)):
        # Handle numpy scalar types (int, float, bool)
        return obj.item()
    elif isinstance(obj, np.ndarray):
        # Convert numpy arrays to lists
        return [convert_numpy_types(item) for item in obj]
    elif isinstance(obj, dict):
        # Recursively convert dictionary values
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        # Recursively convert list/tuple elements
        converted = [convert_numpy_types(item) for item in obj]
        return type(obj)(converted)
    else:
        return obj


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"


class OperationType(str, Enum):
    API_CALL = "API_CALL"
    DATA_FETCH = "DATA_FETCH"
    INDICATOR_CALC = "INDICATOR_CALC"
    SIGNAL_CHECK = "SIGNAL_CHECK"
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    TRADE_OPEN = "TRADE_OPEN"
    TRADE_CLOSE = "TRADE_CLOSE"
    ERROR = "ERROR"
    WARNING = "WARNING"
    SYSTEM = "SYSTEM"
    TERMINAL = "TERMINAL"


class Monitor:
    """Central monitoring system for all backend operations"""

    def __init__(self, max_logs: int = 2000):
        self.logs: deque = deque(maxlen=max_logs)
        self.lock = threading.Lock()
        self.current_cycle = None
        self.cycle_start_time = None
        self.api_call_count = 0
        self.signal_checks = 0
        self.trades_opened = 0
        self.trades_closed = 0

    def log(
        self,
        level: LogLevel,
        operation_type: OperationType,
        message: str,
        strategy: str = None,
        data: Dict[str, Any] = None,
    ) -> None:
        """Log an operation"""
        with self.lock:
            # Convert data to native types to avoid numpy serialization issues
            if data:
                data = convert_numpy_types(data)

            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": level.value,
                "type": operation_type.value,
                "message": message,
                "strategy": strategy,
                "data": data or {},
                "cycle": self.current_cycle,
            }

            self.logs.append(log_entry)

            # Update counters
            if operation_type == OperationType.API_CALL:
                self.api_call_count += 1
            elif operation_type == OperationType.SIGNAL_CHECK:
                self.signal_checks += 1
            elif operation_type == OperationType.TRADE_OPEN:
                self.trades_opened += 1
            elif operation_type == OperationType.TRADE_CLOSE:
                self.trades_closed += 1

    def start_cycle(self, cycle_id: int) -> None:
        """Mark the start of a processing cycle"""
        with self.lock:
            self.current_cycle = cycle_id
            self.cycle_start_time = datetime.utcnow()

        self.log(
            LogLevel.INFO,
            OperationType.SYSTEM,
            f"Starting processing cycle #{cycle_id}",
        )

    def end_cycle(self) -> None:
        """Mark the end of a processing cycle"""
        if self.cycle_start_time:
            duration = (datetime.utcnow() - self.cycle_start_time).total_seconds()
            self.log(
                LogLevel.INFO,
                OperationType.SYSTEM,
                f"Cycle #{self.current_cycle} completed in {duration:.2f}s",
            )

    def get_logs(
        self, limit: int = 100, level: str = None, strategy: str = None
    ) -> List[Dict]:
        """Fetch logs with filtering"""
        with self.lock:
            logs = list(self.logs)

        # Filter by level
        if level:
            logs = [l for l in logs if l["level"] == level]

        # Filter by strategy
        if strategy:
            logs = [l for l in logs if l.get("strategy") == strategy]

        # Return most recent first
        return list(reversed(logs))[-limit:]

    def get_current_status(self) -> Dict[str, Any]:
        """Get current monitoring status"""
        with self.lock:
            return {
                "current_cycle": self.current_cycle,
                "cycle_start_time": self.cycle_start_time.isoformat()
                if self.cycle_start_time
                else None,
                "total_api_calls": self.api_call_count,
                "total_signal_checks": self.signal_checks,
                "total_trades_opened": self.trades_opened,
                "total_trades_closed": self.trades_closed,
                "log_queue_size": len(self.logs),
            }

    def log_api_call(
        self, endpoint: str, method: str, status: int = None, duration: float = None
    ):
        """Log API call"""
        message = f"{method} {endpoint}"
        data = {}
        level = LogLevel.INFO

        if status:
            message += f" -> {status}"
            if status >= 400:
                level = LogLevel.ERROR
            data["status_code"] = status

        if duration:
            message += f" ({duration * 1000:.1f}ms)"
            data["duration_ms"] = round(duration * 1000, 1)

        self.log(level, OperationType.API_CALL, message, data=data)

    def log_data_fetch(self, source: str, rows: int, duration: float = None):
        """Log data fetch"""
        message = f"Fetched {rows} rows from {source}"
        data = {"source": source, "rows": rows}

        if duration:
            message += f" ({duration * 1000:.1f}ms)"
            data["duration_ms"] = round(duration * 1000, 1)

        self.log(LogLevel.INFO, OperationType.DATA_FETCH, message, data=data)

    def log_indicator_calc(
        self, strategy: str, indicators: List[str], duration: float = None
    ):
        """Log indicator calculation"""
        message = f"Calculated {len(indicators)} indicators: {', '.join(indicators)}"
        data = {"indicator_count": len(indicators), "indicators": indicators}

        if duration:
            message += f" ({duration * 1000:.1f}ms)"
            data["duration_ms"] = round(duration * 1000, 1)

        self.log(
            LogLevel.INFO,
            OperationType.INDICATOR_CALC,
            message,
            strategy=strategy,
            data=data,
        )

    def log_signal_check(
        self, strategy: str, conditions: Dict[str, bool], signal: str = None
    ):
        """Log signal check"""

        # Convert conditions to native types (handles numpy booleans)
        conditions_native = convert_numpy_types(conditions)

        met = sum(1 for v in conditions_native.values() if v)
        total = len(conditions_native)
        message = f"Signal check: {met}/{total} conditions met"

        if signal:
            message += f" → {signal.upper()} SIGNAL"
            level = LogLevel.SUCCESS
        else:
            level = LogLevel.INFO

        data = {
            "conditions_met": met,
            "total_conditions": total,
            "signal": signal,
            "conditions": conditions_native,
        }

        self.log(
            level, OperationType.SIGNAL_CHECK, message, strategy=strategy, data=data
        )

    def log_trade_open(
        self,
        strategy: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        units: float,
    ):
        """Log trade opening"""
        message = f"OPENED {direction.upper()} trade @ ${entry_price:.2f}"
        data = {
            "direction": direction,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "units": round(units, 4),
            "sl_distance": abs(entry_price - stop_loss),
            "tp_distance": abs(take_profit - entry_price),
        }

        self.log(
            LogLevel.SUCCESS,
            OperationType.TRADE_OPEN,
            message,
            strategy=strategy,
            data=data,
        )

    def log_trade_close(
        self, strategy: str, direction: str, exit_price: float, pnl: float, reason: str
    ):
        """Log trade closing"""
        pnl_str = f"${pnl:+.2f}"
        icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
        message = f"CLOSED {direction.upper()} trade @ ${exit_price:.2f} - {reason} - PnL: {pnl_str} {icon}"
        data = {
            "direction": direction,
            "exit_price": exit_price,
            "pnl": round(pnl, 2),
            "reason": reason,
        }

        level = LogLevel.SUCCESS if pnl > 0 else LogLevel.WARNING
        self.log(
            level, OperationType.TRADE_CLOSE, message, strategy=strategy, data=data
        )

    def log_error(
        self, message: str, strategy: str = None, exception: Exception = None
    ):
        """Log error"""
        if exception:
            message += f": {str(exception)}"

        data = {}
        if exception:
            data["exception_type"] = type(exception).__name__
            data["exception_msg"] = str(exception)

        self.log(
            LogLevel.ERROR, OperationType.ERROR, message, strategy=strategy, data=data
        )

    def log_warning(self, message: str, strategy: str = None, data: Dict = None):
        """Log warning"""
        self.log(
            LogLevel.WARNING,
            OperationType.WARNING,
            message,
            strategy=strategy,
            data=data,
        )

    def log_terminal(self, message: str, stream: str = "stdout"):
        """Log terminal output (stdout/stderr)"""
        level = LogLevel.INFO if stream == "stdout" else LogLevel.WARNING
        self.log(level, OperationType.TERMINAL, message, data={"stream": stream})


# Global monitor instance
monitor = Monitor()
