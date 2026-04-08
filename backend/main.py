import asyncio
import json
import os
import sys
import logging
import io
import pandas as pd
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Add backend to path for imports
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

logger.info(f"Python version: {sys.version}")

from data.binance import BinanceClient
from engine.paper_trade import PaperTradeEngine
from engine.indicators import IndicatorEngine
from models.database import get_engine, init_db
from monitoring import monitor, LogLevel, OperationType


# ─── Capture stdout/stderr for monitoring ────────────────────────────────────

class TerminalCapture(io.StringIO):
    def __init__(self, stream_name):
        super().__init__()
        self.stream_name = stream_name

    def write(self, text):
        super().write(text)
        if text.strip():
            monitor.log_terminal(text.strip(), self.stream_name)

    def flush(self):
        pass


sys.stdout = TerminalCapture("stdout")
sys.stderr = TerminalCapture("stderr")

# ─── Config ──────────────────────────────────────────────────────────────────

config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
config_path = os.path.abspath(config_path)
try:
    with open(config_path, "r") as f:
        config = json.load(f)
    logger.info(f"Config loaded: {config['binance']['symbol']} ({config['binance']['interval']})")
except Exception as e:
    logger.error(f"Config loading failed: {e}")
    config = {
        "binance": {"symbol": "BTCUSDT", "interval": "1m", "api_key": "", "api_secret": ""},
        "system": {"check_interval_seconds": 30, "db_path": "paper_trading.db", "host": "127.0.0.1", "port": 8000},
        "strategies": {
            "strategy1": {"enabled": True, "name": "Long-Only Breakout", "initial_capital": 10000, "risk_pct": 0.005, "ema_fast": 20, "ema_slow": 50, "atr_period": 14, "atr_sl_mult": 2.0, "atr_tp_mult": 2.0, "rsi_period": 14, "rsi_max": 75, "breakout_period": 10, "vol_mult": 1.1, "max_hold_hours": 2},
            "strategy2": {"enabled": True, "name": "Long-Only Relaxed", "initial_capital": 10000, "risk_pct": 0.008, "ema_fast": 25, "ema_slow": 60, "atr_period": 14, "atr_sl_mult": 1.8, "atr_tp_mult": 2.5, "rsi_period": 14, "rsi_max": 72, "breakout_period": 15, "vol_mult": 1.08, "fee": 0.0004},
            "strategy3": {"enabled": True, "name": "Long/Short Futures", "initial_capital": 10000, "risk_pct": 0.006, "reward_ratio": 2, "ema_fast": 15, "ema_slow": 35, "atr_period": 10, "atr_sl_mult": 2.5, "atr_tp_mult": 2.0, "rsi_period": 14, "breakout_period": 12, "fee": 0.0004, "slippage": 0.0005, "funding_rate": 0.0001},
            "strategy4": {"enabled": True, "name": "EMA Crossover RSI & Price Action", "initial_capital": 10000, "risk_pct": 0.015, "ema_fast": 8, "ema_slow": 21, "ema_short": 8, "ema_long": 21, "rsi_period": 9, "reward_ratio": 1.5, "fee": 0.0004},
            "strategy5": {"enabled": True, "name": "EMA 8/21 VWAP Momentum", "initial_capital": 10000, "risk_pct": 0.012, "ema_fast": 5, "ema_slow": 13, "rsi_period": 9, "vwap_period": 15, "momentum_period": 3, "reward_ratio": 1.8, "fee": 0.0004}
        }
    }

# ─── Globals ─────────────────────────────────────────────────────────────────

binance_client: BinanceClient = None
paper_engine: PaperTradeEngine = None
scheduler: AsyncIOScheduler = None
db_session = None
system_running = False
cycle_count = 0

# ─── SSE broadcast system ─────────────────────────────────────────────────────

_sse_clients: list[asyncio.Queue] = []


def _broadcast(event_type: str, data: dict):
    """Push an SSE event to all connected clients."""
    if not _sse_clients:
        return
    payload = {
        "type": event_type,
        "data": data,
        "ts": datetime.utcnow().isoformat(),
    }
    for queue in _sse_clients:
        try:
            asyncio.create_task(queue.put(payload))
        except RuntimeError:
            # Not in async context (shouldn't happen in normal use)
            pass


# ─── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global binance_client, paper_engine, scheduler, db_session, system_running

    try:
        logger.info("Starting paper trading system (live mode)...")
        monitor.log(LogLevel.INFO, OperationType.SYSTEM, "Paper trading system starting in LIVE mode")

        # Initialize database
        try:
            engine = get_engine(config["system"]["db_path"])
            db_session = init_db(engine)
            logger.info("Database initialized")
            monitor.log(LogLevel.SUCCESS, OperationType.SYSTEM, "Database initialized")
        except Exception as e:
            logger.error(f"Database error: {e}")
            from sqlalchemy import create_engine as mem_engine
            engine = mem_engine("sqlite:///:memory:")
            db_session = init_db(engine)
            logger.info("Using in-memory database fallback")

        # Initialize paper trading engine
        try:
            config_copy = json.loads(json.dumps(config))
            paper_engine = PaperTradeEngine(config_copy, db_session)
            logger.info(f"Paper trading engine initialized with {len(paper_engine.strategies)} strategies")
            monitor.log(LogLevel.SUCCESS, OperationType.SYSTEM, f"Paper trading engine initialized")
        except Exception as e:
            logger.error(f"Paper trading engine error: {e}")
            monitor.log_error("Paper trading engine initialization failed", exception=e)
            paper_engine = None

        # Initialize Binance client
        try:
            binance_client = BinanceClient(
                symbol=config["binance"]["symbol"],
                interval=config["binance"]["interval"],
                api_key=config["binance"].get("api_key"),
                api_secret=config["binance"].get("api_secret"),
                warmup_candles=config["binance"].get("warmup_candles", 200),
            )
            # Wire up SSE broadcast
            binance_client.sse_broadcast = _broadcast
            logger.info("Binance client created (live mode)")
            monitor.log(LogLevel.SUCCESS, OperationType.SYSTEM, f"Binance client initialized")
        except Exception as e:
            logger.error(f"Binance client error: {e}")
            monitor.log_error("Binance client initialization failed", exception=e)
            binance_client = None

        # ── Callbacks ──────────────────────────────────────────────────────

        def on_closed_kline(kline_data: dict):
            """Called when a 1m candle closes — run strategy logic."""
            if not kline_data.get("is_closed"):
                return
            if not binance_client or not binance_client.is_warmed_up:
                return
            if paper_engine:
                try:
                    df = binance_client.get_cache_df()
                    if len(df) >= 200:
                        paper_engine.process_new_bar(df)
                        # Broadcast closed candle update
                        if paper_engine.last_live_price:
                            _broadcast("live_price", {
                                "price": paper_engine.last_live_price,
                                "source": "kline_closed",
                            })
                except Exception as e:
                    monitor.log_error("Closed candle processing failed", exception=e)

        def on_ticker_update(ticker: dict):
            """Called every ~1s with live ticker data — run SL/TP checks."""
            if not ticker:
                return
            if paper_engine:
                try:
                    price = ticker.get("price", 0)
                    if price > 0:
                        exited = paper_engine.update_live_price(price)
                        # Broadcast live price always
                        _broadcast("live_price", {
                            "price": price,
                            "change_24h": ticker.get("price_change_pct", 0),
                            "high_24h": ticker.get("high_24h", 0),
                            "low_24h": ticker.get("low_24h", 0),
                            "volume": ticker.get("volume", 0),
                            "quote_volume": ticker.get("quote_volume", 0),
                            "bid": ticker.get("bid_price", 0),
                            "ask": ticker.get("ask_price", 0),
                            "source": "ticker",
                        })
                        # Broadcast trade updates if any exited
                        if exited:
                            _broadcast("trade_update", {"action": "live_exit"})
                except Exception as e:
                    monitor.log_error("Ticker processing failed", exception=e)

        # Register callbacks
        if binance_client:
            binance_client.on_kline_closed(on_closed_kline)
            binance_client.on_ticker(on_ticker_update)

        # Init session and start streams
        if binance_client:
            await binance_client.init_session()

            async def safe_streams():
                try:
                    await binance_client.start_live_streams()
                except Exception as e:
                    logger.error(f"Stream loop crashed: {e}")
                    monitor.log_error("Stream loop crashed", exception=e)

            asyncio.create_task(safe_streams())
            logger.info("Live streams task started (warmup + dual stream)")
            monitor.log(LogLevel.INFO, OperationType.SYSTEM, "Live streams connecting...")
        else:
            logger.error("Binance client not available, streams not started")

        # ── Scheduler: SL/TP check every 2s (belt-and-suspenders) ─────────
        try:
            scheduler = AsyncIOScheduler()

            async def sl_tp_check_job():
                """Fallback SL/TP check — ticker callback handles most."""
                if not binance_client or not paper_engine:
                    return
                price = binance_client.get_live_price()
                if price and price > 0:
                    try:
                        paper_engine.update_live_price(price)
                    except Exception as e:
                        monitor.log_error("SL/TP check failed", exception=e)

            scheduler.add_job(
                sl_tp_check_job,
                "interval",
                seconds=config["system"].get("sl_tp_check_interval", 2),
                max_instances=1,
            )
            scheduler.start()
            logger.info("Scheduler started")
        except Exception as e:
            logger.error(f"Scheduler error: {e}")

        system_running = True
        logger.info("========================================")
        logger.info("System started in LIVE mode!")
        logger.info("========================================")
        monitor.log(LogLevel.SUCCESS, OperationType.SYSTEM, "System fully operational in LIVE mode")

    except Exception as e:
        logger.error(f"Startup error: {e}")
        import traceback
        traceback.print_exc()

    yield

    # Shutdown
    logger.info("Shutting down...")
    system_running = False
    if scheduler:
        try:
            scheduler.shutdown()
        except Exception:
            pass
    if binance_client:
        try:
            await binance_client.stop()
        except Exception:
            pass
    logger.info("Shutdown complete")


# ─── FastAPI App ─────────────────────────────────────────────────────────────

app = FastAPI(title="Binance Paper Trading — Live", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")


@app.get("/")
async def serve_frontend():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/monitor")
async def serve_monitor():
    return FileResponse(os.path.join(FRONTEND_DIR, "monitor.html"))


@app.get("/terminal")
async def serve_terminal():
    return FileResponse(os.path.join(FRONTEND_DIR, "terminal.html"))


# ─── SSE Stream ─────────────────────────────────────────────────────────────

@app.get("/api/stream")
async def sse_stream(request: Request):
    """
    Server-Sent Events endpoint.
    Clients connect via EventSource('/api/stream') and receive live updates
    with no polling.
    """
    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        _sse_clients.append(queue)
        logger.info(f"SSE client connected (total: {len(_sse_clients)})")
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive comment every 30s
                    yield f": keepalive\n\n"
        finally:
            _sse_clients.remove(queue)
            logger.info(f"SSE client disconnected (total: {len(_sse_clients)})")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── API Endpoints ──────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    """Get system status with live data."""
    status = {
        "running": system_running,
        "symbol": config["binance"]["symbol"],
        "interval": "1m",
        "warmup_complete": binance_client.is_warmed_up if binance_client else False,
        "warmup_candles": len(binance_client.warmup_cache) if binance_client else 0,
        "sse_clients": len(_sse_clients),
    }
    if binance_client:
        ticker = binance_client.latest_ticker
        if ticker:
            status["live_price"] = ticker.get("price")
            status["change_24h"] = ticker.get("price_change_pct")
            status["high_24h"] = ticker.get("high_24h")
            status["low_24h"] = ticker.get("low_24h")
            status["volume"] = ticker.get("volume")
            status["quote_volume"] = ticker.get("quote_volume")
        else:
            status["live_price"] = None
    if paper_engine:
        status["strategies"] = {}
        for name, strat in paper_engine.strategies.items():
            status["strategies"][name] = {
                "enabled": strat.enabled,
                "capital": round(strat.capital, 2),
                "in_trade": strat.in_trade,
            }
    return status


@app.get("/api/current-price")
async def get_current_price():
    """Get current live price (from ticker stream, no HTTP call)."""
    if not binance_client:
        return {"price": None, "source": "none"}
    ticker = binance_client.latest_ticker
    if ticker:
        return {
            "price": ticker.get("price"),
            "change_24h": ticker.get("price_change_pct"),
            "high_24h": ticker.get("high_24h"),
            "low_24h": ticker.get("low_24h"),
            "volume": ticker.get("volume"),
            "quote_volume": ticker.get("quote_volume"),
            "bid": ticker.get("bid_price"),
            "ask": ticker.get("ask_price"),
            "source": "live_ticker",
        }
    candle = binance_client.latest_candle
    if candle:
        return {
            "price": candle.get("close"),
            "source": "live_candle",
        }
    return {"price": None, "source": "none"}


@app.get("/api/metrics")
async def get_metrics():
    if not paper_engine:
        return {"error": "Engine not initialized"}
    try:
        return paper_engine.get_performance_metrics()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/active-trades")
async def get_active_trades():
    if not paper_engine:
        return []
    try:
        return paper_engine.get_active_trades()
    except Exception:
        return []


@app.get("/api/trade-history")
async def get_trade_history(strategy: str = None, limit: int = 50):
    if not paper_engine:
        return []
    try:
        return paper_engine.get_trade_history(strategy, limit)
    except Exception:
        return []


@app.get("/api/equity-curve")
async def get_equity_curve(strategy: str = None, limit: int = 500):
    if not paper_engine:
        return {}
    try:
        return paper_engine.get_equity_curve(strategy, limit)
    except Exception:
        return {}


@app.get("/api/analytics")
async def get_analytics():
    if not paper_engine:
        return {"error": "Engine not initialized"}
    try:
        return paper_engine.get_trade_analytics()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/drawdown")
async def get_drawdown():
    if not paper_engine:
        return {}
    try:
        return paper_engine.get_drawdown_series()
    except Exception:
        return {}


@app.get("/api/signals")
async def get_signals(limit: int = 50):
    if not paper_engine:
        return []
    try:
        return paper_engine.get_recent_signals(limit)
    except Exception:
        return []


@app.get("/api/signals-debug")
async def get_signals_debug():
    if not paper_engine or not binance_client:
        return {"error": "Engine or Binance client not initialized"}
    try:
        df = binance_client.get_cache_df()
        if len(df) < 2:
            return {"error": "Not enough data", "candles": len(df)}
        results = {}
        current_idx = len(df) - 1
        for strat_name, strategy in paper_engine.strategies.items():
            if not strategy.enabled:
                results[strat_name] = {"enabled": False}
                continue
            strat_config = config["strategies"][strat_name]
            df_with_indicators = IndicatorEngine.compute_all_indicators(df.copy(), strat_config)
            debug_info = strategy.get_signal_debug(df_with_indicators, current_idx)
            if debug_info:
                debug_info["enabled"] = True
                if "conditions" in debug_info:
                    for cond_name, cond_info in debug_info["conditions"].items():
                        for key, value in cond_info.items():
                            if hasattr(value, "item"):
                                cond_info[key] = value.item()
                results[strat_name] = debug_info
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "candles": len(df),
            "current_price": df.iloc[-1]["close"],
            "is_warmed_up": binance_client.is_warmed_up,
            "strategies": results,
        }
    except Exception as e:
        import traceback
        return {"error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/klines")
async def get_klines(limit: int = 200):
    """Get cached klines (live from stream)."""
    if binance_client:
        try:
            df = binance_client.get_cache_df()
            recent = df.tail(limit)
            records = recent.reset_index().to_dict("records")
            # Include current open candle if available
            if binance_client.latest_candle and not binance_client.latest_candle.get("is_closed", True):
                records.append(binance_client.latest_candle)
            return records
        except Exception:
            pass
    return []


# ─── Strategy Controls ───────────────────────────────────────────────────────

class StrategyToggle(BaseModel):
    enabled: bool


@app.post("/api/strategies/{strategy_name}/toggle")
async def toggle_strategy(strategy_name: str, body: StrategyToggle):
    if not paper_engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    if strategy_name not in paper_engine.strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    paper_engine.toggle_strategy(strategy_name, body.enabled)
    return {"status": "ok", "strategy": strategy_name, "enabled": body.enabled}


class RiskUpdate(BaseModel):
    risk_pct: float = None
    reward_ratio: float = None


@app.post("/api/strategies/{strategy_name}/risk")
async def update_risk_params(strategy_name: str, body: RiskUpdate):
    if strategy_name not in config["strategies"]:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if body.risk_pct is not None:
        config["strategies"][strategy_name]["risk_pct"] = body.risk_pct
    if body.reward_ratio is not None:
        config["strategies"][strategy_name]["reward_ratio"] = body.reward_ratio
    return {"status": "ok", "config": config["strategies"][strategy_name]}


# ─── Bloomberg Terminal Endpoints ────────────────────────────────────────────

@app.get("/api/market/orderbook/{symbol}")
async def get_order_book(symbol: str, limit: int = 20):
    if binance_client:
        try:
            return await binance_client.get_order_book(symbol, limit)
        except Exception as e:
            return {"error": str(e), "bids": [], "asks": []}
    return {"error": "Binance client not initialized", "bids": [], "asks": []}


@app.get("/api/market/trades/{symbol}")
async def get_recent_trades(symbol: str, limit: int = 50):
    if binance_client:
        try:
            return await binance_client.get_recent_trades(symbol, limit)
        except Exception as e:
            return {"error": str(e), "trades": []}
    return {"error": "Binance client not initialized", "trades": []}


@app.get("/api/market/ticker/{symbol}")
async def get_24hr_ticker(symbol: str):
    if binance_client:
        try:
            return await binance_client.get_24hr_ticker(symbol)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "Binance client not initialized"}


@app.get("/api/market/top-symbols")
async def get_top_symbols(limit: int = 20):
    if binance_client:
        try:
            return await binance_client.get_top_symbols(limit)
        except Exception as e:
            return {"error": str(e), "symbols": []}
    return {"error": "Binance client not initialized", "symbols": []}


@app.get("/api/market/watchlist")
async def get_watchlist():
    if binance_client:
        try:
            top_symbols = await binance_client.get_top_symbols(10)
            watchlist = []
            for ticker in top_symbols:
                sym = ticker.get("symbol", "")
                if sym:
                    watchlist.append({
                        "symbol": sym,
                        "price": float(ticker.get("lastPrice", 0)),
                        "change": float(ticker.get("priceChangePercent", 0)),
                        "volume": float(ticker.get("volume", 0)),
                        "high": float(ticker.get("highPrice", 0)),
                        "low": float(ticker.get("lowPrice", 0)),
                    })
            return {"watchlist": watchlist}
        except Exception as e:
            return {"error": str(e), "watchlist": []}
    return {"error": "Binance client not initialized", "watchlist": []}


# ─── Monitoring Endpoints ────────────────────────────────────────────────────

@app.get("/api/monitor/logs")
async def get_monitor_logs(limit: int = 100, level: str = None, strategy: str = None):
    logs = monitor.get_logs(limit=limit, level=level, strategy=strategy)
    return {"logs": logs, "total": len(logs), "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/monitor/status")
async def get_monitor_status():
    return {"status": monitor.get_current_status(), "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/monitor/activity")
async def get_monitor_activity(strategy: str = None):
    logs = monitor.get_logs(limit=50, strategy=strategy)
    activity = {
        "api_calls": [l for l in logs if l["type"] == "API_CALL"],
        "data_fetches": [l for l in logs if l["type"] == "DATA_FETCH"],
        "signal_checks": [l for l in logs if l["type"] == "SIGNAL_CHECK"],
        "trades": [l for l in logs if l["type"] in ["TRADE_OPEN", "TRADE_CLOSE"]],
        "errors": [l for l in logs if l["level"] == "ERROR"],
        "warnings": [l for l in logs if l["level"] == "WARNING"],
    }
    return {
        "activity": activity,
        "summary": monitor.get_current_status(),
        "timestamp": datetime.utcnow().isoformat(),
    }


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting uvicorn on {config['system']['host']}:{config['system']['port']}")
    uvicorn.run(
        app,
        host=config["system"]["host"],
        port=config["system"]["port"],
        reload=False,
    )
