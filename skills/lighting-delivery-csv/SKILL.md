---
name: lighting-delivery-csv
description: 为「光影图生图」批次生成或修改交付 CSV（id/prompt/input_image_path/output_image_path/output_image_path_1/output_image_path_2/标签）。会自动修正原图扩展名、按标签选择 colormatch 或原始输出、统计成功率与覆盖率并逐条校验路径。当用户要求生成、修改、校验光影/光效批次的交付 csv，或提到 Prompt_with_images.csv、colormatch、光影数据交付模板时使用。
disable-model-invocation: true
---

# 光影交付 CSV

数据根目录：`\\10.0.0.12\share\frank\光影图生图`，每个日期一个批次目录（`0820`、`0822`……）。

## 批次目录结构

```
<日期>/
├── <批次名>/                          原图，如 260814-光影光效-6840-qt-1707-s35-5133
│   └── <轮次-N张>/                    输入根目录，如 4-1707张；一个批次可能有多个
│       └── <光效-N张>/                标签目录，如 丁达尔光-32张；偶尔再嵌一层场景目录
├── <批次名>-qt-round1/                输出，每次生成同时产出 xxx-rN.png 和 xxx-rN_colormatched.png
└── *Prompt_with_images*.csv           pipeline 产物，一张原图 3 行（r1/r2/r3）
```

pipeline 产物有两种，脚本都支持，优先用 CSV：

- **CSV**：`id,prompt,input_image_path,ai_output_image_path,output_image_path,image_status,model_id,color_ref_path`。`image_status` 非 `success` 的行没有产出文件，能区分失败原因。
- **JSONL**：`id,image_path,prompt,light_type,run`，只是任务清单，没有状态和输出路径，产出只能按 `<id>.png` / `<id>_colormatched.png` 到磁盘上反查，报告里记为 `not_generated`。好处是自带 `light_type`，标签不用靠目录名推断。

一个批次里常同时存在多个 `Prompt_with_images*` 文件（`_wide`、`5.jsonl` 等），自动识别不出唯一文件时脚本会列出候选并退出，用 `--pipeline-file` 指定。

交付 CSV 一行 = 一张原图，最多 3 个输出，表头必须与 `光影数据交付模板.csv` 完全一致。

## 生成流程

**第一步：先摸清标签和覆盖率，不要直接写 csv。**

```bash
python scripts/build_delivery_csv.py --batch-dir "<日期目录>" --list-tags
```

报告会给出标签清单、每图输出数分布、扩展名修正数量、未被 pipeline 覆盖的原图。

**第二步：确认 colormatch 策略。**

`defaults.json` 的 `no_colormatch_tags` 是默认不做 colormatch 的标签。用 AskQuestion 让用户确认，把第一步报告里出现的**新标签**单独列出来问，不要让用户在没见过的标签上盲选。

**第三步：生成。**

```bash
python scripts/build_delivery_csv.py --batch-dir "<日期目录>" --no-colormatch-tags default
```

**第四步：校验，必须做。**

```bash
python scripts/verify_delivery_csv.py --csv "<输出csv>" --batch-dir "<日期目录>"
```

退出码非 0 就是有缺失文件或重复 id，修掉再交付。

## 修改已有 CSV

```bash
# 把某些标签改成不用 colormatch（原地改，自动留 .bak）
python scripts/edit_delivery_csv.py --csv "<csv>" --batch-dir "<日期目录>" --plain-tags "霓虹灯光,舞台灯光"

# 改回 colormatch
python scripts/edit_delivery_csv.py --csv "<csv>" --batch-dir "<日期目录>" --colormatch-tags "太阳光"

# 删标签 / 只留某些标签 / 要求至少 3 个输出
python scripts/edit_delivery_csv.py --csv "<csv>" --batch-dir "<日期目录>" --drop-tags "音乐" --min-outputs 3
```

改完同样跑一遍 `verify_delivery_csv.py`。

## colormatch 怎么选

任务目标是**去除**特殊光效、恢复自然光照，而 colormatch 把输出色彩对齐回原图。对本身带强色偏的光效（霓虹、舞台光、蓝调时刻、烛光……），colormatch 会把要去掉的色调又拉回来，这些标签应该用原始 `-rN.png`。对色偏不重、只是明暗结构变化的光效（硬光、软光、顶光、剪影……），colormatch 能压住模型的整体偏色，应该保留。

当前默认值见 `defaults.json`。遇到新标签时按上面这条原则判断，然后问用户。

## 常用参数

| 参数 | 说明 |
|---|---|
| `--no-colormatch-tags` | `default`（读 defaults.json）/ `none` / `all` / 逗号分隔标签 |
| `--min-outputs` | 输出数少于此值的原图丢弃，默认 1；`0` 表示全保留、输出列留空 |
| `--path-style` | `relative`（默认，相对批次目录）/ `absolute`（pipeline 的 /ytech_milm 路径）/ `unc` |
| `--source-dir` `--pipeline-file` | 一个批次目录里有多个候选时手动指定 |

## 硬性规则

- 输出列按 r1→r2→r3 顺序**左对齐**填充，`output_image_path` 不留空。
- `input_image_path` 必须按磁盘实际文件修正扩展名。pipeline CSV 里统一写 `.png`，实际大量是 `.jpg/.heic/.webp/.arw/.avif`，0820 那批就是因此返工过。
- `标签` 优先取 JSONL 的 `light_type`；没有就用顶层光效目录名去掉 `-N张`，嵌套场景目录（如 `舞台灯光-1张/音乐-98张`）归到顶层的 `舞台灯光`。
- `id` = 原图文件名去扩展名。
- 写 UTF-8 BOM + CRLF，和模板保持一致，否则 Excel 打开乱码。
- 报告一律看脚本写出的 `*_report.txt` / `*_verify.txt`，别看 PowerShell 控制台输出，中文会乱码。

## 交付前要主动汇报的三件事

1. **被丢弃的原图数量和原因**：pipeline 的 `image_status` 里区分安全审核拒绝、原图超 50MB、找不到原图、网络错误。
2. **未被 pipeline 覆盖的原图**：整个标签目录一条都没跑的情况出现过，报告里的「未被 pipeline 覆盖」一节会列出来，这不是失败而是漏跑。
3. **heic/arw/avif 格式的原图数量**：下游不一定能直接读，需要确认是否要先转 png。

更多字段语义和历史坑见 [reference.md](reference.md)。

## 目录索引


| 条目 | 说明 |
|---|---|
| `SKILL.md` | lighting-delivery-csv skill 说明：为光影/风格批次生成或修改交付 CSV，自动修正扩展名、按标签选追色版本 |
| `defaults.json` | 交付表默认规则：no_colormatch_tags（45 种强色偏/去光类光效用 plain）、min_outputs 等 |
| `reference.md` | 交付 CSV 字段语义、追色选择规则与边界情况的参考手册 |
| `scripts/` | 交付表三件套：build（构建）、edit（就地修改）、verify（校验），common 为共用工具 |
