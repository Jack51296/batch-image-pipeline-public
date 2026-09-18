# scripts/ — 交付表三件套

交付表三件套：build（构建）、edit（就地修改）、verify（校验），common 为共用工具。

| 条目 | 说明 |
|---|---|
| `build_delivery_csv.py` | 从 pipeline CSV + 磁盘出图构建 7 列交付表，按标签选 plain/追色版，输出 report |
| `common.py` | 交付表脚本共用：读 pipeline CSV、定位磁盘原图/出图、扩展名修正 |
| `edit_delivery_csv.py` | 就地修改已有交付表：切换追色版本、按标签剔除、按输出数过滤 |
| `verify_delivery_csv.py` | 校验交付表：表头、磁盘路径、id 唯一、标签、追色混用情况 |
