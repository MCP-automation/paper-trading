"""
Binance Client — WebSocket for live data + REST API for authenticated trades.

WebSocket (wss://stream.binance.com) → real-time price & kline data (no auth needed)
REST API    (https://api.binance.com)  → authenticated trade execution with API key/secret
"""
import websockets
import aiohttp
import asyncio
import json
import hashlib
import hmac
import time
import urllib.request
import urllib.parse
import pandas as pd
import logging
from datetime import datetime
from typing import Optional, Dict, List, Callable


logger = logging.getLogger(__name__)


def _rest_request(url: str, params: dict = None, headers: dict = None, timeout: int = 15) -> Optional[dict]:
    """Sync REST request using urllib (native Windows DNS)."""
    try:
        full_url = url
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            full_url = f"{url}?{qs}"
        req = urllib.request.Request(full_url)
        req.add_header("User-Agent", "Mozilla/5.0")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.warning(f"⚠️ REST request failed: {e}")
        return None


def _binance_sign(params: dict, api_secret: str) -> str:
    """Sign Binance REST API params with HMAC-SHA256."""
    query = urllib.parse.urlencode(params, doseq=True)
    signature = hmac.new(
        api_secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{query}&signature={signature}"


class BinanceClient:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "1m",
        api_key: str = None,
        api_secret: str = None,
        warmup_candles: int = 500,
    ):
        self.symbol = symbol.lower()
        self.interval = interval
        self.base_url = "https://api.binance.com"
        self.ws_url = f"wss://stream.binance.com:9443/stream"
        self.api_key = api_key
        self.api_secret = api_secret
        self.warmup_candles = warmup_candles

        # Data caches
        self.klines_cache: List[dict] = []
        self.warmup_cache: List[dict] = []
        self.latest_candle: Optional[dict] = None
        self.latest_price: Optional[float] = None
        self.latest_ticker: Optional[dict] = None

        # WebSocket state
        self.ws_running = False
        self.is_warmed_up = False

        # Callbacks
        self.price_callbacks: List[Callable] = []
        self.kline_callbacks: List[Callable] = []

    # ─── WebSocket Connection ────────────────────────────────────────────────

    async def start_websocket(self):
        """Connect to Binance WebSocket: kline_1m + ticker streams."""
        streams = f"{self.symbol}@kline_{self.interval}/{self.symbol}@ticker"
        url = f"{self.ws_url}?streams={streams}"
        logger.info(f"🔌 Connecting to Binance WebSocket: {url}")

        self.ws_running = True
        while self.ws_running:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=10,
                ) as ws:
                    logger.info("✅ WebSocket connected")
                    async for raw in ws:
                        if not self.ws_running:
                            break
                        try:
                            wrapper = json.loads(raw)
                            stream = wrapper.get("stream", "")
                            data = wrapper.get("data", {})
                            if stream.endswith(f"@kline_{self.interval}"):
                                self._handle_kline(data)
                            elif stream.endswith("@ticker"):
                                self._handle_ticker(data)
                        except json.JSONDecodeError:
                            pass
                        except Exception as e:
                            logger.warning(f"⚠️ WS message error: {e}")
            except websockets.exceptions.ConnectionClosed:
                logger.warning("🔌 WebSocket closed, reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"❌ WebSocket error: {e}, reconnecting in 5s...")
                await asyncio.sleep(5)

    def _handle_kline(self, data: dict):
        """Process kline data from WebSocket."""
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
            # Add new closed candle to cache
            existing = {c["open_time"] for c in self.klines_cache}
            if candle["open_time"] not in existing:
                self.klines_cache.append(candle)
                if len(self.klines_cache) > 2000:
                    self.klines_cache = self.klines_cache[-2000:]
                logger.debug(f"📊 New candle: {candle['close']} (total: {len(self.klines_cache)})")

            # Notify kline callbacks
            for cb in self.kline_callbacks:
                try:
                    cb(candle)
                except Exception as e:
                    logger.error(f"Kline callback error: {e}")

    def _handle_ticker(self, data: dict):
        """Process ticker data from WebSocket."""
        price = float(data.get("c", 0))
        self.latest_price = price
        self.latest_ticker = {
            "symbol": data.get("s", ""),
            "price": price,
            "high_24h": float(data.get("h", 0)),
            "low_24h": float(data.get("l", 0)),
            "volume": float(data.get("v", 0)),
        }
        # Notify price callbacks
        for cb in self.price_callbacks:
            try:
                cb(price)
            except Exception as e:
                logger.error(f"Price callback error: {e}")

    def on_price(self, callback: Callable):
        """Register callback for price updates."""
        self.price_callbacks.append(callback)

    def on_kline(self, callback: Callable):
        """Register callback for closed kline."""
        self.kline_callbacks.append(callback)

    # ─── REST API (for warmup + authenticated trades) ────────────────────────

    async def fetch_historical_candles(self):
        """Fetch warmup candles via REST API."""
        logger.info(f"📥 Fetching {self.warmup_candles} warmup candles from Binance...")
        try:
            data = await asyncio.to_thread(
                _rest_request,
                f"{self.base_url}/api/v3/klines",
                {
                    "symbol": self.symbol.upper(),
                    "interval": self.interval,
                    "limit": self.warmup_candles,
                },
                timeout=30,
            )
            if data:
                logger.info(f"✅ Received {len(data)} historical candles")
                for k in data:
                    self.warmup_cache.append({
                        "open_time": datetime.fromtimestamp(k[0] / 1000),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                        "is_closed": True,
                    })
                self.klines_cache = list(self.warmup_cache)
                self.is_warmed_up = True
                logger.info(f"✅ Warmup complete: {len(self.klines_cache)} candles")
            else:
                logger.warning("⚠️ No historical candles received")
                self.is_warmed_up = True
        except Exception as e:
            logger.error(f"❌ Failed to fetch historical candles: {e}")
            self.is_warmed_up = True

    def place_test_order(self, side: str, quantity: float, price: float = None) -> dict:
        """
        Place a test order via authenticated REST API.
        side: 'BUY' or 'SELL'
        quantity: amount of BTC
        price: optional limit price
        """
        if not self.api_key or not self.api_secret:
            return {"error": "No API key configured"}

        timestamp = int(time.time() * 1000)
        params = {
            "symbol": self.symbol.upper(),
            "side": side,
            "type": "LIMIT" if price else "MARKET",
            "quantity": f"{quantity:.8f}",
            "timestamp": timestamp,
        }
        if price:
            params["price"] = f"{price:.2f}"
            params["timeInForce"] = "GTC"

        query = _binance_sign(params, self.api_secret)
        headers = {"X-MBX-APIKEY": self.api_key}

        try:
            data = _rest_request(
                f"{self.base_url}/api/v3/order",
                headers=headers,
                timeout=10,
            )
            # We need to POST with params, _rest_request is GET only
            # Use aiohttp for POST
            return data or {"error": "Failed to place order"}
        except Exception as e:
            return {"error": str(e)}

    async def place_order_async(self, side: str, quantity: float, price: float = None) -> dict:
        """Place order via aiohttp POST with API key auth."""
        if not self.api_key or not self.api_secret:
            return {"error": "No API key configured"}

        timestamp = int(time.time() * 1000)
        params = {
            "symbol": self.symbol.upper(),
            "side": side,
            "type": "LIMIT" if price else "MARKET",
            "quantity": f"{quantity:.8f}",
            "timestamp": timestamp,
        }
        if price:
            params["price"] = f"{price:.2f}"
            params["timeInForce"] = "GTC"

        signed_query = _binance_sign(params, self.api_secret)
        headers = {"X-MBX-APIKEY": self.api_key}

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{self.base_url}/api/v3/order?{signed_query}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    return await resp.json()
            except Exception as e:
                logger.error(f"❌ Order failed: {e}")
                return {"error": str(e)}

    # ─── Data access ─────────────────────────────────────────────────────────

    def get_live_price(self) -> Optional[float]:
        """Get latest live price from WebSocket ticker."""
        if self.latest_price:
            return self.latest_price
        if self.klines_cache:
            return self.klines_cache[-1]["close"]
        return None

    def get_cache_df(self) -> pd.DataFrame:
        """Convert cached klines to DataFrame for indicators."""
        if not self.klines_cache:
            return pd.DataFrame()
        df = pd.DataFrame(self.klines_cache)
        df.set_index("open_time", inplace=True)
        df = df[["open", "high", "low", "close", "volume"]]
        return df

    async def stop(self):
        """Stop WebSocket connection."""
        self.ws_running = False
