# skills/ — 四个 skill

四个 skill：lighting-batch-jsonl（素材普查/转 PNG/组 JSONL/校验）、lighting-delivery-csv（交付表）、lighting-delivery-pipeline（两步交付说明）、lighting-destyle-align（几何对齐 + 按风格的放大插值/特征域规则）。

| 条目 | 说明 |
|---|---|
| `lighting-batch-jsonl/` | 任务清单 skill：素材普查、转 PNG、按 prompt 表组 Prompt_with_images JSONL、校验（defaults.json + scripts/） |
| `lighting-delivery-csv/` | 交付表 skill：从 pipeline CSV + 磁盘出图构建/修改/校验 7 列交付表，按 no_colormatch_tags 选 plain 或追色版 |
| `lighting-delivery-pipeline/` |  |
| `lighting-destyle-align/` | 几何对齐 skill：ORB + 相似变换把 AI 图对回原图空间；放大插值与特征匹配域按目标风格制定规则（像素最近邻、白描双三次+边缘匹配、新海诚双三次、其余双线性） |
