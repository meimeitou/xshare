---
name: stock
slug: stock-analysis
description: 个股综合分析技能。处理股票行情查询、技术面分析、基本面分析、个股新闻检索等请求，调用 stock_resolve / stock_quote / stock_indicators / stock_fundamentals / stock_news 等工具完成分析。
author: xshare
version: 0.1.0
---

# 股票分析 Skill

## 何时使用

用户询问个股相关问题时激活，包括但不限于：

- 查询某只股票的行情、价格
- 分析某只股票的技术面/基本面
- 查看个股相关新闻

## 工作流程

1. **实体解析**：用户提到股票名称/代码时，先调用 `stock_resolve` 确认标的
2. **数据获取**：根据用户意图调用对应工具
   - 行情查询 → `stock_quote`
   - 技术分析 → `stock_indicators`（选择合适的指标组合）
   - 基本面 → `stock_fundamentals`
   - 新闻 → `stock_news`
   - 约束：个股分析流程中禁止调用 `web_search` / `web_fetch`；新闻统一使用 `stock_news`，不得用 web_fetch 补充
3. **综合分析**：如果用户说"分析一下 XX"，应同时调用行情、技术指标、基本面工具
   - 时间口径：无论是否交易时段，都以工具返回的"最近有数据的交易日"为准，不得因为当前时间是凌晨/周末就判定数据不可用
   - 失败处理：若任一 MCP 工具失败，最多重试 1 次；仍失败则基于其余已成功工具输出，并明确缺失项
4. **输出格式**：
   - 先列出关键数据（当前价格、涨跌幅、PE/PB/PEG 等）
   - 量价配合分析（成交量均线、量比、OBV 趋势）
   - 技术面解读（趋势方向、支撑/压力位、超买超卖）
   - 基本面解读（估值水平及历史分位、盈利趋势、净利率变化）
   - 最后做综合评价

## 指标选择建议

- 短线分析：MA, MACD, RSI, KDJ, VOL_MA, NINE_TURN
- 中线分析：MA, MACD, BOLL, DMI, OBV, TREND
- 量价分析：VOL_MA, OBV, VWAP
- 趋势阶段：TREND（均线排列判断多头/空头/震荡）+ NINE_TURN（反转信号）
- 趋势强度：DMI（ADX > 25 表示趋势明确）
- 基本面：PE, PB, ROE, PEG, 净利率, 营收/利润趋势, PE 历史分位
- 综合分析（用户说"分析一下"）：MA, MACD, RSI, BOLL, VOL_MA, OBV + 基本面

## 免责声明

每次分析结尾附加：

> 以上分析仅基于公开数据，不构成投资建议。投资有风险，入市需谨慎。

## 防循环约束

- 用户只说“XX 分析”时，不要把股票代码拼接成 URL，也不要调用 `web_fetch`
- 若出现异常参数（如 `002594{`、`https://...{`），应先清洗参数并回到 `stock_resolve`，禁止继续请求该异常参数
