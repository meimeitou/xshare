import pandas as pd

from xshare.data.sources.tushare_provider import TushareProvider


class FakePro:
    def daily(self, trade_date: str):
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "300001.SZ", "300002.SZ"],
                "pct_chg": [2.0, 4.0, -1.0, 1.0],
            }
        )

    def stock_basic(self, exchange: str, list_status: str, fields: str):
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "300001.SZ", "300002.SZ"],
                "name": ["平安银行", "万科A", "特锐德", "神州泰岳"],
                "industry": ["银行", "银行", "计算机", "计算机"],
            }
        )


def test_tushare_sector_rankings_by_industry_aggregation():
    provider = TushareProvider.__new__(TushareProvider)
    provider._pro = FakePro()
    provider._latest_trade_date = lambda: "20260419"

    top_up, top_down = provider.get_sector_rankings(top_n=2)

    assert len(top_up) == 2
    assert top_up[0].name == "银行"
    assert round(top_up[0].change_pct, 2) == 3.00
    assert top_up[0].leader == "万科A"

    assert len(top_down) == 2
    assert top_down[0].name in {"银行", "计算机"}
