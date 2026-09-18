# -*- coding: utf-8 -*-
"""建一个 1 张图的 A/B 测试批次 style0911_hqtest（新布局），并生成 quality=high 的脚本副本。"""
import json
from pathlib import Path

P = Path('/mnt/kfs/alice/pipeline')
SRC, B = 'style0911', 'style0911_hqtest'
TEST_ID = '二次元平涂-4-横屏-1-r1'

cfg = json.loads((P / 'batches' / SRC / 'config.json').read_text(encoding='utf-8'))
cfg['id'] = B
cfg['total_images'] = 1
cfg['work_dir'] = '/mnt/kfs/alice/work/' + B
cfg['output_rel_prefix'] = cfg['source_dir'] + '-qt-hq-test'
cfg['jsonl'] = 'batches/%s/Prompt_with_images.jsonl' % B
cfg['pipeline_csv'] = 'Prompt_with_images_%s.csv' % B
cfg['delivery_csv'] = 'batches/%s/%s_风格_final.csv' % (B, B)

(P / 'batches' / B).mkdir(parents=True, exist_ok=True)
(P / 'logs' / B).mkdir(parents=True, exist_ok=True)
(P / 'batches' / B / 'config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')

rows = [l for l in (P / 'batches' / SRC / 'Prompt_with_images.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
sel = [l for l in rows if json.loads(l)['id'] == TEST_ID]
assert len(sel) == 1, len(sel)
(P / 'batches' / B / 'Prompt_with_images.jsonl').write_text(sel[0] + '\n', encoding='utf-8')

idx_p = P / 'batches' / 'index.json'
idx = json.loads(idx_p.read_text(encoding='utf-8'))
idx[B] = cfg
idx_p.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding='utf-8')

src = (P / 'gen_image_lighting.py').read_text(encoding='utf-8')
assert src.count('IMAGE_QUALITY = "low"') == 1
(P / 'gen_image_lighting_hq.py').write_text(src.replace('IMAGE_QUALITY = "low"', 'IMAGE_QUALITY = "high"', 1), encoding='utf-8')

row = json.loads(sel[0])
print('batch ok:', B)
print('  work_dir      :', cfg['work_dir'])
print('  output prefix :', cfg['output_rel_prefix'])
print('  image_path    :', row['image_path'], 'exists=%s' % Path(row['image_path']).exists())
print('  prompt head   :', row['prompt'][:60])
