# 图像工具迁移整合计划

**目标：** 将比例裁剪和尺寸/文件大小匹配工具迁入本仓库，统一入口、本地运行、文档和在线发布。

**结构：** 两个独立的 HTML 工具保留现有浏览器 Canvas 实现，归入 `local_tools/image_tools/`；入口页提供导航，GitHub Pages 仅发布三个 HTML 文件。原流水线仍由已有 Python 脚本执行，工具用于入库前素材准备与交付前规格检查，不自动改写批次素材。

**技术：** HTML/CSS、JavaScript、Canvas、Node.js 内置测试、Playwright（仅测试依赖）、GitHub Actions/Pages。

## 约束

- 保留两个原仓库，不修改其可见性、历史或在线站点。
- 来源版本：裁剪 `06372e411cd1a3f156279d2f5da3324342759ad5`；尺寸匹配 `b60a8aaa93579df652c0bf08dfe13ac27095519b`。
- 工具无需服务端、API key、Python 或 npm；本地双击 HTML 即可运行。
- 新增页面没有远程依赖、统计或图片上传；公共发布内容限定为已公开的图像工具。
- 裁剪保留最长边 3000 的缩小规则、13 种参考比例、拖动定位和 PNG/JPEG/WebP 导出。
- 尺寸匹配保留目标宽高强制缩放、编码质量搜索和尾部填充；说明比例不同会拉伸、小目标可能无法达到、PNG/WebP/JPEG 的兼容限制。

## 步骤

- [x] 先创建浏览器集成测试，在工具未迁入时确认入口检查失败。
- [x] 迁入 `ratio_crop/index.html` 与 `size_matcher/index.html`，添加工具之间及统一入口的相对链接，修正尺寸匹配页面中与实际行为不一致的描述。
- [x] 创建 `index.html`、`README.md`、`MIGRATION.md`；更新根 README 与 local_tools README。
- [x] 使用浏览器实际上传、裁剪和下载；检查 13 比例、无缩放裁剪像素、超大图、拖动、三种格式、目标尺寸及字节数、无法达到的目标与错误提示。
- [x] 添加 Node 测试配置和 GitHub Actions 测试/Pages 工作流；部署包只复制三个 HTML。
- [x] 审查差异、重跑测试；提交并推送 main，配置 Pages，确认远端代码、Actions 和在线页面。

## 交付验证

- 实现提交：`ce0cc6f8f86de81b74f3a3514233f35c6c337e2c`。
- 本机 Chrome：8 项浏览器回归测试通过，0 失败。
- GitHub Linux / Chromium：测试与 Pages 部署均成功，运行 [37298426295](https://github.com/Jack51296/batch-image-pipeline-public/actions/runs/37298426295)。
- 三个线上 HTML 均返回 HTTP 200，内容哈希与实现提交中的 Git blob 一致。
- 统一入口：[https://jack51296.github.io/batch-image-pipeline-public/](https://jack51296.github.io/batch-image-pipeline-public/)。

## 验证命令

```powershell
cd local_tools/image_tools
npm install
npx playwright install chromium
npm test
```

开发机已有 Chrome 时可用 `IMAGE_TOOLS_BROWSER_CHANNEL=chrome`；测试图片在临时目录生成并清理，不写入素材目录。
