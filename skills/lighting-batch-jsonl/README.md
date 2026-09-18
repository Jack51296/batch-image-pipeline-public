# lighting-batch-jsonl/ — 任务清单 skill

任务清单 skill：素材普查、转 PNG、按 prompt 表组 Prompt_with_images JSONL、校验（defaults.json + scripts/）。

| 条目 | 说明 |
|---|---|
| `defaults.json` | 组 JSONL 的默认配置：prompt 表路径、tag_aliases（文件夹类目名 → Excel 光效列）、generic 兜底 |
| `scripts/` | 四个脚本：survey_batch 普查 → convert_to_png 转码 → build_jsonl 组清单 → verify_jsonl 校验，common 为共用工具 |
