# scripts/ — 看板 skill 的四个脚本

看板 skill 的四个脚本：setup 初始化、auto_upload_to_blob 上传出清单、dashboard_server 服务、视频数据看板v3 页面。

| 条目 | 说明 |
|---|---|
| `auto_upload_to_blob.py` | 自动上传 upload_content 下的交付文件到 BS3，生成 URL 清单 |
| `dashboard_server.py` | 看板服务：让看板页面能直接列出并读取交付清单文件 |
| `setup.py` | 一次性初始化：把上传 + 看板流程部署到某个人自己的目录 |
| `视频数据看板v3.html` | 在线数据看板页面：按清单展示视频/图片交付内容 |
