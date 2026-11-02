#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量文件格式转换服务端（配合微信小程序使用）。

启动：   py app.py        （默认端口 5000）
接口：
  GET  /api/health                     健康检查
  GET  /api/formats                    可用的类别与目标格式
  POST /api/convert  (file, target)    上传并转换，返回下载地址
  GET  /api/download/<task>/<name>     下载转换结果
"""

import os
import re
import time
import uuid
import shutil
import threading
from pathlib import Path
from urllib.parse import quote

from flask import Flask, request, jsonify, send_file, abort
import converter

APP_DIR = Path(__file__).resolve().parent
WORK_DIR = APP_DIR / "work"
WORK_DIR.mkdir(exist_ok=True)

MAX_MB = int(os.environ.get("MAX_MB", "200"))
FILE_TTL = int(os.environ.get("FILE_TTL", "3600"))  # 结果保留秒数

ALLOWED_TARGETS = {
    category: [t for t in targets if t != "不转换"]
    for category, targets in converter.TARGET_CHOICES.items()
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024

_clean_lock = threading.Lock()


def cleanup_expired():
    """删除超过保留时间的任务目录。"""
    now = time.time()
    with _clean_lock:
        for item in WORK_DIR.iterdir():
            if not item.is_dir():
                continue
            try:
                if now - item.stat().st_mtime > FILE_TTL:
                    shutil.rmtree(item, ignore_errors=True)
            except OSError:
                pass


def safe_base(name):
    base = converter.base_name(Path(name))
    base = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", base).strip(" .")
    return base or "converted"


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.errorhandler(413)
def handle_too_large(_error):
    return jsonify(ok=False, error=f"文件超过 {MAX_MB}MB 限制"), 413


@app.get("/")
def index():
    return jsonify(ok=True, service="batch-convert-api", max_mb=MAX_MB)


@app.get("/api/health")
def health():
    return jsonify(ok=True)


@app.get("/api/formats")
def formats():
    return jsonify(ok=True, targets=ALLOWED_TARGETS)


@app.post("/api/convert")
def api_convert():
    cleanup_expired()

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify(ok=False, error="没有收到文件"), 400

    original = (request.form.get("filename") or upload.filename or "").replace("\\", "/")
    original = Path(original).name
    if not Path(original).suffix:
        original = Path(upload.filename.replace("\\", "/")).name
    target = converter.normalize_target(request.form.get("target", ""))
    if not target:
        return jsonify(ok=False, error="缺少目标格式"), 400

    category, src_format = converter.detect_format(original)
    if category is None:
        return jsonify(ok=False, error="不支持的文件格式"), 400
    if target not in ALLOWED_TARGETS[category]:
        return jsonify(ok=False, error=f"{category} 不支持转换为 {target}"), 400
    if target == src_format:
        return jsonify(ok=False, error="目标格式与源格式相同"), 400

    task_id = uuid.uuid4().hex
    task_dir = WORK_DIR / task_id
    task_dir.mkdir(parents=True)

    source_path = task_dir / f"source.{src_format}"
    upload.save(str(source_path))

    notes = []
    try:
        outputs = converter.convert_file(
            source_path, target, task_dir, True, note=notes.append
        )
    except converter.SkipError as exc:
        shutil.rmtree(task_dir, ignore_errors=True)
        return jsonify(ok=False, error=str(exc)), 400
    except converter.ConvertError as exc:
        shutil.rmtree(task_dir, ignore_errors=True)
        return jsonify(ok=False, error=str(exc)), 400
    except Exception as exc:  # 保证接口始终返回 JSON
        shutil.rmtree(task_dir, ignore_errors=True)
        return jsonify(ok=False, error=f"转换失败：{exc}"), 500

    base = safe_base(original)
    renamed = []
    for path in outputs:
        suffix = path.name[len("source"):]
        new_path = task_dir / f"{base}{suffix}"
        index = 1
        while new_path.exists():
            new_path = task_dir / f"{base} ({index}){suffix}"
            index += 1
        path.rename(new_path)
        renamed.append(new_path)

    base_url = request.host_url.rstrip("/")
    files = [{
        "name": path.name,
        "size": path.stat().st_size,
        "url": f"{base_url}/api/download/{task_id}/{quote(path.name)}",
    } for path in renamed]

    return jsonify(
        ok=True,
        task=task_id,
        category=category,
        source_format=src_format,
        target=target,
        notes=notes,
        files=files,
    )


@app.get("/api/download/<task_id>/<path:name>")
def download(task_id, name):
    if not re.fullmatch(r"[0-9a-f]{32}", task_id):
        abort(404)
    task_dir = WORK_DIR / task_id
    if not task_dir.is_dir():
        abort(404)
    target_path = (task_dir / name).resolve()
    try:
        target_path.relative_to(task_dir.resolve())
    except ValueError:
        abort(404)
    if not target_path.is_file():
        abort(404)
    return send_file(str(target_path), as_attachment=True, download_name=target_path.name)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"转换服务已启动：http://127.0.0.1:{port}   （文件上限 {MAX_MB}MB）")
    print("手机访问请把 server 地址改成电脑局域网 IP，或部署到公网 HTTPS 服务器")
    app.run(host="0.0.0.0", port=port, threaded=True)
