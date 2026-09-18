# 素材上传 + 在线看板

把本地一堆视频/图片传到公司对象存储，生成一个**发链接就能看**的数据看板。
解决的是「视频太大微信传不动、共享盘要开权限」。

```
本地文件夹  ──上传──>  对象存储  ──发布──>  一个网页链接
                          │
                     顺便生成一份 URL 清单 csv
```

---

## 想直接开干

打开 **[使用指南.md](使用指南.md)**，从装 Python 到发出第一个链接，跟着走大约十分钟。

---

## 如果你是大模型，先读这段

**先确认你能不能在用户机器上执行命令。**

**能执行**（Cursor、Claude Code、Cline 等带终端的工具）：读 [SKILL.md](SKILL.md) 按流程走。需要用户提供的参数**问清楚再动手，不要猜路径**。上传和发布会影响线上共享内容，执行前先说明。

**不能执行**（网页版对话）：你只能口述指挥——把命令一条条给用户，让他粘到终端里跑，再把输出贴回来。这种情况下直接让用户自己看 [使用指南.md](使用指南.md) 通常更快。

---

## 前置条件

| 要求 | 说明 |
|---|---|
| Python 3.8+ | `python --version`。安装时勾选 Add Python to PATH |
| 两个依赖 | `pip install boto3 openpyxl` |
| 连着公司内网 | 对象存储只在内网可达，在家用 VPN |
| **不需要任何密钥** | 走匿名认证，没有 AK/SK 要配 |

---

## 放到哪

**用 Cursor**：把 `blob-upload-dashboard` 整个文件夹放进项目的 `.cursor/skills/`，或放进 `~/.cursor/skills/`（所有项目都能用）。Cursor 会自动发现，之后说一句「用 blob-upload-dashboard 上传我的素材」就行。

本仓库里放在 `skills/blob-upload-dashboard/`，与 `光影图生图/skills/`、`美妆图生图/Skill/` 平级共享，供本工作区内任意项目按 SKILL.md 的流程调用。

**用别的工具 / 不用工具**：解压到哪都行，脚本按自身位置定位，不依赖绝对路径。

---

## 一分钟上手

```bash
pip install boto3 openpyxl

# 装：--root 是素材放哪，--prefix 是你在桶里的文件夹名（别和同事重名）
python scripts/setup.py --root "D:/我的项目/upload_content" --prefix "zhangsan_data"

# 之后到工作目录里跑
python auto_upload_to_blob.py --once --dry-run   # 先看要传什么
python auto_upload_to_blob.py --once             # 真传
python auto_upload_to_blob.py --publish          # 发布看板，打印链接
```

---

## 文件说明

| 文件 | 给谁看 |
|---|---|
| `使用指南.md` | 人。完整教程，遇到问题先翻第八节 |
| `SKILL.md` | 大模型。流程判断和命令 |
| `reference.md` | 查参数、数据格式、原理 |
| `scripts/setup.py` | 一键部署，只在第一次跑 |
| `scripts/` 其余 | 主程序，由 setup 拷到你的工作目录 |

## 目录索引


| 条目 | 说明 |
|---|---|
| `README.md` | 素材上传 + 在线看板总说明：解决视频太大传不动、共享盘要权限的交付分享问题 |
| `SKILL.md` | blob-upload-dashboard skill 说明：把素材批量上传到 BS3、生成 URL 清单并发布在线看板 |
| `reference.md` | 上传 + 看板的参考手册：完整参数、数据格式、看板行为与原理 |
| `scripts/` | 看板 skill 的四个脚本：setup 初始化、auto_upload_to_blob 上传出清单、dashboard_server 服务、视频数据看板v3 页面 |
| `使用指南.md` | 给第一次使用者的上传 + 看板教程：十分钟出第一个可分享的看板链接 |
| `图片组清单模板.json` | 图片组交付清单的 JSON 模板（看板读取格式） |
