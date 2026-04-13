#!/usr/bin/env python3
"""
Test Signal Logger - Verify that signal logging is working correctly
"""

import sys
import os
from datetime import datetime

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from engine.signal_logger import log_signal

print("="*80)
print("TESTING SIGNAL LOGGER")
print("="*80)

# Test 1: Long signal with conditions
print("\nTest 1: LONG signal with conditions")
log_signal(
    strategy_name="strategy1",
    signal="long",
    price=45230.50,
    timestamp=datetime.utcnow(),
    conditions={
        'bull_regime': {'met': True, 'ema50': 45100, 'ema200': 44800},
        'high_volatility': {'met': True, 'atr_pct': 1.8},
        'breakout': {'met': True, 'close': 45230, 'hh20': 45100},
        'rsi_ok': {'met': True, 'rsi': 65.2},
        'volume_ok': {'met': True, 'volume_ratio': 1.5}
    },
    symbol="BTCUSDT"
)

# Test 2: Short signal with conditions
print("\n\nTest 2: SHORT signal with conditions")
log_signal(
    strategy_name="strategy3",
    signal="short",
    price=44850.75,
    timestamp=datetime.utcnow(),
    conditions={
        'bear_regime': {'met': True, 'ema50': 44900, 'ema200': 45200},
        'breakdown': {'met': True, 'close': 44850, 'll20': 44900},
        'volume_ok': {'met': True, 'volume_ratio': 1.3}
    },
    symbol="BTCUSDT"
)

# Test 3: Simple signal without detailed conditions
print("\n\nTest 3: Simple signal (minimal info)")
log_signal(
    strategy_name="strategy4",
    signal="long",
    price=45500.00,
    symbol="BTCUSDT"
)

print("\n" + "="*80)
print("✅ SIGNAL LOGGER TEST COMPLETE")
print("="*80)
print("\nIf you saw the formatted signal messages above, the logger is working!")
print("Signals will now be logged automatically when your strategies generate them.")
