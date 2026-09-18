# batches/ — 批次登记

批次登记：index.json 总表 + 各批次目录（config.json、任务 JSONL、report），出图/交付/对齐脚本都从这里取路径。

| 条目 | 说明 |
|---|---|
| `index.json` | 所有批次的总登记表，键 = 批次 id，值 = 该批次 config.json 全文；gen/交付/对齐脚本都从这里取路径 |
| `style0911/` |  |
| `style0914/` |  |
