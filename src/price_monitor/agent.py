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

SYSTEM_PROMPT = """你是一个数字货币行情监控助手。你可以帮助用户查询实时价格、分析市场波动、设置价格告警规则。

## 交易品种映射
用户可能使用中文名称或简称，请自动转换为标准格式：
- 比特币 / BTC / 大饼 → BTC-USDT
- 以太坊 / ETH → ETH-USDT
- SOL / Solana → SOL-USDT
- 狗狗币 / DOGE → DOGE-USDT
- 瑞波 / XRP → XRP-USDT
- 莱特币 / LTC → LTC-USDT
- BNB → BNB-USDT
- AVAX → AVAX-USDT
- 用户明确指定的其他格式（如 ETH-USDT-SWAP）直接使用。

如果用户提到的币种你无法确定映射，请询问用户确认。

## 数值约定
- "10万美金" = 100000，"1万" = 10000，以此类推（中文"万" = 10,000）
- 时间："30分钟" = 30，"1小时" = 60，"半天" ≈ 720
- 百分比："3%" = 3.0，"5个点" = 5.0

## 工具使用指南
- 用户询问"XX多少钱/什么价格" → get_current_price
- 用户想看详细行情 → get_ticker_detail
- 用户问"涨了多少/波动大吗/最近走势" → calculate_volatility（一次完成统计+分析）
- 用户说"盯着/监控/突破X通知我/跌破X提醒我" → add_price_alert
- 用户说"涨X%告诉我/跌X%通知/波动超过X%" → add_change_alert
  * 如果用户只说"涨"或"跌"一个方向，只创建一个方向的规则
  * 如果用户说"波动超过X%"，则需要同时创建 change_up 和 change_down
- 用户问"有哪些规则/查看监控" → list_alert_rules
- 用户说"删除/取消/去掉XX规则" → remove_alert_rule
  * 如果用户没有提供规则ID，先调用 list_alert_rules 让用户选择

## 重要规则
1. 所有操作须先确认再执行——在回复中明确列出将要执行的操作。
2. 添加告警后，必须在回复中告知规则ID。
3. 如果工具返回错误或无数据，如实告知用户，并建议替代方案。
4. 价格以 $ 显示，使用合理的精度。
5. 分析类问题基于实际数据给出判断，不要编造。
6. 如果用户意图模糊（如只说"帮我盯着"但没给阈值），应询问具体条件。
7. 回复使用中文，语气友好专业，简洁明了。"""


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

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": query}
        ]

        for _ in range(MAX_TOOL_ITERATIONS):
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

            # Build the assistant message from response content blocks.
            assistant_blocks: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []

            for block in response.content:
                if block.type == "text":
                    return block.text

                elif block.type == "tool_use":
                    tool_name: str = block.name
                    tool_input: dict[str, Any] = block.input or {}
                    tool_use_id: str = block.id

                    logger.info(
                        "LLM 请求工具调用: %s(%s)", tool_name, tool_input
                    )

                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": tool_name,
                        "input": tool_input,
                    })

                    executor = TOOL_EXECUTOR_MAP.get(tool_name)
                    if executor is None:
                        result_str = f"未知工具: {tool_name}"
                    else:
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

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result_str,
                    })

            if not tool_results:
                # No text and no tool calls — shouldn't happen, but guard.
                return "❌ 抱歉，我没有理解您的请求，请换一种方式描述。"

            messages.append({"role": "assistant", "content": assistant_blocks})
            messages.append({"role": "user", "content": tool_results})

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

        for _ in range(MAX_TOOL_ITERATIONS):
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

            for block in response.content:
                if block.type == "text":
                    all_raw.extend(messages)
                    return block.text, all_raw

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

            if not tool_results:
                return "❌ 无法解析 LLM 响应。", all_raw

            messages.append({"role": "assistant", "content": assistant_blocks})
            messages.append({"role": "user", "content": tool_results})

        return "❌ 处理步骤过多。", all_raw
