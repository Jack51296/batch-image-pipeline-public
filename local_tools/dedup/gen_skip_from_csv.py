# -*- coding: utf-8 -*-
"""从 dup_groups_*.csv（组号,md5,处置,相对路径）生成跳过清单。Python 3.6 可跑。

用法: python3 gen_skip_from_csv.py <dup_groups.csv> <相对路径的基准目录> <输出.txt>
只取 处置=SKIP 的行（每组保留第 1 张，其余跳过）。
"""
import sys, csv, io

src, base, out = sys.argv[1], sys.argv[2].rstrip('/'), sys.argv[3]
n = 0
with io.open(src, encoding='utf-8-sig') as f, io.open(out, 'w', encoding='utf-8') as g:
    for row in csv.reader(f):
        if len(row) >= 4 and row[2] == 'SKIP':
            g.write(base + '/' + row[3] + '\n')
            n += 1
print('SKIP 条数: %d -> %s' % (n, out))
print('GEN_SKIP_DONE')
