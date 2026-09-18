# runbooks/ — 实跑用过的脚本，照抄改值即可复用

实跑用过的脚本，照抄改值即可复用：建批与后处理（batch_style0914）、quality A/B（ab_quality_high_vs_low）、像素规则补丁与回归（pixel_rule_regress）。

| 条目 | 说明 |
|---|---|
| `ab_quality_high_vs_low/` | quality low vs high 单图 A/B：建 1 张图测试批、客观指标、原图|low|high 拼图与细节放大 |
| `batch_style0914/` | style0914 正式批全流程脚本：建批（含 gen quality 补丁）→ 等出图 → 交付表 → 对齐 → md5 清单，以及审核拦截行重试 |
| `pixel_rule_regress/` | 像素风放大规则的补丁脚本、本地单测与开发机回归（新版默认行为必须与旧版逐字节一致） |
