---
name: blob-upload-dashboard
description: 把本地视频/图片素材批量上传到 BS3 对象存储，自动生成 URL 清单，并发布一个可在线访问的数据看板网页。适用于用户提到上传素材到桶/blob/BS3、生成交付链接清单、搭视频数据看板、把交付内容分享给别人在线看，或提到 auto_upload_to_blob、dashboard_server、blob_config.json 时。
---

# 素材上传 BS3 + 在线数据看板

一条龙：本地文件夹 → 上传到对象存储 → 生成 URL 清单 → 发布在线看板。
用户只需要提供两样东西：**本地目录** 和 **他在桶里的前缀**。

## 第一步：判断是新装还是已装

在用户的工作目录里找 `blob_config.json`：

- **找不到** → 走「初次部署」
- **找到了** → 直接走「日常使用」，先 `python auto_upload_to_blob.py --show-config` 确认配置对不对

## 初次部署

问用户要两个值，缺哪个问哪个，别自己瞎猜：

| 要问的 | 说明 | 例子 |
|---|---|---|
| 本地根目录 | 素材放哪。**它下面每个子文件夹算一个批次** | `D:/项目/upload_content` |
| 线上前缀 | 他在桶里的文件夹名，**不能和同事重名，会互相覆盖** | `zhangsan_data` |

然后跑：

```bash
python scripts/setup.py --root "<本地根目录>" --prefix "<线上前缀>"
```

这一步会建好目录、把主程序拷到工作目录、写 `blob_config.json`、并连一次桶做自检。
自检报 `[×] 连不上` 就停下来，先解决网络，别继续往下走。

依赖：`pip install boto3 openpyxl`。

## 日常使用

以下命令都在**工作目录**（`blob_config.json` 所在的那层）里跑。

### 1. 上传

```bash
python auto_upload_to_blob.py --once --dry-run   # 先看要传什么
python auto_upload_to_blob.py --once             # 确认后真传
python auto_upload_to_blob.py --watch            # 常驻，丢文件进去自动传
```

传完自动在每个批次目录下生成 `<批次>_blob_urls.csv`（如 `0806/0806_blob_urls.csv`）。

### 2. 发布看板

```bash
python auto_upload_to_blob.py --publish
```

把清单 csv、`dashboard_index.json`、看板页面推到线上，打印一个谁都能打开的网址。

**每次新增素材后都要重跑一次 `--publish`**，否则线上看板还是旧的。

### 3. 本地看（不发布）

```bash
python dashboard_server.py          # 默认 8765 端口
```

## 目录怎么摆

`local_root` 下面一层是批次，再下面一层是角色，本地相对路径 = 线上路径：

```
upload_content/              ← local_root
  0806/                      ← 一个批次
    source_video/  a.mp4
    target_video/  a.mp4
    reference_image/ a.png
    prompt_list.xlsx         ← 提示词清单，可选
  0812/
```

上传后 `0806/source_video/a.mp4` 就是 `<public_host>/<bucket>/<key_prefix>/0806/source_video/a.mp4`。

角色目录名带 `source` / `target` / `reference` 时看板会自动识别成对比三栏。名字不规范也能传，只是看板认不出角色。

## 改配置

只改 `blob_config.json`，**不要改 .py**。换人用通常只需要动 `local_root` 和 `key_prefix`：

```json
{
  "local_root": "D:/项目/upload_content",
  "bucket": "bucket-b",
  "key_prefix": "zhangsan_data",
  "endpoint": "https://blobstore.example.com",
  "public_host": "https://blobstore.example.com",
  "service_header": "grpc_onlineEarningGenRpcService"
}
```

临时覆盖用 `--root` / `--prefix` / `--bucket` / `--config`，不写回文件。

## 常见问题先查这几条

| 症状 | 原因 | 处理 |
|---|---|---|
| 传上去的视频播不了、提示文件不完整 | 早期 aws-chunked 编码损坏 | `python auto_upload_to_blob.py --once --repair` |
| 明明有新文件却说「没有需要上传的」 | 文件刚写完不到 5 秒，防半截保护 | 等几秒重跑，这是正常保护 |
| 线上看板打不开或内容是旧的 | 没发布 | 重跑 `--publish` |
| 看板显示「读取失败」 | 缺 `dashboard_index.json` | 重跑 `--publish` |
| 换了目录后所有文件重传一遍 | 上传记录跟着 `local_root` 走 | 正常，`--verify-remote` 可跳过线上已有的 |

## 配套文档

- [使用指南.md](使用指南.md) — 给人看的完整教程，从装 Python 到发出第一个链接。用户问「怎么用」「有没有文档」时直接指这份，或按它的步骤带着走。
- [reference.md](reference.md) — 全部参数、数据格式、上传行为细节、原理。

## 两条硬规矩

1. **前缀不能和同事重名**，同名会直接覆盖对方的文件，没有提示。
2. 部署到别人机器时，**从 `scripts/` 拷过去**，那是唯一的正本；不要从某个同事的工作目录二次拷贝，容易拷到改花了的版本。
