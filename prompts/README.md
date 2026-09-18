# prompts/ — prompt 与模板

prompt 与模板：风格种类-提示词总表.xlsx（风格批次 prompt 来源）、光照提示词通过清单、风格数据交付模板。

| 条目 | 说明 |
|---|---|
| `光照-提示词总表-通过清单.csv` | 从网盘《光照种类-提示词总表》筛出的进度=通过 的 光效×目标 组合清单（local_tools/prompts/filter_pass.py 刷新） |
| `风格数据交付模板.csv` | 风格化交付表的 7 列表头模板：id / prompt / input_image_path / output_image_path(_1/_2) / 标签 |
| `风格种类-提示词总表.xlsx` | 风格化批次的 prompt 来源：按 aigc生成风格 取、进度=通过 的行才用 |
