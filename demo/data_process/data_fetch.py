# 加载所需使用的模块
import os
import tushare as ts
from datetime import datetime

from vnpy.trader.datafeed import get_datafeed
from vnpy.trader.database import get_database, DB_TZ
from vnpy.trader.constant import Interval
from vnpy.trader.object import BarData, HistoryRequest
from vnpy.trader.utility import extract_vt_symbol
from vnpy.trader.setting import SETTINGS


#tushare配置
# pro = ts.pro_api('SnKInQIAVUenKgNzmniKAAVpSovBIKophnYOLTaADlgJZZLyVNJQRvEsmrsTHZUP')
# pro._DataApi__http_url = "http://118.89.66.41:8010/"
# df = ts.pro_bar(api=pro, ts_code="000001.SZ", adj="qfq", start_date="20200101", end_date="20200131", freq="D")
# print(df)


# 配置数据服务
SETTINGS["datafeed.name"] = "tushare"            # 可以根据自己的需求选择数据服务：rqdata/xt/wind等
# SETTINGS["datafeed.username"] = "license"       # RQData的用户名统一为“license”这个字符串
# SETTINGS["datafeed.password"] = "123456"        # 这里需要替换为你购买或者申请试用的RQData数据license

# 配置数据库
SETTINGS["database.name"] = "sqlite"              # 可以根据自己的需求选择数据库，这里使用的是TDengine
SETTINGS["database.database"] = "my_data.db"
# SETTINGS["database.host"] = "127.0.0.1"
SETTINGS["database.port"] = 0
# SETTINGS["database.user"] = "root"
# SETTINGS["database.password"] = "taosdata"

# 创建对象实例
datafeed = get_datafeed()

database = get_database()

# 要下载数据的合约代码
vt_symbols = [
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
    "511010.SSE",
    "511880.SSE",
]

# 要下载数据的起止时间
# start = datetime(2025, 1, 1, tzinfo=DB_TZ)
# end = datetime(2025, 3, 30, tzinfo=DB_TZ)
start = ""
end = ""

# 遍历列表执行下载
for vt_symbol in vt_symbols:
    # 拆分合约代码和交易所
    symbol, exchange = extract_vt_symbol(vt_symbol)

    # 创建历史数据请求对象
    req: HistoryRequest = HistoryRequest(
        symbol=symbol,
        exchange=exchange,
        start=start,
        end=end,
        interval=Interval.DAILY        # 日线
    )

    # 从数据服务下载数据
    bars: list[BarData] = datafeed.query_bar_history(req)

    # 如果下载成功则保存
    if bars:
        database.save_bar_data(bars)
        print(f"下载数据成功：{vt_symbol}，总数据量：{len(bars)}")
    # 否则失败则打印信息
    else:
        print(f"下载数据失败：{vt_symbol}")
