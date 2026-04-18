import pandas as pd
import numpy as np
from typing import Dict, Any, Optional


class StrategyBase:
    """Base class for all strategies with integrated risk management"""
    
    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.enabled = config.get("enabled", True)
        self.capital = config.get("initial_capital", 100.0)
        self.initial_capital = config.get("initial_capital", 100.0)
        
        # Risk Management Configuration for $100 account
        self.max_risk_pct = config.get("risk_pct", 0.015)  # 1.5% risk per trade = $1.50
        self.min_rr_ratio = config.get("min_rr_ratio", 2.0)    # Minimum 1:2 risk:reward
        self.position_scale = config.get("position_scale", 1.0)  # Scale multiplier
        self.max_trades_per_day = config.get("max_trades_per_day", 3)
        
        # Trade state attributes (set by PaperTradeEngine when a trade is opened)
        self.direction: Optional[str] = None       # 'long' or 'short'
        self.entry_price: float = 0.0
        self.stop_loss: float = 0.0
        self.take_profit_1: float = 0.0           # First partial profit target
        self.take_profit_2: float = 0.0           # Second partial profit target
        self.trail_stop: float = 0.0              # Trailing stop level
        self.units: float = 0.0
        self.units_partial_1: float = 0.0         # Units for first partial
        self.units_partial_2: float = 0.0         # Units for second partial
        self.units_trail: float = 0.0             # Units for trailing
        self.entry_idx: int = -1
        self.risk_amount: float = 0.0             # Dollar amount at risk
        self.expected_rr: float = 0.0             # Expected risk:reward ratio
        self.signal_strength: float = 0.0         # 0.0-1.0 confidence level
        self.in_trade = False

    def calculate_position_size(self, entry_price: float, stop_loss_price: float) -> Dict[str, float]:
        """
        Calculate position size using fixed risk method for $100 account.
        
        Returns: {
            'units': total units to trade,
            'risk_amount': dollar amount at risk,
            'position_scale': applied scaling factor
        }
        """
        # Risk = 1.5% of account
        risk_amount = self.capital * self.max_risk_pct
        
        # SL distance in price
        sl_distance = abs(entry_price - stop_loss_price)
        
        if sl_distance == 0:
            return {'units': 0, 'risk_amount': 0, 'position_scale': 0}
        
        # Position units = Risk / SL Distance
        units = risk_amount / sl_distance
        
        # Apply strategy-specific scaling
        units = units * self.position_scale
        
        return {
            'units': max(units, 0.01),
            'risk_amount': risk_amount,
            'position_scale': self.position_scale
        }

    def calculate_profit_targets(
        self,
        entry_price: float,
        stop_loss_price: float,
        units: float,
        direction: str
    ) -> Dict[str, Any]:
        """
        Calculate multi-level profit targets using risk/reward pyramid.
        
        Returns profit target levels and units to take at each level.
        """
        sl_distance = abs(entry_price - stop_loss_price)
        side = 1 if direction == 'long' else -1
        
        targets = {
            'take_profit_1': entry_price + side * (sl_distance * 1.5),  # 1.5x risk
            'units_partial_1': units * 0.50,                             # 50% at TP1
            'take_profit_2': entry_price + side * (sl_distance * 2.5),  # 2.5x risk
            'units_partial_2': units * 0.25,                             # 25% at TP2
            'trail_units': units * 0.25,                                 # 25% for trailing
            'trail_stop': entry_price + side * (sl_distance * 0.75),    # Trail from +0.75x
            'expected_rr': 2.5  # Expected risk:reward ratio
        }
        
        return targets

    def generate_signal(self, df: pd.DataFrame, current_idx: int) -> Optional[str]:
        """Return 'long', 'short', or None"""
        raise NotImplementedError

    def get_signal_debug(self, df: pd.DataFrame, current_idx: int) -> Optional[Dict]:
        """Return detailed condition states for debugging"""
        raise NotImplementedError


# ============================================================================
# STRATEGY 1: Long-Only Strict Breakout (HIGH CONVICTION)
# ============================================================================

class Strategy1(StrategyBase):
    """
    Long-Only Breakout Strategy - HIGH CONVICTION
    
    Math-Based Entry Conditions (ALL must be met):
    - EMA50 > EMA200 (bull regime = trending environment)
    - ATR/Close > 0.015 (1.5% ATR = high volatility required)
    - Close > HH20 (20-bar breakout = momentum confirmation)
    - RSI < 70 (not overbought = room to run)
    - Volume > 1.2 × VolMA20 (120% of avg volume = surge confirmation)
    
    Risk Management:
    - Position Scale: 1.0 (full size, high conviction)
    - Stop Loss: ATR below entry
    - Profit Targets: 50% at 1.5x risk, 25% at 2.5x risk, 25% trailing
    - Expected R:R: 2.5:1
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("strategy1", config)
        # Strategy1-specific optimization: Only full-size, fewer signals
        self.position_scale = config.get("position_scale", 1.0)

    def generate_signal(self, df: pd.DataFrame, current_idx: int) -> Optional[str]:
        if current_idx < 1:
            return None

        row = df.iloc[current_idx]

        if pd.isna(row.get('ema50')) or pd.isna(row.get('atr')):
            return None

        # Bull regime: EMA50 > EMA200
        bull_regime = row['ema50'] > row['ema200']

        # High volatility: ATR/Close > 0.015 (1.5%)
        high_vol = (row['atr'] / row['close']) > 0.015

        # Breakout: Close > HH20 (highest high in 20 bars)
        breakout = row['close'] > row['hh20']

        # Not overbought: RSI < 70
        not_overbought = row['rsi'] < 70

        # High volume: Volume > 1.2 * VolMA20
        high_volume = row['volume'] > (1.2 * row['vol_ma20'])

        # ALL conditions must be met for high conviction
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
            'bull_regime': {'met': bull_regime, 'ema50': round(row['ema50'], 2), 'ema200': round(row['ema200'], 2), 'required': 'EMA50 > EMA200'},
            'high_volatility': {'met': high_vol, 'atr_pct': round(atr_pct, 3), 'required_pct': 1.5, 'msg': 'Need 1.5%+ volatility'},
            'breakout': {'met': breakout, 'close': round(row['close'], 4), 'hh20': round(row['hh20'], 4), 'msg': 'Close > 20-bar high'},
            'rsi_ok': {'met': not_overbought, 'rsi': round(row['rsi'], 2), 'max_allowed': 70, 'msg': 'RSI must be < 70'},
            'volume_surge': {'met': high_volume, 'volume': round(row['volume'], 0), 'vol_ma20': round(row['vol_ma20'], 0), 'ratio': round(row['volume'] / row['vol_ma20'], 2) if row['vol_ma20'] > 0 else 0, 'required': '1.2x avg'}
        }

        signal = 'long' if all(v['met'] for v in conditions.values()) else None

        return {
            'strategy': 'strategy1',
            'signal': signal,
            'conditions': conditions,
            'all_met': signal is not None,
            'signal_strength': 1.0 if signal else 0.0,
            'risk_management': {
                'position_scale': self.position_scale,
                'profit_targets': '50% @ 1.5x risk, 25% @ 2.5x risk, 25% trailing',
                'expected_rr': 2.5
            }
        }


# ============================================================================
# STRATEGY 2: Long-Only Relaxed Breakout (MEDIUM CONVICTION)
# ============================================================================

class Strategy2(StrategyBase):
    """
    Long-Only Relaxed Breakout - MEDIUM CONVICTION
    
    Math-Based Entry Conditions (ALL must be met):
    - EMA50 > EMA200 (bull regime)
    - ATR/Close > 0.008 (0.8% ATR = relaxed volatility)
    - Close > HH20 (breakout confirmation)
    - RSI < 70 (not overbought)
    - Volume > 1.05 × VolMA20 (only 105% = relaxed volume)
    
    Risk Management:
    - Position Scale: 0.85 (reduce by 15%, lower conviction)
    - Stop Loss: ATR below entry
    - Profit Targets: 50% at 1.5x, 25% at 2.0x, 25% trailing
    - Expected R:R: 2.0:1
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("strategy2", config)
        # Relaxed strategy = smaller position size
        self.position_scale = config.get("position_scale", 0.85)

    def generate_signal(self, df: pd.DataFrame, current_idx: int) -> Optional[str]:
        if current_idx < 1:
            return None

        row = df.iloc[current_idx]

        if pd.isna(row.get('ema50')) or pd.isna(row.get('atr')):
            return None

        # Relaxed conditions compared to Strategy1
        bull_regime = row['ema50'] > row['ema200']
        vol_condition = (row['atr'] / row['close']) > 0.008  # 0.8% vs 1.5%
        breakout = row['close'] > row['hh20']
        not_overbought = row['rsi'] < 70
        volume_ok = row['volume'] > (1.05 * row['vol_ma20'])  # 1.05x vs 1.2x

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
            'bull_regime': {'met': bull_regime, 'ema50': round(row['ema50'], 2), 'ema200': round(row['ema200'], 2)},
            'vol_condition': {'met': vol_condition, 'atr_pct': round(atr_pct, 3), 'required_pct': 0.8, 'msg': 'Relaxed volatility filter'},
            'breakout': {'met': breakout, 'close': round(row['close'], 4), 'hh20': round(row['hh20'], 4)},
            'rsi_ok': {'met': not_overbought, 'rsi': round(row['rsi'], 2), 'max_allowed': 70},
            'volume_ok': {'met': volume_ok, 'volume': round(row['volume'], 0), 'vol_ma20': round(row['vol_ma20'], 0), 'ratio': round(row['volume'] / row['vol_ma20'], 2) if row['vol_ma20'] > 0 else 0}
        }

        signal = 'long' if all(v['met'] for v in conditions.values()) else None

        return {
            'strategy': 'strategy2',
            'signal': signal,
            'conditions': conditions,
            'all_met': signal is not None,
            'signal_strength': 0.75 if signal else 0.0,
            'risk_management': {
                'position_scale': self.position_scale,
                'profit_targets': '50% @ 1.5x risk, 25% @ 2.0x risk, 25% trailing',
                'expected_rr': 2.0
            }
        }


# ============================================================================
# STRATEGY 3: Long/Short Futures (DIRECTIONAL, ALL MARKET CONDITIONS)
# ============================================================================

class Strategy3(StrategyBase):
    """
    Long/Short Futures Strategy - DIRECTIONAL, ALL CONDITIONS
    
    Math-Based Entry Conditions:
    
    FOR LONG:
    - EMA50 > EMA200 (bull regime)
    - Close > HH20 (breakout above 20-bar high)
    - Volume > VolMA20 (basic volume confirmation)
    
    FOR SHORT:
    - EMA50 < EMA200 (bear regime)
    - Close < LL20 (breakdown below 20-bar low)
    - Volume > VolMA20 (basic volume confirmation)
    
    Risk Management:
    - Position Scale: 0.75 (reduce by 25%, shorts are riskier)
    - Stop Loss: ATR distance
    - Profit Targets: 50% @ 1.2x risk, 50% @ 1.8x risk
    - Expected R:R: 1.8:1 (lower due to 2-way trades)
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("strategy3", config)
        # Futures strategy = reduce position due to leverage risk
        self.position_scale = config.get("position_scale", 0.75)

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

        # Volume confirmation (only 1x avg, not 1.2x)
        volume_ok = row['volume'] > row['vol_ma20']

        # LONG: Bull regime + breakout + volume
        if bull_regime and breakout and volume_ok:
            return 'long'

        # SHORT: Bear regime + breakdown + volume
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
            'bull_regime': {'met': bull_regime, 'ema50': round(row['ema50'], 2), 'ema200': round(row['ema200'], 2)},
            'bear_regime': {'met': bear_regime, 'ema50': round(row['ema50'], 2), 'ema200': round(row['ema200'], 2)},
            'breakout': {'met': breakout, 'close': round(row['close'], 4), 'hh20': round(row['hh20'], 4)},
            'breakdown': {'met': breakdown, 'close': round(row['close'], 4), 'll20': round(row['ll20'], 4)},
            'volume_ok': {'met': volume_ok, 'volume': round(row['volume'], 0), 'vol_ma20': round(row['vol_ma20'], 0)}
        }

        signal = None
        signal_strength = 0.0
        if bull_regime and breakout and volume_ok:
            signal = 'long'
            signal_strength = 0.7
        elif bear_regime and breakdown and volume_ok:
            signal = 'short'
            signal_strength = 0.6  # Shorts slightly riskier

        return {
            'strategy': 'strategy3',
            'signal': signal,
            'conditions': conditions,
            'all_met': signal is not None,
            'signal_strength': signal_strength,
            'risk_management': {
                'position_scale': self.position_scale,
                'profit_targets': '50% @ 1.2x risk, 50% @ 1.8x risk',
                'expected_rr': 1.8,
                'note': 'Shorts reduced 20% more due to risk'
            }
        }


# ============================================================================
# STRATEGY 4: EMA Crossover with Price Action (SWING/SCALP)
# ============================================================================

class Strategy4(StrategyBase):
    """
    EMA Crossover + Price Action Strategy - SWING/SCALP
    
    Math-Based Entry Conditions:
    
    FOR LONG:
    - EMA20 > EMA50 (bullish crossover = momentum shift)
    - Low < Previous Low (pullback = lower low)
    - Close > Open (green candle = bullish reversal)
    - RSI > 50 (bullish momentum threshold)
    
    FOR SHORT:
    - EMA20 < EMA50 (bearish crossover)
    - High > Previous High (pullback = higher high)
    - Close < Open (red candle = bearish reversal)
    - RSI < 50 (bearish momentum threshold)
    
    Risk Management:
    - Position Scale: 0.80 (reduce by 20%, pullback-based can whipsaw)
    - Entry: 60% on signal, 40% on confirmation
    - Stop Loss: 0.8% (tighter for scalp)
    - Profit Targets: 50% @ 1.2x risk, 50% @ 1.8x risk
    - Expected R:R: 1.8:1
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("strategy4", config)
        # Pullback-based strategy = reduce position
        self.position_scale = config.get("position_scale", 0.80)

    def generate_signal(self, df: pd.DataFrame, current_idx: int) -> Optional[str]:
        if current_idx < 1:
            return None

        row = df.iloc[current_idx]
        prev_row = df.iloc[current_idx - 1]

        if pd.isna(row.get('ema20')) or pd.isna(row.get('ema50')) or pd.isna(row.get('rsi')):
            return None

        # BUY signal: EMA20 > EMA50 + pullback + green candle + RSI > 50
        if (
            row['ema20'] > row['ema50'] and           # Bullish EMA crossover
            row['low'] < prev_row['low'] and           # Lower low (pullback)
            row['close'] > row['open'] and             # Green candle
            row['rsi'] > 50                             # Bullish momentum
        ):
            return 'long'

        # SELL signal: EMA20 < EMA50 + pullback + red candle + RSI < 50
        if (
            row['ema20'] < row['ema50'] and           # Bearish EMA crossover
            row['high'] > prev_row['high'] and         # Higher high (pullback)
            row['close'] < row['open'] and             # Red candle
            row['rsi'] < 50                             # Bearish momentum
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
            'ema_crossover_bullish': {'met': ema20_above_ema50, 'ema20': round(row['ema20'], 2), 'ema50': round(row['ema50'], 2), 'msg': 'EMA20 above EMA50'},
            'lower_low': {'met': lower_low, 'current_low': round(row['low'], 4), 'prev_low': round(prev_row['low'], 4), 'msg': 'Lower low = pullback'},
            'green_candle': {'met': green_candle, 'close': round(row['close'], 4), 'open': round(row['open'], 4)},
            'rsi_bullish': {'met': rsi_above_50, 'rsi': round(row['rsi'], 2), 'threshold': 50},
            'ema_crossover_bearish': {'met': ema20_below_ema50, 'ema20': round(row['ema20'], 2), 'ema50': round(row['ema50'], 2)},
            'higher_high': {'met': higher_high, 'current_high': round(row['high'], 4), 'prev_high': round(prev_row['high'], 4)},
            'red_candle': {'met': red_candle, 'close': round(row['close'], 4), 'open': round(row['open'], 4)},
            'rsi_bearish': {'met': rsi_below_50, 'rsi': round(row['rsi'], 2), 'threshold': 50}
        }

        signal = None
        signal_strength = 0.0
        if ema20_above_ema50 and lower_low and green_candle and rsi_above_50:
            signal = 'long'
            signal_strength = 0.75
        elif ema20_below_ema50 and higher_high and red_candle and rsi_below_50:
            signal = 'short'
            signal_strength = 0.75

        return {
            'strategy': 'strategy4',
            'signal': signal,
            'conditions': conditions,
            'all_met': signal is not None,
            'signal_strength': signal_strength,
            'risk_management': {
                'position_scale': self.position_scale,
                'entry_method': '60% initial + 40% on confirmation',
                'profit_targets': '50% @ 1.2x risk, 50% @ 1.8x risk',
                'expected_rr': 1.8
            }
        }


# ============================================================================
# STRATEGY 5: EMA 8/21 VWAP Momentum (TREND-FOLLOWING, HIGH CONVICTION)
# ============================================================================

class Strategy5(StrategyBase):
    """
    EMA 8/21 VWAP Momentum Strategy - TREND-FOLLOWING, HIGH CONVICTION
    
    Math-Based Entry Conditions:
    
    FOR LONG:
    - EMA8 > EMA21 (fast EMA above slow = strong uptrend)
    - Close > VWAP (price above volume-weighted average price)
    - Close > Previous Close (upward momentum)
    - Close > Open (bullish candle = conviction)
    - (Close - Previous Close) > 0 (positive momentum confirmed)
    
    FOR SHORT:
    - EMA8 < EMA21 (fast EMA below slow = strong downtrend)
    - Close < VWAP (price below volume-weighted average price)
    - Close < Previous Close (downward momentum)
    - Close < Open (bearish candle = conviction)
    - (Close - Previous Close) < 0 (negative momentum confirmed)
    
    Risk Management:
    - Position Scale: 1.0 (full size, highest conviction)
    - Stop Loss: 1.2% (wider, momentum trades can run)
    - Profit Targets: 40% @ 1.5x, 30% @ 2.5x, 30% trail @ 1.0x
    - Expected R:R: 2.5:1 (let winners run)
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__("strategy5", config)
        # Momentum/VWAP = highest conviction = full position
        self.position_scale = config.get("position_scale", 1.0)

    def generate_signal(self, df: pd.DataFrame, current_idx: int) -> Optional[str]:
        if current_idx < 1:
            return None

        row = df.iloc[current_idx]
        prev_close = df['close'].iloc[current_idx - 1]

        if pd.isna(row.get('ema8')) or pd.isna(row.get('ema21')) or pd.isna(row.get('vwap')):
            return None

        # LONG: All conditions momentum-aligned
        long = (
            row['ema8'] > row['ema21'] and
            row['close'] > row['vwap'] and
            row['close'] > prev_close and
            row['close'] > row['open'] and
            (row['close'] - prev_close) > 0
        )

        # SHORT: All conditions momentum-aligned downside
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
        price_above_prev = row['close'] > prev_row['close']

        ema_fast_below = row['ema8'] < row['ema21']
        close_below_vwap = row['close'] < row['vwap']
        bearish_candle = row['close'] < row['open']
        momentum_down = (row['close'] - prev_row['close']) < 0
        price_below_prev = row['close'] < prev_row['close']

        conditions = {
            'ema_fast_vs_slow_long': {'met': ema_fast_above, 'ema8': round(row['ema8'], 2), 'ema21': round(row['ema21'], 2), 'msg': 'EMA8 > EMA21 = uptrend'},
            'price_vs_vwap_long': {'met': close_above_vwap, 'close': round(row['close'], 4), 'vwap': round(row['vwap'], 4)},
            'price_above_prev': {'met': price_above_prev, 'close': round(row['close'], 4), 'prev_close': round(prev_row['close'], 4)},
            'bullish_candle': {'met': bullish_candle, 'close': round(row['close'], 4), 'open': round(row['open'], 4)},
            'momentum_up': {'met': momentum_up, 'momentum': round((row['close'] - prev_row['close']), 4), 'msg': 'All aligned upward'},
            'ema_fast_vs_slow_short': {'met': ema_fast_below, 'ema8': round(row['ema8'], 2), 'ema21': round(row['ema21'], 2)},
            'price_vs_vwap_short': {'met': close_below_vwap, 'close': round(row['close'], 4), 'vwap': round(row['vwap'], 4)},
            'price_below_prev': {'met': price_below_prev, 'close': round(row['close'], 4), 'prev_close': round(prev_row['close'], 4)},
            'bearish_candle': {'met': bearish_candle, 'close': round(row['close'], 4), 'open': round(row['open'], 4)},
            'momentum_down': {'met': momentum_down, 'momentum': round((row['close'] - prev_row['close']), 4)}
        }

        signal = None
        signal_strength = 0.0
        if ema_fast_above and close_above_vwap and bullish_candle and momentum_up and price_above_prev:
            signal = 'long'
            signal_strength = 1.0  # Highest conviction
        elif ema_fast_below and close_below_vwap and bearish_candle and momentum_down and price_below_prev:
            signal = 'short'
            signal_strength = 1.0

        return {
            'strategy': 'strategy5',
            'signal': signal,
            'conditions': conditions,
            'all_met': signal is not None,
            'signal_strength': signal_strength,
            'risk_management': {
                'position_scale': self.position_scale,
                'profit_targets': '40% @ 1.5x, 30% @ 2.5x, 30% trail',
                'expected_rr': 2.5,
                'note': 'Highest conviction - let winners run'
            }
        }



