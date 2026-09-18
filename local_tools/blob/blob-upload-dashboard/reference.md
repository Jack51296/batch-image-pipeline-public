# 参考手册

三份文档的分工：`SKILL.md` 是给 agent 看的流程，[使用指南.md](使用指南.md) 是给人看的教程，
这份放细节——完整参数、数据格式、看板行为、原理。

## auto_upload_to_blob.py 全部参数

| 参数 | 作用 |
|---|---|
| `--once` | 扫一次就退出。不给 `--once` 也不给 `--watch` 时默认就是它 |
| `--watch` | 常驻监听，默认 10 秒扫一次；会在代码更新后自动重启加载新版 |
| `--interval N` | 改监听间隔（秒） |
| `--dir 0806` | 只处理根目录下某一个批次 |
| `--dry-run` | 只打印要传什么，不真传 |
| `--force` | 无视本地上传记录，全部重传 |
| `--verify-remote` | 传之前 head_object 核对线上是否已有同样大小的对象，有就跳过 |
| `--repair` | 逐个核对线上大小，只重传损坏或缺失的（修 aws-chunked 损坏用这个） |
| `--fill-xlsx` | 把 URL 回填进批次目录下的 xlsx |
| `--write-urls` | 不上传，只按已有记录重新生成各批次的 URL 清单 csv |
| `--publish` | 不扫素材，只把清单 csv、索引 json、看板页面推到线上 |
| `--root PATH` | 临时覆盖本地根目录 |
| `--prefix NAME` | 临时覆盖线上前缀 |
| `--bucket NAME` | 临时覆盖桶名 |
| `--config PATH` | 指定配置文件 |
| `--show-config` | 打印当前生效配置就退出，排查用 |

配置查找顺序：`--config` → 环境变量 `BLOB_CONFIG` → 脚本旁边 → 当前目录 → 内置默认值。

## dashboard_server.py

```bash
python dashboard_server.py                  # 8765 端口，数据根目录 = ./upload_content
python dashboard_server.py 9000             # 换端口
python dashboard_server.py 8765 D:/别的/目录  # 换端口和数据根目录
```

监听 `0.0.0.0`，同事在同一网段能用你机器的 IP 直接访问，启动时会打印。

桶配置是 `import auto_upload_to_blob` 拿的，所以改 `blob_config.json` 服务端自动跟着变。

提供的接口：

| 接口 | 用途 |
|---|---|
| `/api/files` | 列出数据根目录下所有 xlsx / csv / json 清单 |
| `/api/blob` | 列出线上有哪些媒体文件（缓存 60 秒） |
| `/api/file?src=...` | 读某个清单的内容 |

## 上传行为的几个细节

**去重**：`<local_root>/.blob_upload_state.json` 按文件哈希 + 修改时间记账，没变过的不重传。这个文件跟着 `local_root` 走，换根目录等于换了一本账。

**防半截**：文件最后修改时间不足 5 秒（`STABLE_SECONDS`）的跳过不传，避免把还在写的 mp4 传上去。所以刚拷完文件立刻跑会看到「没有需要上传的」，等几秒就好。

**分片**：超过 64 MB 走分片上传，16 MB 一片，4 线程并发。

**不传什么**：`frames` / `_logs` / `_build` / `__pycache__` / `.git` / `_archive` 目录，以及 `~$*`、`.*`、`*.tmp`、`*.part` 等临时文件。清单 csv 和索引 json 也不走扫描器——它们的内容依赖上传结果，被扫进来会形成「上传→内容变→再上传」的死循环，只在 `--publish` 阶段显式上传。

**校验**：每个文件传完立刻 head_object 核对大小，对不上会报出来。

**aws-chunked 那个坑**：boto3 默认会自动算 CRC32 校验和，触发 `aws-chunked` 传输编码，BS3 不认，结果是文件传上去了但字节被写坏，下载下来播不了。客户端里已经用 `request_checksum_calculation='when_required'` 关掉了。历史损坏文件用 `--repair` 修。

## 清单文件格式

### `<批次>_blob_urls.csv`（上传自动生成）

```csv
filename,relative_path,size_bytes,url
demo_01.mp4,0806/source_video/demo_01.mp4,22765,https://.../demo_01.mp4?x-bs-client-force=true&ts=...
```

早期版本每个批次都叫 `_blob_urls.csv`，在选择框里分不清是哪天的，所以现在带批次前缀。

### 素材清单（自己写的 xlsx / csv）

看板认这几列，有哪列填哪列：

```
source_video_url, target_video_url, reference_image_url, prompt, prompt_setting
```

只要有其中任一 URL 列，看板就当成「一行一条素材」的对比清单；没有就当成 URL 清单。参考格式见 [图片组清单模板.json](图片组清单模板.json)（json 同样支持这几列）。

### `dashboard_index.json`（发布时生成）

看板托管到 BS3 后没有后端接口可用，靠这份索引自描述线上有什么。**新增素材后不重跑 `--publish`，线上看板就看不到新内容。**

## 看板的两种视图

**对比卡片**：一行一条素材，source / target / reference 并排对比，适合逐条审片。

**图墙**：只看某一路（比如全部 source），按缩略图密排，鼠标悬停自动播放，点开放大。适合快速扫一遍有没有明显问题。切换在顶部的视图切换按钮，图墙模式下可以选看哪一路。

侧边栏按类别、镜头类型、批次分组，顶部可筛选和翻页。

## 三种运行形态

| 形态 | 怎么起 | 谁能看 |
|---|---|---|
| 本地文件 | 直接双击 html | 只有自己，需要手动选清单文件 |
| 本地服务 | `python dashboard_server.py` | 同网段的人，用你的 IP |
| 线上托管 | `--publish` | 任何人，链接发出去就行 |

页面会自己判断当前是哪种形态（`apiMode`），不用手动切。

## 依赖

```bash
pip install boto3 openpyxl
```

`openpyxl` 只在 `--fill-xlsx` 和看板读 xlsx 时用到，不装也能传文件。
