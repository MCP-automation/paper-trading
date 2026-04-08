# Advanced Monitoring Panel - Paper Trading System

## Overview

The **Advanced Monitoring Panel** gives you complete real-time visibility into what your paper trading system is doing with the Binance API and trading logic.

## Features

### 🔍 Real-Time Monitoring
- **Live Operations Log**: See every action the backend is performing
- **Activity Feed**: High-level summary of important events
- **System Status**: Current cycle count, API calls, trades opened
- **Operation Tracking**: Track data fetching, signal generation, trade execution

### 📊 What It Tracks

1. **API Calls**
   - All Binance REST API calls
   - Response status codes
   - Execution time (ms)

2. **Data Fetching**
   - Historical data loads from Binance
   - Number of candles fetched
   - Fetch duration

3. **Signal Generation**
   - Technical indicator calculations
   - Signal check conditions and results
   - Even when NO signal is generated

4. **Trade Execution**
   - Trade openings (entry price, SL, TP, units)
   - Trade closings (exit price, PnL, exit reason)
   - Fee calculations and slippage

5. **Error Tracking**
   - All errors and warnings
   - Exception details
   - Recovery attempts

### 📈 Key Metrics Displayed

```
System Status:
├─ Current Cycle: Processing cycle number
├─ API Calls: Total API calls made to Binance
├─ Trades Opened: Total trades that were opened
└─ Trades Closed: Total trades that were closed
```

## How to Access

### Local Access
```
http://127.0.0.1:8000/monitor
```

### What You'll See

#### **Status Panel** (Top)
Shows real-time counts of:
- Current processing cycle
- Total API calls made
- Total trades opened
- System running status

#### **Live Operations Log** (Bottom Left)
Raw log entries showing:
- Timestamp
- Log level (INFO, SUCCESS, WARNING, ERROR)
- Operation type
- Detailed message
- Data JSON (when applicable)

Features:
- Filter by level (Info, Success, Warning, Error)
- Filter by operation type (API Calls, Signal Checks, Trades, etc.)
- Color-coded by severity
- Newest logs at top
- Auto-scrolls to latest entries

#### **Recent Activity** (Bottom Right)
Simplified activity feed showing:
- Icon indicating action type
- Activity title and description
- Timestamp
- Last 10 recent activities

## How to Interpret the Logs

### ✅ Success Indicators
```
TIMESTAMP [SUCCESS] OPENED LONG trade @ $50000.00
  Shows a trade was successfully opened with entry signal
```

### 📊 Signal Check Example
```
TIMESTAMP [INFO] Signal check: 5/5 conditions met → LONG SIGNAL
  Shows all conditions aligned for a long trade
```

### 🌐 API Call Example
```
TIMESTAMP [INFO] GET /api/v3/klines → 200 (1250.5ms)
  Shows successful API call to fetch historical data
```

### ❌ Error Example
```
TIMESTAMP [ERROR] Processing cycle error: Connection refused
  Shows an error occurred during a processing cycle
```

## Example Workflow: What You Should See

### On Startup
```
1. [INFO] Paper trading system starting up
2. [SUCCESS] Database initialized successfully
3. [SUCCESS] Binance client initialized for BTCUSDT
4. [SUCCESS] Paper trading engine initialized with 4 strategies
5. [INFO] Calculating 5 indicators: ema50, ema200, atr, rsi, hh20, ll20, vol_ma20
6. [INFO] Signal check: 4/5 conditions met (no trade)
7. [INFO] Signal check: 5/5 conditions met → LONG SIGNAL
...
```

### During Trading
```
1. [SUCCESS] OPENED LONG trade @ $50123.45
   SL: $49500.00 | TP: $51500.00
2. [INFO] Cycle processing completed
3. [WARNING] Calculated 5 indicators (500ms)
4. [SUCCESS] CLOSED LONG trade @ $50500.00 - tp_hit - PnL: $250.55 🟢
```

## Interpreting "Nothing Happening"

If you see no signal checks or trades:

### Possible Reasons:
1. **Not enough data**: System needs 200+ candles minimum for EMA200
2. **Signal conditions not met**: Use the filter to view signal checks and see which conditions are failing
3. **Market conditions**: Price might not be breaking out/down per your strategy criteria
4. **Strategies disabled**: Check if all 4 strategies are in "ENABLED" status

### How to Debug:
1. Go to the Monitor panel
2. Filter logs to show only "Signal Check" operations
3. Look at the "conditions" data
4. See which specific condition is failing (e.g., "breakout: false")

## API Endpoints (For Developers)

### Get Logs
```
GET /api/monitor/logs?limit=100&level=INFO&operation_type=TRADE_OPEN
```

### Get Status
```
GET /api/monitor/status
```
Returns current cycle, API call count, trades count

### Get Activity
```
GET /api/monitor/activity
```
Returns categorized recent activity

### Stream Logs (SSE)
```
GET /api/monitor/stream
```
Server-sent events for real-time log streaming

## Performance Tips

1. **Auto-Refresh ON**: Dashboard auto-refreshes every 2 seconds
2. **Clear Logs**: Clears display (doesn't affect backend logging)
3. **Filtering**: Use filters to reduce noise and focus on specific operations
4. **Log Retention**: System keeps ~2000 recent logs in memory

## Troubleshooting

### No logs appearing?
- Check that server is running: `python check_status.py`
- Verify auto-refresh is ON
- Check browser console for JavaScript errors

### API calls not showing?
- Server might be having connectivity issues with Binance
- Check "Recent Activity" for error messages
- Look for error logs showing DNS or connection failures

### All signal checks show "0 conditions met"?
- Not enough data loaded yet, wait a few minutes
- Check data is being fetched (look for DATA_FETCH logs)

## Dashboard Integration

The monitor panel runs alongside the main dashboard:
- Main Dashboard: `http://127.0.0.1:8000/`
- Monitor Panel: `http://127.0.0.1:8000/monitor`

Click your monitor link in the navigation to switch between views.

---

**Now you can see EXACTLY what your paper trading system is doing with every Binance API call and every trade decision!**
