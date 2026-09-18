# scripts/ — 四个脚本

四个脚本：survey_batch 普查 → convert_to_png 转码 → build_jsonl 组清单 → verify_jsonl 校验，common 为共用工具。

| 条目 | 说明 |
|---|---|
| `build_jsonl.py` | 按素材目录与 prompt 表组 Prompt_with_images JSONL（7 字段，一行一图一轮） |
| `common.py` | 四个脚本共用的工具函数：读 Excel prompt 表、遍历素材、路径与报告输出 |
| `convert_to_png.py` | 把素材树里所有图片转成 PNG，输出到平行目录（TIFF 等接口不收的格式必做） |
| `survey_batch.py` | 素材普查：转码前统计一批原图的数量、格式、尺寸与异常 |
| `verify_jsonl.py` | 交给流水线前校验 JSONL：字段、路径存在、id 唯一、轮次数 |
