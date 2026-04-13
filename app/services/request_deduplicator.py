import asyncio
import logging
from typing import Dict, Any, Callable, Awaitable, Tuple, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("RequestDeduplicator")

class RequestDeduplicator:
    """
    Prevents duplicate concurrent API calls for the same resource.
    If multiple strategies request the same symbol/timeframe at once, 
    only the first one triggers the API call; others wait for the result.
    """

    def __init__(self):
        # Maps (symbol, timeframe) -> asyncio.Future
        self._pending_requests: Dict[Tuple[str, str], asyncio.Future] = {}
        # Internal lock to protect dictionary operations
        self._lock = asyncio.Lock()

    async def deduplicate_request(
        self, 
        symbol: str, 
        timeframe: str, 
        fetch_function: Callable[[], Awaitable[Any]]
    ) -> Any:
        """
        Main entry point for fetching data with deduplication logic.
        
        :param symbol: Trading symbol (e.g., 'BTC/USDT')
        :param timeframe: Chart interval (e.g., '1m')
        :param fetch_function: An awaitable function that performs the actual API call
        :return: The result of the fetch_function (e.g., OHLCV list)
        """
        key = (symbol, timeframe)
        
        # 1. Check if a request is already in flight
        async with self._lock:
            if key in self._pending_requests:
                logger.info(f"DEDUPLICATE: Request in flight for {symbol} {timeframe}. Awaiting existing Future...")
                # Important: we return the result of the existing future
                return await self._pending_requests[key]
            
            # 2. This is the first request. Create a new Future and register it.
            future = asyncio.Future()
            self._pending_requests[key] = future

        # 3. Perform the actual API call
        try:
            logger.info(f"FETCH: Initiating primary API call for {symbol} {timeframe}")
            result = await fetch_function()
            
            # 4. Success: Set the result for all strategies awaiting this future
            async with self._lock:
                if not future.done():
                    future.set_result(result)
            return result
            
        except Exception as e:
            # 5. Error: Propagate the exception to all waiters
            logger.error(f"FETCH ERROR: Primary call for {symbol} {timeframe} failed: {e}")
            async with self._lock:
                if not future.done():
                    future.set_exception(e)
            raise e
            
        finally:
            # 6. Cleanup: Remove the future from the pending list so future requests can start fresh
            async with self._lock:
                if key in self._pending_requests and self._pending_requests[key] is future:
                    del self._pending_requests[key]

# ==========================================
# EXAMPLE USAGE
# ==========================================
"""
import ccxt.async_support as ccxt
import asyncio
from request_deduplicator import RequestDeduplicator

dedup = RequestDeduplicator()
exchange = ccxt.binance()

async def get_data_for_strategy(strategy_id):
    # Define the actual API task
    async def fetch_task():
        return await exchange.fetch_ohlcv('BTC/USDT', '1m', limit=5)

    print(f"Strategy {strategy_id} requesting data...")
    # Wrap it in deduplicator
    data = await dedup.deduplicate_request('BTC/USDT', '1m', fetch_task)
    print(f"Strategy {strategy_id} received {len(data)} candles.")
    return data

async def main():
    # Simulate 3 strategies firing at the exact same time
    await asyncio.gather(
        get_data_for_strategy("A"),
        get_data_for_strategy("B"),
        get_data_for_strategy("C")
    )
    await exchange.close()

if __name__ == "__main__":
    asyncio.run(main())
"""
