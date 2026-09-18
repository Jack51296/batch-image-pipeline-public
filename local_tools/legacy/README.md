# legacy/ — 最早的单机脚本

最早的单机脚本：从图片夹组 JSONL、直接调 GPT-Image 出图、生成 Labkit 送标表。

| 条目 | 说明 |
|---|---|
| `build_jsonl_from_images_v2.py` | 早期单机版：扫描文件夹图片，按 Excel prompt 表生成 gen_image_from_csv.py 可读的 JSONL |
| `gen_image_from_csv.py` | 早期单机版出图脚本：从 JSONL 读 prompt 调 GPT-Image generations/edits 批量生成 |
| `run_songbiao_img2img_stage1_0803_读取align_csv.py` | 早期送标脚本：读交付 CSV + 对齐明细，生成 Labkit 五列表 |
