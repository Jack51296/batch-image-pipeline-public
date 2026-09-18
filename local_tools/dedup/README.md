# dedup/ — 跨批次内容级查重

跨批次内容级查重：找 md5 重复、生成跳过清单、过滤 JSONL、统计与已完成批次的重叠；data/ 为 4 档重复清单。

| 条目 | 说明 |
|---|---|
| `check_overlap_with_done.py` | 统计 0814/0815 任务清单中与已跑完批次（0818/0819）内容相同的图 |
| `data/` |  |
| `filter_dup_jsonl.py` | 按跳过清单过滤任务 JSONL（与根目录同名脚本的本机版本） |
| `gen_skip_from_csv.py` | 从 dup_groups_*.csv（组号,md5,处置,相对路径）生成跳过清单 |
| `make_dup_lists.py` | 扫描 6 个 4-*张 目录找内容级重复（md5），生成重复组清单与跳过清单 |
| `make_dup_lists_p0.py` | 只读扫描 carol 光影光效P0 下所有 4-* 目录找内容级重复（md5） |
| `register_0814_0815.py` | 注册 0814 / 0815 两个光影批次（照抄 0818 条目改值） |
| `save_overlap_list.py` | 把与已完成批次内容相同的图写成清单，供交付阶段剔除 |
