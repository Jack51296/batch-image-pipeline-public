---
name: lighting-destyle-align
description: >-
  光影 / 风格化 / 美妆共用的几何对齐（destyle）：ORB 特征点 + 相似变换（缩放/旋转/平移）
  把 AI 出图对齐到原图空间位置（必要时 crop），结果分 ok / crop / unaligned；
  放大插值与特征匹配域按目标风格制定规则（像素→最近邻，白描→双三次 + 边缘匹配，
  新海诚等细线平涂→双三次，其余双线性），可按标签自动命中或命令行覆盖。
  在出图与 colormatch 之后、整理最终交付表之前使用。
---

# 几何对齐（destyle）：一套配准，按风格定规则

与美妆交付同一套对齐逻辑，核心在 `align_core.py`。配准算法对所有风格一样；
**按风格变的是放大插值和特征匹配域**（见下文「按风格的规则」），追色开关则在出图批次 config 里定。

## 做什么

| 输入 | 处理 | 输出 |
|---|---|---|
| 含 `input_image_path` + `output_image_path`（可有 `_1`/`_2`）的 CSV | ORB + 相似变换，必要时 crop | `{csv名}_align_out/` |

结果类别：

- **ok**：对齐成功  
- **crop**：对齐后需裁切  
- **unaligned**：无法可靠配准  

## 推荐顺序（光影）

1. `gen_image_lighting.py` 出图 + colormatch  
2. **本 skill**：对齐（先）  
3. `lighting-delivery-csv` / `make_lighting_delivery_csv.py`：整理交付表（再）

## 用法

```bash
# 在项目根目录（skills 的上一级）
pip3 install -r skills/lighting-destyle-align/requirements-batch.txt

python3 -u skills/lighting-destyle-align/align_batch.py \
  --csv /path/to/Prompt_with_images_0818.csv --workers 4
```

相对路径以 **CSV 所在目录** 为根。光影建议用第 1 步写在 `work_dir` 里的 pipeline CSV。

## 按风格的规则

AI 出图通常比原图小（如 1672×941 → 5760×3240），导出时要放大 2～4 倍；不同风格对"怎么放大、在哪个域配准"要求不同：

| 目标风格 | 放大插值 | 特征匹配域 | 触发方式 |
|---|---|---|---|
| 像素 | 最近邻 `INTER_NEAREST`（保住色块硬边） | 灰度 | `pixel_style` 规则：标签/prompt 命中即生效，`--pixel-mode auto|on|off` |
| 白描 / 线稿 | 双三次 `INTER_CUBIC`（线条锐利、无锯齿） | 灰度与 Canny 边缘图两路各估一次，取内点多者 | `lineart_style` 规则，`--lineart-mode auto|on|off` |
| 新海诚等细线 + 平涂 | 双三次（源素材若是像素，标签会误触发最近邻，需 `--pixel-mode off`） | 灰度 | `--upscale-interp cubic` 全局覆盖 |
| 光影、莫奈、水墨及其它 | 双线性 `INTER_LINEAR`（与旧版逐字节一致） | 灰度 | 默认 |

`--upscale-interp nearest|linear|cubic|lanczos` 对全批强制指定，优先级高于两条规则。三者都只在**放大**时切换，
缩小仍双线性，ok/crop/unaligned 判定不变。每对实际用了什么看 `align_pair_details.csv` 的
`upscale_interp` / `pixel_style_hit` / `lineart_style_hit` / `feature_used` 列。

### 像素风规则（放大用最近邻）

默认双线性会把像素画的色块边缘抹平，所以 **只对「像素」类标签/提示词命中的配对，放大改用最近邻 `INTER_NEAREST`**；
其它风格不命中，走原来的双线性，导出结果与旧版逐字节一致。

判定（每一对单独判，`defaults.json` 里可改）：

| 依据 | 字段 | 关键词（子串、不分大小写） |
|---|---|---|
| 标签 | `标签` / `tag` / `light_type` / `prompt_target` / `style` … | `像素` `pixel` `8bit` `8-bit` `16bit` `16-bit` `点阵` |
| 提示词 | `prompt` | 仅强短语：`像素画` `像素风` `像素艺术` `pixel art` `8-bit` `16-bit` `点阵`（"不改变像素尺寸"不命中） |

- 只在 **放大** 时切换；AI 图比原图大需要缩小时仍用双线性。
- 对齐类别（ok / crop / unaligned）判定逻辑不变，只影响导出图的重采样。
- 命令行覆盖：`--pixel-mode auto|on|off`（默认 auto）、`--pixel-tags 像素,pixel`。
- 结果可查：`align_pair_details.csv` 新增 `upscale_interp`（nearest / linear）与 `pixel_style_hit`
  （如 `prompt~像素画`）；`source_meta.json` 记录本批用的规则与命中数；控制台打印命中对数。
- 注意 output 仍是 JPEG，像素块边缘会有轻微 JPEG 振铃，属于体积预算的既有取舍。

### 白描/线稿规则（双三次 + 边缘匹配）

线稿与彩色原图在灰度上差异很大，ORB 内点常只有个位数；但"线在哪里"是一致的，两边都转成 Canny 边缘图后
ORB 同构，内点数可升到上百。规则命中时放大用 `INTER_CUBIC`，特征匹配在灰度与边缘图两路各估一次相似变换，取内点多者
（`feature_used` 记录最终用的域）。像素规则命中时优先像素规则。`--lineart-mode` / `--lineart-tags` 可覆盖。

可选人工审核后导出：

```bash
python3 -u skills/lighting-destyle-align/align_batch.py \
  --finalize --out /path/to/Prompt_with_images_0818_align_out
```

## 产物

```text
{csv名}_align_out/
├── processed/
├── align_all_results.csv
├── aligned_candidates.csv
├── align_pair_details.csv
└── review/index.html
```

## 文件

| 文件 | 说明 |
|---|---|
| `align_batch.py` | 批处理入口：读 CSV 组配对、按风格规则选插值/特征域、多进程对齐、导出 processed/ 与审核页、送标 CSV |
| `align_core.py` | ORB + 相似变换核心（`upscale_interp` 控制放大插值，`feature_mode` 控制灰度/边缘特征域） |
| `defaults.json` | 像素风与白描规则：模式 / 标签字段 / 关键词 |
| `requirements-batch.txt` | 依赖 |
| `使用说明-批处理.txt` | 详细说明 |
| `index.html` / `preview.svg` | review 用 |
