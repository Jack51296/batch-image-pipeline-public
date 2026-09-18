#!/bin/bash
# 像素风规则回归：新版(--pixel-mode off) 必须与旧版逐字节一致；auto 模式对像素批次切换为最近邻。
set -u
PY=/root/miniconda3/envs/comfyui-v4.0/bin/python
BASE=/mnt/kfs/alice
NEW=$BASE/pipeline/skills/lighting-destyle-align/align_batch.py
OLD=/tmp/align_old/align_batch.py
R=$BASE/work/style0911_hqtest/regress
rm -rf "$R"; mkdir -p "$R"

# 真实交付表前 2 行（每行 3 个 output → 6 对），相对路径以 work/style0911 为根
head -n 3 "$BASE/pipeline/batches/style0911/style0911_风格_final.csv" > "$R/subset2.csv"

# quality=high 那张的 1 行交付表（绝对路径）
$PY - "$R/hq1.csv" <<'EOF'
import csv, json, sys
base = '/mnt/kfs/alice'
row = json.loads(open(base + '/pipeline/batches/style0911_hqtest/Prompt_with_images.jsonl', encoding='utf-8').readline())
out = base + '/work/style0911_hqtest/20260904-非写实风格化-4148-qt-2072-s35-2076-qt-hq-test/二次元平涂-4-横屏-1-r1.png'
with open(sys.argv[1], 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['id', 'prompt', 'input_image_path', 'output_image_path', 'output_image_path_1', 'output_image_path_2', '标签'])
    w.writerow(['二次元平涂-4-横屏-1', row['prompt'], row['image_path'], out, '', '', row.get('light_type', '二次元平涂')])
print('hq1.csv ok')
EOF

run() {
  name=$1; shift
  $PY -u "$@" --workers 2 > "$R/$name.log" 2>&1
  echo "[$name] exit=$? $(grep -E 'ok=|像素风命中|放大插值' "$R/$name.log" | tr '\n' ' ' | cut -c1-230)"
}
run old_subset2  "$OLD" --csv "$R/subset2.csv" --root "$BASE/work/style0911" --out "$R/old_subset2"
run off_subset2  "$NEW" --csv "$R/subset2.csv" --root "$BASE/work/style0911" --out "$R/off_subset2" --pixel-mode off
run auto_subset2 "$NEW" --csv "$R/subset2.csv" --root "$BASE/work/style0911" --out "$R/auto_subset2"
run old_hq  "$OLD" --csv "$R/hq1.csv" --out "$R/old_hq"
run off_hq  "$NEW" --csv "$R/hq1.csv" --out "$R/off_hq" --pixel-mode off
run auto_hq "$NEW" --csv "$R/hq1.csv" --out "$R/auto_hq"

cmp_dir() {
  a=$1; b=$2
  (cd "$a/processed" && md5sum * | sort -k2) > /tmp/a.md5
  (cd "$b/processed" && md5sum * | sort -k2) > /tmp/b.md5
  if diff -q /tmp/a.md5 /tmp/b.md5 > /dev/null; then
    echo "IDENTICAL  $(basename "$a") == $(basename "$b")  ($(wc -l < /tmp/a.md5) files)"
  else
    echo "DIFFER     $(basename "$a") vs $(basename "$b"):"
    diff /tmp/a.md5 /tmp/b.md5 | grep '^[<>]' | awk '{print "   " $1, $3}'
  fi
}
echo "--- 回归（必须 IDENTICAL）:"
cmp_dir "$R/old_subset2" "$R/off_subset2"
cmp_dir "$R/old_hq" "$R/off_hq"
echo "--- auto 模式（像素批次：output 应不同，input 应相同）:"
cmp_dir "$R/old_subset2" "$R/auto_subset2"
cmp_dir "$R/old_hq" "$R/auto_hq"
echo "--- align_pair_details（auto）:"
$PY - "$R/auto_subset2/align_pair_details.csv" "$R/auto_hq/align_pair_details.csv" <<'EOF'
import csv, sys
for p in sys.argv[1:]:
    for r in csv.DictReader(open(p, encoding='utf-8-sig')):
        print('  %-28s %-5s %-8s %-14s out=%s q?' % (r['id'], r['category'], r['upscale_interp'], r['pixel_style_hit'], r['output_export_bytes']))
EOF
echo "REGRESS_DONE"
