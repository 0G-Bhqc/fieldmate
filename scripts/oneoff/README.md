# scripts/oneoff —— 一次性脚本归档

这些 `_*.py` 是开发期的一次性数据修复/诊断脚本，不是正式入口，不被测试覆盖。

⚠ 它们写于 v0.4.0 **数据进包之前**，里面的 `libraries/`、`contracts/` 相对路径
对应的是旧仓库布局；重跑前需把路径改为 `fieldmate/libraries/`、`fieldmate/contracts/`。
