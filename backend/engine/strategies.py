import pandas as pd
import numpy as np
from typing import Dict, Any, Optional


class StrategyBase:
    """Base class for all strategies"""
    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.enabled = config.get("enabled", True)
        self.capital = config.get("initial_capital", 10000.0)
        self.initial_capital = config.get("initial_capital", 10000.0)
        self.in_trade = False

        # Trade state attributes (set by PaperTradeEngine when a trade is opened)
        self.direction: Optional[str] = None       # 'long' or 'short'
        self.entry_price: float = 0.0
        self.stop_loss: float = 0.0
        self.take_profit: float = 0.0
        self.units: float = 0.0
        self.entry_idx: int = -1

    def generate_signal(self, df: pd.DataFrame, current_idx: int) -> Optional[str]:
        """Return 'long', 'short', or None"""
        raise NotImplementedError

    def get_signal_debug(self, df: pd.DataFrame, current_idx: int) -> Optional[Dict]:
        """Return detailed condition states for debugging"""
        raise NotImplementedError


class Strategy1(StrategyBase):
    """
    Long-Only Breakout Strategy (from stratgey.txt)
    
    Conditions for LONG:
    - EMA50 > EMA200 (bull regime)
    - ATR/Close > 0.015 (high volatility)
    - Close > HH20 (breakout)
    - RSI < 70 (not overbought)
    - Volume > 1.2 * VolMA20 (high volume)
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("strategy1", config)

    def generate_signal(self, df: pd.DataFrame, current_idx: int) -> Optional[str]:
        if current_idx < 1:
            return None

        row = df.iloc[current_idx]

        if pd.isna(row.get('ema50')) or pd.isna(row.get('atr')):
            return None

        # Bull regime: EMA50 > EMA200
        bull_regime = row['ema50'] > row['ema200']

        # High volatility: ATR/Close > 0.015
        high_vol = (row['atr'] / row['close']) > 0.015

        # Breakout: Close > HH20
        breakout = row['close'] > row['hh20']

        # Not overbought: RSI < 70
        not_overbought = row['rsi'] < 70

        # High volume: Volume > 1.2 * VolMA20
        high_volume = row['volume'] > (1.2 * row['vol_ma20'])

        signal = bull_regime and high_vol and breakout and not_overbought and high_volume

        return 'long' if signal else None

    def get_signal_debug(self, df: pd.DataFrame, current_idx: int) -> Optional[Dict]:
        if current_idx < 1:
            return {'error': 'Not enough data (need at least 2 bars)'}

        row = df.iloc[current_idx]

        if pd.isna(row.get('ema50')) or pd.isna(row.get('atr')):
            return {'error': 'Missing indicators (EMA50 or ATR is NaN)'}

        bull_regime = row['ema50'] > row['ema200']
        atr_pct = (row['atr'] / row['close']) * 100 if row['close'] > 0 else 0
        high_vol = (row['atr'] / row['close']) > 0.015
        breakout = row['close'] > row['hh20']
        not_overbought = row['rsi'] < 70
        high_volume = row['volume'] > (1.2 * row['vol_ma20'])

        conditions = {
            'bull_regime': {'met': bull_regime, 'ema50': row['ema50'], 'ema200': row['ema200']},
            'high_volatility': {'met': high_vol, 'atr_pct': round(atr_pct, 3), 'required_pct': 1.5},
            'breakout': {'met': breakout, 'close': row['close'], 'hh20': row['hh20']},
            'rsi_ok': {'met': not_overbought, 'rsi': round(row['rsi'], 2), 'max_allowed': 70},
            'volume_ok': {'met': high_volume, 'volume': row['volume'], 'vol_ma20': row['vol_ma20'], 'ratio': round(row['volume'] / row['vol_ma20'], 3) if row['vol_ma20'] > 0 else 0}
        }

        signal = 'long' if all(v['met'] for v in conditions.values()) else None

        return {
            'strategy': 'strategy1',
            'signal': signal,
            'conditions': conditions,
            'all_met': signal is not None
        }


class Strategy2(StrategyBase):
    """
    Long-Only Relaxed Strategy (from stratgey2.txt)
    
    Conditions for LONG:
    - EMA50 > EMA200 (bull regime)
    - ATR/Close > 0.008 (relaxed volatility)
    - Close > HH20 (breakout)
    - RSI < 70 (not overbought)
    - Volume > 1.05 * VolMA20 (relaxed volume)
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("strategy2", config)

    def generate_signal(self, df: pd.DataFrame, current_idx: int) -> Optional[str]:
        if current_idx < 1:
            return None

        row = df.iloc[current_idx]

        if pd.isna(row.get('ema50')) or pd.isna(row.get('atr')):
            return None

        # Relaxed conditions
        bull_regime = row['ema50'] > row['ema200']
        vol_condition = (row['atr'] / row['close']) > 0.008
        breakout = row['close'] > row['hh20']
        not_overbought = row['rsi'] < 70
        volume_ok = row['volume'] > (1.05 * row['vol_ma20'])

        signal = bull_regime and vol_condition and breakout and not_overbought and volume_ok

        return 'long' if signal else None

    def get_signal_debug(self, df: pd.DataFrame, current_idx: int) -> Optional[Dict]:
        if current_idx < 1:
            return {'error': 'Not enough data (need at least 2 bars)'}

        row = df.iloc[current_idx]

        if pd.isna(row.get('ema50')) or pd.isna(row.get('atr')):
            return {'error': 'Missing indicators (EMA50 or ATR is NaN)'}

        bull_regime = row['ema50'] > row['ema200']
        atr_pct = (row['atr'] / row['close']) * 100 if row['close'] > 0 else 0
        vol_condition = (row['atr'] / row['close']) > 0.008
        breakout = row['close'] > row['hh20']
        not_overbought = row['rsi'] < 70
        volume_ok = row['volume'] > (1.05 * row['vol_ma20'])

        conditions = {
            'bull_regime': {'met': bull_regime, 'ema50': row['ema50'], 'ema200': row['ema200']},
            'vol_condition': {'met': vol_condition, 'atr_pct': round(atr_pct, 3), 'required_pct': 0.8},
            'breakout': {'met': breakout, 'close': row['close'], 'hh20': row['hh20']},
            'rsi_ok': {'met': not_overbought, 'rsi': round(row['rsi'], 2), 'max_allowed': 70},
            'volume_ok': {'met': volume_ok, 'volume': row['volume'], 'vol_ma20': row['vol_ma20'], 'ratio': round(row['volume'] / row['vol_ma20'], 3) if row['vol_ma20'] > 0 else 0}
        }

        signal = 'long' if all(v['met'] for v in conditions.values()) else None

        return {
            'strategy': 'strategy2',
            'signal': signal,
            'conditions': conditions,
            'all_met': signal is not None
        }


class Strategy3(StrategyBase):
    """
    Long/Short Futures Strategy (from stratgey3.txt)
    
    Conditions for LONG:
    - EMA50 > EMA200 (bull regime)
    - Close > HH20 (breakout)
    - Volume > VolMA20 (volume confirmation)
    
    Conditions for SHORT:
    - EMA50 < EMA200 (bear regime)
    - Close < LL20 (breakdown)
    - Volume > VolMA20 (volume confirmation)
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("strategy3", config)

    def generate_signal(self, df: pd.DataFrame, current_idx: int) -> Optional[str]:
        if current_idx < 1:
            return None

        row = df.iloc[current_idx]

        if pd.isna(row.get('ema50')) or pd.isna(row.get('atr')):
            return None

        # Bull/Bear regime
        bull_regime = row['ema50'] > row['ema200']
        bear_regime = row['ema50'] < row['ema200']

        # Breakout/Breakdown
        breakout = row['close'] > row['hh20']
        breakdown = row['close'] < row['ll20']

        # Volume confirmation
        volume_ok = row['volume'] > row['vol_ma20']

        # Long signal
        if bull_regime and breakout and volume_ok:
            return 'long'

        # Short signal
        if bear_regime and breakdown and volume_ok:
            return 'short'

        return None

    def get_signal_debug(self, df: pd.DataFrame, current_idx: int) -> Optional[Dict]:
        if current_idx < 1:
            return {'error': 'Not enough data (need at least 2 bars)'}

        row = df.iloc[current_idx]

        if pd.isna(row.get('ema50')) or pd.isna(row.get('atr')):
            return {'error': 'Missing indicators (EMA50 or ATR is NaN)'}

        bull_regime = row['ema50'] > row['ema200']
        bear_regime = row['ema50'] < row['ema200']
        breakout = row['close'] > row['hh20']
        breakdown = row['close'] < row['ll20']
        volume_ok = row['volume'] > row['vol_ma20']

        conditions = {
            'bull_regime': {'met': bull_regime, 'ema50': row['ema50'], 'ema200': row['ema200']},
            'bear_regime': {'met': bear_regime, 'ema50': row['ema50'], 'ema200': row['ema200']},
            'breakout': {'met': breakout, 'close': row['close'], 'hh20': row['hh20']},
            'breakdown': {'met': breakdown, 'close': row['close'], 'll20': row['ll20']},
            'volume_ok': {'met': volume_ok, 'volume': row['volume'], 'vol_ma20': row['vol_ma20']}
        }

        signal = None
        if bull_regime and breakout and volume_ok:
            signal = 'long'
        elif bear_regime and breakdown and volume_ok:
            signal = 'short'

        return {
            'strategy': 'strategy3',
            'signal': signal,
            'conditions': conditions,
            'all_met': signal is not None
        }


class Strategy4(StrategyBase):
    """
    EMA Crossover with RSI & Price Action Strategy (from stratgey 4.txt)
    
    Conditions for LONG:
    - EMA20 > EMA50 (bullish crossover)
    - Low < Previous Low (pullback / lower low)
    - Close > Open (green candle - bullish reversal)
    - RSI > 50 (bullish momentum)
    
    Conditions for SHORT:
    - EMA20 < EMA50 (bearish crossover)
    - High > Previous High (pullback / higher high)
    - Close < Open (red candle - bearish reversal)
    - RSI < 50 (bearish momentum)
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("strategy4", config)

    def generate_signal(self, df: pd.DataFrame, current_idx: int) -> Optional[str]:
        if current_idx < 1:
            return None

        row = df.iloc[current_idx]
        prev_row = df.iloc[current_idx - 1]

        if pd.isna(row.get('ema20')) or pd.isna(row.get('ema50')) or pd.isna(row.get('rsi')):
            return None

        # BUY signal
        if (
            row['ema20'] > row['ema50'] and           # EMA20 above EMA50 (bullish)
            row['low'] < prev_row['low'] and           # Lower low (pullback)
            row['close'] > row['open'] and             # Green candle (bullish reversal)
            row['rsi'] > 50                             # RSI above 50 (bullish momentum)
        ):
            return 'long'

        # SELL signal
        if (
            row['ema20'] < row['ema50'] and           # EMA20 below EMA50 (bearish)
            row['high'] > prev_row['high'] and         # Higher high (pullback)
            row['close'] < row['open'] and             # Red candle (bearish reversal)
            row['rsi'] < 50                             # RSI below 50 (bearish momentum)
        ):
            return 'short'

        return None

    def get_signal_debug(self, df: pd.DataFrame, current_idx: int) -> Optional[Dict]:
        if current_idx < 1:
            return {'error': 'Not enough data (need at least 2 bars)'}

        row = df.iloc[current_idx]
        prev_row = df.iloc[current_idx - 1]

        if pd.isna(row.get('ema20')) or pd.isna(row.get('ema50')) or pd.isna(row.get('rsi')):
            return {'error': 'Missing indicators (EMA20, EMA50, or RSI is NaN)'}

        # Check BUY conditions
        ema20_above_ema50 = row['ema20'] > row['ema50']
        lower_low = row['low'] < prev_row['low']
        green_candle = row['close'] > row['open']
        rsi_above_50 = row['rsi'] > 50

        # Check SELL conditions
        ema20_below_ema50 = row['ema20'] < row['ema50']
        higher_high = row['high'] > prev_row['high']
        red_candle = row['close'] < row['open']
        rsi_below_50 = row['rsi'] < 50

        conditions = {
            'ema_crossover_bullish': {'met': ema20_above_ema50, 'ema20': row['ema20'], 'ema50': row['ema50']},
            'lower_low': {'met': lower_low, 'current_low': row['low'], 'prev_low': prev_row['low']},
            'green_candle': {'met': green_candle, 'close': row['close'], 'open': row['open']},
            'rsi_bullish': {'met': rsi_above_50, 'rsi': round(row['rsi'], 2)},
            'ema_crossover_bearish': {'met': ema20_below_ema50, 'ema20': row['ema20'], 'ema50': row['ema50']},
            'higher_high': {'met': higher_high, 'current_high': row['high'], 'prev_high': prev_row['high']},
            'red_candle': {'met': red_candle, 'close': row['close'], 'open': row['open']},
            'rsi_bearish': {'met': rsi_below_50, 'rsi': round(row['rsi'], 2)}
        }

        signal = None
        if ema20_above_ema50 and lower_low and green_candle and rsi_above_50:
            signal = 'long'
        elif ema20_below_ema50 and higher_high and red_candle and rsi_below_50:
            signal = 'short'

        return {
            'strategy': 'strategy4',
            'signal': signal,
            'conditions': conditions,
            'all_met': signal is not None
        }


class Strategy5(StrategyBase):
    """
    EMA 8/21 VWAP Momentum Strategy (from stratgey 5.txt)
    
    Conditions for LONG:
    - EMA8 > EMA21 (fast above slow)
    - Close > VWAP (above value area)
    - Close > Previous Close (upward momentum)
    - Close > Open (bullish candle)
    - (Close - Previous Close) > 0 (positive momentum)
    
    Conditions for SHORT:
    - EMA8 < EMA21 (fast below slow)
    - Close < VWAP (below value area)
    - Close < Previous Close (downward momentum)
    - Close < Open (bearish candle)
    - (Close - Previous Close) < 0 (negative momentum)
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("strategy5", config)

    def generate_signal(self, df: pd.DataFrame, current_idx: int) -> Optional[str]:
        if current_idx < 1:
            return None

        row = df.iloc[current_idx]
        prev_close = df['close'].iloc[current_idx - 1]

        if pd.isna(row.get('ema8')) or pd.isna(row.get('ema21')) or pd.isna(row.get('vwap')):
            return None

        long = (
            row['ema8'] > row['ema21'] and
            row['close'] > row['vwap'] and
            row['close'] > prev_close and
            row['close'] > row['open'] and
            (row['close'] - prev_close) > 0
        )

        short = (
            row['ema8'] < row['ema21'] and
            row['close'] < row['vwap'] and
            row['close'] < prev_close and
            row['close'] < row['open'] and
            (row['close'] - prev_close) < 0
        )

        if long:
            return 'long'
        if short:
            return 'short'
        return None

    def get_signal_debug(self, df: pd.DataFrame, current_idx: int) -> Optional[Dict]:
        if current_idx < 1:
            return {'error': 'Not enough data (need at least 2 bars)'}

        row = df.iloc[current_idx]
        prev_row = df.iloc[current_idx - 1]

        if pd.isna(row.get('ema8')) or pd.isna(row.get('ema21')) or pd.isna(row.get('vwap')):
            return {'error': 'Missing indicators (EMA8, EMA21, or VWAP is NaN)'}

        ema_fast_above = row['ema8'] > row['ema21']
        close_above_vwap = row['close'] > row['vwap']
        bullish_candle = row['close'] > row['open']
        momentum_up = (row['close'] - prev_row['close']) > 0

        ema_fast_below = row['ema8'] < row['ema21']
        close_below_vwap = row['close'] < row['vwap']
        bearish_candle = row['close'] < row['open']
        momentum_down = (row['close'] - prev_row['close']) < 0

        conditions = {
            'ema_fast_vs_slow_long': {'met': ema_fast_above, 'ema8': row['ema8'], 'ema21': row['ema21']},
            'price_vs_vwap_long': {'met': close_above_vwap, 'close': row['close'], 'vwap': row['vwap']},
            'bullish_candle': {'met': bullish_candle, 'close': row['close'], 'open': row['open']},
            'momentum_up': {'met': momentum_up, 'close': row['close'], 'prev_close': prev_row['close']},
            'ema_fast_vs_slow_short': {'met': ema_fast_below, 'ema8': row['ema8'], 'ema21': row['ema21']},
            'price_vs_vwap_short': {'met': close_below_vwap, 'close': row['close'], 'vwap': row['vwap']},
            'bearish_candle': {'met': bearish_candle, 'close': row['close'], 'open': row['open']},
            'momentum_down': {'met': momentum_down, 'close': row['close'], 'prev_close': prev_row['close']}
        }

        signal = None
        if ema_fast_above and close_above_vwap and bullish_candle and momentum_up:
            signal = 'long'
        elif ema_fast_below and close_below_vwap and bearish_candle and momentum_down:
            signal = 'short'

        return {
            'strategy': 'strategy5',
            'signal': signal,
            'conditions': conditions,
            'all_met': signal is not None
        }


class Strategy6(StrategyBase):
    """
    Momentum + Mean Reversion Scalping Strategy (from stratgey6.txt)

    Conditions for LONG:
    - Momentum: (close - prev_close) / prev_close > 0.001 (0.1% up move)
      OR mean reversion: candle is red (close < open)

    Conditions for SHORT:
    - Momentum: (close - prev_close) / prev_close < -0.001 (0.1% down move)
      OR mean reversion: candle is green (close > open)

    TP = 0.15%, SL = 0.10% (configured via reward_ratio or defaults)
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("strategy6", config)

    def generate_signal(self, df: pd.DataFrame, current_idx: int) -> Optional[str]:
        if current_idx < 1:
            return None

        row = df.iloc[current_idx]
        prev_row = df.iloc[current_idx - 1]

        if pd.isna(row.get('close')) or pd.isna(prev_row.get('close')):
            return None

        # Calculate momentum percentage
        pct_change = (row['close'] - prev_row['close']) / prev_row['close']

        # Momentum signal
        if pct_change > 0.001:
            return 'long'
        elif pct_change < -0.001:
            return 'short'

        # Mean reversion fallback: counter-trend trade
        if row['close'] > row['open']:
            return 'short'
        elif row['close'] < row['open']:
            return 'long'

        return None

    def get_signal_debug(self, df: pd.DataFrame, current_idx: int) -> Optional[Dict]:
        if current_idx < 1:
            return {'error': 'Not enough data (need at least 2 bars)'}

        row = df.iloc[current_idx]
        prev_row = df.iloc[current_idx - 1]

        if pd.isna(row.get('close')) or pd.isna(prev_row.get('close')):
            return {'error': 'Missing close price data'}

        pct_change = (row['close'] - prev_row['close']) / prev_row['close']
        momentum_up = pct_change > 0.001
        momentum_down = pct_change < -0.001
        bullish_candle = row['close'] > row['open']
        bearish_candle = row['close'] < row['open']

        # Mean reversion signals (opposite of candle color)
        mr_short = bullish_candle  # green candle → mean reversion short
        mr_long = bearish_candle   # red candle → mean reversion long

        conditions = {
            'momentum_up': {'met': momentum_up, 'pct_change': round(pct_change * 100, 3), 'threshold': 0.1},
            'momentum_down': {'met': momentum_down, 'pct_change': round(pct_change * 100, 3), 'threshold': -0.1},
            'bullish_candle': {'met': bullish_candle, 'close': row['close'], 'open': row['open']},
            'bearish_candle': {'met': bearish_candle, 'close': row['close'], 'open': row['open']},
            'mr_short': {'met': mr_short, 'reason': 'green candle → mean reversion short'},
            'mr_long': {'met': mr_long, 'reason': 'red candle → mean reversion long'},
        }

        signal = None
        if momentum_up:
            signal = 'long'
        elif momentum_down:
            signal = 'short'
        elif mr_short:
            signal = 'short'
        elif mr_long:
            signal = 'long'

        return {
            'strategy': 'strategy6',
            'signal': signal,
            'conditions': conditions,
            'all_met': signal is not None
        }
