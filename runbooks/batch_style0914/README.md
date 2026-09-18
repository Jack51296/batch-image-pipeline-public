# batch_style0914/ — style0914 正式批全流程脚本

style0914 正式批全流程脚本：建批（含 gen quality 补丁）→ 等出图 → 交付表 → 对齐 → md5 清单，以及审核拦截行重试。

| 条目 | 说明 |
|---|---|
| `post_gen_style0914.sh` | style0914 后处理：等出图结束 → 交付表（不追色）→ 对齐（像素规则）→ 生成 md5 传输清单 |
| `retry_blocked_style0914.py` | 对 pipeline CSV 里非 success（审核拦截等）的行只重跑这几行，再合并回 CSV / out.jsonl |
| `setup_style0914.py` | 建批次 style0914：从 style0911 JSONL 取 run==1 行、写 config/index、给 gen 脚本打 quality 补丁 |
