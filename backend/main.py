# 🚀 BINANCE PAPER TRADING BACKEND - FINAL STABLE VERSION

import asyncio
import json
import os
import sys
import logging
import traceback
import pandas as pd
from typing import List
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Load environment variables from .env file (must be before config loading)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, fall back to system env vars

# Add backend to path
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Configure logging to be VERY loud so we see everything
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Load config safely
try:
    config_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "config.json")
    )
    with open(config_path, "r") as f:
        config = json.load(f)
    logger.info(f"✅ Config loaded from {config_path}")
except Exception as e:
    logger.error(f"❌ Failed to load config: {e}")
    config = {}

from data.binance import BinanceClient
from engine.paper_trade import PaperTradeEngine
from engine.indicators import IndicatorEngine
from engine.signal_logger import log_signal
from engine.trade_executor import log_trade_attempt, log_trade_result, log_strategy_state_change
from models.database import get_engine, init_db, Trade, EquitySnapshot, StrategyStatus

# ─────────────────────────────────────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────────────────────────────────────

binance_client = None
paper_engine = None
scheduler = None
db_session = None
system_running = False
_ws_clients = []


def get_internal_summary():
    status = {
        "running": system_running,
        "symbol": config.get("binance", {}).get("symbol", "BTCUSDT"),
        "warmup_complete": binance_client.is_warmed_up if binance_client else False,
        "live_price": binance_client.latest_ticker.get("price")
        if binance_client and binance_client.latest_ticker
        else None,
        "strategies": {},
    }
    if paper_engine:
        for name, strat in paper_engine.strategies.items():
            status["strategies"][name] = {"enabled": strat.enabled}

    metrics = {}
    active_list = []
    if paper_engine:
        for name, strat in paper_engine.strategies.items():
            # Get trade count for this strategy (closed only)
            trade_count = 0
            win_rate = 0
            closed_trades = []
            if db_session:
                closed_trades = (
                    db_session.query(Trade)
                    .filter_by(strategy_name=name, status="closed")
                    .all()
                )
                trade_count = len(closed_trades)
                if trade_count > 0:
                    wins = sum(1 for t in closed_trades if t.pnl and t.pnl > 0)
                    win_rate = round(wins / trade_count * 100, 2)

                # Get open trades for this strategy
                open_trades = (
                    db_session.query(Trade)
                    .filter_by(strategy_name=name, status="open")
                    .all()
                )
                for t in open_trades:
                    live_pnl = 0
                    current_price = status.get("live_price")
                    if current_price and t.entry_price and t.units:
                        if t.direction == "long":
                            live_pnl = (current_price - t.entry_price) * t.units
                        elif t.direction == "short":
                            live_pnl = (t.entry_price - current_price) * t.units

                    active_list.append(
                        {
                            "strategy": name,
                            "direction": t.direction,
                            "entry_price": t.entry_price,
                            "stop_loss": t.stop_loss,
                            "take_profit": t.take_profit,
                            "current_pnl": round(live_pnl, 2),
                        }
                    )

            logger.info(
                f"📊 [{name}] capital={strat.capital}, initial={getattr(strat, 'initial_capital', 10000)}"
            )

            # Calculate current capital from closed trades + initial capital
            current_capital = getattr(strat, "initial_capital", 10000)
            if db_session:
                for t in closed_trades:
                    if t.pnl:
                        current_capital += t.pnl

            return_pct = 0
            if getattr(strat, "initial_capital", 10000) > 0:
                return_pct = round(
                    (current_capital - getattr(strat, "initial_capital", 10000))
                    / getattr(strat, "initial_capital", 10000)
                    * 100,
                    2,
                )

            metrics[name] = {
                "name": config.get("strategies", {}).get(name, {}).get("name", name),
                "current_equity": round(current_capital, 2),
                "return_pct": return_pct,
                "trade_count": trade_count,
                "win_rate": win_rate,
                "enabled": strat.enabled,
            }
    return {"status": status, "active_trades": active_list, "metrics_summary": metrics}


async def broadcast(data: dict):
    """Push data to all connected WebSocket clients"""
    if not _ws_clients:
        return

    disconnected = []
    for client in _ws_clients:
        try:
            # Check if connection is still open before sending
            if client.client_state.value == 1:  # CONNECTED
                await client.send_json(data)
            else:
                disconnected.append(client)
        except Exception:
            disconnected.append(client)

    for client in disconnected:
        if client in _ws_clients:
            _ws_clients.remove(client)


# ─────────────────────────────────────────────────────────────────────────────
# BROADCAST LOGIC
# ─────────────────────────────────────────────────────────────────────────────


async def broadcast_summary():
    """Scheduled task to push summary data"""
    try:
        data = get_internal_summary()
        await broadcast({"type": "summary", "data": data})
    except Exception as e:
        logger.error(f"Summary broadcast failed: {e}")


async def broadcast_price(price: float):
    """Push price update"""
    logger.info(f"📡 Broadcasting price: {price}")
    await broadcast({"type": "price", "price": price})


async def broadcast_warmup(completed: int, total: int):
    """Push warmup progress"""
    await broadcast({"type": "warmup", "completed": completed, "total": total})


async def heartbeat():
    """Send heartbeat to keep connection alive"""
    while True:
        await asyncio.sleep(10)
        await broadcast({"type": "heartbeat", "timestamp": datetime.now().isoformat()})


# ─────────────────────────────────────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global binance_client, paper_engine, scheduler, db_session, system_running
    logger.info("⚡ LIFESPAN STARTING")

    engine = get_engine(config["system"]["db_path"])
    db_session = init_db(engine)
    paper_engine = PaperTradeEngine(config, db_session)
    # Get API keys from .env (preferred) or config.json (fallback)
    api_key = os.environ.get("BINANCE_API_KEY") or config["binance"].get("api_key")
    api_secret = os.environ.get("BINANCE_API_SECRET") or config["binance"].get("api_secret")

    binance_client = BinanceClient(
        symbol=config["binance"]["symbol"],
        interval=config["binance"]["interval"],
        api_key=api_key,
        api_secret=api_secret,
        warmup_candles=config["binance"].get("warmup_candles", 500),
    )

    # ─── Step 1: Fetch historical candles via REST ──────────────────────────
    await binance_client.fetch_historical_candles()

    # ─── Step 2: Start WebSocket for live data ─────────────────────────────

    def on_ws_price_sync(price: float):
        """Handle price update from WebSocket (sync wrapper)."""
        if paper_engine:
            try:
                paper_engine.update_live_price(price)
                # Broadcast price to frontend via async task
                asyncio.get_event_loop().create_task(broadcast_price(price))
            except Exception as e:
                logger.error(f"❌ Price update error: {e}")

    def on_ws_kline_sync(candle: dict):
        """Handle new closed candle from WebSocket (sync wrapper)."""
        logger.debug(f"📊 New kline: close={candle['close']}")

    binance_client.on_price(on_ws_price_sync)
    binance_client.on_kline(on_ws_kline_sync)

    ws_task = asyncio.create_task(binance_client.start_websocket())

    # ─── Step 3: Scheduler for signals + broadcasts ────────────────────────
    # Per-strategy locks to prevent double-firing
    _signal_locks = {name: False for name in paper_engine.strategies}

    async def check_signals():
        """Check all strategies for signals every 5 seconds."""
        try:
            if not paper_engine or not binance_client:
                return
            if not binance_client.klines_cache:
                return

            df = binance_client.get_cache_df()
            if df is None or len(df) < 200:
                return

            price = binance_client.get_live_price()
            if not price:
                return

            for strat_name, strat in paper_engine.strategies.items():
                already_in_trade = strat.in_trade or (strat_name in paper_engine.active_trades) or _signal_locks.get(strat_name, False)
                if already_in_trade:
                    logger.debug(f"⏭️ [{strat_name}] Skipping — already in trade")
                    continue
                if not strat.enabled:
                    continue

                # Set lock immediately to prevent double-fire
                _signal_locks[strat_name] = True
                try:
                    strat_config = config["strategies"][strat_name]
                    df_ind = IndicatorEngine.compute_all_indicators(df.copy(), strat_config)
                    if len(df_ind) < 200:
                        _signal_locks[strat_name] = False
                        continue

                    current_idx = len(df_ind) - 1
                    signal = strat.generate_signal(df_ind, current_idx)

                    if signal:
                        try:
                            debug_info = strat.get_signal_debug(df_ind, current_idx)
                            conditions = debug_info.get('conditions', {}) if debug_info else None
                        except:
                            conditions = None

                        log_signal(
                            strategy_name=strat_name,
                            signal=signal,
                            price=price,
                            timestamp=datetime.now(timezone.utc),
                            conditions=conditions,
                            symbol=config["binance"]["symbol"]
                        )

                        log_trade_attempt(
                            strategy_name=strat_name,
                            signal=signal,
                            entry_price=price,
                            stop_loss=0,
                            take_profit=0,
                            units=0,
                            capital=strat.capital,
                            risk_pct=strat_config.get("risk_pct", 0.01),
                            symbol=config["binance"]["symbol"],
                            trade_type="live",
                            extra_details={
                                "Bar Index": current_idx,
                                "Data Length": len(df_ind),
                                "Strategy Enabled": strat.enabled,
                                "Strategy in_trade": strat.in_trade,
                            }
                        )

                        paper_engine._open_live_trade(
                            strat_name,
                            signal,
                            price,
                            strat_config,
                        )

                        active_trade = paper_engine.active_trades.get(strat_name)
                        trade_id = active_trade.id if active_trade else "unknown"
                        logger.info(f"✅ [{strat_name}] Live trade CONFIRMED: trade_id={trade_id}, signal={signal.upper()}, price=${price:,.2f}")

                        log_trade_result(
                            strategy_name=strat_name,
                            success=True,
                            message=f"Live trade opened: {signal.upper()} @ ${price:,.2f}"
                        )

                except Exception as e:
                    _signal_locks[strat_name] = False
                    logger.error(f"❌ Signal check error for {strat_name}: {e}")
                    logger.error(traceback.format_exc())
                    log_trade_result(
                        strategy_name=strat_name,
                        success=False,
                        error=e,
                        message=f"Signal check failed: {str(e)}",
                        stack_trace=traceback.format_exc()
                    )
                else:
                    # Lock will be released by the trade guard next cycle,
                    # but if no trade was opened, release now
                    if strat_name not in paper_engine.active_trades:
                        _signal_locks[strat_name] = False

        except Exception as e:
            logger.error(f"❌ Signal check loop error: {e}")

    # ─── Scheduler Setup ───────────────────────────────────────────────────
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_signals, "interval", seconds=5)
    scheduler.add_job(broadcast_summary, "interval", seconds=30)
    scheduler.add_job(heartbeat, "interval", seconds=60)
    scheduler.start()

    system_running = True
    logger.info(f"🎯 SYSTEM ONLINE — WebSocket data + REST trades (signals=5s)")
    yield
    system_running = False
    if scheduler:
        scheduler.shutdown()
    await binance_client.stop()
    ws_task.cancel()
    logger.info("🛑 SYSTEM OFFLINE")


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")


@app.get("/")
async def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/api/summary")
async def summary():
    data = get_internal_summary()
    # Add extended stats
    try:
        if db_session:
            total_trades = db_session.query(Trade).filter(Trade.status == "closed", Trade.strategy_name != "strategy6").count()
            wins = (
                db_session.query(Trade)
                .filter(Trade.status == "closed", Trade.pnl > 0, Trade.strategy_name != "strategy6")
                .count()
            )
            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

            # Calculate total P&L
            total_pnl = 0
            for t in db_session.query(Trade).filter(Trade.status == "closed", Trade.strategy_name != "strategy6").all():
                total_pnl += t.pnl or 0

            # Find best strategy
            best = None
            best_pnl = -float("inf")
            if paper_engine:
                for name, strat in paper_engine.strategies.items():
                    pnl = strat.capital - getattr(strat, "initial_capital", 10000)
                    if pnl > best_pnl:
                        best_pnl = pnl
                        best = name

            data["total_stats"] = {
                "total_trades": total_trades,
                "total_pnl": round(total_pnl, 2),
                "win_rate": round(win_rate, 2),
                "best_strategy": best,
                "active_positions": len(data.get("active_trades", [])),
            }
    except Exception as e:
        logger.error(f"Stats error: {e}")

    return data


@app.get("/api/debug")
async def debug_strategies():
    """Live debug: show indicator values and signal conditions for all strategies."""
    if not paper_engine or not binance_client:
        return {"error": "Engine not initialized yet"}

    df = binance_client.get_cache_df()
    if df is None or len(df) < 10:
        return {"error": f"Not enough candle data yet ({len(df) if df is not None else 0} bars)"}

    result = {
        "candles_loaded": len(df),
        "live_price": binance_client.latest_ticker.get("price") if binance_client.latest_ticker else None,
        "warmup_complete": binance_client.is_warmed_up,
        "strategies": {}
    }

    def safe_json(obj):
        import math
        if isinstance(obj, dict):
            return {k: safe_json(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [safe_json(v) for v in obj]
        elif isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return obj
        # Handle numpy types
        if type(obj).__module__ == 'numpy':
            if hasattr(obj, 'item'):
                val = obj.item()
                if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                    return None
                return val
        return obj

    for strat_name, strat in paper_engine.strategies.items():
        try:
            strat_config = config["strategies"][strat_name]
            from engine.indicators import IndicatorEngine
            df_ind = IndicatorEngine.compute_all_indicators(df.copy(), strat_config)
            current_idx = len(df_ind) - 1
            debug_info = strat.get_signal_debug(df_ind, current_idx)
            signal = strat.generate_signal(df_ind, current_idx)
            raw_strat_data = {
                "name": strat_config.get("name", strat_name),
                "enabled": strat.enabled,
                "in_trade": strat.in_trade,
                "capital": round(strat.capital, 2),
                "signal": signal,
                "debug": debug_info,
            }
            result["strategies"][strat_name] = safe_json(raw_strat_data)
        except Exception as e:
            result["strategies"][strat_name] = {"error": str(e)}

    return safe_json(result)


@app.get("/api/analytics")
async def analytics():
    try:
        if not paper_engine or not db_session:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "sharpe_ratio": 0,
            }
        total_trades = 0
        wins = 0
        total_pnl = 0.0
        for name, strat in paper_engine.strategies.items():
            trades = (
                db_session.query(Trade)
                .filter_by(strategy_name=name, status="closed")
                .all()
            )
            total_trades += len(trades)
            for t in trades:
                if t.pnl and t.pnl > 0:
                    wins += 1
                total_pnl += t.pnl or 0
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 2),
            "profit_factor": round(total_pnl / abs(total_pnl), 2)
            if total_pnl != 0
            else 0,
            "sharpe_ratio": 0,
        }
    except Exception as e:
        logger.error(f"Analytics error: {e}")
        return {"total_trades": 0, "win_rate": 0, "profit_factor": 0, "sharpe_ratio": 0}


@app.get("/api/trade-history")
@app.get("/api/trades")
async def trade_history(limit: int = 1000):
    try:
        logger.info(f"Trade history API called with limit={limit}")
        logger.info(f"db_session is: {db_session}")

        if db_session is None:
            logger.error("db_session is None!")
            return []

        # Get ALL trades (open and closed)
        all_trades = db_session.query(Trade).all()
        logger.info(f"Total trades in DB: {len(all_trades)}")

        closed_trades = db_session.query(Trade).filter_by(status="closed").all()
        logger.info(f"Closed trades: {len(closed_trades)}")

        open_trades = db_session.query(Trade).filter_by(status="open").all()
        logger.info(f"Open trades: {len(open_trades)}")

        # Use a large limit if requesting more than default
        actual_limit = min(limit, 5000)  # Cap at 5000 to prevent memory issues
        trades = (
            db_session.query(Trade).order_by(Trade.entry_time.desc()).limit(actual_limit).all()
        )

        result = []
        for t in trades:
            result.append(
                {
                    "id": t.id,
                    "strategy": t.strategy_name,
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "entry": t.entry_price,
                    "exit": t.exit_price,
                    "pnl": t.pnl,
                    "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                    "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                    "exit_reason": t.exit_reason,
                    "status": t.status,
                }
            )

        return result
    except Exception as e:
        logger.error(f"Trade history error: {e}")
        import traceback

        traceback.print_exc()
        return []


@app.get("/api/equity-curve")
async def equity_curve(limit: int = 300):
    try:
        if not db_session:
            return {}
        snapshots = (
            db_session.query(EquitySnapshot)
            .order_by(EquitySnapshot.timestamp.desc())
            .limit(limit)
            .all()
        )
        by_strategy = {}
        for s in snapshots:
            if s.strategy_name not in by_strategy:
                by_strategy[s.strategy_name] = []
            by_strategy[s.strategy_name].append(
                {"time": s.timestamp.isoformat(), "equity": s.equity}
            )
        return by_strategy
    except Exception as e:
        logger.error(f"Equity curve error: {e}")
        return {}


@app.get("/api/drawdown")
async def drawdown():
    try:
        if not db_session:
            return {}
        snapshots = (
            db_session.query(EquitySnapshot)
            .order_by(EquitySnapshot.timestamp.desc())
            .limit(300)
            .all()
        )
        data = {}
        for s in snapshots:
            if s.strategy_name not in data:
                data[s.strategy_name] = []
            data[s.strategy_name].append(
                {"time": s.timestamp.isoformat(), "drawdown": 0}
            )
        return data
    except Exception as e:
        logger.error(f"Drawdown error: {e}")
        return {}


@app.get("/api/strategies")
async def get_strategies():
    """Return strategy list from config — available even before warmup completes."""
    strategies = {}
    strat_cfg = config.get("strategies", {})
    for key, cfg in strat_cfg.items():
        enabled = cfg.get("enabled", True)
        # If engine is running, use live enabled state
        if paper_engine and key in paper_engine.strategies:
            enabled = paper_engine.strategies[key].enabled
        strategies[key] = {
            "name": cfg.get("name", key),
            "enabled": enabled,
            "initial_capital": cfg.get("initial_capital", 10000),
        }
    return strategies


@app.post("/api/strategies/{name}/toggle")
async def toggle_strategy(name: str):
    try:
        if not paper_engine or name not in paper_engine.strategies:
            return {"enabled": True, "error": "Strategy not found"}

        strat = paper_engine.strategies[name]
        # Toggle: if enabled, disable it. If disabled, enable it.
        strat.enabled = not strat.enabled

        logger.info(f"🔄 Toggled {name}: enabled={strat.enabled}")

        # Save to database
        if db_session:
            status = (
                db_session.query(StrategyStatus).filter_by(strategy_name=name).first()
            )
            if status:
                status.enabled = strat.enabled
            else:
                status = StrategyStatus(strategy_name=name, enabled=strat.enabled)
                db_session.add(status)
            db_session.commit()

        return {"enabled": strat.enabled, "strategy": name}
    except Exception as e:
        logger.error(f"Toggle strategy error: {e}")
        return {"enabled": False, "error": str(e)}


@app.websocket("/api/ws/price")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.append(websocket)
    logger.info(f"🔌 Client connected. Total: {len(_ws_clients)}")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("🔌 Client disconnected")
    finally:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


if __name__ == "__main__":
    import uvicorn

    # Use 127.0.0.1 instead of 0.0.0.0 for local access
    uvicorn.run(app, host="127.0.0.1", port=8000)
