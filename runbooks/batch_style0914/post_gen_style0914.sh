#!/bin/bash
# style0914：等出图结束 -> 交付表(不追色) -> 对齐(像素规则) -> 生成传输清单(md5)
set -u
PY=/root/miniconda3/envs/comfyui-v4.0/bin/python
BASE=/mnt/kfs/alice
B=style0914
cd "$BASE/pipeline" || exit 1
LOG=logs/$B

while pgrep -f "gen_image_lighting.py --batch $B" > /dev/null; do sleep 20; done
echo "gen finished: $(date)"
tail -n 12 "$LOG/gen.log" | grep -E "成功|失败|跳过|Traceback|Error" || true
$PY - <<'EOF'
import csv, collections
rows = list(csv.DictReader(open('/mnt/kfs/alice/work/style0914/Prompt_with_images_style0914.csv', encoding='utf-8-sig')))
c = collections.Counter(r['image_status'].split(':')[0] for r in rows)
print('pipeline rows', len(rows), dict(c))
bad = [r['id'] for r in rows if r['image_status'] != 'success']
print('non-success ids:', bad)
EOF

echo "=== 1/3 交付表"
$PY -u make_lighting_delivery_csv.py --batch $B --no-colormatch-tags all --min-outputs 1 --verify > "$LOG/deliver.log" 2>&1
echo "deliver exit=$?"
grep -E "写出行数|原图|已写出|no_colormatch|每图输出|verify|通过|失败|缺失" "$LOG/deliver.log" | head -n 14

echo "=== 2/3 对齐"
CSV=batches/$B/${B}_风格_final.csv
OUT=$BASE/work/$B/${B}_风格_final_align_out
$PY -u skills/lighting-destyle-align/align_batch.py --csv "$CSV" --root "$BASE/work/$B" --out "$OUT" --style 二次元平涂转像素 --workers 4 > "$LOG/align.log" 2>&1
echo "align exit=$?"
grep -E "可处理|像素风|ok=|放大插值|警告" "$LOG/align.log"

echo "=== 3/3 传输清单（只列已知的三层目录，不做递归扫描）"
{
  for d in "$OUT" "$OUT/processed" "$OUT/review"; do
    for f in "$d"/*; do [ -f "$f" ] && echo "$f"; done
  done
  echo "$BASE/work/$B/Prompt_with_images_$B.csv"
} | sort | xargs -d '\n' md5sum > "/tmp/${B}_manifest_work.txt"
{
  echo "$BASE/pipeline/$CSV"
  echo "$BASE/pipeline/batches/$B/${B}_风格_final_report.txt"
  echo "$BASE/pipeline/batches/$B/${B}_风格_final_verify.txt"
} | xargs -d '\n' md5sum > "/tmp/${B}_manifest_batch.txt"
echo "manifest work=$(wc -l < /tmp/${B}_manifest_work.txt) batch=$(wc -l < /tmp/${B}_manifest_batch.txt)"
ls -l "$OUT/processed" | awk '{s+=$5} END {printf "processed size: %.2f GB\n", s/1e9}'
echo POST_DONE
