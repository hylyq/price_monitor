# OKX 价格监控程序

基于 OKX WebSocket 的数字货币价格监控程序，支持微信交互配置监控规则。

## 功能特性

- **实时价格监控**：通过 OKX WebSocket 获取实时价格数据
- **价格告警**：价格突破/跌破指定价位时发送通知
- **波动告警**：指定时间周期内涨跌幅达到阈值时发送通知
- **微信交互**：通过微信命令添加/删除/查询监控规则
- **规则持久化**：监控规则存储在 Redis，程序重启后自动恢复
- **智能价格显示**：根据价格大小自动调整显示精度，支持从 BTC 到 SHIB/PEPE 等各种精度

## 架构

```
┌─────────────────┐     ┌─────────────────┐
│   WeChatService │     │  price_monitor  │
│   (larky独立进程) │◄───►│   (本程序)       │
└────────┬────────┘     └────────┬────────┘
         │                       │
         └───────────┬───────────┘
                     ▼
            ┌─────────────────┐
            │  Redis Pub/Sub  │
            └─────────────────┘
```

## 安装

```bash
uv sync
```

## 运行

### 前置条件

1. Redis 服务已运行
2. larky 微信服务已运行（`uv run python -m larky`）

### 启动监控程序

```bash
uv run python main.py
```

## 微信命令

所有命令以 `/pm` 开头，避免与 WeChatService 的命令冲突。

### 查询命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/pm help` | 查看帮助 | `/pm help` |
| `/pm price <品种>` | 查询当前价格 | `/pm price BTC-USDT` |
| `/pm price` | 查询所有已订阅品种价格 | `/pm price` |
| `/pm list [品种]` | 查看监控规则 | `/pm list` |
| `/pm list BTC-USDT` | 查看指定品种的规则 | `/pm list BTC-USDT` |

### 添加监控

| 命令 | 说明 | 示例 |
|------|------|------|
| `/pm add <品种> > <价格>` | 价格突破告警 | `/pm add BTC-USDT > 100000` |
| `/pm add <品种> < <价格>` | 价格跌破告警 | `/pm add ETH-USDT < 3000` |
| `/pm add <品种> up <涨幅>% <分钟>` | 涨幅告警 | `/pm add BTC-USDT up 5 60` |
| `/pm add <品种> down <跌幅>% <分钟>` | 跌幅告警 | `/pm add BTC-USDT down 3 30` |

### 删除监控

| 命令 | 说明 | 示例 |
|------|------|------|
| `/pm del <规则ID>` | 删除指定规则 | `/pm del abc12345` |
| `/pm clear [品种]` | 清除所有规则 | `/pm clear` |
| `/pm clear BTC-USDT` | 清除指定品种规则 | `/pm clear BTC-USDT` |

## 使用示例

```
# 查询BTC价格
/pm price BTC-USDT

# 添加BTC突破10万美元告警
/pm add BTC-USDT > 100000

# 添加ETH跌破3000美元告警
/pm add ETH-USDT < 3000

# 添加BTC 60分钟涨幅超过5%告警
/pm add BTC-USDT up 5 60

# 添加BTC 30分钟跌幅超过3%告警
/pm add BTC-USDT down 3 30

# 查看所有监控规则
/pm list

# 删除规则
/pm del abc12345
```

## LLM Agent 功能（实验性）

通过自然语言与价格监控系统交互。LLM 作为"大脑"理解用户意图，自动选择合适的工具，支持智能查询、市场分析和告警配置。

### 为什么加入 LLM 能带来质的飞跃

**传统命令行方式：**

```
/pm add BTC-USDT > 100000
```

用户必须记住精确的语法和参数顺序，不支持模糊表达，复合意图需要手动拆分为多条命令，分析类问题完全无法处理。

**LLM Agent 方式：**

```
/ask 帮我盯着比特币，突破10万美金就通知我
```

| 对比维度 | 传统命令 | LLM Agent |
|---------|---------|-----------|
| 币种表达 | 必须用 `BTC-USDT` | 支持"比特币""BTC""大饼"等自然表达 |
| 数值表达 | 必须用 `100000` | 支持"10万美金""十万"等中文表达 |
| 复合意图 | 需手动拆分为多条命令 | 自动拆解："波动超过3%"→双向涨跌告警 |
| 数据分析 | 不支持，需手动计算 | 自动获取历史数据+计算波动率+给出结论 |
| 容错性 | 格式错误直接报错 | 意图模糊时反问澄清 |
| 学习成本 | 需记忆所有命令格式 | 零学习成本，自然语言即可 |

### 架构设计

```
用户微信消息 "/ask 帮我监控ETH"
         │
         ▼
┌──────────────────────────────────────────────┐
│  CommandHandler.handle_text()                │
│  → 识别 /ask 前缀 → _handle_ask()            │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │  Agent.answer(query)                   │  │
│  │                                        │  │
│  │  1. 构建 system prompt + 8 个 tools   │  │
│  │  2. 调用 DeepSeek API                  │  │
│  │     (Anthropic 兼容端点)               │  │
│  │  3. LLM 返回 tool_use blocks           │  │
│  │  4. Agent 执行对应 Python 函数          │  │
│  │  5. 将 tool_result 回传 LLM            │  │
│  │  6. LLM 生成最终中文回复               │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  返回回复 → wechat_client.notify()           │
└──────────────────────────────────────────────┘
```

**核心设计原则：**

- **Tool-Use 模式**：LLM 不直接操作用户数据，而是通过调用预定义的 8 个工具函数来完成任务。每个工具封装了一个或多个已有的 Python 函数调用，确保操作安全和一致性。
- **预计算优先**：数值计算（如波动率标准差）在 Python 层完成，LLM 只负责自然语言解读，避免 LLM 的数学计算错误。
- **原子操作打包**：添加告警需要同时执行 3 步（创建规则→订阅 WebSocket→失效缓存），封装为单个工具调用，保证原子性。
- **无状态设计**：每次 `/ask` 调用是独立的，不保留对话历史，匹配微信机器人的消息驱动模型。

### 可用工具一览

| 工具名称 | 功能 | 对应已有函数 |
|---------|------|-------------|
| `get_current_price` | 查询实时价格 | `okx_client.get_price()` |
| `get_ticker_detail` | 查询详细行情（价格+24h高低+成交量） | `okx_client.get_ticker()` |
| `get_price_history` | 获取价格历史数据 | `storage.get_price_history()` |
| `calculate_volatility` | 波动率分析（最高/最低/范围/涨跌幅/标准差） | `get_price_history()` + `statistics.stdev()` |
| `add_price_alert` | 添加价格突破/跌破告警 | `storage.add_rule()` + subscribe + cache invalidate |
| `add_change_alert` | 添加涨跌幅告警 | 同上 |
| `list_alert_rules` | 列出所有告警规则 | `storage.get_all_rules()` |
| `remove_alert_rule` | 删除告警规则 | `storage.remove_rule()` + unsubscribe |

### 命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/ask <问题>` | 向 AI 助手提问 | `/ask BTC现在多少钱？` |
| `/ai <问题>` | 同上 | `/ai 帮我监控ETH` |
| `/pm ask <问题>` | 通过 /pm 入口调用 AI | `/pm ask 帮我监控ETH` |

### 使用示例

```bash
# 查询价格
/ask BTC现在多少钱？
/ask 查一下ETH的详细行情

# 配置告警
/ask 帮我盯着比特币，突破10万美金就通知我
/ask 如果ETH跌到3000以下告诉我

# 市场分析
/ask ETH最近30分钟波动大吗？
/ask SOL和BTC最近谁涨得好？

# 管理规则
/ask 我有哪些监控规则？
/ask 帮我删除规则abc12345

# 复杂场景（多步 Agent）
/ask 帮我盯着ETH，波动超过3%就告诉我
/ask 如果SOL跌破100或者涨超200就通知我
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | LLM API 密钥（必需，在 DeepSeek 平台获取） | - |
| `LLM_BASE_URL` | Anthropic 兼容 API 端点 | `https://api.deepseek.com/anthropic` |
| `LLM_MODEL` | 模型名称 | `deepseek-chat` |

不设置 `LLM_API_KEY` 时，`/ask` 命令返回友好提示，`/pm` 命令不受影响（降级方案）。

### Eval 测试

项目包含 10 条 eval 测试用例，覆盖三大功能场景：

| # | 分类 | 输入 | 期望工具 | 期望参数 |
|---|------|------|---------|---------|
| 1 | 价格查询 | "BTC现在多少钱" | get_current_price | inst_id=BTC-USDT |
| 2 | 价格查询 | "查一下ETH-USDT-SWAP的价格" | get_current_price | inst_id=ETH-USDT-SWAP |
| 3 | 添加告警 | "帮我盯着比特币，突破10万美金就通知我" | add_price_alert | BTC-USDT, price_above, 100000 |
| 4 | 添加告警 | "ETH跌破3000提醒我" | add_price_alert | ETH-USDT, price_below, 3000 |
| 5 | 波动告警 | "BTC涨5%就通知我，看60分钟" | add_change_alert | BTC-USDT, change_up, 5, 60 |
| 6 | 删除告警 | "删除abc12345这个规则" | remove_alert_rule | rule_id=abc12345 |
| 7 | 查询规则 | "我有哪些监控规则" | list_alert_rules | (无) |
| 8 | 行情分析 | "ETH最近30分钟波动大吗" | calculate_volatility | ETH-USDT, 30 |
| 9 | 多步Agent | "帮我盯着ETH，波动超过3%就告诉我" | add_change_alert ×2 | change_up + change_down |
| 10 | 币种映射 | "比特币什么价格" | get_current_price | inst_id=BTC-USDT |

**运行测试：**

```bash
# Mock 模式（不需要 API key，CI 安全）
uv run pytest tests/test_agent_eval.py -v

# 真实 LLM 模式
LLM_API_KEY=sk-xxx uv run pytest tests/test_agent_eval.py -v --real-llm
```

**Eval 结果（`deepseek-v4-flash` / `LLM_BASE_URL=https://api.deepseek.com/anthropic`）：**

| # | 分类 | 输入 | 期望工具 | 结果 | 备注 |
|---|------|------|---------|------|------|
| 1 | 价格查询 | "BTC现在多少钱" | get_current_price | ✅ | inst_id=BTC-USDT ✓ |
| 2 | 价格查询 | "查一下ETH-USDT-SWAP的价格" | get_current_price | ✅ | inst_id=ETH-USDT-SWAP ✓ |
| 3 | 添加告警 | "帮我盯着比特币，突破10万美金..." | add_price_alert | ✅ | price_above, 100000 ✓ |
| 4 | 添加告警 | "ETH跌破3000提醒我" | add_price_alert | ✅ | 先调 get_current_price 查价，再正确添加 price_below 告警 |
| 5 | 波动告警 | "BTC涨5%就通知我，看60分钟" | add_change_alert | ✅ | change_up, 5%, 60min ✓（LLM 先查了行情再添加） |
| 6 | 删除告警 | "删除abc12345这个规则" | remove_alert_rule | ✅ | rule_id=abc12345 ✓ |
| 7 | 查询规则 | "我有哪些监控规则" | list_alert_rules | ✅ | ✓ |
| 8 | 行情分析 | "ETH最近30分钟波动大吗" | calculate_volatility | ✅ | ETH-USDT, 30min ✓ |
| 9 | 多步Agent | "帮我盯着ETH，30分钟内波动超过3%..." | add_change_alert ×2 | ✅ | 双向告警（change_up + change_down）✓ |
| 10 | 币种映射 | "比特币什么价格" | get_current_price | ✅ | 中文名→BTC-USDT ✓ |

**准确率: 10/10 (100%)** | Mock 测试: 23/23 (100%) | 总耗时: ~29s

**已知设计取舍：**
- Agent 内置"规划语言检测"：当模型返回"好的我来查..."而不调工具时，自动追加提示推动 tool_use（针对 DeepSeek 的已知行为）
- LLM 可能在执行主要操作前先调用辅助工具（如添加告警前先查价），eval 框架对此做了匹配而非严格的第一调用检查

### Observability（可观测性）

每次 `/ask` 交互自动记录三层结构化日志，无需额外配置：

```
INFO  LLM #1: latency=850ms tokens_in=1200 tokens_out=45
INFO  工具 #1: add_price_alert(inst_id=BTC-USDT, alert_type=price_above, threshold=100000) → 12ms
INFO  LLM #2: latency=620ms tokens_in=2100 tokens_out=80
INFO  交互完成: total=1820ms llm_calls=2 tool_calls=1 tokens_in=3300 tokens_out=125
```

| 层级 | 指标 | 用途 |
|------|------|------|
| LLM API 调用 | `latency_ms`, `tokens_in`, `tokens_out` | 定位延迟瓶颈（网络 vs 推理）、成本估算 |
| 工具执行 | `tool_name`, `params`, `elapsed_ms` | 排查工具层性能问题 |
| 交互汇总 | `total_ms`, `llm_calls`, `tool_calls`, `tokens_total` | 一次交互的全局画像 |

面试中可以直接引用这些数字：**每次 `/ask` 约消耗 3000-4000 tokens，总延迟 ~2 秒（其中网络往返占大头，工具执行 <20ms），单次成本约人民币几分钱。**

### 设计决策记录

| 决策 | 选择 | 原因 |
|------|------|------|
| LLM SDK | `anthropic` | DeepSeek 提供 Anthropic 兼容端点 (`api.deepseek.com/anthropic`)；tool_use 机制比 OpenAI function calling 更直观 |
| 默认模型 | `deepseek-chat` | tool_use 能力强；`v4-flash` 在 Anthropic 端点上工具调用不稳定 |
| 保留 `/pm` 命令 | 是 | 作为 LLM 不可用时的降级方案；精确命令在某些场景下更高效 |
| calculate_volatility 独立工具 | 是 | LLM 不擅长精确数值计算；统计计算在 Python 层完成，LLM 专注自然语言解读 |
| 无状态 Agent | 是 | 匹配微信消息驱动模型；每次 `/ask` 独立处理，避免会话状态管理复杂度 |

## 告警消息示例

### 价格告警

```
📈 【价格告警】
品种: BTC-USDT
当前价格: $100,500.00
已突破目标价位: $100,000.00
时间: 2026-03-27 21:30:00
```

### 波动告警

```
⬆️ 【波动告警】
品种: BTC-USDT
当前价格: $98,500.00
60分钟涨幅: 5.23%
起始价格: $93,600.00
时间: 2026-03-27 21:30:00
```

### 小数值品种告警

```
📈 【价格告警】
品种: SHIB-USDT
当前价格: $0.00002345
已突破目标价位: $0.00002000
时间: 2026-03-27 21:30:00
```

## 价格显示精度

程序会根据价格大小自动调整显示精度：

| 价格范围 | 示例品种 | 显示效果 |
|---------|---------|---------|
| >= $1,000 | BTC, ETH | $95,432.15 (千位分隔符，2位小数) |
| $1 ~ $1,000 | XRP, DOGE | $2.3456 (最多4位小数) |
| $0.01 ~ $1 | DOGE | $0.1234 (最多4位小数) |
| < $0.01 | SHIB, PEPE | $0.00002345 (自动适应精度) |

## 环境变量

在 `.env` 文件中配置（可选）：

```env
# Redis 配置
REDIS_URL=redis://localhost:6379
# 或
REDIS_HOST=localhost
REDIS_PORT=6379

# OKX WebSocket 地址（可选，默认为官方地址）
OKX_WS_URL=wss://ws.okx.com:8443/ws/v5/public
```

## 品种格式

支持 OKX 交易品种格式：
- 现货：`BTC-USDT`、`ETH-USDT`
- 合约：`BTC-USDT-SWAP`、`ETH-USDT-SWAP`

## 性能优化

程序针对高频 WebSocket 数据流进行了优化，确保低 CPU 和内存占用：

- **Ticker 节流**：每个品种每秒最多处理 1 次 ticker，避免 OKX 高频推送（约 10次/秒/品种）导致的资源浪费
- **规则内存缓存**：监控规则缓存 10 秒，减少 Redis 查询频率
- **价格批量写入**：价格数据每 5 秒批量写入 Redis，而非每条消息都写入
- **延迟历史清理**：价格历史数据每 60 秒清理一次，而非每 tick 重建列表

优化后，即使监控多个品种，CPU 占用也可保持在 **<1%**。

## 依赖

- Python >= 3.13
- larky - 微信机器人框架
- websockets - WebSocket 客户端
- redis - Redis 客户端
- python-dotenv - 环境变量管理
