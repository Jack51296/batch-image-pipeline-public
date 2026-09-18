# local_tools/ — 本机 Windows 侧工具

本机 Windows 侧工具：kml/ 驱动 code-server、blob/ BlobStore 上传下载与看板、dedup/ 跨批次查重、prompts/ 刷新通过清单、legacy/ 早期单机脚本。

| 条目 | 说明 |
|---|---|
| `blob/` | BlobStore（S3 协议网关）整目录上传/下载脚本 + blob-upload-dashboard 上传与在线看板 skill |
| `dedup/` | 跨批次内容级查重：找 md5 重复、生成跳过清单、过滤 JSONL、统计与已完成批次的重叠；data/ 为 4 档重复清单 |
| `kml/` | 驱动调试 Chrome 里的 code-server（CDP）：跑命令、base64 上传、HTTP 直连下载、按 md5 清单交付到网盘、截图目检 |
| `legacy/` | 最早的单机脚本：从图片夹组 JSONL、直接调 GPT-Image 出图、生成 Labkit 送标表 |
| `prompts/` |  |
| `requirements.txt` | 本机 Windows 侧工具依赖（websocket-client、requests、Pillow、openpyxl 等，Python 3.10+） |
