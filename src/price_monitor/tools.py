"""Tool definitions for the LLM Agent.

Each tool has an Anthropic-format JSON Schema definition and a corresponding
async Python executor function that wraps existing domain services.

Architecture:
    LLM (DeepSeek via Anthropic-compatible API)
      → tool_use block (name + input)
      → TOOL_EXECUTOR_MAP[name](okx_client, monitor, storage, **input)
      → tool_result string
      → LLM generates final text reply
"""

from __future__ import annotations

import logging
import statistics
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from price_monitor.storage import RuleStorage
    from price_monitor.monitor import PriceMonitor
    from price_monitor.okx_client import OKXClient

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Tool schemas — Anthropic tool_use format
# ──────────────────────────────────────────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_current_price",
        "description": (
            "查询指定交易对的实时价格。返回当前最新成交价。"
            "用于用户询问\"XX现在多少钱\"类型的价格查询。"
            "注意：返回的是 WebSocket 推送的实时价格，来自内存缓存，非 HTTP 请求。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inst_id": {
                    "type": "string",
                    "description": (
                        "交易对 ID，如 BTC-USDT、ETH-USDT、SOL-USDT。"
                        "格式: 币种名-USDT，全部大写。"
                    ),
                }
            },
            "required": ["inst_id"],
        },
    },
    {
        "name": "get_ticker_detail",
        "description": (
            "查询交易对的详细行情，包括当前价格、24小时最高价、"
            "24小时最低价和24小时成交量。一次性获取多个维度的数据，"
            "减少 API 调用次数。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inst_id": {
                    "type": "string",
                    "description": "交易对 ID，如 BTC-USDT、ETH-USDT",
                }
            },
            "required": ["inst_id"],
        },
    },
    {
        "name": "get_price_history",
        "description": (
            "获取指定交易对在过去 N 分钟内的价格历史数据。"
            "返回时间戳和价格的列表，可用于分析价格走势。"
            "数据来源为 Redis 持久化的历史价格（每 5 秒一个数据点，最多保留 1000 条）。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inst_id": {
                    "type": "string",
                    "description": "交易对 ID，如 BTC-USDT、ETH-USDT",
                },
                "since_minutes": {
                    "type": "integer",
                    "description": "查询过去多少分钟的历史数据（1-60 分钟）",
                    "minimum": 1,
                    "maximum": 60,
                },
            },
            "required": ["inst_id", "since_minutes"],
        },
    },
    {
        "name": "calculate_volatility",
        "description": (
            "计算交易对在指定时间窗口内的波动率分析。"
            "返回最高价、最低价、价格范围百分比、涨跌幅和标准差。"
            "用于回答\"波动大吗\"\"涨了多少\"\"跌了多少\"等分析类问题。"
            "注意：这个工具会先获取历史数据，然后在 Python 层完成统计计算，"
            "比让 LLM 手动计算更精确可靠。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inst_id": {
                    "type": "string",
                    "description": "交易对 ID，如 BTC-USDT、ETH-USDT",
                },
                "minutes": {
                    "type": "integer",
                    "description": "波动率计算的时间窗口（1-60 分钟）",
                    "minimum": 1,
                    "maximum": 60,
                },
            },
            "required": ["inst_id", "minutes"],
        },
    },
    {
        "name": "add_price_alert",
        "description": (
            "添加一个价格阈值告警规则。当价格突破或跌破指定价位时触发通知。"
            "用户说\"帮我盯着\"\"突破X就通知我\"\"跌破X提醒我\"时使用此工具。"
            "price_above: 价格向上突破阈值时告警（\"涨到X就告诉我\"）。"
            "price_below: 价格向下跌破阈值时告警（\"跌到X就告诉我\"）。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inst_id": {
                    "type": "string",
                    "description": "交易对 ID，如 BTC-USDT、ETH-USDT",
                },
                "alert_type": {
                    "type": "string",
                    "enum": ["price_above", "price_below"],
                    "description": (
                        "price_above: 价格 >= 阈值时触发; "
                        "price_below: 价格 <= 阈值时触发"
                    ),
                },
                "threshold": {
                    "type": "number",
                    "description": "价格阈值（USDT），如 100000 表示 10 万美金",
                },
            },
            "required": ["inst_id", "alert_type", "threshold"],
        },
    },
    {
        "name": "add_change_alert",
        "description": (
            "添加一个涨跌幅告警规则。当价格在指定时间窗口内的涨跌幅度"
            "达到阈值时触发通知。用户说\"波动超过X%\"\"涨X%就告诉我\""
            "\"跌X%提醒我\"时使用此工具。"
            "change_up: 涨幅达到阈值时告警。"
            "change_down: 跌幅达到阈值时告警。"
            "用户说\"波动超过3%\"时，通常需要同时创建 change_up 和 change_down。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inst_id": {
                    "type": "string",
                    "description": "交易对 ID，如 BTC-USDT、ETH-USDT",
                },
                "alert_type": {
                    "type": "string",
                    "enum": ["change_up", "change_down"],
                    "description": (
                        "change_up: 涨幅 >= 阈值时触发; "
                        "change_down: 跌幅 >= 阈值时触发"
                    ),
                },
                "threshold_pct": {
                    "type": "number",
                    "description": "涨跌幅百分比阈值（如 3.0 表示 3%）",
                },
                "interval_minutes": {
                    "type": "integer",
                    "description": "监控的时间窗口（分钟），如 30 表示 30 分钟内",
                    "minimum": 1,
                    "maximum": 1440,
                },
            },
            "required": ["inst_id", "alert_type", "threshold_pct", "interval_minutes"],
        },
    },
    {
        "name": "list_alert_rules",
        "description": (
            "列出当前所有活跃的告警规则。可选按交易对过滤。"
            "用于用户询问\"我有哪些监控\"\"查看规则\"时。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inst_id": {
                    "type": "string",
                    "description": "可选。按交易对过滤，如 BTC-USDT。不填则返回全部规则。",
                }
            },
            "required": [],
        },
    },
    {
        "name": "remove_alert_rule",
        "description": (
            "删除一个告警规则。需要提供规则 ID。"
            "用户说\"删除规则\"\"取消监控\"\"去掉XX告警\"时使用。"
            "如果用户不知道规则 ID，先调用 list_alert_rules 查看。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rule_id": {
                    "type": "string",
                    "description": "规则 ID（8 位字符，如 abc12345）",
                }
            },
            "required": ["rule_id"],
        },
    },
]


# ──────────────────────────────────────────────────────────────────────
# Tool executor functions
# ──────────────────────────────────────────────────────────────────────


async def execute_get_current_price(
    okx_client: OKXClient,
    **kwargs: Any,
) -> str:
    inst_id: str = kwargs["inst_id"]
    price = okx_client.get_price(inst_id)
    if price is None:
        await okx_client.subscribe([inst_id])
        return (
            f"{inst_id} 暂时没有价格数据，已自动订阅该品种。"
            f"请稍后再次查询。"
        )
    from price_monitor.storage import format_price
    return f"{inst_id} 当前价格: ${format_price(price)}"


async def execute_get_ticker_detail(
    okx_client: OKXClient,
    **kwargs: Any,
) -> str:
    inst_id: str = kwargs["inst_id"]
    ticker = okx_client.get_ticker(inst_id)
    if ticker is None:
        await okx_client.subscribe([inst_id])
        return (
            f"{inst_id} 暂时没有行情数据，已自动订阅该品种。"
            f"请稍后再次查询。"
        )
    from price_monitor.storage import format_price
    return (
        f"{inst_id} 详细行情:\n"
        f"  当前价格: ${format_price(ticker.last)}\n"
        f"  24h 最高: ${format_price(ticker.high_24h)}\n"
        f"  24h 最低: ${format_price(ticker.low_24h)}\n"
        f"  24h 成交量: {ticker.vol_24h:,.0f}"
    )


async def execute_get_price_history(
    storage: RuleStorage,
    **kwargs: Any,
) -> str:
    inst_id: str = kwargs["inst_id"]
    since_minutes: int = kwargs["since_minutes"]
    history = await storage.get_price_history(inst_id, since_minutes)
    if not history:
        return (
            f"{inst_id} 在过去 {since_minutes} 分钟内没有价格数据。"
            f"数据可能尚未积累，请等待几分钟后再试。"
        )
    from price_monitor.storage import format_price
    lines = [
        f"  [{ts.strftime('%H:%M:%S')}] ${format_price(price)}"
        for ts, price in history
    ]
    return (
        f"{inst_id} 过去 {since_minutes} 分钟价格历史 "
        f"（{len(history)} 个数据点）:\n" + "\n".join(lines)
    )


async def execute_calculate_volatility(
    okx_client: OKXClient,
    storage: RuleStorage,
    **kwargs: Any,
) -> str:
    inst_id: str = kwargs["inst_id"]
    minutes: int = kwargs["minutes"]
    history = await storage.get_price_history(inst_id, minutes)

    if len(history) < 2:
        current = okx_client.get_price(inst_id)
        if current is not None:
            return (
                f"{inst_id} 在过去 {minutes} 分钟内数据点不足 "
                f"（仅 {len(history)} 个），无法计算波动率。"
                f"当前价格: ${current:.6g}"
            )
        return f"{inst_id} 没有可用的价格数据。"

    prices = [p for _, p in history]
    high = max(prices)
    low = min(prices)
    first = prices[0]
    last = prices[-1]
    change_pct = ((last - first) / first) * 100
    price_range_pct = ((high - low) / low) * 100
    std_dev = statistics.stdev(prices) if len(prices) >= 2 else 0.0

    from price_monitor.storage import format_price

    return (
        f"{inst_id} 波动率分析（过去 {minutes} 分钟，{len(history)} 个数据点）:\n"
        f"  最高价: ${format_price(high)}\n"
        f"  最低价: ${format_price(low)}\n"
        f"  价格区间: {price_range_pct:.2f}%\n"
        f"  涨跌幅: {change_pct:+.2f}%\n"
        f"  标准差: ${std_dev:.6g}"
    )


async def execute_add_price_alert(
    okx_client: OKXClient,
    monitor: PriceMonitor,
    storage: RuleStorage,
    **kwargs: Any,
) -> str:
    from price_monitor.storage import AlertRule, AlertType, format_price
    from price_monitor.monitor import PriceMonitor as PM

    inst_id: str = kwargs["inst_id"]
    alert_type: AlertType = (
        AlertType.PRICE_ABOVE
        if kwargs["alert_type"] == "price_above"
        else AlertType.PRICE_BELOW
    )
    threshold: float = kwargs["threshold"]

    rule_id = PM.create_rule_id()
    rule = AlertRule(
        id=rule_id,
        inst_id=inst_id,
        alert_type=alert_type,
        threshold=threshold,
    )
    await storage.add_rule(rule)
    await okx_client.subscribe([inst_id])
    monitor.invalidate_rules_cache()

    direction = "突破" if alert_type == AlertType.PRICE_ABOVE else "跌破"
    return (
        f"✅ 已添加价格告警规则\n"
        f"规则ID: {rule_id}\n"
        f"条件: {inst_id} {direction} ${format_price(threshold)} 时通知"
    )


async def execute_add_change_alert(
    okx_client: OKXClient,
    monitor: PriceMonitor,
    storage: RuleStorage,
    **kwargs: Any,
) -> str:
    from price_monitor.storage import AlertRule, AlertType
    from price_monitor.monitor import PriceMonitor as PM

    inst_id: str = kwargs["inst_id"]
    alert_type: AlertType = (
        AlertType.CHANGE_UP
        if kwargs["alert_type"] == "change_up"
        else AlertType.CHANGE_DOWN
    )
    threshold_pct: float = kwargs["threshold_pct"]
    interval_minutes: int = kwargs["interval_minutes"]

    rule_id = PM.create_rule_id()
    rule = AlertRule(
        id=rule_id,
        inst_id=inst_id,
        alert_type=alert_type,
        threshold=threshold_pct,
        interval_minutes=interval_minutes,
    )
    await storage.add_rule(rule)
    await okx_client.subscribe([inst_id])
    monitor.invalidate_rules_cache()

    direction = "涨幅" if alert_type == AlertType.CHANGE_UP else "跌幅"
    return (
        f"✅ 已添加波动告警规则\n"
        f"规则ID: {rule_id}\n"
        f"条件: {inst_id} {interval_minutes} 分钟{direction} >= {threshold_pct}% 时通知"
    )


async def execute_list_alert_rules(
    storage: RuleStorage,
    **kwargs: Any,
) -> str:
    inst_id: str | None = kwargs.get("inst_id")
    if inst_id:
        rules = await storage.get_rules_by_inst(inst_id)
    else:
        rules = await storage.get_all_rules()

    if not rules:
        scope = inst_id or "所有品种"
        return f"📭 {scope}暂无监控规则。"

    lines = [f"  [{r.id}] {r.get_description()}" for r in rules]
    return f"📋 监控规则列表（共 {len(rules)} 条）:\n" + "\n".join(lines)


async def execute_remove_alert_rule(
    okx_client: OKXClient,
    monitor: PriceMonitor,
    storage: RuleStorage,
    **kwargs: Any,
) -> str:
    rule_id: str = kwargs["rule_id"]
    rule = await storage.get_rule(rule_id)

    if not rule:
        return f"❌ 未找到规则: {rule_id}"

    await storage.remove_rule(rule_id)

    remaining = await storage.get_rules_by_inst(rule.inst_id)
    if not remaining:
        await okx_client.unsubscribe([rule.inst_id])

    monitor.invalidate_rules_cache()

    return f"✅ 已删除规则 [{rule_id}]: {rule.get_description()}"


# ──────────────────────────────────────────────────────────────────────
# Tool executor dispatch map
# ──────────────────────────────────────────────────────────────────────

TOOL_EXECUTOR_MAP: dict[str, Any] = {
    "get_current_price": execute_get_current_price,
    "get_ticker_detail": execute_get_ticker_detail,
    "get_price_history": execute_get_price_history,
    "calculate_volatility": execute_calculate_volatility,
    "add_price_alert": execute_add_price_alert,
    "add_change_alert": execute_add_change_alert,
    "list_alert_rules": execute_list_alert_rules,
    "remove_alert_rule": execute_remove_alert_rule,
}
