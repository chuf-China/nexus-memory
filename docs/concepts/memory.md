# 记忆系统

Nexus 采用四层记忆架构，模拟人类记忆的形成和衰退过程。

## 四层架构

```
instant (即时层)
  ↓ 被多次访问
candidate (候选层)
  ↓ 信念引擎确认
consolidated (巩固层)
  ↓ 超过保留阈值
archived (归档层)
```

| 层级 | 置信度 | 保留策略 |
|------|--------|----------|
| instant | 0.25-0.45 | 7 天无访问则降级 |
| candidate | 0.45-0.65 | 30 天无访问则降级 |
| consolidated | 0.65-0.85 | 长期保留 |
| archived | <0.25 | 可清理 |

## 信念引擎

每条知识关联一个信念记录，包含：

- **置信度** (confidence)：0.0-1.0，基于证据强度
- **6 域评分**：identity, workflow, rule, preference, fact, observation
- **时间衰减**：access_score = 1 / (1 + hours_since_access / 24)
- **反馈学习**：正反馈 +0.05，负反馈 -0.10

## 记忆演化

- **晋升**：置信度超过阈值时自动晋升到更高层
- **降级**：长期未访问且置信度低时降级
- **蒸馏**：DreamDistiller 四阶段（Orient → Gather → Consolidate → Prune）
- **老化**：freshness_score 基于最后访问时间计算

## 检索优先级

retrieval_priority 综合四个维度：

```
priority = 0.40 × quality_score    (信念质量)
         + 0.25 × freshness_score  (时间新鲜度)
         + 0.15 × access_score     (访问频率)
         + 0.10 × confidence       (置信度)
         + 0.10 × type_score       (类型权重)
```
