# 图像工具迁移记录

迁移日期：2026-10-05。

## 来源

| 原仓库 | 来源提交 | 原文件 | 本仓库路径 |
|---|---|---|---|
| [image-ratio-crop-tool](https://github.com/Jack51296/image-ratio-crop-tool) | [06372e4](https://github.com/Jack51296/image-ratio-crop-tool/commit/06372e411cd1a3f156279d2f5da3324342759ad5) | `index.html` | `local_tools/image_tools/ratio_crop/index.html` |
| [image-size-matcher](https://github.com/Jack51296/image-size-matcher) | [b60a8aa](https://github.com/Jack51296/image-size-matcher/commit/b60a8aaa93579df652c0bf08dfe13ac27095519b) | `图像文件大小匹配工具/index.html` | `local_tools/image_tools/size_matcher/index.html` |

原尺寸匹配仓库根目录的 `index.html` 仅用于跳转，本次直接迁入实际工具页面。
两个原仓库保留，未删除、改名、归档或改变可见性；后续统一入口与维护位置为本仓库。
三个仓库均未提供 LICENSE，本次未额外声明新许可。

## 保留的功能

- 裁剪：13 比例、自动匹配、最大整数区域、拖动定位、超大图最长边 3000、PNG / JPEG / WebP 下载。
- 尺寸匹配：原图 / 参照图读取、强制目标宽高、目标字节数编辑、格式自动选择、编码质量搜索、尾部填充、误差提示。
- 两个工具仍是可单独打开的 HTML，无需构建和服务端。

## 整合改动与回归修复

1. 统一入口、双向工具导航、根 README 与本机工具目录索引。
2. 修正尺寸匹配描述：它会读取目标宽高并拉伸到该尺寸，不能声称“保持比例不变”或“只读取文件大小”。
3. 约分 `21:9` / `9:21` 后计算最大裁剪区域，避免因直接使用未约分比值而多裁像素。
4. WebP 与 JPEG 同样优先选不超过目标的候选再填充，避免已有可补齐候选时仍返回略大于目标的文件。
5. 目标字节数须为正的安全整数，拒绝小数、无穷值及空值。
6. 补充拉伸、重编码、透明度、过小目标和尾部填充的边界说明。
7. 增加实际图片的浏览器回归测试和受限范围的 Pages 发布工作流。

## 维护边界

本次迁移不调用生成 API，不自动处理批次，不修改既有追色 / 配准算法。
公共站点只发布迁入的图像工具页面。原仓库 Git 历史未合并到目标仓库，以上固定提交链接用于来源追溯。
