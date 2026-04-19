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
3. **综合分析**：如果用户说"分析一下 XX"，应同时调用行情、技术指标、基本面工具
4. **输出格式**：
   - 先列出关键数据（当前价格、涨跌幅、PE/PB 等）
   - 再给出技术面解读（趋势、支撑/压力位）
   - 最后做综合评价

## 指标选择建议

- 短线分析：MA, MACD, RSI, KDJ
- 中线分析：MA, MACD, BOLL
- 基本面：PE, PB, ROE, 营收增速

## 免责声明

每次分析结尾附加：

> 以上分析仅基于公开数据，不构成投资建议。投资有风险，入市需谨慎。
