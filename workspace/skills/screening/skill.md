---
name: screening
slug: stock-screening
description: 股票条件筛选技能。将用户的自然语言筛选需求转为结构化条件，调用 stock_screen 工具按 PE/PB/ROE/行业等多维度筛选股票。
author: xshare
version: 0.1.0
---

# 条件筛选 Skill

## 何时使用

用户需要按条件筛选股票时激活：

- "帮我找 PE 低于 15 的银行股"
- "ROE 大于 20% 的消费股有哪些"
- "找一些低估值高分红的股票"

## 工作流程

1. **理解条件**：将用户的自然语言描述转为结构化筛选条件
2. **调用工具** → `stock_screen`
   - 支持字段：pe, pb, roe, revenue_yoy, profit_yoy
   - 支持行业/板块筛选
3. **结果呈现**：
   - 表格展示筛选结果（代码、名称、行业、关键指标）
   - 说明筛选条件和结果数量

## 条件映射示例

- "低估值" → PE < 15 AND PB < 2
- "高成长" → revenue_yoy > 20 AND profit_yoy > 20
- "高分红" → 需要配合基本面数据判断
