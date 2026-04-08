# Binance Paper Trading System

A fully automated 24/7 paper trading system for Binance BTC/USDT with a real-time localhost dashboard.

## Features

- **3 Trading Strategies** (exactly as you specified):
  - **Strategy 1**: Long-Only Breakout (EMA50/200, ATR14, RSI14, Donchian 20, volume filter, 1.5x ATR SL, 3x ATR TP, 24h timeout)
  - **Strategy 2**: Long-Only Relaxed (same as S1 but relaxed volume/volatility thresholds, includes fees)
  - **Strategy 3**: Long/Short Futures (bidirectional trading with slippage, fees, and funding cost modeling)

- **Real-time Dashboard**:
  - Live BTC price ticker
  - Equity curve charts for all strategies
  - Active trades monitoring (entry, SL, TP, units)
  - Trade history with exit reasons and PnL
  - Strategy toggle controls (enable/disable on the fly)
  - Performance metrics (Return, CAGR, Sharpe, Max Drawdown)

- **Realistic Simulation**:
  - Trading fees (0.04%)
  - Slippage (0.03%)
  - Funding rates for futures (0.01% per 8h)
  - Proper risk management (1% risk per trade)

## Architecture

```
paper-trading/
├── backend/
│   ├── main.py                 # FastAPI server + APScheduler
│   ├── data/
│   │   └── binance.py          # Binance REST + WebSocket client
│   ├── engine/
│   │   ├── strategies.py       # All 3 strategy implementations
│   │   ├── indicators.py       # Technical indicator engine
│   │   └── paper_trade.py      # Paper trade execution engine
│   ├── models/
│   │   └── database.py         # SQLite database models
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.js              # Main dashboard component
│   │   └── index.js
│   └── package.json
├── config.json                 # Strategy parameters & system config
└── run.bat                     # One-click launcher
```

## Quick Start

### Option 1: Using the Batch File (Recommended)
1. Double-click `start.bat` or run:
   ```
   start.bat
   ```
   This will:
   - Check Python and dependencies
   - Install any missing packages
   - Launch the server in a new window

2. Wait ~10 seconds, then open your browser to:
   ```
   http://127.0.0.1:8000
   ```

### Option 2: Manual Start
1. Open a terminal in the `paper-trading` folder
2. Run:
   ```
   venv\Scripts\python.exe backend\run_with_logging.py
   ```

### Check Server Status
```
venv\Scripts\python.exe check_status.py
```

### Important Notes
- The server runs in a **separate window** - don't close it!
- Logs are saved to `server_output.log`
- Server runs on `http://127.0.0.1:8000`
- To stop the server, close the server window or press Ctrl+C in it

## Configuration

Edit `config.json` to customize:
- Strategy parameters (EMA periods, ATR multipliers, RSI thresholds, etc.)
- Risk per trade (`risk_pct`)
- Initial capital per strategy
- Enable/disable strategies by default

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/status` | System status and strategy states |
| `GET /api/metrics` | Performance metrics for all strategies |
| `GET /api/active-trades` | Currently open trades |
| `GET /api/trade-history` | Historical closed trades |
| `GET /api/equity-curve` | Equity curve data for charting |
| `GET /api/current-price` | Latest BTC price |
| `POST /api/strategies/{name}/toggle` | Enable/disable a strategy |
| `POST /api/strategies/{name}/risk` | Update risk parameters |

## How It Works

1. **Data Collection**: Fetches 500 historical 1H candles from Binance, then streams real-time data via WebSocket
2. **Indicator Calculation**: Computes EMA50, EMA200, ATR(14), RSI(14), Donchian channels, Volume MA on every bar
3. **Signal Generation**: Each strategy evaluates conditions (regime, breakout, volume, RSI) to generate long/short signals
4. **Paper Execution**: Simulates entries with SL/TP, applies fees/slippage, tracks PnL
5. **Dashboard Updates**: Frontend polls API every 10 seconds for real-time visualization

## Notes

- This is **paper trading only** - no real money is at risk
- Strategies run continuously as long as the backend is running
- All trade data is persisted in SQLite (`paper_trading.db`)
- The system auto-recovers from Binance disconnects
