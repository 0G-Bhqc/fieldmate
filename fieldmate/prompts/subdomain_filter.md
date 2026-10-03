# 任务：子领域过滤（subdomain_filter）

你会收到一条检索式（`data.query`）与一篇通过学科/词法闸门的论文元数据
（`data.title`、`data.abstract`）。请判断：这篇论文是否**真的在做该检索式
所指的子领域工作**，而不是仅仅词面沾边。

## 背景（为什么要这道闸门）

词法闸门挡得住「完全无关」，挡不住「方法同名、目的不同」。实测：
`all:"phase field" AND all:denoising` 命中的论文里有扩散模型微结构生成、
CNN 光栅条纹滤波——它们都在"用某个场做去噪"，但与"相场方法做几何去噪"
不是同一条研究线。这道闸门判断的就是**应用对象与研究线是否一致**。

## 判定口径

- `relevant: true` = 论文的主要贡献就在该检索式所指的子领域（方法×对象都对得上）。
- `relevant: false` = 仅词面沾边、方法同名目的不同、或该子领域只是背景提及。
- 拿不准时 `relevant: false`（拒收可复核、误收难发现；被拒的都会记进 rejection log）。

## 输出（stdout，恰好一个 JSON 对象，不要多余文本）

{"ok": true, "result": {"relevant": true|false, "reason": "<≤120字，引用摘要中的关键依据>"}}
