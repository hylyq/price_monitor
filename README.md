# OKX 价格监控程序

基于 OKX WebSocket 的数字货币价格监控程序，支持微信交互配置监控规则。

## 功能特性

- **实时价格监控**：通过 OKX WebSocket 获取实时价格数据
- **价格告警**：价格突破/跌破指定价位时发送通知
- **波动告警**：指定时间周期内涨跌幅达到阈值时发送通知
- **微信交互**：通过微信命令添加/删除/查询监控规则
- **规则持久化**：监控规则存储在 Redis，程序重启后自动恢复

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

## 依赖

- Python >= 3.13
- larky - 微信机器人框架
- websockets - WebSocket 客户端
- redis - Redis 客户端
- python-dotenv - 环境变量管理
