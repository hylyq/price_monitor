# CryptoGuard — AI-Powered Cryptocurrency Price Monitor

English | [中文](README.zh-CN.md)

An intelligent cryptocurrency monitoring system built on OKX WebSocket + LLM Agent, supporting natural language interaction via WeChat alongside precise command-based control.

## Features

- **Real-time Price Monitoring**: Fetch live prices via OKX WebSocket
- **Price Alerts**: Notifications when price breaks above/below a specified threshold
- **Volatility Alerts**: Notifications when price change reaches a threshold within a given time window
- **WeChat Integration**: Add, remove, and query monitoring rules via WeChat commands
- **AI Smart Assistant**: Natural language interaction via `/ask` command (powered by LLM Agent + Tool-Use architecture)
- **Rule Persistence**: Monitoring rules stored in Redis, automatically restored on restart
- **Smart Price Display**: Auto-adjusts decimal precision by price magnitude — works for everything from BTC to SHIB/PEPE

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│   WeChatService  │     │  price_monitor  │
│ (larky process)  │◄───►│   (this app)    │
└────────┬─────────┘     └────────┬────────┘
         │                        │
         └────────────┬───────────┘
                      ▼
             ┌─────────────────┐
             │  Redis Pub/Sub  │
             └─────────────────┘
```

## Installation

```bash
uv sync
```

## Running

### Prerequisites

1. Redis server running
2. larky WeChat service running (`uv run python -m larky`)

### Start the Monitor

```bash
uv run python main.py
```

## WeChat Commands

All commands are prefixed with `/pm` to avoid conflicts with WeChatService commands.

### Query Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/pm help` | Show help | `/pm help` |
| `/pm price <symbol>` | Query current price | `/pm price BTC-USDT` |
| `/pm price` | Query all subscribed prices | `/pm price` |
| `/pm list [symbol]` | List monitoring rules | `/pm list` |
| `/pm list BTC-USDT` | List rules for a symbol | `/pm list BTC-USDT` |

### Add Monitoring Rules

| Command | Description | Example |
|---------|-------------|---------|
| `/pm add <symbol> > <price>` | Price break-above alert | `/pm add BTC-USDT > 100000` |
| `/pm add <symbol> < <price>` | Price drop-below alert | `/pm add ETH-USDT < 3000` |
| `/pm add <symbol> up <pct>% <min>` | Price surge alert | `/pm add BTC-USDT up 5 60` |
| `/pm add <symbol> down <pct>% <min>` | Price drop alert | `/pm add BTC-USDT down 3 30` |

### Delete Monitoring Rules

| Command | Description | Example |
|---------|-------------|---------|
| `/pm del <rule_id>` | Delete a specific rule | `/pm del abc12345` |
| `/pm clear [symbol]` | Clear all rules | `/pm clear` |
| `/pm clear BTC-USDT` | Clear rules for a symbol | `/pm clear BTC-USDT` |

## Usage Examples

```
# Query BTC price
/pm price BTC-USDT

# Alert when BTC breaks above $100,000
/pm add BTC-USDT > 100000

# Alert when ETH drops below $3,000
/pm add ETH-USDT < 3000

# Alert when BTC surges 5% within 60 minutes
/pm add BTC-USDT up 5 60

# Alert when BTC drops 3% within 30 minutes
/pm add BTC-USDT down 3 30

# List all monitoring rules
/pm list

# Delete a rule
/pm del abc12345
```

## LLM Agent (Experimental)

Interact with the price monitoring system in natural language. The LLM acts as the "brain," understanding user intent and automatically selecting the right tools — enabling intelligent queries, market analysis, and alert configuration.

### Why LLM is a Quantum Leap

**Traditional command-line approach:**

```
/pm add BTC-USDT > 100000
```

Users must memorize exact syntax and parameter order. Fuzzy expressions are unsupported. Compound intents require manually splitting into multiple commands. Analytical questions are impossible to handle.

**LLM Agent approach:**

```
/ask Help me watch Bitcoin and notify me if it breaks above $100K
```

| Dimension | Traditional Commands | LLM Agent |
|-----------|---------------------|-----------|
| Asset naming | Must use `BTC-USDT` | Supports "Bitcoin", "BTC", "大饼", etc. |
| Value expression | Must use `100000` | Supports "$100K", "one hundred thousand", etc. |
| Compound intent | Split manually into multiple commands | Auto-decomposition: "volatility > 3%" → bidirectional alerts |
| Data analysis | Not supported; manual calculation | Auto-fetch history + compute volatility + draw conclusions |
| Error tolerance | Format error → immediate failure | Asks clarifying questions when intent is ambiguous |
| Learning curve | Must memorize all command formats | Zero learning curve; natural language suffices |

### Architecture Design

```
User WeChat message "/ask Monitor ETH for me"
         │
         ▼
┌──────────────────────────────────────────────┐
│  CommandHandler.handle_text()                │
│  → Recognize /ask prefix → _handle_ask()     │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │  Agent.answer(query)                   │  │
│  │                                        │  │
│  │  1. Build system prompt + 8 tools     │  │
│  │  2. Call DeepSeek API                  │  │
│  │     (Anthropic-compatible endpoint)    │  │
│  │  3. LLM returns tool_use blocks        │  │
│  │  4. Agent executes Python functions    │  │
│  │  5. Send tool_results back to LLM      │  │
│  │  6. LLM generates final response       │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  Return reply → wechat_client.notify()       │
└──────────────────────────────────────────────┘
```

**Core design principles:**

- **Tool-Use Pattern**: The LLM never directly manipulates user data. It calls 8 predefined tool functions, each wrapping one or more existing Python functions, ensuring operational safety and consistency.
- **Pre-computation First**: Numerical calculations (e.g., volatility standard deviation) are done in the Python layer. The LLM focuses solely on natural language interpretation, avoiding LLM math errors.
- **Atomic Operations**: Adding an alert requires 3 steps (create rule → subscribe WebSocket → invalidate cache), packaged as a single tool call for atomicity.
- **Stateless Design**: Each `/ask` call is independent with no conversation history, matching WeChat's message-driven model.

### Available Tools

| Tool Name | Function | Wraps |
|-----------|----------|-------|
| `get_current_price` | Query real-time price | `okx_client.get_price()` |
| `get_ticker_detail` | Query detailed ticker (price + 24h high/low + volume) | `okx_client.get_ticker()` |
| `get_price_history` | Fetch price history | `storage.get_price_history()` |
| `calculate_volatility` | Volatility analysis (high/low/range/change/std dev) | `get_price_history()` + `statistics.stdev()` |
| `add_price_alert` | Add price break-above/drop-below alert | `storage.add_rule()` + subscribe + cache invalidate |
| `add_change_alert` | Add price change alert | Same as above |
| `list_alert_rules` | List all alert rules | `storage.get_all_rules()` |
| `remove_alert_rule` | Remove an alert rule | `storage.remove_rule()` + unsubscribe |

### Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/ask <question>` | Ask the AI assistant | `/ask What's BTC's current price?` |
| `/ai <question>` | Same as above | `/ai Help me monitor ETH` |
| `/pm ask <question>` | Invoke AI via `/pm` entry | `/pm ask Monitor ETH for me` |

### Usage Examples

```bash
# Query prices
/ask What's BTC's price now?
/ask Show me ETH's detailed ticker

# Configure alerts
/ask Watch Bitcoin and notify me if it breaks above $100K
/ask Tell me if ETH falls below 3000

# Market analysis
/ask Has ETH been volatile in the last 30 minutes?
/ask Which has performed better lately, SOL or BTC?

# Manage rules
/ask What monitoring rules do I have?
/ask Delete rule abc12345

# Complex scenarios (multi-step agent)
/ask Watch ETH and tell me if it moves more than 3% within 30 minutes
/ask Notify me if SOL drops below 100 or breaks above 200
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_API_KEY` | LLM API key (required; obtain from DeepSeek) | - |
| `LLM_BASE_URL` | Anthropic-compatible API endpoint | `https://api.deepseek.com/anthropic` |
| `LLM_MODEL` | Model name | `deepseek-v4-flash` |

When `LLM_API_KEY` is not set, `/ask` returns a friendly message and `/pm` commands continue to work (graceful degradation).

### Eval Tests

The project includes 10 eval test cases covering three functional areas:

| # | Category | Input | Expected Tool | Expected Params |
|---|----------|-------|---------------|-----------------|
| 1 | Price Query | "BTC现在多少钱" | get_current_price | inst_id=BTC-USDT |
| 2 | Price Query | "查一下ETH-USDT-SWAP的价格" | get_current_price | inst_id=ETH-USDT-SWAP |
| 3 | Add Alert | "帮我盯着比特币，突破10万美金就通知我" | add_price_alert | BTC-USDT, price_above, 100000 |
| 4 | Add Alert | "ETH跌破3000提醒我" | add_price_alert | ETH-USDT, price_below, 3000 |
| 5 | Volatility Alert | "BTC涨5%就通知我，看60分钟" | add_change_alert | BTC-USDT, change_up, 5, 60 |
| 6 | Delete Alert | "删除abc12345这个规则" | remove_alert_rule | rule_id=abc12345 |
| 7 | List Rules | "我有哪些监控规则" | list_alert_rules | (none) |
| 8 | Market Analysis | "ETH最近30分钟波动大吗" | calculate_volatility | ETH-USDT, 30 |
| 9 | Multi-step Agent | "帮我盯着ETH，30分钟内波动超过3%..." | add_change_alert ×2 | change_up + change_down |
| 10 | Symbol Mapping | "比特币什么价格" | get_current_price | inst_id=BTC-USDT |

**Running the tests:**

```bash
# Mock mode (no API key needed, CI-safe)
uv run pytest tests/test_agent_eval.py -v

# Real LLM mode
LLM_API_KEY=sk-xxx uv run pytest tests/test_agent_eval.py -v --real-llm
```

> **Note:** Real LLM eval tests use a separate Redis database (DB 1) and auto-cleanup before/after each case, so they never pollute production data (DB 0) with test alert rules.

**Eval results (`deepseek-v4-flash` / `LLM_BASE_URL=https://api.deepseek.com/anthropic`):**

| # | Category | Input | Expected Tool | Result | Notes |
|---|----------|-------|---------------|--------|-------|
| 1 | Price Query | "BTC现在多少钱" | get_current_price | ✅ | inst_id=BTC-USDT ✓ |
| 2 | Price Query | "查一下ETH-USDT-SWAP的价格" | get_current_price | ✅ | inst_id=ETH-USDT-SWAP ✓ |
| 3 | Add Alert | "帮我盯着比特币，突破10万美金..." | add_price_alert | ✅ | price_above, 100000 ✓ |
| 4 | Add Alert | "ETH跌破3000提醒我" | add_price_alert | ✅ | Queried price first, then correctly added price_below alert |
| 5 | Volatility Alert | "BTC涨5%就通知我，看60分钟" | add_change_alert | ✅ | change_up, 5%, 60min ✓ (LLM checked ticker first) |
| 6 | Delete Alert | "删除abc12345这个规则" | remove_alert_rule | ✅ | rule_id=abc12345 ✓ |
| 7 | List Rules | "我有哪些监控规则" | list_alert_rules | ✅ | ✓ |
| 8 | Market Analysis | "ETH最近30分钟波动大吗" | calculate_volatility | ✅ | ETH-USDT, 30min ✓ |
| 9 | Multi-step Agent | "帮我盯着ETH，30分钟内波动超过3%..." | add_change_alert ×2 | ✅ | Bidirectional alerts (change_up + change_down) ✓ |
| 10 | Symbol Mapping | "比特币什么价格" | get_current_price | ✅ | Chinese name → BTC-USDT ✓ |

**Accuracy: 10/10 (100%)** | Mock tests: 23/23 (100%) | Total time: ~29s

**Known design trade-offs:**
- The Agent enforces tool usage with a multi-iteration pushback: if the LLM returns text without calling any tools, it pushes back up to 2 times with a reminder to use tools. After 2 pushes, the text is **refused entirely** — an error message suggests using deterministic `/pm` commands instead. This prevents the model from silently fabricating prices, rules, or other data from training data (mitigating a known DeepSeek behavior where flash models may skip tool calls and hallucinate stale prices or phantom alert rules)
- The system prompt explicitly forbids calling `add_price_alert` / `add_change_alert` unless the user explicitly requests monitoring or alerting (e.g., "盯着", "监控", "通知"). Query-only intents like market overviews are restricted to read-only tools — preventing the LLM from "helpfully" adding default monitoring rules as a side effect
- Every Agent response appends a tool-call summary footer (`已调用工具: get_current_price ×2, list_alert_rules ×1`) so users can see exactly which tools were invoked and detect unexpected actions
- The LLM may call auxiliary tools before the main action (e.g., checking price before adding an alert); the eval framework matches on eventual behavior rather than strictly checking the first tool call
- Price data flows through WebSocket → in-memory cache → query; when the WebSocket disconnects, the cache is purged and a 30-second staleness threshold warns users of outdated data — this trades a brief "no data" window for never silently serving frozen prices
- Real LLM eval tests (`--real-llm`) use Redis DB 1 with automatic setup/teardown cleanup to avoid polluting production data with test alert rules

### Observability

Each `/ask` interaction automatically records three layers of structured logs — no extra configuration needed:

```
INFO  LLM #1: latency=850ms tokens_in=1200 tokens_out=45
INFO  Tool #1: add_price_alert(inst_id=BTC-USDT, alert_type=price_above, threshold=100000) → 12ms
INFO  LLM #2: latency=620ms tokens_in=2100 tokens_out=80
INFO  Interaction complete: total=1820ms llm_calls=2 tool_calls=1 tokens_in=3300 tokens_out=125
```

| Layer | Metrics | Purpose |
|-------|---------|---------|
| LLM API call | `latency_ms`, `tokens_in`, `tokens_out` | Identify latency bottlenecks (network vs. inference), cost estimation |
| Tool execution | `tool_name`, `params`, `elapsed_ms` | Diagnose tool-layer performance issues |
| Interaction summary | `total_ms`, `llm_calls`, `tool_calls`, `tokens_total` | Holistic profile of a single interaction |

**Real-world metrics: each `/ask` consumes ~3000-4000 tokens, total latency ~2 seconds (network round-trips dominate; tool execution <20ms), cost per call is fractions of a cent (USD).**

### Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| LLM SDK | `anthropic` | DeepSeek provides an Anthropic-compatible endpoint (`api.deepseek.com/anthropic`); the tool_use mechanism is more intuitive than OpenAI function calling |
| Default model | `deepseek-v4-flash` | DeepSeek's latest flagship model (`deepseek-chat` will be deprecated 2026/07/24); stable tool_use behavior when paired with first-iteration tool-call enforcement |
| Keep `/pm` commands | Yes | Fallback when LLM is unavailable; precise commands are more efficient in certain scenarios |
| `calculate_volatility` as standalone tool | Yes | LLMs aren't reliable at precise numerical computation; stats done in Python layer, LLM focuses on natural language interpretation |
| Stateless agent | Yes | Matches WeChat's message-driven model; each `/ask` is independent, avoiding session state management complexity |

## Alert Message Examples

### Price Alert

```
📈 【Price Alert】
Symbol: BTC-USDT
Current Price: $100,500.00
Broken Above Threshold: $100,000.00
Time: 2026-03-27 21:30:00
```

### Volatility Alert

```
⬆️ 【Volatility Alert】
Symbol: BTC-USDT
Current Price: $98,500.00
60min Change: +5.23%
Starting Price: $93,600.00
Time: 2026-03-27 21:30:00
```

### Low-Value Asset Alert

```
📈 【Price Alert】
Symbol: SHIB-USDT
Current Price: $0.00002345
Broken Above Threshold: $0.00002000
Time: 2026-03-27 21:30:00
```

## Price Display Precision

The program automatically adjusts decimal precision based on price magnitude:

| Price Range | Example Assets | Display |
|-------------|---------------|---------|
| >= $1,000 | BTC, ETH | $95,432.15 (thousands separator, 2 decimals) |
| $1 ~ $1,000 | XRP, DOGE | $2.3456 (up to 4 decimals) |
| $0.01 ~ $1 | DOGE | $0.1234 (up to 4 decimals) |
| < $0.01 | SHIB, PEPE | $0.00002345 (auto-adaptive precision) |

## Environment Variables

Configure in `.env` file (optional):

```env
# Redis configuration
REDIS_URL=redis://localhost:6379
# or
REDIS_HOST=localhost
REDIS_PORT=6379

# OKX WebSocket URL (optional; defaults to official endpoint)
OKX_WS_URL=wss://ws.okx.com:8443/ws/v5/public

# LLM Agent (optional; /ask is unavailable without it)
LLM_API_KEY=sk-xxx
# LLM_BASE_URL=https://api.deepseek.com/anthropic   # default
# LLM_MODEL=deepseek-v4-flash                        # default
```

## Symbol Format

Supports OKX trading symbol formats:
- Spot: `BTC-USDT`, `ETH-USDT`
- Swap: `BTC-USDT-SWAP`, `ETH-USDT-SWAP`

## Performance Optimizations

The program is optimized for high-frequency WebSocket data streams to keep CPU and memory usage low:

- **Ticker Throttling**: Each symbol processes at most 1 ticker per second, avoiding resource waste from OKX's high-frequency pushes (~10/sec/symbol)
- **Rule Caching**: Monitoring rules cached for 10 seconds to reduce Redis query frequency
- **Batch Price Writes**: Price data batched to Redis every 5 seconds rather than per-message
- **Deferred History Cleanup**: Price history purged every 60 seconds rather than rebuilding the list on every tick

With these optimizations, CPU usage stays **<1%** even when monitoring multiple symbols.

## Data Freshness & Reliability

To prevent serving stale prices during WebSocket disconnections, CryptoGuard implements multiple layers of protection:

- **Cache Invalidation on Disconnect**: When the OKX WebSocket connection drops, the in-memory price cache is immediately cleared. Subsequent queries trigger a re-subscription rather than silently returning the last known (frozen) price.
- **Subscription Preservation**: When a new instrument is added via auto-subscribe (e.g., the LLM queries SOL while BTC/ETH are already monitored), the subscribe message re-sends **all** tracked instruments — not just the new one. This prevents OKX's WebSocket server from treating the new subscribe as a replacement and silently dropping previously-subscribed instruments (whose prices would then freeze at the last snapshot).
- **Staleness Detection**: Every price query checks the ticker's timestamp against the current time. If data is older than 30 seconds, a `⚠️ Data is X seconds stale` warning is appended to the response — the user knows the data may not be real-time.
- **Resilient Message Parsing**: Malformed ticker messages from OKX are caught and logged individually instead of crashing the entire WebSocket read loop. The connection stays alive and healthy messages continue to be processed.
- **Dead Connection Detection**: Ping failures are no longer silently swallowed. When a ping fails, the WebSocket is force-closed to trigger a reconnect cycle, preventing the system from serving stale prices through an undetected dead connection.

These measures ensure that `/pm price` and `/ask` always surface the freshest available data — or transparently warn when data may be outdated.

## Dependencies

- Python >= 3.13
- larky — WeChat bot framework
- websockets — WebSocket client
- redis — Redis client
- python-dotenv — Environment variable management
- anthropic — LLM API SDK (optional, required only for `/ask` functionality)
