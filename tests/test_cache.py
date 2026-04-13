import pytest
import os
import time
import sqlite3
import asyncio
from app.cache.market_data_cache import MarketDataCache

# Configuration for test database
TEST_DB = "test_market_cache.db"

@pytest.fixture
def cache():
    """Fixture to provide a clean MarketDataCache for each test."""
    # Ensure a fresh database for every test
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    
    cache_instance = MarketDataCache(db_path=TEST_DB, default_ttl=5)
    yield cache_instance
    
    # Cleanup after test
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

def test_insert_and_retrieve(cache):
    """1. Test that we can insert OHLCV data and retrieve it correctly."""
    symbol = "BTC/USDT"
    timeframe = "1m"
    # CCXT format: [timestamp, open, high, low, close, volume]
    data = [
        [1700000000000, 40000.0, 40100.0, 39900.0, 40050.0, 1.5],
        [1700000060000, 40050.0, 40200.0, 40050.0, 40150.0, 2.0]
    ]
    
    cache.set(symbol, timeframe, data)
    retrieved = cache.get(symbol, timeframe)
    
    assert retrieved is not None
    assert len(retrieved) == 2
    assert retrieved[0] == data[0]
    assert retrieved[1] == data[1]

def test_cache_expiration(cache):
    """2. Test that data older than TTL returns None."""
    symbol = "ETH/USDT"
    timeframe = "5m"
    data = [[1700000000000, 2000.0, 2100.0, 1900.0, 2050.0, 10.0]]
    
    # Set with a very short TTL (1 second)
    cache.set(symbol, timeframe, data)
    
    # Immediate get should work
    assert cache.get(symbol, timeframe, max_age_seconds=1) is not None
    
    # Wait for expiration
    time.sleep(1.1)
    
    # Should now return None
    assert cache.get(symbol, timeframe, max_age_seconds=1) is None

def test_symbol_isolation(cache):
    """3. Test that multiple symbols don't interfere with each other."""
    data_btc = [[1700000000000, 40000, 40000, 40000, 40000, 1]]
    data_eth = [[1700000000000, 2000, 2000, 2000, 2000, 1]]
    
    cache.set("BTC/USDT", "1m", data_btc)
    cache.set("ETH/USDT", "1m", data_eth)
    
    assert cache.get("BTC/USDT", "1m")[0][1] == 40000
    assert cache.get("ETH/USDT", "1m")[0][1] == 2000

def test_database_cleanup(cache):
    """4. Test that clear_old_cache removes data older than specified time."""
    symbol = "SOL/USDT"
    data = [[1700000000000, 50, 51, 49, 50, 100]]
    
    cache.set(symbol, "1m", data)
    
    # Verify data exists
    assert cache.get(symbol, "1m") is not None
    
    # Clear anything older than 0 seconds (everything)
    cache.clear_old_cache(max_age_seconds=0)
    
    # Manual check in DB to ensure it's actually deleted (not just hidden by TTL logic)
    with sqlite3.connect(TEST_DB) as conn:
        count = conn.execute("SELECT COUNT(*) FROM ohlcv_cache").fetchone()[0]
        assert count == 0

@pytest.mark.asyncio
async def test_concurrent_access(cache):
    """5. Test that multiple concurrent set/get operations don't crash the DB."""
    symbol = "BNB/USDT"
    
    async def task(i):
        data = [[1700000000000 + i, 300 + i, 301, 299, 300, 1]]
        cache.set(symbol, "1m", data)
        return cache.get(symbol, "1m")

    # Run 10 simultaneous tasks
    results = await asyncio.gather(*(task(i) for i in range(10)))
    
    assert len(results) == 10
    for r in results:
        assert r is not None

def test_edge_cases(cache):
    """6. Test edge cases like empty inputs or non-existent symbols."""
    # Non-existent symbol
    assert cache.get("UNKNOWN/USDT", "1m") is None
    
    # Empty data set
    cache.set("EMPTY/USDT", "1m", [])
    assert cache.get("EMPTY/USDT", "1m") is None
    
    # is_stale check for non-existent symbol
    assert cache.is_stale("NEW/SYMBOL", "1m") is True
    
    # Update existing data (Upsert check)
    initial_data = [[1000, 10, 10, 10, 10, 1]]
    updated_data = [[1000, 20, 20, 20, 20, 2]]
    
    cache.set("UPSERT/USDT", "1m", initial_data)
    cache.set("UPSERT/USDT", "1m", updated_data)
    
    res = cache.get("UPSERT/USDT", "1m")
    assert len(res) == 1
    assert res[0][1] == 20 # Should have been replaced
