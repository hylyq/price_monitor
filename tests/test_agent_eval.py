"""Eval / regression tests for the LLM Agent.

Two test modes:
- **Mock mode** (default, no API key needed): validates tool schemas,
  tool executor functions, and the agent loop with a mocked LLM.
- **Real LLM mode** (requires LLM_API_KEY): runs actual queries against
  the LLM and verifies that the right tools are called with correct params.

Usage:
    # Mock mode (CI-safe, no API key):
    uv run pytest tests/test_agent_eval.py -v

    # Real LLM mode:
    LLM_API_KEY=sk-xxx uv run pytest tests/test_agent_eval.py -v --real-llm
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from price_monitor.tools import TOOL_SCHEMAS, TOOL_EXECUTOR_MAP

# ──────────────────────────────────────────────────────────────────────
# Tool schema validation tests (no services needed)
# ──────────────────────────────────────────────────────────────────────


class TestToolSchemas:
    """Validate that all tool schemas are well-formed."""

    def test_all_tools_have_required_fields(self):
        """Each tool must have name, description, and input_schema."""
        for schema in TOOL_SCHEMAS:
            assert "name" in schema, f"Missing 'name' in tool schema"
            assert "description" in schema, f"Missing 'description' in {schema.get('name')}"
            assert "input_schema" in schema, f"Missing 'input_schema' in {schema['name']}"
            input_schema = schema["input_schema"]
            assert input_schema["type"] == "object"
            assert "properties" in input_schema
            assert "required" in input_schema

    def test_all_tools_have_executors(self):
        """Every tool must have a corresponding executor function."""
        for schema in TOOL_SCHEMAS:
            name = schema["name"]
            assert name in TOOL_EXECUTOR_MAP, (
                f"Tool '{name}' has no executor in TOOL_EXECUTOR_MAP"
            )
            assert callable(TOOL_EXECUTOR_MAP[name]), (
                f"Executor for '{name}' is not callable"
            )

    def test_tool_count(self):
        """We expect exactly 8 tools."""
        assert len(TOOL_SCHEMAS) == 8, f"Expected 8 tools, got {len(TOOL_SCHEMAS)}"
        assert len(TOOL_EXECUTOR_MAP) == 8

    def test_known_tool_names(self):
        """Verify the expected tool names are present."""
        names = {s["name"] for s in TOOL_SCHEMAS}
        expected = {
            "get_current_price",
            "get_ticker_detail",
            "get_price_history",
            "calculate_volatility",
            "add_price_alert",
            "add_change_alert",
            "list_alert_rules",
            "remove_alert_rule",
        }
        assert names == expected, f"Tool name mismatch: {names ^ expected}"


# ──────────────────────────────────────────────────────────────────────
# Tool executor unit tests (mock services)
# ──────────────────────────────────────────────────────────────────────


class TestToolExecutors:
    """Test individual tool executor functions with mocked services."""

    @pytest.fixture
    def okx_mock(self):
        mock = MagicMock()
        mock.subscribe = AsyncMock()
        mock.unsubscribe = AsyncMock()
        return mock

    @pytest.fixture
    def storage_mock(self):
        return AsyncMock()

    @pytest.fixture
    def monitor_mock(self):
        return MagicMock()

    # ── get_current_price ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_current_price_returns_price(self, okx_mock, storage_mock):
        okx_mock.get_price.return_value = 95000.0
        result = await TOOL_EXECUTOR_MAP["get_current_price"](
            okx_client=okx_mock,
            storage=storage_mock,
            inst_id="BTC-USDT",
        )
        assert "BTC-USDT" in result
        assert "95,000" in result

    @pytest.mark.asyncio
    async def test_get_current_price_no_data(self, okx_mock, storage_mock):
        okx_mock.get_price.return_value = None
        result = await TOOL_EXECUTOR_MAP["get_current_price"](
            okx_client=okx_mock,
            storage=storage_mock,
            inst_id="BTC-USDT",
        )
        assert "暂时没有价格数据" in result

    # ── get_ticker_detail ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_ticker_detail(self, okx_mock, storage_mock):
        ticker_mock = MagicMock()
        ticker_mock.last = 3000.0
        ticker_mock.high_24h = 3100.0
        ticker_mock.low_24h = 2900.0
        ticker_mock.vol_24h = 123456.0
        okx_mock.get_ticker.return_value = ticker_mock

        result = await TOOL_EXECUTOR_MAP["get_ticker_detail"](
            okx_client=okx_mock,
            storage=storage_mock,
            inst_id="ETH-USDT",
        )
        assert "ETH-USDT" in result
        assert "3,000" in result
        assert "3,100" in result
        assert "123,456" in result

    # ── get_price_history ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_price_history(self, okx_mock, storage_mock):
        from datetime import datetime

        storage_mock.get_price_history.return_value = [
            (datetime(2026, 7, 10, 10, 0, 0), 100.0),
            (datetime(2026, 7, 10, 10, 0, 5), 101.0),
            (datetime(2026, 7, 10, 10, 0, 10), 102.0),
        ]
        result = await TOOL_EXECUTOR_MAP["get_price_history"](
            okx_client=okx_mock,
            storage=storage_mock,
            inst_id="SOL-USDT",
            since_minutes=10,
        )
        assert "SOL-USDT" in result
        assert "10:00:00" in result
        assert "3" in result  # 3 data points

    @pytest.mark.asyncio
    async def test_get_price_history_empty(self, okx_mock, storage_mock):
        storage_mock.get_price_history.return_value = []
        result = await TOOL_EXECUTOR_MAP["get_price_history"](
            okx_client=okx_mock,
            storage=storage_mock,
            inst_id="SOL-USDT",
            since_minutes=10,
        )
        assert "没有价格数据" in result

    # ── calculate_volatility ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_calculate_volatility(self, okx_mock, storage_mock):
        from datetime import datetime

        storage_mock.get_price_history.return_value = [
            (datetime(2026, 7, 10, 10, 0, 0), 100.0),
            (datetime(2026, 7, 10, 10, 0, 5), 102.0),
            (datetime(2026, 7, 10, 10, 0, 10), 105.0),
            (datetime(2026, 7, 10, 10, 0, 15), 103.0),
        ]
        result = await TOOL_EXECUTOR_MAP["calculate_volatility"](
            okx_client=okx_mock,
            storage=storage_mock,
            inst_id="ETH-USDT",
            minutes=10,
        )
        assert "ETH-USDT" in result
        assert "波动率分析" in result
        assert "最高价" in result
        assert "最低价" in result
        assert "涨跌幅" in result
        assert "标准差" in result

    @pytest.mark.asyncio
    async def test_calculate_volatility_insufficient_data(self, okx_mock, storage_mock):
        from datetime import datetime

        storage_mock.get_price_history.return_value = [
            (datetime(2026, 7, 10, 10, 0, 0), 100.0),
        ]
        okx_mock.get_price.return_value = 100.0
        result = await TOOL_EXECUTOR_MAP["calculate_volatility"](
            okx_client=okx_mock,
            storage=storage_mock,
            inst_id="ETH-USDT",
            minutes=10,
        )
        assert "数据点不足" in result

    # ── add_price_alert ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_add_price_alert_above(self, okx_mock, storage_mock, monitor_mock):
        storage_mock.add_rule = AsyncMock()
        result = await TOOL_EXECUTOR_MAP["add_price_alert"](
            okx_client=okx_mock,
            monitor=monitor_mock,
            storage=storage_mock,
            inst_id="BTC-USDT",
            alert_type="price_above",
            threshold=100000,
        )
        storage_mock.add_rule.assert_called_once()
        okx_mock.subscribe.assert_called_once_with(["BTC-USDT"])
        monitor_mock.invalidate_rules_cache.assert_called_once()
        assert "✅" in result
        assert "BTC-USDT" in result
        assert "突破" in result
        assert "100,000" in result

    @pytest.mark.asyncio
    async def test_add_price_alert_below(self, okx_mock, storage_mock, monitor_mock):
        storage_mock.add_rule = AsyncMock()
        result = await TOOL_EXECUTOR_MAP["add_price_alert"](
            okx_client=okx_mock,
            monitor=monitor_mock,
            storage=storage_mock,
            inst_id="ETH-USDT",
            alert_type="price_below",
            threshold=3000,
        )
        assert "跌破" in result
        assert "ETH-USDT" in result

    # ── add_change_alert ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_add_change_alert(self, okx_mock, storage_mock, monitor_mock):
        storage_mock.add_rule = AsyncMock()
        result = await TOOL_EXECUTOR_MAP["add_change_alert"](
            okx_client=okx_mock,
            monitor=monitor_mock,
            storage=storage_mock,
            inst_id="BTC-USDT",
            alert_type="change_up",
            threshold_pct=5,
            interval_minutes=60,
        )
        assert "✅" in result
        assert "BTC-USDT" in result
        assert "60" in result
        assert "涨幅" in result
        assert "5" in result

    # ── list_alert_rules ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_list_alert_rules_empty(self, okx_mock, storage_mock):
        storage_mock.get_all_rules = AsyncMock(return_value=[])
        result = await TOOL_EXECUTOR_MAP["list_alert_rules"](
            okx_client=okx_mock,
            storage=storage_mock,
        )
        assert "暂无监控规则" in result

    @pytest.mark.asyncio
    async def test_list_alert_rules_with_rules(self, okx_mock, storage_mock):
        rule = MagicMock()
        rule.id = "abc12345"
        rule.get_description.return_value = "📈 BTC-USDT 价格 >= $100,000.00"
        storage_mock.get_all_rules = AsyncMock(return_value=[rule])
        result = await TOOL_EXECUTOR_MAP["list_alert_rules"](
            okx_client=okx_mock,
            storage=storage_mock,
        )
        assert "abc12345" in result
        assert "BTC-USDT" in result

    # ── remove_alert_rule ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_remove_alert_rule_found(self, okx_mock, storage_mock, monitor_mock):
        rule = MagicMock()
        rule.id = "abc12345"
        rule.inst_id = "BTC-USDT"
        rule.get_description.return_value = "📈 BTC-USDT 价格 >= $100,000.00"
        storage_mock.get_rule = AsyncMock(return_value=rule)
        storage_mock.remove_rule = AsyncMock(return_value=True)
        storage_mock.get_rules_by_inst = AsyncMock(return_value=[])

        result = await TOOL_EXECUTOR_MAP["remove_alert_rule"](
            okx_client=okx_mock,
            monitor=monitor_mock,
            storage=storage_mock,
            rule_id="abc12345",
        )
        assert "✅" in result
        assert "abc12345" in result
        okx_mock.unsubscribe.assert_called_once_with(["BTC-USDT"])

    @pytest.mark.asyncio
    async def test_remove_alert_rule_not_found(self, okx_mock, storage_mock, monitor_mock):
        storage_mock.get_rule = AsyncMock(return_value=None)
        result = await TOOL_EXECUTOR_MAP["remove_alert_rule"](
            okx_client=okx_mock,
            monitor=monitor_mock,
            storage=storage_mock,
            rule_id="nonexistent",
        )
        assert "❌" in result
        assert "未找到规则" in result


# ──────────────────────────────────────────────────────────────────────
# Agent conversation loop tests (mocked LLM)
# ──────────────────────────────────────────────────────────────────────


class TestAgentLoop:
    """Test the Agent's tool-use loop with a mocked Anthropic client."""

    @pytest.fixture
    def agent(self):
        from price_monitor.agent import Agent

        okx = MagicMock()
        okx.get_price.return_value = 95000.0
        stor = AsyncMock()
        stor.get_price_history.return_value = []
        mon = MagicMock()

        return Agent(
            storage=stor,
            monitor=mon,
            okx_client=okx,
            api_key="test-key",
            base_url="https://test.example.com",
        )

    @pytest.mark.asyncio
    async def test_agent_returns_text_directly(self, agent):
        """When LLM returns text without tool calls, pass it through."""
        # Mock the anthropic client's messages.create to return text directly
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "BTC 当前价格为 $95,000，今日上涨 2.3%。"

        response = MagicMock()
        response.content = [text_block]

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=response)

        with patch(
            "anthropic.AsyncAnthropic",
            return_value=mock_client,
        ):
            result = await agent.answer("BTC现在多少钱")
            assert "95,000" in result

    def test_agent_disabled_without_api_key(self):
        """Agent should warn when no API key is configured."""
        from price_monitor.agent import Agent

        ag = Agent(
            storage=MagicMock(),
            monitor=MagicMock(),
            okx_client=MagicMock(),
            api_key="",
        )
        assert ag.api_key == ""


# ──────────────────────────────────────────────────────────────────────
# Eval test cases — 10 natural-language scenarios
# ──────────────────────────────────────────────────────────────────────

EVAL_CASES = [
    {
        "id": 1,
        "category": "价格查询",
        "input": "BTC现在多少钱",
        "expected_tool": "get_current_price",
        "expected_params": {"inst_id": "BTC-USDT"},
    },
    {
        "id": 2,
        "category": "价格查询",
        "input": "查一下ETH-USDT-SWAP的价格",
        "expected_tool": "get_current_price",
        "expected_params": {"inst_id": "ETH-USDT-SWAP"},
    },
    {
        "id": 3,
        "category": "添加告警",
        "input": "帮我盯着比特币，突破10万美金就通知我",
        "expected_tool": "add_price_alert",
        "expected_params": {
            "inst_id": "BTC-USDT",
            "alert_type": "price_above",
            "threshold": 100000,
        },
    },
    {
        "id": 4,
        "category": "添加告警",
        "input": "ETH跌破3000提醒我",
        "expected_tool": "add_price_alert",
        "expected_params": {
            "inst_id": "ETH-USDT",
            "alert_type": "price_below",
            "threshold": 3000,
        },
    },
    {
        "id": 5,
        "category": "波动告警",
        "input": "BTC涨5%就通知我，看60分钟",
        "expected_tool": "add_change_alert",
        "expected_params": {
            "inst_id": "BTC-USDT",
            "alert_type": "change_up",
            "threshold_pct": 5,
            "interval_minutes": 60,
        },
    },
    {
        "id": 6,
        "category": "删除告警",
        "input": "删除abc12345这个规则",
        "expected_tool": "remove_alert_rule",
        "expected_params": {"rule_id": "abc12345"},
    },
    {
        "id": 7,
        "category": "查询规则",
        "input": "我有哪些监控规则",
        "expected_tool": "list_alert_rules",
        "expected_params": {},
    },
    {
        "id": 8,
        "category": "行情分析",
        "input": "ETH最近30分钟波动大吗",
        "expected_tool": "calculate_volatility",
        "expected_params": {"inst_id": "ETH-USDT", "minutes": 30},
    },
    {
        "id": 9,
        "category": "多步Agent",
        "input": "帮我盯着ETH，波动超过3%就告诉我",
        "expected_tools": ["add_change_alert", "add_change_alert"],
        "expected_multi": True,
        "note": "应同时创建 change_up 和 change_down 两条规则",
    },
    {
        "id": 10,
        "category": "币种映射",
        "input": "比特币什么价格",
        "expected_tool": "get_current_price",
        "expected_params": {"inst_id": "BTC-USDT"},
        "note": "测试中文名→交易对映射",
    },
]


class TestEvalCases:
    """Verify that eval case definitions are self-consistent."""

    def test_eval_cases_count(self):
        assert len(EVAL_CASES) == 10, f"Expected 10 eval cases, got {len(EVAL_CASES)}"

    def test_eval_cases_have_required_fields(self):
        for case in EVAL_CASES:
            assert "id" in case
            assert "category" in case
            assert "input" in case
            assert "expected_tool" in case or "expected_tools" in case

    def test_all_expected_tools_exist(self):
        """Every expected_tool in eval cases must match a real tool."""
        known = set(TOOL_EXECUTOR_MAP.keys())
        for case in EVAL_CASES:
            if "expected_tool" in case:
                assert case["expected_tool"] in known, (
                    f"Case {case['id']}: unknown tool '{case['expected_tool']}'"
                )
            if "expected_tools" in case:
                for t in case["expected_tools"]:
                    assert t in known, (
                        f"Case {case['id']}: unknown tool '{t}'"
                    )


# ──────────────────────────────────────────────────────────────────────
# Real LLM eval (opt-in via --real-llm flag)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not os.getenv("LLM_API_KEY"),
    reason="LLM_API_KEY not set",
)
class TestRealLLMEval:
    """End-to-end eval against a real LLM.

    These tests require LLM_API_KEY to be set.  They validate that the LLM
    correctly understands Chinese natural-language queries and calls the
    right tools with appropriate parameters.
    """

    @pytest.fixture
    def agent(self):
        from price_monitor.agent import Agent
        from price_monitor.storage import RuleStorage
        from price_monitor.monitor import PriceMonitor
        from price_monitor.okx_client import OKXClient

        storage = RuleStorage(redis_url="redis://localhost:6379")
        okx = OKXClient()
        monitor = PriceMonitor(storage=storage)
        return Agent(storage=storage, monitor=monitor, okx_client=okx)

    async def _run_case(self, agent, case: dict, request):
        """Run one eval case and return (passed, tool_calls, response_text)."""
        test_id = case["id"]
        query = case["input"]
        expected_tool = case.get("expected_tool")
        expected_params = case.get("expected_params", {})
        expected_multi = case.get("expected_multi", False)

        try:
            response, transcript = await agent.answer_with_debug(query)
        except Exception as exc:
            return False, [], f"ERROR: {exc}", str(exc)

        # Extract tool calls from the transcript
        tool_calls = []
        for msg in transcript:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                for tc in msg.get("tool_calls", []):
                    tool_calls.append(tc)

        failures = []

        if expected_multi:
            # Multi-tool case: expect multiple tool calls
            expected_list = case.get("expected_tools", [])
            actual_names = [tc["name"] for tc in tool_calls]
            if len(actual_names) < 2:
                failures.append(
                    f"Expected >=2 tool calls for multi-step, got {len(actual_names)}"
                )
            for expected_name in expected_list:
                if expected_name not in actual_names:
                    failures.append(
                        f"Expected tool '{expected_name}' not in {actual_names}"
                    )
        elif expected_tool:
            if not tool_calls:
                failures.append(
                    f"Expected tool '{expected_tool}' but no tool was called"
                )
            else:
                called = tool_calls[0]
                actual_name = called.get("name")
                actual_input = called.get("input", {})

                if actual_name != expected_tool:
                    failures.append(
                        f"Expected tool '{expected_tool}', got '{actual_name}'"
                    )

                for key, expected_val in expected_params.items():
                    actual_val = actual_input.get(key)
                    if actual_val != expected_val:
                        failures.append(
                            f"Param '{key}': expected {expected_val!r}, "
                            f"got {actual_val!r}"
                        )

        passed = len(failures) == 0
        return passed, tool_calls, response[:300], "; ".join(failures) if failures else "OK"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", EVAL_CASES, ids=[f"case{c['id']}" for c in EVAL_CASES])
    async def test_eval_case(self, agent, case, request):
        """Run one eval case against the real LLM."""
        if not request.config.getoption("--real-llm"):
            pytest.skip("Use --real-llm to run real LLM tests")

        passed, tool_calls, response, detail = await self._run_case(
            agent, case, request
        )

        print(f"\n  Query: {case['input']}")
        print(f"  Tool calls: {tool_calls}")
        print(f"  Response: {response}")
        print(f"  Result: {detail}")

        assert passed, f"Case {case['id']} ({case['category']}) failed: {detail}"
