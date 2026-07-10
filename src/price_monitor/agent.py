"""LLM Agent — natural language interface to the price monitor.

Uses the Anthropic SDK pointed at a configurable API endpoint (defaults to
DeepSeek's Anthropic-compatible API) to run a tool-use loop: the LLM decides
which tool to call, the agent executes it, and results are fed back until the
LLM produces a final text response.

Usage:
    agent = Agent(storage, monitor, okx_client)
    reply = await agent.answer("帮我盯着BTC，突破10万就通知我")
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from price_monitor.storage import RuleStorage
    from price_monitor.monitor import PriceMonitor
    from price_monitor.okx_client import OKXClient

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-v4-flash"
MAX_TOOL_ITERATIONS = 10

# ──────────────────────────────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一个数字货币行情监控助手。你拥有调用工具的能力，**必须通过工具来完成任务**。

## ⚠️ 核心规则（违反将导致错误）

1. **必须直接调用工具**——当用户要求查询/分析/设置告警时，立即调用对应工具。
   不要回复"好的我来查一下"而不调用工具，不要先描述再执行。
   唯一例外：用户意图确实模糊（如未提供阈值）时，才先询问。

2. **不要重复确认**——用户说"帮我监控BTC突破10万"时，直接调用 add_price_alert，
   在工具返回成功后再告知用户结果。不要在调用工具前发确认消息。

3. **分析问题先取数据**——用户问"波动大吗/涨了多少"，必须先调用 calculate_volatility
   或 get_price_history 获取真实数据，再基于数据回答。绝不编造数据。

## 交易品种映射
用户可能使用中文名称或简称，自动转换为标准格式：
- 比特币 / BTC / 大饼 → BTC-USDT
- 以太坊 / ETH → ETH-USDT
- SOL / Solana → SOL-USDT
- 狗狗币 / DOGE → DOGE-USDT
- 瑞波 / XRP → XRP-USDT
- 莱特币 / LTC → LTC-USDT
- BNB → BNB-USDT
- AVAX → AVAX-USDT
- 用户明确指定的其他格式（如 ETH-USDT-SWAP）直接使用。

如果用户提到的币种你无法确定映射，先调用 get_current_price 尝试（不填 inst_id 时返回所有已订阅价格），再询问用户。

## 数值约定
- "10万美金" = 100000，"1万" = 10000（中文"万" = 10,000）
- 时间："30分钟" = 30，"1小时" = 60
- 百分比："3%" = 3.0，"5个点" = 5.0
- **如果用户说"波动超过X%"但未指定时间窗口 → 默认 30 分钟**

## 工具选择规则
- "XX多少钱/什么价格" → get_current_price
- "详细行情/24小时" → get_ticker_detail
- "涨了多少/波动大吗/最近走势" → calculate_volatility
- "盯着/监控/突破X通知我/跌破X提醒我" → add_price_alert
- "涨X%告诉我/跌X%通知" → add_change_alert (单方向)
- "波动超过X%" → add_change_alert ×2 (双向: change_up + change_down)
- "有哪些规则/查看监控" → list_alert_rules
- "删除/取消/去掉XX规则" → remove_alert_rule (不知道ID时先 list_alert_rules)

## 回复风格
- 工具返回成功后，用中文简洁确认结果（附带规则ID、具体数值）
- 工具返回错误时，如实告知并建议替代方案
- 价格始终用 $ 显示"""


# ──────────────────────────────────────────────────────────────────────
# Agent
# ──────────────────────────────────────────────────────────────────────


class Agent:
    """Natural language agent that uses an LLM to operate the price monitor.

    Each ``answer()`` call is a self-contained interaction — no conversation
    history is preserved across calls.  This keeps the WeChat bot stateless
    and matches the existing command-handler pattern.

    Parameters
    ----------
    storage:
        RuleStorage instance for rule / price-history persistence.
    monitor:
        PriceMonitor instance for rule cache invalidation.
    okx_client:
        OKXClient instance for real-time price queries and subscriptions.
    api_key:
        LLM API key.  Defaults to ``LLM_API_KEY`` env var.
    base_url:
        Anthropic-compatible API base URL.  Defaults to DeepSeek endpoint.
    model:
        Model name.  Defaults to ``deepseek-v4-flash``.
    """

    def __init__(
        self,
        storage: RuleStorage,
        monitor: PriceMonitor,
        okx_client: OKXClient,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.storage = storage
        self.monitor = monitor
        self.okx_client = okx_client
        self.api_key = api_key if api_key is not None else os.getenv("LLM_API_KEY", "")
        self.base_url = base_url if base_url is not None else os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)
        self.model = model if model is not None else os.getenv("LLM_MODEL", DEFAULT_MODEL)

        if not self.api_key:
            logger.warning("LLM_API_KEY 未设置——Agent 将无法调用 LLM")

    # ── public API ────────────────────────────────────────────────────

    async def answer(self, query: str) -> str:
        """Process a natural-language query and return a reply string.

        Runs the Anthropic tool-use loop: sends the query + tool definitions
        to the LLM, executes any requested tools, feeds results back, and
        repeats until the LLM produces a final text answer (or the iteration
        limit is reached).
        """
        # Lazy import — module loads cleanly even without the SDK installed.
        try:
            import anthropic
        except ImportError:
            return (
                "❌ anthropic SDK 未安装，无法使用 AI 功能。\n"
                "请运行: uv add anthropic\n"
                "或使用 /pm 命令手动操作。"
            )

        if not self.api_key:
            return (
                "❌ LLM_API_KEY 未配置，无法使用 AI 功能。\n"
                "请在 .env 文件中设置 LLM_API_KEY。\n"
                "或使用 /pm 命令手动操作（发送 /pm help 查看帮助）。"
            )

        from price_monitor.tools import TOOL_SCHEMAS, TOOL_EXECUTOR_MAP

        client = anthropic.AsyncAnthropic(
            api_key=self.api_key,
            base_url=self.base_url,
        )

        t_start = time.monotonic()
        total_tokens_in = 0
        total_tokens_out = 0
        total_tool_calls = 0

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": query}
        ]

        iteration = 0
        while iteration < MAX_TOOL_ITERATIONS:
            iteration += 1
            t_call = time.monotonic()
            try:
                response = await client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    tools=TOOL_SCHEMAS,
                    messages=messages,
                )
            except Exception as exc:
                logger.error("LLM API 调用失败: %s", exc)
                return (
                    f"❌ AI 服务暂时不可用: {exc}\n"
                    f"请稍后重试，或使用 /pm 命令手动操作。"
                )

            # ── Observability: log API call latency + token usage ──
            latency_ms = (time.monotonic() - t_call) * 1000
            usage = getattr(response, "usage", None)
            tokens_in = usage.input_tokens if usage else 0
            tokens_out = usage.output_tokens if usage else 0
            total_tokens_in += tokens_in
            total_tokens_out += tokens_out
            logger.info(
                "LLM #%d: latency=%dms tokens_in=%d tokens_out=%d",
                iteration, int(latency_ms), tokens_in, tokens_out,
            )

            # Collect all content blocks first.
            text_blocks: list[str] = []
            assistant_blocks: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []

            for block in response.content:
                if block.type == "text":
                    text_blocks.append(block.text)

                elif block.type == "tool_use":
                    tool_name: str = block.name
                    tool_input: dict[str, Any] = block.input or {}
                    tool_use_id: str = block.id

                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": tool_name,
                        "input": tool_input,
                    })

                    executor = TOOL_EXECUTOR_MAP.get(tool_name)
                    if executor is None:
                        result_str = f"未知工具: {tool_name}"
                        t_tool = 0
                    else:
                        t_tool = time.monotonic()
                        try:
                            result_str = await executor(
                                okx_client=self.okx_client,
                                monitor=self.monitor,
                                storage=self.storage,
                                **tool_input,
                            )
                        except Exception as exc:
                            logger.exception("工具 %s 执行失败", tool_name)
                            result_str = f"工具 {tool_name} 执行出错: {exc}"
                        t_tool = (time.monotonic() - t_tool) * 1000

                    total_tool_calls += 1
                    logger.info(
                        "工具 #%d: %s(%s) → %dms",
                        total_tool_calls, tool_name,
                        {k: v for k, v in tool_input.items()},
                        int(t_tool),
                    )

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result_str,
                    })

            # ── Decision: text vs tool_use ──────────────────────────
            if assistant_blocks:
                # Model called tools → execute and continue the loop.
                messages.append({"role": "assistant", "content": assistant_blocks})
                messages.append({"role": "user", "content": tool_results})
                continue

            if text_blocks:
                combined_text = "\n".join(text_blocks)

                # First iteration with no tool calls: model is "planning"
                # instead of doing.  Push it to actually use tools.
                if iteration == 1 and messages[0]["role"] == "user":
                    # Check for planning-language markers.
                    planning_markers = (
                        "我来查", "我来帮", "让我查", "让我帮",
                        "好的，", "马上执行", "正在为您",
                        "先获取", "先查询", "我先",
                    )
                    if any(m in combined_text for m in planning_markers):
                        logger.info("检测到规划语言，推动模型调用工具")
                        messages.append({
                            "role": "assistant",
                            "content": [{"type": "text", "text": combined_text}],
                        })
                        messages.append({
                            "role": "user",
                            "content": (
                                "请直接调用工具执行操作，不要再描述计划。"
                                "调用工具后我会把结果告诉你。"
                            ),
                        })
                        continue

                total_ms = (time.monotonic() - t_start) * 1000
                logger.info(
                    "交互完成: total=%dms llm_calls=%d tool_calls=%d "
                    "tokens_in=%d tokens_out=%d",
                    int(total_ms), iteration, total_tool_calls,
                    total_tokens_in, total_tokens_out,
                )
                return combined_text

            # No text and no tool calls — unexpected.
            return "❌ 抱歉，我没有理解您的请求，请换一种方式描述。"

        return (
            "❌ 处理步骤过多，请简化您的请求后重试。\n"
            "提示: 可以将复杂任务拆分为多个简单请求。"
        )

    async def answer_with_debug(
        self, query: str
    ) -> tuple[str, list[dict[str, Any]]]:
        """Like :meth:`answer` but also returns the raw message transcript.

        Used for eval / debugging to inspect which tools were called.
        """
        import anthropic

        if not self.api_key:
            return (
                "❌ LLM_API_KEY 未配置。",
                [],
            )

        from price_monitor.tools import TOOL_SCHEMAS, TOOL_EXECUTOR_MAP

        client = anthropic.AsyncAnthropic(
            api_key=self.api_key,
            base_url=self.base_url,
        )

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": query}
        ]
        all_raw: list[dict[str, Any]] = []

        iteration = 0
        while iteration < MAX_TOOL_ITERATIONS:
            iteration += 1
            response = await client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )

            assistant_blocks: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []
            raw_tool_calls: list[dict[str, Any]] = []
            text_parts: list[str] = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)

                elif block.type == "tool_use":
                    raw_tool_calls.append({
                        "name": block.name,
                        "input": block.input,
                    })
                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

                    executor = TOOL_EXECUTOR_MAP.get(block.name)
                    if executor:
                        try:
                            result_str = await executor(
                                okx_client=self.okx_client,
                                monitor=self.monitor,
                                storage=self.storage,
                                **(block.input or {}),
                            )
                        except Exception as exc:
                            result_str = f"Error: {exc}"
                    else:
                        result_str = f"Unknown tool: {block.name}"

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

            all_raw.append({
                "role": "assistant",
                "tool_calls": raw_tool_calls,
            })

            if assistant_blocks:
                messages.append({"role": "assistant", "content": assistant_blocks})
                messages.append({"role": "user", "content": tool_results})
                continue

            if text_parts:
                combined_text = "\n".join(text_parts)
                if iteration == 1:
                    planning_markers = (
                        "我来查", "我来帮", "让我查", "让我帮",
                        "好的，", "马上执行", "正在为您",
                        "先获取", "先查询", "我先",
                    )
                    if any(m in combined_text for m in planning_markers):
                        messages.append({
                            "role": "assistant",
                            "content": [{"type": "text", "text": combined_text}],
                        })
                        messages.append({
                            "role": "user",
                            "content": (
                                "请直接调用工具执行操作，不要再描述计划。"
                                "调用工具后我会把结果告诉你。"
                            ),
                        })
                        continue
                return combined_text, all_raw

            return "❌ 无法解析 LLM 响应。", all_raw

        return "❌ 处理步骤过多。", all_raw
