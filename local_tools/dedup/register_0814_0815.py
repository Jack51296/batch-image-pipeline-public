# -*- coding: utf-8 -*-
"""注册 0814 / 0815 两个批次（照抄 0818 条目改值）。Python 3.6 可跑。
- 写 batches/<id>/config.json
- 更新 batches/index.json（先备份 index.json.bak_0907）
- 建 prod_work 目录
只动 bob 自己的代码目录。
"""
import json, os, shutil, io

ROOT = '/mnt/kfs/bob/光影光效2'
IMG_BASE = '/mnt/kfs/bob/光影光效'
NEW = [
    ('0814', '260814-光影光效-6840-qt-1707-s35-5133', 6840, ''),
    ('0815', '260815-光影光效-1598-qt-289-s35-1309', 1598, '0815光影光效类目分布.xlsx'),
]

idx_path = os.path.join(ROOT, 'batches/index.json')
idx = json.load(io.open(idx_path, encoding='utf-8'))
base = json.loads(json.dumps(idx['0818']))
bak = idx_path + '.bak_0907'
if not os.path.exists(bak):
    shutil.copy2(idx_path, bak)

for bid, d, total, xlsx in NEW:
    root = os.path.join(IMG_BASE, d)
    leaf = 0
    for sub in os.listdir(root):
        p = os.path.join(root, sub)
        if os.path.isdir(p) and (sub.startswith('4-') or sub.startswith('3.5-')):
            leaf += sum(1 for x in os.listdir(p) if os.path.isdir(os.path.join(p, x)))
    e = dict(base)
    e.update({
        'id': bid,
        'excel_distribution': xlsx,
        'root_name': '光影光效-%d张' % total,
        'total_images': total,
        'leaf_categories': leaf,
        'image_root': root,
        'kml_image_root': root,
        'local_image_root': 'source_images/%s' % bid,
        'delivery_root': d,
        'source_dir': d,
        'output_rel_prefix': d + '-qt-round1',
        'work_dir': ROOT + '/prod_work',
        'jsonl': 'batches/%s/Prompt_with_images.jsonl' % bid,
        'pipeline_csv': 'Prompt_with_images_%s.csv' % bid,
        'delivery_csv': 'batches/%s/%s_光影_final.csv' % (bid, bid),
    })
    os.makedirs(os.path.join(ROOT, 'batches', bid), exist_ok=True)
    with io.open(os.path.join(ROOT, 'batches', bid, 'config.json'), 'w', encoding='utf-8') as f:
        f.write(json.dumps(e, ensure_ascii=False, indent=2))
    idx[bid] = e
    print('注册', bid, '->', d, ' leaf目录:', leaf)

with io.open(idx_path, 'w', encoding='utf-8') as f:
    f.write(json.dumps(idx, ensure_ascii=False, indent=2))
os.makedirs(os.path.join(ROOT, 'prod_work'), exist_ok=True)
print('index 批次:', sorted(idx.keys()))
print('REGISTER_DONE')
