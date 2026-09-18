#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频数据看板服务：让看板页面能直接列出并读取交付清单文件。

功能：
  1. 提供看板页面本身（http://<本机IP>:8765/）
  2. /api/files                    扫描数据根目录，列出所有 xlsx / csv / json 清单及条数
  3. /api/file?src=<链接或路径>    读取任意清单文件（xlsx / csv / json）
     - blob / http(s) 链接：服务端下载后转发给页面（绕开浏览器跨域限制）
     - 文件路径：直接读取运行本服务这台机器上的文件
  4. /api/xlsx?src=...            旧接口，等价于 /api/file，保留兼容

用法：
    python dashboard_server.py                      # 端口 8765，数据根目录 = ./upload_content
    python dashboard_server.py 9000                 # 指定端口
    python dashboard_server.py 8765 D:\\其他\\目录     # 指定端口与数据根目录

在哪台机器上运行，就能读哪台机器的路径：
  - 在 Windows 本机运行：可读本机 / 网络共享路径，如 D:\\data\\demo.xlsx
  - 在开发机运行：可读 /ytech_milm/... 这类路径

页面地址栏支持自动加载，例如：
    http://<IP>:8765/?src=https://blobstore.example.com/....xlsx
    http://<IP>:8765/?src=0729/0729_demo_blob.xlsx
"""
import csv
import json
import os
import socket
import ssl
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = os.path.join(BASE_DIR, 'upload_content')


def find_html():
    """看板页面文件名允许改（视频数据看板v2.html 等），优先精确名，其次取最新的 html。"""
    exact = os.path.join(BASE_DIR, '视频数据看板.html')
    if os.path.isfile(exact):
        return exact
    candidates = [os.path.join(BASE_DIR, n) for n in os.listdir(BASE_DIR)
                  if n.lower().endswith('.html')]
    if not candidates:
        return exact
    return max(candidates, key=os.path.getmtime)

ALLOW_EXT = ('.xlsx', '.xls', '.csv', '.json')
MEDIA_EXT = ('.mp4', '.mov', '.webm', '.png', '.jpg', '.jpeg', '.webp', '.gif')
MIME = {
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.xls': 'application/vnd.ms-excel',
    '.csv': 'text/csv; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
}

# 一行一条素材的清单必须有这些列之一，否则算成「URL 清单」（auto_upload 生成的 _blob_urls.csv）
ITEM_COLUMNS = ('source_video_url', 'target_video_url', 'reference_image_url',
                'source_video', 'target_video', 'reference_image')

# 内网服务常用自签名证书，跳过校验
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

_scan_cache = {}        # path -> (mtime, size, rows, kind, columns)
_blob_cache = {'at': 0, 'items': None, 'error': ''}
BLOB_TTL = 60           # 线上列表缓存秒数


# ---------------------------------------------------------------------------
# 线上（BS3）对象列表：直接复用上传脚本里的客户端与路径规则
# ---------------------------------------------------------------------------
def list_blob_objects():
    """返回 (items, error)。items = [{key, path, size, url}]，path 是去掉 Blender_data/ 的相对路径。"""
    import time
    now = time.time()
    if _blob_cache['items'] is not None and now - _blob_cache['at'] < BLOB_TTL:
        return _blob_cache['items'], _blob_cache['error']

    items, error = [], ''
    try:
        sys.path.insert(0, BASE_DIR)
        import auto_upload_to_blob as up
        client = up.get_blob_s3_client()
        prefix = up.KEY_PREFIX.strip('/') + '/'
        paginator = client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=up.BUCKET, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.lower().endswith(MEDIA_EXT):
                    continue
                items.append({
                    'key': key,
                    'path': key[len(prefix):],
                    'size': obj['Size'],
                    'url': up.build_url(key),
                })
    except Exception as e:
        error = '%s: %s' % (type(e).__name__, e)

    _blob_cache.update(at=now, items=items, error=error)
    return items, error


def blob_summary(items):
    """按一级目录（批次）汇总数量与体积。"""
    groups = {}
    for it in items:
        parts = it['path'].split('/')
        top = parts[0] if len(parts) > 1 else '(根目录)'
        g = groups.setdefault(top, {'name': top, 'count': 0, 'size': 0})
        g['count'] += 1
        g['size'] += it['size']
    return sorted(groups.values(), key=lambda g: g['name'], reverse=True)


# ---------------------------------------------------------------------------
# 清单文件探查
# ---------------------------------------------------------------------------
def probe_xlsx(path):
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None) or ()
        columns = [str(c).strip() for c in header if c is not None]
        count = sum(1 for r in rows if any(c not in (None, '') for c in r))
        return count, columns
    finally:
        wb.close()


def probe_csv(path):
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f)
        header = next(reader, [])
        columns = [c.strip() for c in header]
        count = sum(1 for r in reader if any(str(c).strip() for c in r))
    return count, columns


def probe_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):
        for key in ('rows', 'data', 'items', 'list'):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return 0, []
    columns = list(data[0].keys()) if data and isinstance(data[0], dict) else []
    return len(data), columns


def probe(path):
    """返回 (条数, 类型, 列名)，带 mtime 缓存。类型：items / urls / unknown。"""
    try:
        st = os.stat(path)
    except OSError:
        return 0, 'unknown', []

    cached = _scan_cache.get(path)
    if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return cached[2], cached[3], cached[4]

    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in ('.xlsx', '.xls'):
            count, columns = probe_xlsx(path)
        elif ext == '.csv':
            count, columns = probe_csv(path)
        elif ext == '.json':
            count, columns = probe_json(path)
        else:
            count, columns = 0, []
    except Exception:
        count, columns = 0, []

    lower = [c.lower() for c in columns]
    if any(c in lower for c in ITEM_COLUMNS):
        kind = 'items'
    elif 'url' in lower and 'relative_path' in lower:
        kind = 'urls'
    else:
        kind = 'unknown'

    _scan_cache[path] = (st.st_mtime, st.st_size, count, kind, columns)
    return count, kind, columns


def scan_data_root(root):
    """扫描数据根目录下的清单文件，按 目录/文件名 返回。"""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        # _archive 是人工归档的冗余表，不该再出现在下拉里
        dirnames[:] = [d for d in dirnames
                       if not d.startswith('.') and d != '_archive']
        for name in sorted(filenames):
            if name.startswith(('~$', '.')):
                continue
            if os.path.splitext(name)[1].lower() not in ALLOW_EXT:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, '/')
            count, kind, columns = probe(full)
            st = os.stat(full)
            out.append({
                'src': rel,
                'name': name,
                'folder': os.path.dirname(rel) or '/',
                'ext': os.path.splitext(name)[1].lower().lstrip('.'),
                'rows': count,
                'kind': kind,
                'columns': columns,
                'size': st.st_size,
                'mtime': int(st.st_mtime),
            })
    # 批次目录倒序（新的在前），同目录内条数多的在前
    out.sort(key=lambda f: (f['folder'], f['name']), reverse=True)
    return out


# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ('/', '/index.html'):
            self.serve_html()
        elif parsed.path == '/api/files':
            self.serve_file_list()
        elif parsed.path == '/api/blob':
            self.serve_blob_list(parsed)
        elif parsed.path in ('/api/file', '/api/xlsx'):
            self.serve_data_file(parsed)
        else:
            self.fail(404, 'not found')

    def serve_html(self):
        path = find_html()
        try:
            with open(path, 'rb') as f:
                self.send_body(200, 'text/html; charset=utf-8', f.read())
        except OSError as e:
            self.fail(500, '看板页面读取失败: %s' % e)

    def serve_file_list(self):
        files = scan_data_root(DATA_ROOT)
        items, error = list_blob_objects()
        body = json.dumps({
            'root': DATA_ROOT,
            'files': files,
            'blob': {
                'total': len(items),
                'groups': blob_summary(items),
                'error': error,
            },
        }, ensure_ascii=False)
        self.send_body(200, 'application/json; charset=utf-8', body.encode('utf-8'))

    def serve_blob_list(self, parsed):
        """列出线上已上传的素材。prefix 为空表示全部（相对 Blender_data/）。"""
        prefix = (urllib.parse.parse_qs(parsed.query).get('prefix') or [''])[0].strip().strip('/')
        items, error = list_blob_objects()
        if error and not items:
            return self.fail(502, '读取线上文件列表失败（%s）。请确认本机能访问 BS3，'
                                  '或改用本地 _blob_urls.csv 查看' % error)
        if prefix:
            items = [it for it in items if it['path'].startswith(prefix + '/')]
        body = json.dumps({'items': items, 'error': error}, ensure_ascii=False)
        self.send_body(200, 'application/json; charset=utf-8', body.encode('utf-8'))

    def serve_data_file(self, parsed):
        src = (urllib.parse.parse_qs(parsed.query).get('src') or [''])[0].strip()
        if not src:
            return self.fail(400, '缺少 src 参数')
        try:
            if src.lower().startswith(('http://', 'https://')):
                data = self.fetch_url(src)
                ext = os.path.splitext(urllib.parse.urlparse(src).path)[1].lower()
            else:
                path = self.resolve_path(src)
                ext = os.path.splitext(path)[1].lower()
                with open(path, 'rb') as f:
                    data = f.read()
        except Exception as e:
            return self.fail(502, str(e))
        self.send_body(200, MIME.get(ext, 'application/octet-stream'), data)

    @staticmethod
    def resolve_path(src):
        """相对路径按数据根目录解析，绝对路径直接用。"""
        if os.path.splitext(src)[1].lower() not in ALLOW_EXT:
            raise RuntimeError('只支持 xlsx / xls / csv / json 文件')
        candidates = [src] if os.path.isabs(src) else \
                     [os.path.join(DATA_ROOT, src), os.path.join(BASE_DIR, src), src]
        for p in candidates:
            p = os.path.normpath(p)
            if os.path.isfile(p):
                return p
        raise RuntimeError('服务所在机器上找不到该文件: %s（请确认服务运行在存放此文件的机器上）' % src)

    @staticmethod
    def fetch_url(url):
        req = urllib.request.Request(url, headers={'User-Agent': 'video-dashboard/2.0'})
        try:
            with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as resp:
                return resp.read()
        except Exception as e:
            raise RuntimeError('下载链接失败（%s），请确认本机能访问该地址' % e)

    def fail(self, code, msg):
        self.send_body(code, 'application/json; charset=utf-8',
                       json.dumps({'error': msg}, ensure_ascii=False).encode('utf-8'))

    def send_body(self, code, ctype, body):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return '127.0.0.1'


def main():
    global DATA_ROOT
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    if len(sys.argv) > 2:
        DATA_ROOT = os.path.abspath(sys.argv[2])

    files = scan_data_root(DATA_ROOT)
    print('看板页面  : http://%s:%d/' % (local_ip(), port))
    print('数据根目录: %s' % DATA_ROOT)
    print('本地清单  : %d 个（素材清单 %d 个，上传 URL 清单 %d 个）'
          % (len(files),
             sum(1 for f in files if f['kind'] == 'items'),
             sum(1 for f in files if f['kind'] == 'urls')))
    for f in files:
        if f['kind'] in ('items', 'urls'):
            print('   %-40s %-6s %d 条' % (f['src'], f['kind'], f['rows']))

    items, error = list_blob_objects()
    if error:
        print('线上文件  : 读取失败（%s）' % error)
    else:
        print('线上文件  : 共 %d 个' % len(items))
        for g in blob_summary(items):
            print('   %-12s %d 个' % (g['name'], g['count']))
    print('按 Ctrl+C 停止')
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    main()
