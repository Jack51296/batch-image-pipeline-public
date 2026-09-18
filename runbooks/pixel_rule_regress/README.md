# pixel_rule_regress/ — 像素风放大规则的补丁脚本、本地单测与开发机回归（新版默认行为必须与旧版逐字节一致）

像素风放大规则的补丁脚本、本地单测与开发机回归（新版默认行为必须与旧版逐字节一致）。

| 条目 | 说明 |
|---|---|
| `patch_align_pixel_rule.py` | 给 lighting-destyle-align 加「像素风放大用最近邻」规则的补丁脚本（锚点替换） |
| `regress_align.sh` | 开发机回归：新版 --pixel-mode off 必须与旧版逐字节一致，auto 模式对像素批次切最近邻 |
| `test_align_pixel.py` | 像素风规则本地单测：关键词检测、插值选择、与旧版逐字节一致 |
