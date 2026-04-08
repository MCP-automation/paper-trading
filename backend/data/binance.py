import aiohttp
import asyncio
import websockets
import json
import pandas as pd
import time
from datetime import datetime
from typing import Optional, Callable, List
from monitoring import monitor, LogLevel, OperationType
import ccxt


class BinanceClient:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "1m",
        api_key: str = None,
        api_secret: str = None,
        warmup_candles: int = 200,
    ):
        self.symbol = symbol.lower()
        self.interval = interval
        self.base_url = "https://api.binance.com"
        self.warmup_candles = warmup_candles
        self.session: Optional[aiohttp.ClientSession] = None

        # CCXT exchange
        exchange_params = {"enableRateLimit": True}
        if api_key and api_secret:
            exchange_params["apiKey"] = api_key
            exchange_params["secret"] = api_secret
        self.exchange = ccxt.binance(exchange_params)

        # Live data caches
        self.klines_cache: List[dict] = []
        self.warmup_cache: List[dict] = []
        self.latest_candle: Optional[dict] = None
        self.latest_ticker: Optional[dict] = None

        # Stream state
        self.running = False
        self.is_warmed_up = False

        # Subscriber callbacks
        self.kline_callbacks: List[Callable] = []
        self.ticker_callbacks: List[Callable] = []
        self.warmup_callbacks: List[Callable] = []

        # Background tasks
        self._warmup_task: Optional[asyncio.Task] = None
        self._stream_task: Optional[asyncio.Task] = None

        # SSE broadcast fn set externally by main.py
        self.sse_broadcast: Optional[Callable] = None

    # ─── Session ───────────────────────────────────────────────────────────────

    async def init_session(self):
        if not self.session:
            connector = aiohttp.TCPConnector(
                verify_ssl=True,
                ttl_dns_cache=30,
                use_dns_cache=True,
                limit=10,
                limit_per_host=2,
            )
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=30, sock_read=10, sock_connect=10),
            )

    async def close(self):
        self.running = False
        if self.session:
            await self.session.close()
            self.session = None

    async def stop(self):
        await self.close()

    # ─── Subscriber registration ───────────────────────────────────────────────

    def on_kline_closed(self, callback: Callable):
        """Register callback for closed 1m kline events"""
        self.kline_callbacks.append(callback)

    def on_ticker(self, callback: Callable):
        """Register callback for 1s ticker updates"""
        self.ticker_callbacks.append(callback)

    def on_warmup_progress(self, callback: Callable):
        """Register callback for warmup progress events"""
        self.warmup_callbacks.append(callback)

    # ─── Live streams ─────────────────────────────────────────────────────────

    async def start_live_streams(self):
        """
        Phase 1: Fetch warmup candles from REST API (~instant).
        Phase 2: Dual stream: 1m klines + 1s ticker.
        """
        self.running = True
        print(f"[BINANCE] Starting live streams for {self.symbol}")
        await self._fetch_historical_candles()
        print(f"[BINANCE] Historical candles fetched, starting WebSocket...")
        await self._dual_stream_phase()

    async def _fetch_historical_candles(self):
        """Fetch warmup_candles from Binance REST API."""
        await self.init_session()
        url = f"{self.base_url}/api/v3/klines"
        params = {
            "symbol": self.symbol.upper(),
            "interval": self.interval,
            "limit": self.warmup_candles,
        }

        monitor.log(
            LogLevel.INFO,
            OperationType.SYSTEM,
            f"Fetching {self.warmup_candles} historical candles from REST API",
        )

        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for k in data:
                        candle = {
                            "open_time": datetime.fromtimestamp(k[0] / 1000),
                            "open": float(k[1]),
                            "high": float(k[2]),
                            "low": float(k[3]),
                            "close": float(k[4]),
                            "volume": float(k[5]),
                            "is_closed": True,
                        }
                        self.warmup_cache.append(candle)

                    self._seed_cache_from_warmup()
                    self.is_warmed_up = True
                    monitor.log(
                        LogLevel.SUCCESS,
                        OperationType.SYSTEM,
                        f"Warmup complete: {len(self.klines_cache)} candles loaded from REST API",
                    )
                    if self.sse_broadcast:
                        self.sse_broadcast(
                            "warmup_complete",
                            {"candles": len(self.klines_cache), "ready": True},
                        )
                    for cb in self.warmup_callbacks:
                        try:
                            cb(self.warmup_candles, self.warmup_candles)
                        except Exception:
                            pass
                else:
                    monitor.log_error(
                        f"Failed to fetch historical candles: HTTP {resp.status}"
                    )
                    # Fallback to empty cache
                    self._seed_cache_from_warmup()
                    self.is_warmed_up = True
        except Exception as e:
            monitor.log_error(f"Error fetching historical candles: {e}")
            # Fallback to empty cache
            self._seed_cache_from_warmup()
            self.is_warmed_up = True

    def _seed_cache_from_warmup(self):
        """Move warmup cache into the live klines cache."""
        self.klines_cache = []
        for row in self.warmup_cache:
            self.klines_cache.append(
                {
                    "open_time": row["open_time"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"],
                    "is_closed": True,
                }
            )

    async def _dual_stream_phase(self):
        """
        Run dual stream: 1m klines (strategy signals) + 1s ticker (live price).
        Single combined WebSocket connection via Binance streams.
        """
        combined_url = (
            f"wss://stream.binance.com:9443/stream"
            f"?streams={self.symbol}@kline_1m/{self.symbol}@ticker"
        )

        while self.running:
            try:
                async with websockets.connect(
                    combined_url,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=10,
                ) as ws:
                    monitor.log(
                        LogLevel.INFO,
                        OperationType.SYSTEM,
                        "Dual stream connected: kline_1m + ticker",
                    )
                    async for raw in ws:
                        if not self.running:
                            break
                        try:
                            wrapper = json.loads(raw)
                            stream = wrapper.get("stream", "")
                            data = wrapper.get("data", {})
                            if stream.endswith("@kline_1m"):
                                self._handle_kline_stream(data)
                            elif stream.endswith("@ticker"):
                                self._handle_ticker_stream(data)
                        except Exception:
                            pass
            except Exception as e:
                monitor.log_error(f"Dual stream error: {e}")
                await asyncio.sleep(5)

    def _handle_kline_stream(self, data: dict):
        """Process incoming 1m kline from stream."""
        k = data.get("k", {})
        candle = {
            "open_time": datetime.fromtimestamp(k["t"] / 1000),
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
            "is_closed": k["x"],
        }

        self.latest_candle = candle

        if candle["is_closed"]:
            if (
                self.klines_cache
                and self.klines_cache[-1]["open_time"] == candle["open_time"]
            ):
                self.klines_cache[-1] = candle
            else:
                self.klines_cache.append(candle)
                if len(self.klines_cache) > 1000:
                    self.klines_cache.pop(0)

            for cb in self.kline_callbacks:
                try:
                    cb(candle)
                except Exception:
                    pass

    def _handle_ticker_stream(self, data: dict):
        """Process incoming 1s ticker from stream."""
        self.latest_ticker = {
            "symbol": data.get("s", ""),
            "price": float(data.get("c", 0)),
            "price_change": float(data.get("p", 0)),
            "price_change_pct": float(data.get("P", 0)),
            "high_24h": float(data.get("h", 0)),
            "low_24h": float(data.get("l", 0)),
            "volume": float(data.get("v", 0)),
            "quote_volume": float(data.get("q", 0)),
            "bid_price": float(data.get("b", 0)),
            "ask_price": float(data.get("a", 0)),
            "open_24h": float(data.get("o", 0)),
        }

        for cb in self.ticker_callbacks:
            try:
                cb(self.latest_ticker)
            except Exception:
                pass

    # ─── Data access ─────────────────────────────────────────────────────────

    def get_live_price(self) -> Optional[float]:
        """Get the latest live price from ticker stream."""
        if self.latest_ticker:
            return self.latest_ticker["price"]
        if self.latest_candle:
            return self.latest_candle["close"]
        return None

    def get_cache_df(self) -> pd.DataFrame:
        """Convert cached klines to DataFrame for indicator computation."""
        if not self.klines_cache:
            return pd.DataFrame()
        df = pd.DataFrame(self.klines_cache)
        df.set_index("open_time", inplace=True)
        df = df[["open", "high", "low", "close", "volume"]]
        return df

    def seed_cache(self, df: pd.DataFrame) -> None:
        """Seed the internal cache with a DataFrame (backward compat)."""
        self.klines_cache = []
        for idx, row in df.reset_index().iterrows():
            self.klines_cache.append(
                {
                    "open_time": row["open_time"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "is_closed": True,
                }
            )

    # ─── Market data REST (for terminal) ────────────────────────────────────

    async def get_order_book(self, symbol: str = None, limit: int = 20) -> dict:
        if not symbol:
            symbol_ccxt = self.symbol.upper().replace("USDT", "/USDT")
        else:
            symbol_ccxt = symbol.upper().replace("USDT", "/USDT")
        try:
            start_time = time.time()
            order_book = await asyncio.to_thread(
                self.exchange.fetch_order_book, symbol_ccxt, limit=limit
            )
            duration = time.time() - start_time
            monitor.log_api_call(
                "ccxt_order_book", "GET", status=200, duration=duration
            )
            return order_book
        except Exception as e:
            monitor.log_error(f"Order book fetch error", exception=e)
            return {"bids": [], "asks": []}

    async def get_recent_trades(self, symbol: str = None, limit: int = 50) -> list:
        if not symbol:
            symbol_ccxt = self.symbol.upper().replace("USDT", "/USDT")
        else:
            symbol_ccxt = symbol.upper().replace("USDT", "/USDT")
        try:
            start_time = time.time()
            ccxt_trades = await asyncio.to_thread(
                self.exchange.fetch_trades, symbol_ccxt, limit=limit
            )
            duration = time.time() - start_time
            mapped_trades = []
            for t in ccxt_trades:
                mapped_trades.append(
                    {
                        "id": t.get("id"),
                        "price": str(t.get("price")),
                        "qty": str(t.get("amount")),
                        "quoteQty": str(t.get("cost", 0)),
                        "time": t.get("timestamp"),
                        "isBuyerMaker": t.get("side") == "sell",
                        "isBestMatch": True,
                    }
                )
            monitor.log_api_call("ccxt_trades", "GET", status=200, duration=duration)
            return mapped_trades
        except Exception as e:
            monitor.log_error(f"Recent trades fetch error", exception=e)
            return []

    async def get_24hr_ticker(self, symbol: str = None) -> dict:
        if not symbol:
            symbol_ccxt = self.symbol.upper().replace("USDT", "/USDT")
        else:
            symbol_ccxt = symbol.upper().replace("USDT", "/USDT")
        try:
            ticker = await asyncio.to_thread(self.exchange.fetch_ticker, symbol_ccxt)
            mapped_ticker = {
                "symbol": ticker.get("symbol", "").replace("/", ""),
                "priceChange": str(ticker.get("change", 0)),
                "priceChangePercent": str(ticker.get("percentage", 0)),
                "weightedAvgPrice": str(ticker.get("vwap", 0)),
                "prevClosePrice": str(ticker.get("previousClose", 0)),
                "lastPrice": str(ticker.get("last", 0)),
                "lastQty": "0",
                "bidPrice": str(ticker.get("bid", 0)),
                "askPrice": str(ticker.get("ask", 0)),
                "openPrice": str(ticker.get("open", 0)),
                "highPrice": str(ticker.get("highPrice", 0)),
                "lowPrice": str(ticker.get("lowPrice", 0)),
                "volume": str(ticker.get("baseVolume", 0)),
                "quoteVolume": str(ticker.get("quoteVolume", 0)),
                "openTime": ticker.get("timestamp", 0) - 86400000,
                "closeTime": ticker.get("timestamp", 0),
                "firstId": 0,
                "lastId": 0,
                "count": 0,
            }
            monitor.log_api_call("ccxt_ticker", "GET", status=200, duration=0)
            return mapped_ticker
        except Exception as e:
            monitor.log_error(f"24hr ticker fetch error", exception=e)
            return {}

    async def get_top_symbols(self, limit: int = 20) -> list:
        try:
            start_time = time.time()
            tickers = await asyncio.to_thread(self.exchange.fetch_tickers)
            duration = time.time() - start_time
            usdt_tickers = []
            for sym, ticker in tickers.items():
                if sym.endswith("/USDT"):
                    usdt_tickers.append(ticker)
            usdt_tickers.sort(key=lambda x: x.get("quoteVolume", 0) or 0, reverse=True)
            monitor.log_api_call(
                "ccxt_top_symbols", "GET", status=200, duration=duration
            )
            return usdt_tickers[:limit]
        except Exception as e:
            monitor.log_error(f"Top symbols fetch error", exception=e)
            return []

    async def get_exchange_info(self) -> dict:
        try:
            start_time = time.time()
            markets = await asyncio.to_thread(self.exchange.load_markets)
            duration = time.time() - start_time
            monitor.log_api_call(
                "ccxt_exchange_info", "GET", status=200, duration=duration
            )
            symbols = []
            for sym, market in markets.items():
                if sym.endswith("/USDT"):
                    symbols.append(
                        {
                            "symbol": market["id"],
                            "base_asset": market["base"],
                            "quote_asset": market["quote"],
                        }
                    )
            return {"symbols": symbols, "ccxt_markets": markets}
        except Exception as e:
            monitor.log_error(f"Exchange info fetch error", exception=e)
            return {"symbols": []}
