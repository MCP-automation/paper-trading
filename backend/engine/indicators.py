import pandas as pd
import numpy as np
from typing import Dict, Tuple

class IndicatorEngine:
    """Compute all technical indicators needed by strategies"""
    
    @staticmethod
    def compute_ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()
    
    @staticmethod
    def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(period).mean()
    
    @staticmethod
    def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    @staticmethod
    def compute_hh(high: pd.Series, period: int = 20) -> pd.Series:
        return high.rolling(period).max().shift(1)
    
    @staticmethod
    def compute_ll(low: pd.Series, period: int = 20) -> pd.Series:
        return low.rolling(period).min().shift(1)
    
    @staticmethod
    def compute_volume_ma(volume: pd.Series, period: int = 20) -> pd.Series:
        return volume.rolling(period).mean()
    
    @staticmethod
    def compute_all_indicators(df: pd.DataFrame, config: Dict) -> pd.DataFrame:
        """Compute all indicators on a DataFrame"""
        close = df['close']
        high = df['high']
        low = df['low']
        volume = df['volume']

        cfg = config

        # ── Long-term trend filter (always 200-period, never overridden) ──────
        df['ema200'] = IndicatorEngine.compute_ema(close, 200)

        # ── Medium EMA: uses ema_slow from config (50 for S1, 60 for S2, etc.) ─
        ema_slow_period = cfg.get('ema_slow', 50)
        df['ema50'] = IndicatorEngine.compute_ema(close, ema_slow_period)
        df['ema_slow'] = df['ema50']

        # ── Fast EMA: uses ema_fast from config ──────────────────────────────
        ema_fast_period = cfg.get('ema_fast', 20)
        df['ema_fast'] = IndicatorEngine.compute_ema(close, ema_fast_period)

        # ── Strategy4 aliases: ema20 (fast) / ema50 (slow = ema_long) ────────
        # Strategy4 has explicit ema_short/ema_long keys; fall back to fast/slow
        ema_short_period = cfg.get('ema_short', ema_fast_period)
        ema_long_period  = cfg.get('ema_long', ema_slow_period)
        df['ema_short'] = IndicatorEngine.compute_ema(close, ema_short_period)
        df['ema_long']  = IndicatorEngine.compute_ema(close, ema_long_period)
        df['ema20'] = df['ema_short']   # S4 checks ema20/ema50
        # Override ema50 for Strategy4 so crossover uses correct pair
        if 'ema_long' in cfg:
            df['ema50'] = df['ema_long']

        # ── Strategy5 aliases: ema8 / ema21 (mapped from fast/slow) ──────────
        df['ema8']  = df['ema_fast']    # named ema8 but uses ema_fast period
        df['ema21'] = df['ema_slow']    # named ema21 but uses ema_slow period

        # ATR
        df['atr'] = IndicatorEngine.compute_atr(high, low, close, cfg.get('atr_period', 14))

        # RSI
        df['rsi'] = IndicatorEngine.compute_rsi(close, cfg.get('rsi_period', 14))

        # Donchian channels (highest-high / lowest-low of last N bars, shifted 1)
        breakout_period = cfg.get('breakout_period', 20)
        df['hh20'] = IndicatorEngine.compute_hh(high, breakout_period)
        df['ll20'] = IndicatorEngine.compute_ll(low, breakout_period)

        # Volume MA (same window as breakout)
        df['vol_ma20'] = IndicatorEngine.compute_volume_ma(volume, breakout_period)

        # VWAP (rolling, using vwap_period or breakout_period)
        vwap_period = cfg.get('vwap_period', breakout_period)
        df['vwap'] = (close * volume).rolling(vwap_period).sum() / volume.rolling(vwap_period).sum()

        # Momentum
        momentum_period = cfg.get('momentum_period', 4)
        df['momentum'] = close - close.shift(momentum_period)

        return df

