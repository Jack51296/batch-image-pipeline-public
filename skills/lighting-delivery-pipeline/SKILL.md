---
name: lighting-delivery-pipeline
description: >-
  光影图生图完整交付流水线：第 1 步用 lighting-delivery-csv 建 7 列交付表（每张原图
  三列 output r1/r2/r3），第 2 步 align_batch.py 像素对齐一次即为最终交付。不含换脸分割。
---

# 光影图生图交付流水线

回传 `Prompt_with_images.csv` 之后到送标，**两步、只对齐一次**。

| 步 | Skill / 脚本 | 产物 |
|---|---|---|
| 1 | [lighting-delivery-csv](../lighting-delivery-csv/SKILL.md) | `<批次>_光影_final.csv` |
| 2 | `destyle-align-copy/align_batch.py` | `<批次>_光影_final_align_out/align_all_results.csv` |

7 列表头：`id, prompt, input_image_path, output_image_path, output_image_path_1, output_image_path_2, 标签`

pipeline 长表 **一张原图 3 行**（`-r1` / `-r2` / `-r3`）。交付表 **一行 = 一张原图 + 三次生成**，三列全填：

| 列 | 含义 |
|---|---|
| `output_image_path` | r1 |
| `output_image_path_1` | r2 |
| `output_image_path_2` | r3 |

数据根：`\\10.0.0.12\share\frank\光影图生图`  
本地任务示例：`chatgpt/光照/{日期}/`

## 第 1 步 建交付 CSV

**完整流程、colormatch 策略、参数说明见 [lighting-delivery-csv](../lighting-delivery-csv/SKILL.md)。**

```bash
cd "<日期目录>"
python ../skills/lighting-delivery-csv/scripts/build_delivery_csv.py \
  --batch-dir . --list-tags

python ../skills/lighting-delivery-csv/scripts/build_delivery_csv.py \
  --batch-dir . --no-colormatch-tags default \
  --out "./<批次>_光影_final.csv"

python ../skills/lighting-delivery-csv/scripts/verify_delivery_csv.py \
  --csv "./<批次>_光影_final.csv" --batch-dir .
```

## 第 2 步 像素对齐（最终交付）

```bash
pip install -r destyle-align-copy/requirements-batch.txt
python destyle-align-copy/align_batch.py \
  --csv "<批次>_光影_final.csv" --workers 4
```

`align_batch.py` 把一行展开为 **3 对**（input × r1/r2/r3），对齐后写回 7 列宽表，三列 `output_*` 更新为 `processed/` 下路径。

像素风批次（`标签` 或 `prompt` 命中 像素/pixel/像素画 等关键词）放大 AI 图自动改用最近邻，其它风格不受影响；
规则与开关见 [lighting-destyle-align](../lighting-destyle-align/SKILL.md)「像素风规则」。

输出目录 `{csv名}_align_out/`：

| 产物 | 说明 |
|---|---|
| `processed/` | 对齐后的 input / output 图 |
| **`align_all_results.csv`** | **最终交付表** |
| `aligned_candidates.csv` | 去掉 unaligned，送标可选 |
| `review/index.html` | 人工审核（可选 finalize） |

## 上游出图

`lighting-batch-jsonl` → `gen_image_lighting.py`（每张原图固定 3 run）→ `Prompt_with_images.csv` → 本流水线。

## skills 目录分工

```
skills/
├── lighting-batch-jsonl/       组 JSONL、出图
├── lighting-delivery-csv/      第 1 步：建交付 CSV
└── lighting-delivery-pipeline/ 本文件：两步全流程
```
