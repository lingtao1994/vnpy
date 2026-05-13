from math import floor

from vnpy.trader.constant import Direction, Interval
from vnpy.trader.object import BarData, TickData
from vnpy.trader.utility import ArrayManager

from vnpy_portfoliostrategy import StrategyEngine, StrategyTemplate


class EtfMomentumRotationV2Strategy(StrategyTemplate):
    """ETF动量轮动策略V2：盈利再投入，双标的50:50配置"""

    author = "Steve"

    EQUITY_SYMBOLS = [
        "510300.SSE",
        "510500.SSE",
        "512100.SSE",
        "159915.SZSE",
        "588000.SSE",
        "512880.SSE",
        "510150.SSE",
        "512010.SSE",
        "512480.SSE",
        "515700.SSE",
        "512660.SSE",
        "512800.SSE",
        "510880.SSE",
    ]
    BOND_SYMBOL = "511010.SSE"
    MONEY_SYMBOL = "511880.SSE"
    SLOT_COUNT = 2

    initial_capital = 1_000_000
    rebalance_days = 10
    short_window = 20
    middle_window = 60
    long_window = 120
    short_weight = 0.3
    middle_weight = 0.4
    long_weight = 0.3
    lot_size = 100
    price_add = 0.01

    rebalance_count = 0
    portfolio_value = 0.0
    selected_symbols: list[str] = []
    defensive_symbols: list[str] = []
    momentum_scores: dict[str, float] = {}
    target_values: dict[str, float] = {}
    last_prices: dict[str, float] = {}

    parameters = [
        "initial_capital",
        "rebalance_days",
        "short_window",
        "middle_window",
        "long_window",
        "short_weight",
        "middle_weight",
        "long_weight",
        "lot_size",
        "price_add",
    ]
    variables = [
        "rebalance_count",
        "portfolio_value",
        "selected_symbols",
        "defensive_symbols",
        "momentum_scores",
        "target_values",
        "last_prices",
    ]

    def __init__(
        self,
        strategy_engine: StrategyEngine,
        strategy_name: str,
        vt_symbols: list[str],
        setting: dict
    ) -> None:
        """构造函数"""
        super().__init__(strategy_engine, strategy_name, vt_symbols, setting)

        size: int = max(self.short_window, self.middle_window, self.long_window) + 1
        self.ams: dict[str, ArrayManager] = {
            vt_symbol: ArrayManager(size) for vt_symbol in self.vt_symbols
        }

        self.equity_symbols: list[str] = [
            vt_symbol for vt_symbol in self.EQUITY_SYMBOLS if vt_symbol in self.vt_symbols
        ]

        self.portfolio_value = float(self.initial_capital)
        self.selected_symbols = []
        self.defensive_symbols = []
        self.momentum_scores = {}
        self.target_values = {}
        self.last_prices = {}

    def on_init(self) -> None:
        """策略初始化回调"""
        self.write_log("策略初始化")

        init_days: int = max(self.long_window * 3, 250)
        self.load_bars(init_days, Interval.DAILY)

    def on_start(self) -> None:
        """策略启动回调"""
        self.write_log("策略启动")

    def on_stop(self) -> None:
        """策略停止回调"""
        self.write_log("策略停止")

    def on_tick(self, tick: TickData) -> None:
        """行情推送回调"""
        return

    def on_bars(self, bars: dict[str, BarData]) -> None:
        """K线切片回调"""
        self.update_last_prices(bars)

        for vt_symbol, bar in bars.items():
            am: ArrayManager | None = self.ams.get(vt_symbol)
            if am is None:
                continue

            am.update_bar(bar)

        self.rebalance_count += 1
        if self.rebalance_count >= max(int(self.rebalance_days), 1):
            self.rebalance_count = 0
            self.update_targets()

        self.rebalance_portfolio(bars)
        self.put_event()

    def update_targets(self) -> None:
        """计算动量排名并按组合权益更新50:50目标持仓"""
        for vt_symbol in self.vt_symbols:
            self.set_target(vt_symbol, 0)

        self.portfolio_value = self.get_portfolio_value()
        slot_value: float = self.portfolio_value / self.SLOT_COUNT

        self.momentum_scores = self.calculate_equity_scores()
        self.selected_symbols = [
            vt_symbol
            for vt_symbol, score in sorted(
                self.momentum_scores.items(),
                key=lambda item: item[1],
                reverse=True,
            )
            if score > 0
        ][:self.SLOT_COUNT]

        target_symbols: list[str] = self.select_target_symbols(self.selected_symbols)
        self.defensive_symbols = [
            vt_symbol for vt_symbol in target_symbols if vt_symbol not in self.selected_symbols
        ]
        self.target_values = {}

        for vt_symbol in target_symbols:
            target_volume: int = self.calculate_target_volume(vt_symbol, slot_value)
            self.set_target(vt_symbol, target_volume)
            self.target_values[vt_symbol] = slot_value

    def update_last_prices(self, bars: dict[str, BarData]) -> None:
        """更新最新价格缓存"""
        for vt_symbol, bar in bars.items():
            if bar.close_price > 0:
                self.last_prices[vt_symbol] = bar.close_price

    def select_target_symbols(self, selected_symbols: list[str]) -> list[str]:
        """选择最终持仓标的，不足两个时用防守ETF补齐"""
        target_symbols: list[str] = []

        for vt_symbol in selected_symbols:
            if vt_symbol in self.vt_symbols and self.last_prices.get(vt_symbol, 0) > 0:
                target_symbols.append(vt_symbol)

        for vt_symbol in self.get_defensive_candidates():
            if len(target_symbols) >= self.SLOT_COUNT:
                break

            if vt_symbol not in self.vt_symbols:
                continue
            if vt_symbol in target_symbols:
                continue
            if self.last_prices.get(vt_symbol, 0) <= 0:
                continue

            target_symbols.append(vt_symbol)

        return target_symbols[:self.SLOT_COUNT]

    def get_defensive_candidates(self) -> list[str]:
        """获取防守ETF候选顺序"""
        bond_momentum: float | None = self.calculate_momentum(self.BOND_SYMBOL, self.middle_window)
        if bond_momentum is not None and bond_momentum > 0:
            return [self.BOND_SYMBOL, self.MONEY_SYMBOL]

        return [self.MONEY_SYMBOL, self.BOND_SYMBOL]

    def calculate_equity_scores(self) -> dict[str, float]:
        """计算权益ETF综合动量分数"""
        scores: dict[str, float] = {}

        for vt_symbol in self.equity_symbols:
            if self.last_prices.get(vt_symbol, 0) <= 0:
                continue

            score: float | None = self.calculate_score(vt_symbol)
            if score is None:
                continue

            scores[vt_symbol] = score

        return scores

    def calculate_score(self, vt_symbol: str) -> float | None:
        """计算单个ETF的综合动量分数"""
        short_momentum: float | None = self.calculate_momentum(vt_symbol, self.short_window)
        middle_momentum: float | None = self.calculate_momentum(vt_symbol, self.middle_window)
        long_momentum: float | None = self.calculate_momentum(vt_symbol, self.long_window)

        if short_momentum is None or middle_momentum is None or long_momentum is None:
            return None

        return (
            self.short_weight * short_momentum
            + self.middle_weight * middle_momentum
            + self.long_weight * long_momentum
        )

    def calculate_momentum(self, vt_symbol: str, window: int) -> float | None:
        """计算指定窗口动量"""
        am: ArrayManager | None = self.ams.get(vt_symbol)
        if not am or am.count < window + 1:
            return None

        close_array = am.close
        current_price: float = close_array[-1]
        previous_price: float = close_array[-window - 1]

        if current_price <= 0 or previous_price <= 0:
            return None

        return current_price / previous_price - 1

    def calculate_target_volume(
        self,
        vt_symbol: str,
        target_value: float,
    ) -> int:
        """根据目标市值和最新价换算目标持仓"""
        price: float = self.last_prices.get(vt_symbol, 0)
        if price <= 0:
            return 0

        lot_size: int = max(int(self.lot_size), 1)
        return floor(target_value / price / lot_size) * lot_size

    def calculate_price(
        self,
        vt_symbol: str,
        direction: Direction,
        reference: float
    ) -> float:
        """计算调仓委托价格"""
        if direction == Direction.LONG:
            return reference + self.price_add

        return max(reference - self.price_add, 0)
