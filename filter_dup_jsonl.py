# -*- coding: utf-8 -*-
"""按跳过清单过滤任务 JSONL（跑量防重复跑）。Python 3.6 可跑。

用法: python3 filter_dup_jsonl.py <输入.jsonl> <输出.jsonl>

- 自动合并本脚本同目录下所有 dup_skip_list*.txt（4档、P0 等各来源清单）。
- 匹配规则：image_path 与清单条目全路径一致，或**最后 3 段路径**一致
  （<4-xxx张>/<光效-张数>/<文件名>），这样原图被复制到新目录后仍能命中。
- **过滤范围只限 4-… 档目录**：路径中不含 /4-xxx/ 目录段的行一律保留，
  其他档位（3.5 档等）即使出现在清单里也不会被剔除。
"""
import sys, os, json, io, glob, re

SCOPE = re.compile(r'/4-[^/]*/')   # 只允许剔除 4-… 档目录里的行

def tail3(p):
    return '/'.join(p.replace('\\', '/').rstrip('/').split('/')[-3:])

here = os.path.dirname(os.path.abspath(__file__))
full, tails = set(), set()
lists = sorted(glob.glob(os.path.join(here, 'dup_skip_list*.txt')))
if not lists:
    sys.exit('未找到 dup_skip_list*.txt，先生成跳过清单')
for lf in lists:
    n = 0
    for l in io.open(lf, encoding='utf-8'):
        l = l.strip()
        if not l:
            continue
        full.add(l)
        tails.add(tail3(l))
        n += 1
    print('清单 %s: %d 条' % (os.path.basename(lf), n))

src, dst = sys.argv[1], sys.argv[2]
kept = removed = 0
removed_ids = []
with io.open(src, encoding='utf-8') as f, io.open(dst, 'w', encoding='utf-8') as g:
    for line in f:
        if not line.strip():
            continue
        d = json.loads(line)
        ip = d.get('image_path', '')
        if SCOPE.search(ip.replace('\\', '/')) and (ip in full or tail3(ip) in tails):
            removed += 1
            removed_ids.append(d.get('id', os.path.basename(ip)))
            continue
        g.write(line)
        kept += 1
print('保留 %d 行，剔除 %d 行 -> %s' % (kept, removed, dst))
if removed_ids:
    print('剔除示例（前10）:', ', '.join(str(x) for x in removed_ids[:10]))
print('FILTER_DONE')
