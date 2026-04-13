import asyncio
import time
import logging
from collections import defaultdict
from typing import Dict, List, Any

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("RateLimiter")

class AdaptiveRateLimiter:
    """
    Adaptive Rate Limiter for Binance API.
    Enforces 5 req/sec and 1,200 req/min limits.
    Handles 429 errors with exponential backoff cooldowns.
    """

    def __init__(self):
        # endpoint -> list of timestamps of successful requests
        self._history: Dict[str, List[float]] = defaultdict(list)
        
        # endpoint -> timestamp when cooldown ends
        self._cooldown_until: Dict[str, float] = defaultdict(float)
        
        # endpoint -> number of consecutive 429s received
        self._consecutive_429s: Dict[str, int] = defaultdict(int)
        
        # Cooldown sequence: 60s, 120s, 300s (5 minutes)
        self._backoff_schedule = [60, 120, 300]
        
        self._lock = asyncio.Lock()

    async def wait_if_needed(self, endpoint: str):
        """
        Ensures the next request stays within rate limits.
        If limits are reached, it sleeps until a slot is available.
        """
        async with self._lock:
            while True:
                now = time.time()
                
                # 1. Check Cooldown Status
                if now < self._cooldown_until[endpoint]:
                    wait_time = self._cooldown_until[endpoint] - now
                    logger.warning(f"COOLDOWN: {endpoint} is restricted for another {wait_time:.1f}s")
                    await asyncio.sleep(wait_time)
                    now = time.time()

                # 2. Prune history (older than 60 seconds)
                self._history[endpoint] = [t for t in self._history[endpoint] if t > now - 60]
                history = self._history[endpoint]

                # 3. Check 1-Second Limit (Max 5)
                last_sec = [t for t in history if t > now - 1.0]
                if len(last_sec) >= 5:
                    # Wait until the oldest request in the last second is outside the window
                    wait_time = 1.0 - (now - last_sec[0]) + 0.01
                    logger.info(f"THROTTLE: 1s limit reached for {endpoint}. Waiting {wait_time:.3f}s")
                    await asyncio.sleep(wait_time)
                    continue # Re-check all limits after sleeping

                # 4. Check 1-Minute Limit (Max 1200)
                if len(history) >= 1200:
                    wait_time = 60.0 - (now - history[0]) + 0.1
                    logger.info(f"THROTTLE: 1m limit reached for {endpoint}. Waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)
                    continue

                # If we passed all checks, break the loop
                break

            # Record the request timestamp
            self._history[endpoint].append(time.time())

    def record_429(self, endpoint: str):
        """
        Marks an endpoint as having received a 429 Too Many Requests error.
        Activates/Increases cooldown period.
        """
        idx = min(self._consecutive_429s[endpoint], len(self._backoff_schedule) - 1)
        wait_seconds = self._backoff_schedule[idx]
        
        self._consecutive_429s[endpoint] += 1
        self._cooldown_until[endpoint] = time.time() + wait_seconds
        
        logger.error(f"429 ERROR: Received for {endpoint}. "
                     f"Entering cooldown for {wait_seconds}s. "
                     f"(Attempt #{self._consecutive_429s[endpoint]})")

    def reset_cooldown(self, endpoint: str):
        """Call this on a successful request to reset the backoff counter."""
        self._consecutive_429s[endpoint] = 0

    def get_stats(self, endpoint: str) -> Dict[str, Any]:
        """Returns usage statistics for monitoring."""
        now = time.time()
        history = self._history[endpoint]
        
        return {
            "requests_last_minute": len(history),
            "requests_last_second": len([t for t in history if t > now - 1]),
            "cooldown_active": now < self._cooldown_until[endpoint],
            "cooldown_remaining": max(0, self._cooldown_until[endpoint] - now),
            "consecutive_429s": self._consecutive_429s[endpoint]
        }

# ==========================================
# EXAMPLE USAGE
# ==========================================
"""
import ccxt.async_support as ccxt
import asyncio
from rate_limiter import AdaptiveRateLimiter

limiter = AdaptiveRateLimiter()
exchange = ccxt.binance()

async def safe_api_call():
    endpoint = 'fetch_ohlcv'
    
    # 1. Wait until a slot is free
    await limiter.wait_if_needed(endpoint)
    
    try:
        data = await exchange.fetch_ohlcv('BTC/USDT', '1m')
        # Success: reset backoff counter
        limiter.reset_cooldown(endpoint)
        return data
    except Exception as e:
        # Check if error is 429
        if "429" in str(e) or "Too Many Requests" in str(e):
            limiter.record_429(endpoint)
        raise e

async def main():
    # Spaming 10 requests fast
    tasks = [safe_api_call() for _ in range(10)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    print(limiter.get_stats('fetch_ohlcv'))
    await exchange.close()

if __name__ == "__main__":
    asyncio.run(main())
"""
