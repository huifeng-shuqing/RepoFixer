# FAILURES 案例库（公开失败全过程）

> 三组评测（完整流水线 / 无 repo map / 无上下文压缩，各 29 题）共 24 个失败。
> 每一题的完整过程（每步调了什么工具、输出什么、怎么失败的）都在 `failures/*.jsonl`，
> 用 `uv run python scripts/show_trace.py failures/full_<任务名>.jsonl` 即可逐步回放。

## 一、21/21/21 是不是巧合？——是巧合，但需要解释清楚

三组都解决了 21 题，但**解决的题目并不完全相同**：

| 类别 | 数量 | 说明 |
|---|---|---|
| 三组都解决 | 17 | 简单题，谁跑都能过 |
| 三组都失败 | 5 | 难题（或环境不适），谁跑都不过 |
| 边缘题（各组结果不同） | 7 | **这几题在"过与不过"之间摇摆，每次跑结果随机翻转** |

7 道边缘题的翻转情况（例）：
- humanize-e46732ed：只有 nomap 组解决
- humanize-ca892b36：只有 nocompact 组解决
- inflection-e32443bb / tabulate-5373ebfa：full 解决、nomap 没解决
- tabulate-8014ec66 / boltons-73e22c90 / tabulate-3aa568c6：各组结果不同

**结论**：
1. **不是逻辑 bug**。如果是 bug（比如判分恒真），三组的解决集合会完全一样、或者全 29 题都通过。现在的情况是"每组赢的题、输的题各不相同"，这正是**模型随机性**（温度 0.2，同模型同代码跑三次，边缘题会翻转）的表现。
2. 21/21/21 总数相同纯属巧合：7 道边缘题在各组之间翻转，恰好每组翻出同样多的赢面。
3. 这恰恰说明：**29 题规模下，差 1-2 题的分数差没有意义**；这也是调研报告建议"报告方差/置信区间"的原因。我们不做重采样（预算有限），所以在报告里如实声明"单次运行、±8.5 个百分点波动范围"即可。

## 二、三组都失败的 5 题（可回放）

| 任务 | 是什么 bug | 三组失败方式 | 回放命令 |
|---|---|---|---|
| humanize-08cf2c30 | intword 舍入相关 | full 9 步 no_progress / nomap 11 步 / nocompact 16 步 | show_trace.py failures/full_humanize-08cf2c30.jsonl |
| pathspec-e51866f2 | gitignore 括号目录匹配 | 40/40/32 步（步数耗尽与探测循环） | show_trace.py failures/nomap_pathspec-e51866f2.jsonl |
| pathspec-f70a1d2f | gitignore 取反与祖先排除 | 34/40/24 步 | show_trace.py failures/nocompact_pathspec-f70a1d2f.jsonl |
| boltons-1cdc0e9d | 直方图零分位区间 | 8/9/13 步（快速放弃） | show_trace.py failures/full_boltons-1cdc0e9d.jsonl |
| boltons-4e5faa3d | spooled stream 写兼容 | 35/22/25 步 | show_trace.py failures/nocompact_boltons-4e5faa3d.jsonl |

> pathspec 的两题是三组一致失败率最高的，值得面试时主动讲：agent 在"改递归匹配算法"这类需要严谨推理的题上最容易打转——这正是当前模型能力的天花板所在，也指明了下一步（加 verifier 复核、或换强模型跑这 5 题验证假设）。

## 三、失败模式统计（三组合计）

| 失败模式 | 数量 | 含义（大白话） |
|---|---|---|
| 死循环 | 9 | 模型反复做完全相同的动作，被熔断 |
| 探测循环 | 7 | 模型不停试小命令找路，6 步没进展被止损 |
| 步数耗尽 | 6 | 40 步用完还没修好 |
| 提前收手 | 1 | 没修完就谎报"做完了" |
| 策略拦截 | 1 | 想做危险操作被安全策略拦下 |
