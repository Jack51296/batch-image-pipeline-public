# kml/ — 驱动调试 Chrome 里的 code-server（CDP）

驱动调试 Chrome 里的 code-server（CDP）：跑命令、base64 上传、HTTP 直连下载、按 md5 清单交付到网盘、截图目检。

| 条目 | 说明 |
|---|---|
| `cdp_helper.py` | 最小 CDP 客户端：连调试 Chrome 的标签页，收发 DevTools 协议消息、Runtime.evaluate |
| `deliver_to_share.py` | 按开发机生成的 md5 清单把交付文件下载到网盘目录，保持相对结构、逐文件校验、已存在且一致的跳过 |
| `grab_editor_image.py` | 打开工作区图片后整页截图并按编辑器背景色差自动裁出图片区域（1:1 取图） |
| `kml_fetch.py` | 通过 code-server /vscode-remote-resource 接口用浏览器登录态直连下载开发机文件（任意大小，支持清单批量） |
| `kml_heredoc.py` | 把本地 Python 脚本以 heredoc 方式粘贴到 KML 终端直接执行（等效上传 + 运行） |
| `kml_run.py` | 在 KML 终端逐字符输入并执行一条命令，然后整页截图 |
| `kml_run2.py` | kml_run 改良版：先 Ctrl+C 清行、合成粘贴代替逐字输入、回车前重新聚焦（长命令用 @cmd.txt） |
| `kml_term.py` | 驱动 code-server 网页终端的工具类：聚焦 xterm、按键/合成粘贴输入命令、命令面板、截图 |
| `kml_upload.py` | 经 xterm 合成粘贴通道把本地文件 base64 分块上传到开发机，末尾 md5 校验（适合 <1MB 脚本） |
| `kml_upload_align_skill.py` | 把 lighting-destyle-align skill 整目录上传到开发机代码目录并 md5 校验 |
| `kml_view_big.py` | 隐藏侧栏和面板后打开图片截图（显示区域更大），完毕恢复布局 |
| `kml_view_img.py` | 用 QuickOpen 在网页版 VS Code 打开图片预览并截图（目检生成图） |
