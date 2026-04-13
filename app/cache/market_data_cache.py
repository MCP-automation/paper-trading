import sqlite3
import json
import time
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Union

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MarketDataCache")

class MarketDataCache:
    """
    SQLite-backed cache for OHLCV data to prevent API rate limiting.
    Stores candle data and metadata about when it was fetched.
    """

    def __init__(self, db_path: str = "market_data_cache.db", default_ttl: int = 60):
        self.db_path = db_path
        self.default_ttl = default_ttl
        self._init_db()

    def _init_db(self):
        """Initialize the SQLite database and create tables with indexes."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                # Table for OHLCV data
                # We store the full candle data. 
                # Note: We use a compound primary key to avoid duplicates.
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ohlcv_cache (
                        symbol TEXT,
                        timeframe TEXT,
                        timestamp INTEGER,
                        open REAL,
                        high REAL,
                        low REAL,
                        close REAL,
                        volume REAL,
                        fetched_at REAL,
                        PRIMARY KEY (symbol, timeframe, timestamp)
                    )
                """)
                
                # Index for fast lookups by symbol and timeframe
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_lookup ON ohlcv_cache (symbol, timeframe)")
                # Index for cleanup operations
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_fetched_at ON ohlcv_cache (fetched_at)")
                
                conn.commit()
                logger.info(f"Cache database initialized at {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def get(self, symbol: str, timeframe: str, max_age_seconds: Optional[int] = None) -> Optional[List[List]]:
        """
        Retrieve OHLCV data from cache if it exists and is not stale.
        Returns a CCXT-style list of lists: [[timestamp, open, high, low, close, volume], ...]
        """
        ttl = max_age_seconds if max_age_seconds is not None else self.default_ttl
        now = time.time()
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Check the most recent 'fetched_at' for this specific symbol/timeframe
                cursor.execute("""
                    SELECT MAX(fetched_at) FROM ohlcv_cache 
                    WHERE symbol = ? AND timeframe = ?
                """, (symbol, timeframe))
                
                last_fetched = cursor.fetchone()[0]
                
                if last_fetched is None:
                    logger.debug(f"Cache MISS: No data for {symbol} {timeframe}")
                    return None
                
                age = now - last_fetched
                if age > ttl:
                    logger.info(f"Cache STALE: Data for {symbol} {timeframe} is {age:.1f}s old (TTL: {ttl}s)")
                    return None
                
                # Fetch all candles for this group
                cursor.execute("""
                    SELECT timestamp, open, high, low, close, volume 
                    FROM ohlcv_cache 
                    WHERE symbol = ? AND timeframe = ?
                    ORDER BY timestamp ASC
                """, (symbol, timeframe))
                
                rows = cursor.fetchall()
                if not rows:
                    return None
                
                # Convert back to CCXT format
                ohlcv = [list(row) for row in rows]
                logger.info(f"Cache HIT: {len(ohlcv)} candles for {symbol} {timeframe} (Age: {age:.1f}s)")
                return ohlcv
                
        except sqlite3.Error as e:
            logger.error(f"Error reading from cache: {e}")
            return None

    def set(self, symbol: str, timeframe: str, ohlcv: List[List]):
        """
        Store OHLCV data in the cache. 
        Automatically updates 'fetched_at' for all candles in the set.
        """
        if not ohlcv:
            return

        now = time.time()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Prepare data for bulk insertion
                # CCXT format: [timestamp, open, high, low, close, volume]
                data_to_insert = [
                    (symbol, timeframe, candle[0], candle[1], candle[2], candle[3], candle[4], candle[5], now)
                    for candle in ohlcv
                ]
                
                # UPSERT: Insert or replace if timestamp already exists for this symbol/timeframe
                cursor.executemany("""
                    INSERT OR REPLACE INTO ohlcv_cache 
                    (symbol, timeframe, timestamp, open, high, low, close, volume, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, data_to_insert)
                
                conn.commit()
                logger.info(f"Cache INSERT: {len(ohlcv)} candles for {symbol} {timeframe}")
                
            # Trigger cleanup periodically (internal management)
            self.clear_old_cache()
            
        except sqlite3.Error as e:
            logger.error(f"Error writing to cache: {e}")

    def is_stale(self, symbol: str, timeframe: str, max_age_seconds: Optional[int] = None) -> bool:
        """Helper to check if data for a symbol needs a refresh."""
        ttl = max_age_seconds if max_age_seconds is not None else self.default_ttl
        now = time.time()
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT MAX(fetched_at) FROM ohlcv_cache 
                    WHERE symbol = ? AND timeframe = ?
                """, (symbol, timeframe))
                last_fetched = cursor.fetchone()[0]
                
                if last_fetched is None:
                    return True
                return (now - last_fetched) > ttl
        except sqlite3.Error:
            return True

    def clear_old_cache(self, max_age_seconds: int = 3600):
        """Remove data older than 1 hour by default to keep DB small."""
        cutoff = time.time() - max_age_seconds
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM ohlcv_cache WHERE fetched_at < ?", (cutoff,))
                if cursor.rowcount > 0:
                    logger.info(f"Cache CLEANUP: Removed {cursor.rowcount} stale records older than {max_age_seconds}s")
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error during cleanup: {e}")

# ==========================================
# EXAMPLE INTEGRATION WITH CCXT
# ==========================================
"""
import ccxt
import asyncio
from market_data_cache import MarketDataCache

async def fetch_ohlcv_with_cache(exchange, symbol, timeframe, limit=100):
    cache = MarketDataCache()
    
    # 1. Try to get from cache (TTL 60s)
    cached_data = cache.get(symbol, timeframe, max_age_seconds=60)
    
    if cached_data:
        return cached_data
        
    # 2. If not in cache or stale, fetch from API
    print(f"Fetching {symbol} from API...")
    try:
        # Rate limit protection via CCXT's built-in fetch_ohlcv
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        
        # 3. Store in cache
        cache.set(symbol, timeframe, ohlcv)
        return ohlcv
    except Exception as e:
        print(f"API Error: {e}")
        return None

async def main():
    exchange = ccxt.binance({'enableRateLimit': True})
    symbol = 'BTC/USDT'
    timeframe = '1m'
    
    # First call: Cache Miss -> API Call
    data1 = await fetch_ohlcv_with_cache(exchange, symbol, timeframe)
    
    # Second call (immediate): Cache Hit -> No API Call
    data2 = await fetch_ohlcv_with_cache(exchange, symbol, timeframe)
    
    await exchange.close()

if __name__ == "__main__":
    asyncio.run(main())
"""
