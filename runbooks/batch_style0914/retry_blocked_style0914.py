# -*- coding: utf-8 -*-
"""对 pipeline CSV 里非 success 的行重试一次出图（只跑这几行），再把结果合并回原 CSV / out.jsonl。"""
import collections
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

B = 'style0914'
P = Path('/mnt/kfs/alice/pipeline')
W = Path('/mnt/kfs/alice/work') / B
csvp = W / f'Prompt_with_images_{B}.csv'
outj = W / f'Prompt_with_images_{B}_out.jsonl'
bak_csv = P / 'logs' / B / 'pipeline_csv_before_retry.csv'
bak_j = P / 'logs' / B / 'out_jsonl_before_retry.jsonl'
shutil.copy2(csvp, bak_csv)
shutil.copy2(outj, bak_j)

rows = list(csv.DictReader(open(csvp, encoding='utf-8-sig')))
fields = list(rows[0].keys())
bad = [r for r in rows if r['image_status'] != 'success']
print('retry:', [(r['id'], r['image_status'][:160]) for r in bad])
if not bad:
    print('nothing to retry')
    sys.exit(0)
bad_ids = {r['id'] for r in bad}
src = [json.loads(l) for l in open(P / 'batches' / B / 'Prompt_with_images.jsonl', encoding='utf-8') if l.strip()]
retry = [r for r in src if r['id'] in bad_ids]
assert len(retry) == len(bad_ids), (len(retry), bad_ids)
rj = Path('/tmp/retry_%s.jsonl' % B)
rj.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in retry), encoding='utf-8')

rc = subprocess.call([sys.executable, '-u', str(P / 'gen_image_lighting.py'), '--batch', B, '--input-jsonl', str(rj)], cwd=str(P))
print('gen rc', rc)

new_by_id = {r['id']: r for r in csv.DictReader(open(csvp, encoding='utf-8-sig'))}
merged = []
for r in rows:
    n = new_by_id.get(r['id'])
    if n is not None and r['image_status'] != 'success':
        print('row', r['id'], ':', r['image_status'][:50], '->', n['image_status'][:120])
        r = n
    merged.append(r)
with open(csvp, 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
    w.writeheader()
    w.writerows(merged)

new_j = {}
for l in open(outj, encoding='utf-8'):
    if l.strip():
        new_j[json.loads(l)['id']] = l.rstrip('\n') + '\n'
lines = []
for l in open(bak_j, encoding='utf-8'):
    if not l.strip():
        continue
    d = json.loads(l)
    if d['id'] in new_j and d.get('image_status') != 'success':
        l = new_j[d['id']]
    lines.append(l.rstrip('\n') + '\n')
outj.write_text(''.join(lines), encoding='utf-8')
print('final rows', len(merged), dict(collections.Counter(r['image_status'].split(':')[0] for r in merged)))
print('RETRY_DONE', 'ALL_SUCCESS' if all(r['image_status'] == 'success' for r in merged) else 'STILL_FAILED')
