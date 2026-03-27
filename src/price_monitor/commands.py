import logging
import re
from typing import TYPE_CHECKING

from .storage import AlertRule, AlertType, format_price
from .monitor import PriceMonitor

if TYPE_CHECKING:
    from larky import WeChatClient

logger = logging.getLogger(__name__)

VALID_INST_ID_PATTERN = re.compile(r"^[A-Z]+-USDT(-SWAP)?$")


class CommandHandler:
    def __init__(
        self,
        storage: "RuleStorage",
        monitor: PriceMonitor,
        okx_client: "OKXClient",
        wechat_client: "WeChatClient",
    ):
        self.storage = storage
        self.monitor = monitor
        self.okx_client = okx_client
        self.wechat_client = wechat_client

    async def handle_text(self, text: str, client: "WeChatClient") -> None:
        text = text.strip()
        if not text.startswith("/pm"):
            return

        parts = text.split()
        if len(parts) < 2:
            await client.notify("❌ 请指定子命令\n用法: /pm <命令> [参数]\n发送 /pm help 查看帮助")
            return

        cmd = parts[1].lower()
        args = parts[2:]

        handlers = {
            "help": self._cmd_help,
            "h": self._cmd_help,
            "add": self._cmd_add,
            "del": self._cmd_del,
            "list": self._cmd_list,
            "ls": self._cmd_list,
            "price": self._cmd_price,
            "p": self._cmd_price,
            "clear": self._cmd_clear,
        }

        handler = handlers.get(cmd)
        if handler:
            result = await handler(args)
            await client.notify(result)
        else:
            await client.notify(f"❌ 未知命令: {cmd}\n发送 /pm help 查看帮助")

    async def _cmd_help(self, args: list) -> str:
        return """🤖 OKX价格监控命令

📊 查询命令:
  /pm price <品种> - 查询当前价格
  /pm list [品种] - 查看监控规则

➕ 添加监控:
  /pm add <品种> > <价格> - 价格突破告警
  /pm add <品种> < <价格> - 价格跌破告警
  /pm add <品种> up <涨幅>% <分钟> - 涨幅告警
  /pm add <品种> down <跌幅>% <分钟> - 跌幅告警

➖ 删除监控:
  /pm del <规则ID> - 删除指定规则
  /pm clear [品种] - 清除所有规则

📝 示例:
  /pm price BTC-USDT
  /pm add BTC-USDT > 100000
  /pm add ETH-USDT < 3000
  /pm add BTC-USDT up 5 60
  /pm add BTC-USDT down 3 30
  /pm del abc12345

💡 品种格式: BTC-USDT, ETH-USDT-SWAP"""

    async def _cmd_add(self, args: list) -> str:
        if len(args) < 3:
            return "❌ 参数不足\n用法: /pm add <品种> <操作> <阈值> [分钟]"

        inst_id = args[0].upper()
        if not VALID_INST_ID_PATTERN.match(inst_id):
            return f"❌ 无效品种格式: {inst_id}\n正确格式: BTC-USDT 或 ETH-USDT-SWAP"

        op = args[1].lower()

        try:
            if op in (">", "above"):
                if len(args) < 3:
                    return "❌ 缺少价格参数"
                threshold = float(args[2].replace(",", ""))
                alert_type = AlertType.PRICE_ABOVE
                interval = 0
            elif op in ("<", "below"):
                if len(args) < 3:
                    return "❌ 缺少价格参数"
                threshold = float(args[2].replace(",", ""))
                alert_type = AlertType.PRICE_BELOW
                interval = 0
            elif op == "up":
                if len(args) < 4:
                    return "❌ 缺少涨幅或时间参数"
                threshold = float(args[2].rstrip("%"))
                interval = int(args[3])
                alert_type = AlertType.CHANGE_UP
            elif op == "down":
                if len(args) < 4:
                    return "❌ 缺少跌幅或时间参数"
                threshold = float(args[2].rstrip("%"))
                interval = int(args[3])
                alert_type = AlertType.CHANGE_DOWN
            else:
                return f"❌ 未知操作: {op}\n支持: >, <, up, down"
        except ValueError as e:
            return f"❌ 参数格式错误: {e}"

        rule_id = PriceMonitor.create_rule_id()
        rule = AlertRule(
            id=rule_id,
            inst_id=inst_id,
            alert_type=alert_type,
            threshold=threshold,
            interval_minutes=interval,
        )

        await self.storage.add_rule(rule)

        await self.okx_client.subscribe([inst_id])

        return f"✅ 已添加监控规则\nID: {rule_id}\n{rule.get_description()}"

    async def _cmd_del(self, args: list) -> str:
        if len(args) < 1:
            return "❌ 缺少规则ID\n用法: /pm del <规则ID>"

        rule_id = args[0]
        rule = await self.storage.get_rule(rule_id)

        if not rule:
            return f"❌ 规则不存在: {rule_id}"

        await self.storage.remove_rule(rule_id)

        rules = await self.storage.get_rules_by_inst(rule.inst_id)
        if not rules:
            await self.okx_client.unsubscribe([rule.inst_id])

        return f"✅ 已删除规则: {rule_id}"

    async def _cmd_list(self, args: list) -> str:
        if args:
            inst_id = args[0].upper()
            rules = await self.storage.get_rules_by_inst(inst_id)
        else:
            rules = await self.storage.get_all_rules()

        if not rules:
            return "📭 暂无监控规则"

        lines = ["📋 监控规则列表:\n"]
        for rule in rules:
            status = "✅" if rule.enabled else "⏸️"
            lines.append(f"{status} [{rule.id}] {rule.get_description()}")

        return "\n".join(lines)

    async def _cmd_price(self, args: list) -> str:
        if len(args) < 1:
            prices = self.okx_client.get_all_prices()
            if not prices:
                return "📭 暂无价格数据，请稍后再试"
            lines = ["📊 当前价格:"]
            for inst_id, price in sorted(prices.items()):
                lines.append(f"  {inst_id}: ${format_price(price)}")
            return "\n".join(lines)

        inst_id = args[0].upper()
        ticker = self.okx_client.get_ticker(inst_id)

        if not ticker:
            await self.okx_client.subscribe([inst_id])
            return f"⏳ 正在订阅 {inst_id}，请稍后再查询"

        return (
            f"📊 {inst_id}\n"
            f"当前价格: ${format_price(ticker.last)}\n"
            f"24h最高: ${format_price(ticker.high_24h)}\n"
            f"24h最低: ${format_price(ticker.low_24h)}\n"
            f"24h成交量: {ticker.vol_24h:,.0f}"
        )

    async def _cmd_clear(self, args: list) -> str:
        if args:
            inst_id = args[0].upper()
            rules = await self.storage.get_rules_by_inst(inst_id)
            for rule in rules:
                await self.storage.remove_rule(rule.id)
            await self.okx_client.unsubscribe([inst_id])
            return f"✅ 已清除 {inst_id} 的所有规则 ({len(rules)}条)"
        else:
            rules = await self.storage.get_all_rules()
            await self.storage.clear_all_rules()
            for rule in rules:
                await self.okx_client.unsubscribe([rule.inst_id])
            return f"✅ 已清除所有规则 ({len(rules)}条)"
