import asyncio
import logging
import time
from typing import Dict, List, Set, Any, Optional
import ccxt.async_support as ccxt

# Assuming standard package structure
from app.cache.market_data_cache import MarketDataCache
from app.services.request_deduplicator import RequestDeduplicator
from app.services.rate_limiter import AdaptiveRateLimiter
from app.services.metrics_collector import metrics as system_metrics

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("StrategyCoordinator")

class StrategyCoordinator:
    """
    Coordinates data fetching across multiple trading strategies.
    Ensures unique symbols are fetched only once, respects rate limits,
    deduplicates concurrent requests, and utilizes caching with stale fallback.
    """

    def __init__(
        self, 
        exchange: ccxt.Exchange, 
        cache: MarketDataCache, 
        rate_limiter: AdaptiveRateLimiter, 
        deduplicator: RequestDeduplicator
    ):
        self.exchange = exchange
        self.cache = cache
        self.rate_limiter = rate_limiter
        self.deduplicator = deduplicator
        
        # strategy_name -> set of symbols
        self.strategies: Dict[str, Set[str]] = {}
        # default timeframe
        self.timeframe = '1m'

    def register_strategy(self, name: str, symbols: List[str]):
        """Register a strategy and its required symbols."""
        self.strategies[name] = set(symbols)
        logger.info(f"REGISTER: Strategy '{name}' registered with {len(symbols)} symbols.")

    async def fetch_data_for_all_strategies(self) -> Dict[str, Dict[str, List]]:
        """
        Main loop: Fetches data for all unique symbols across all registered strategies.
        Returns a mapping of {strategy_name: {symbol: ohlcv_data}}.
        """
        start_time = time.time()
        
        # 1. Identify all unique symbols across all strategies
        all_unique_symbols = set()
        for symbols in self.strategies.values():
            all_unique_symbols.update(symbols)

        logger.info(f"COORDINATE: Starting batch fetch for {len(all_unique_symbols)} unique symbols.")

        # 2. Create fetching tasks for all unique symbols
        # We use gather to fetch them in parallel
        symbol_tasks = {
            symbol: self._fetch_single_symbol(symbol) 
            for symbol in all_unique_symbols
        }
        
        # 3. Execute all tasks in parallel
        results = await asyncio.gather(*symbol_tasks.values(), return_exceptions=True)
        
        # 4. Map results back to symbols
        symbol_data_map = {}
        for symbol, result in zip(symbol_tasks.keys(), results):
            if isinstance(result, Exception):
                logger.error(f"COORDINATE ERROR: Persistent failure for {symbol}: {result}")
                symbol_data_map[symbol] = None
            else:
                symbol_data_map[symbol] = result

        # 5. Distribute data back to strategies
        strategy_data = {}
        duration_ms = (time.time() - start_time) * 1000
        for strat_name, strat_symbols in self.strategies.items():
            strategy_data[strat_name] = {
                symbol: symbol_data_map.get(symbol)
                for symbol in strat_symbols
            }
            # Record execution time for each strategy (simplified as total batch time)
            system_metrics.record_strategy_exec(strat_name, duration_ms)

        return strategy_data

    async def _fetch_single_symbol(self, symbol: str) -> Optional[List]:
        """Internal logic for fetching a single symbol with cache, dedup, and rate limits."""
        
        # A. Try Cache First (60s TTL)
        cached_data = self.cache.get(symbol, self.timeframe, max_age_seconds=60)
        if cached_data:
            system_metrics.record_cache_hit()
            return cached_data

        system_metrics.record_cache_miss()

        # B. Define the fetch function to be passed to the deduplicator
        async def api_fetch_logic():
            # I. Wait for rate limit slot
            await self.rate_limiter.wait_if_needed('fetch_ohlcv')
            
            try:
                # II. Perform actual API call
                system_metrics.record_api_call()
                ohlcv = await self.exchange.fetch_ohlcv(symbol, self.timeframe, limit=100)
                
                # III. Update Cache and reset limiter cooldown on success
                self.cache.set(symbol, self.timeframe, ohlcv)
                self.rate_limiter.reset_cooldown('fetch_ohlcv')
                
                return ohlcv
            except Exception as e:
                # IV. Record 429 if applicable
                if "429" in str(e) or "Too Many Requests" in str(e):
                    system_metrics.record_429()
                raise e

        # C. Use Deduplicator to handle concurrent requests for the same symbol
        # Before calling deduplicator, we should check if another strategy is already fetching this
        # BUT the deduplicator itself handles this. We just need to know if it deduplicated.
        # Since RequestDeduplicator doesn't have a direct "was_deduplicated" return, 
        # we can wrap the fetch_logic or just assume if multiple calls happen fast, 
        # hits in RequestDeduplicator logic would be dedups.
        
        # To track dedups specifically, we'd need to modify RequestDeduplicator or 
        # check if key in pending_requests here.
        
        # Let's check pending_requests here for metrics purpose
        if symbol in self.deduplicator._pending_requests:
            system_metrics.record_dedup()

        try:
            return await self.deduplicator.deduplicate_request(symbol, self.timeframe, api_fetch_logic)
        except Exception as e:
            logger.warning(f"FETCH FAILED: Symbol {symbol} failed API call. Attempting stale fallback...")
            
            # D. Fallback: Return stale data from cache (up to 1 hour old)
            stale_data = self.cache.get(symbol, self.timeframe, max_age_seconds=3600)
            if stale_data:
                logger.info(f"FALLBACK SUCCESS: Using stale data for {symbol}")
                return stale_data
            
            # If even stale cache is missing, return None
            return None

# ==========================================
# EXAMPLE USAGE
# ==========================================
"""
import asyncio
import ccxt.async_support as ccxt
from app.cache.market_data_cache import MarketDataCache
from app.services.rate_limiter import AdaptiveRateLimiter
from app.services.request_deduplicator import RequestDeduplicator
from app.services.strategy_coordinator import StrategyCoordinator

async def main():
    # Initialize components
    exchange = ccxt.binance({'enableRateLimit': True})
    cache = MarketDataCache()
    limiter = AdaptiveRateLimiter()
    dedup = RequestDeduplicator()
    
    coordinator = StrategyCoordinator(exchange, cache, limiter, dedup)
    
    # Register strategies
    coordinator.register_strategy('momentum', ['BTC/USDT', 'ETH/USDT'])
    coordinator.register_strategy('grid', ['BNB/USDT', 'SOL/USDT'])
    coordinator.register_strategy('ml', ['BTC/USDT', 'ETH/USDT', 'BNB/USDT'])
    
    # Fetch data for all (will only call API for 4 unique symbols)
    data = await coordinator.fetch_data_for_all_strategies()
    
    # Access data
    btc_for_ml = data['ml']['BTC/USDT']
    print(f"Fetched {len(btc_for_ml)} candles for BTC/USDT in ML strategy")
    
    await exchange.close()

if __name__ == "__main__":
    asyncio.run(main())
"""
