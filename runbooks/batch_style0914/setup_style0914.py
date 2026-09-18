# -*- coding: utf-8 -*-
"""建批次 style0914（二次元平涂 -> 像素，quality=high，1 轮）。
1) gen_image_lighting.py 增加 config.image_quality 支持（备份原文件）
2) batches/style0914/config.json + index.json 登记
3) 由 style0911 的 JSONL 取 run==1 的 61 行生成 Prompt_with_images.jsonl，并校验
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

P = Path('/mnt/kfs/alice/pipeline')
SRC, B = 'style0911', 'style0914'
QUALITY = 'high'
RUNS = 1

# ---------- 1. gen 脚本：quality 从 config 读 ----------
gen = P / 'gen_image_lighting.py'
text = gen.read_text(encoding='utf-8')
if 'image_quality' not in text:
    bak = P / 'logs' / 'backup_gen_20260914'
    bak.mkdir(parents=True, exist_ok=True)
    shutil.copy2(gen, bak / 'gen_image_lighting.py')
    hq = P / 'gen_image_lighting_hq.py'
    if hq.exists():
        shutil.move(str(hq), str(bak / 'gen_image_lighting_hq.py'))  # 已被 config.image_quality 取代
    old1 = ('    global DELIVERY_ROOT_NAME, OUTPUT_REL_PREFIX, IMAGE_OUT_DIR\n'
            '    index = json.loads(BATCH_INDEX_PATH.read_text(encoding="utf-8"))\n')
    new1 = ('    global DELIVERY_ROOT_NAME, OUTPUT_REL_PREFIX, IMAGE_OUT_DIR\n'
            '    global IMAGE_QUALITY, REQUEST_TIMEOUT\n'
            '    index = json.loads(BATCH_INDEX_PATH.read_text(encoding="utf-8"))\n')
    old2 = ('    IMAGE_OUT_DIR = str(Path(WORK_DIR) / OUTPUT_REL_PREFIX)\n'
            '    print("batch=%s work_dir=%s jsonl=%s out=%s csv=%s" % (\n'
            '        batch_id, WORK_DIR, INPUT_JSONL, IMAGE_OUT_DIR, OUTPUT_CSV\n'
            '    ))\n')
    new2 = ('    IMAGE_OUT_DIR = str(Path(WORK_DIR) / OUTPUT_REL_PREFIX)\n'
            '    # 出图质量按批次配置：config.image_quality = low | medium | high | auto（缺省沿用脚本默认 low）\n'
            '    q = str(cfg.get("image_quality") or "").strip().lower()\n'
            '    if q:\n'
            '        if q not in ("low", "medium", "high", "auto"):\n'
            '            raise SystemExit("config.image_quality 只能是 low/medium/high/auto，收到: %s" % q)\n'
            '        IMAGE_QUALITY = q\n'
            '    if IMAGE_QUALITY != "low" and REQUEST_TIMEOUT[1] < 600:\n'
            '        # high 单张实测 130s+，默认 90s 读超时会直接 Read timed out\n'
            '        REQUEST_TIMEOUT = (REQUEST_TIMEOUT[0], 600)\n'
            '    print("batch=%s work_dir=%s jsonl=%s out=%s csv=%s quality=%s read_timeout=%ss" % (\n'
            '        batch_id, WORK_DIR, INPUT_JSONL, IMAGE_OUT_DIR, OUTPUT_CSV, IMAGE_QUALITY, REQUEST_TIMEOUT[1]\n'
            '    ))\n')
    old3 = '    print(f"并发数: {MAX_IMAGE_WORKERS}，模型: {MODEL_ID}")\n'
    new3 = ('    print(f"并发数: {MAX_IMAGE_WORKERS}，模型: {MODEL_ID}")\n'
            '    print(f"质量: {IMAGE_QUALITY} · 尺寸: {IMAGE_SIZE} · 读超时: {REQUEST_TIMEOUT[1]}s")\n')
    for o, n in ((old1, new1), (old2, new2), (old3, new3)):
        assert text.count(o) == 1, o[:80]
        text = text.replace(o, n)
    gen.write_text(text, encoding='utf-8')
    print('gen_image_lighting.py patched, md5=%s (backup -> %s)' % (hashlib.md5(gen.read_bytes()).hexdigest(), bak))
else:
    print('gen_image_lighting.py already supports image_quality')
compile(gen.read_text(encoding='utf-8'), str(gen), 'exec')

# ---------- 2. 批次配置 ----------
cfg = json.loads((P / 'batches' / SRC / 'config.json').read_text(encoding='utf-8'))
cfg['id'] = B
cfg['work_dir'] = '/mnt/kfs/alice/work/' + B
cfg['local_image_root'] = 'source_images/' + B
cfg['jsonl'] = 'batches/%s/Prompt_with_images.jsonl' % B
cfg['pipeline_csv'] = 'Prompt_with_images_%s.csv' % B
cfg['delivery_csv'] = 'batches/%s/%s_风格_final.csv' % (B, B)
cfg['image_quality'] = QUALITY
cfg['runs'] = RUNS
cfg['style_from_to'] = '二次元平涂 -> 像素'
cfg['colormatch'] = 'none (--no-colormatch-tags all)'
(P / 'batches' / B).mkdir(parents=True, exist_ok=True)
(P / 'logs' / B).mkdir(parents=True, exist_ok=True)
(P / 'batches' / B / 'config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
idx_p = P / 'batches' / 'index.json'
idx = json.loads(idx_p.read_text(encoding='utf-8'))
idx[B] = cfg
idx_p.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding='utf-8')
print('config ok:', B, '| quality', QUALITY, '| runs', RUNS)

# ---------- 3. JSONL：取 style0911 的 run==1 行 ----------
src_rows = [json.loads(l) for l in (P / 'batches' / SRC / 'Prompt_with_images.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
rows = []
for r in src_rows:
    if int(r.get('run', 1)) != 1:
        continue
    r = dict(r)
    if 'batch_id' in r:
        r['batch_id'] = B
    rows.append(r)
out = P / 'batches' / B / 'Prompt_with_images.jsonl'
out.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')

ids = [r['id'] for r in rows]
prompts = {r['prompt'] for r in rows}
missing = [r['image_path'] for r in rows if not Path(r['image_path']).is_file()]
assert len(rows) == 61, len(rows)
assert len(set(ids)) == 61 and all(i.endswith('-r1') for i in ids)
assert len(prompts) == 1 and '像素画' in next(iter(prompts))
assert not missing, missing[:3]
assert all(r.get('light_type') == '二次元平涂' and r.get('prompt_target') == '像素' for r in rows)
rep = out.with_name('Prompt_with_images_report.txt')
rep.write_text(
    'batch: %s | 来源: batches/%s/Prompt_with_images.jsonl 的 run==1 行\n'
    '行数: %d | 图片: %d | runs: %d | 标签: 二次元平涂 -> 目标: 像素\n'
    'prompt 长度: %d | image_path 全部存在: %s\n'
    'quality: %s | colormatch: 不追色（交付 --no-colormatch-tags all）\n'
    % (B, SRC, len(rows), len(ids), RUNS, len(next(iter(prompts))), not missing, QUALITY), encoding='utf-8')
print('jsonl ok: %d rows -> %s' % (len(rows), out))
print('sample:', {k: (v if k != 'prompt' else v[:40] + '...') for k, v in rows[0].items()})
