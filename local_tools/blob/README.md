# blob/ — BlobStore（S3 协议网关）整目录上传/下载脚本 + blob-upload-dashboard 上传与在线看板 skill

BlobStore（S3 协议网关）整目录上传/下载脚本 + blob-upload-dashboard 上传与在线看板 skill。

| 条目 | 说明 |
|---|---|
| `blob-upload-dashboard/` | 上传 + 在线看板 skill：把素材批量传到 BS3、生成 URL 清单并发布可在线访问的数据看板 |
| `download_0903_makeup_images.py` | 按《交付记录汇总表》的 image1/image2 列批量下载 src/target 图片（0903 美妆交付） |
| `download_dir_from_blob.py` | 按 key 前缀把 BlobStore 上的一整棵目录拉到本地，保持目录结构 |
| `upload_file_to_blob.py` | 把本地目录整棵树上传到 BlobStore（S3 协议网关，UNSIGNED + service 头），保持目录结构 |
