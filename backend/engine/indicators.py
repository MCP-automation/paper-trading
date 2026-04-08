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

        # EMAs (use configurable periods for flexibility)
        ema_short_period = cfg.get('ema_short', cfg.get('ema_fast', 20))
        ema_long_period = cfg.get('ema_long', cfg.get('ema_fast', 50))
        ema_slow_period = cfg.get('ema_slow', 200)

        df['ema_short'] = IndicatorEngine.compute_ema(close, ema_short_period)
        df['ema_long'] = IndicatorEngine.compute_ema(close, ema_long_period)
        df['ema200'] = IndicatorEngine.compute_ema(close, ema_slow_period)

        # Additional EMA aliases for strategy support
        df['ema_fast'] = IndicatorEngine.compute_ema(close, cfg.get('ema_fast', 20))
        df['ema_slow'] = IndicatorEngine.compute_ema(close, cfg.get('ema_slow', 50))
        df['ema8'] = IndicatorEngine.compute_ema(close, cfg.get('ema_fast', 8))
        df['ema21'] = IndicatorEngine.compute_ema(close, cfg.get('ema_slow', 21))

        # Legacy column names for backward compatibility
        df['ema20'] = df['ema_short']
        df['ema50'] = df['ema_long']

        # ATR
        df['atr'] = IndicatorEngine.compute_atr(high, low, close, cfg.get('atr_period', 14))

        # RSI
        df['rsi'] = IndicatorEngine.compute_rsi(close, cfg.get('rsi_period', 14))

        # Donchian channels
        df['hh20'] = IndicatorEngine.compute_hh(high, cfg.get('breakout_period', 20))
        df['ll20'] = IndicatorEngine.compute_ll(low, cfg.get('breakout_period', 20))

        # Volume MA
        df['vol_ma20'] = IndicatorEngine.compute_volume_ma(volume, cfg.get('breakout_period', 20))

        # VWAP for momentum breakout and filter strategies
        vwap_period = cfg.get('vwap_period', cfg.get('breakout_period', 20))
        df['vwap'] = (close * volume).rolling(vwap_period).sum() / volume.rolling(vwap_period).sum()

        # Momentum
        momentum_period = cfg.get('momentum_period', 4)
        df['momentum'] = close - close.shift(momentum_period)

        return df
