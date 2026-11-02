#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量文件格式转换 - 拖拽图形界面

支持四类文件：
  图像    png / jpg / webp / bmp / gif / tif / ico，可同时转成 pdf
  音频    mp3 / wav / flac / aac / m4a / ogg
  视频    mp4 / mkv / mov / webm / avi / gif，也可提取为音频
  文档    txt / md / html / pdf / docx / doc（Word 可用时保留版式）
  压缩包  zip / tar / tar.gz / tar.bz2
"""

import os
import re
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zipfile
import tarfile
import ctypes
from html import escape
from html.parser import HTMLParser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEPS_DIR = BASE_DIR / "deps"

# 优先使用脚本同目录 deps 中的依赖，方便拷贝到其他电脑直接运行
if DEPS_DIR.is_dir():
    sys.path.insert(0, str(DEPS_DIR))
    for _sub in ("win32", os.path.join("win32", "lib"), "pywin32_system32"):
        _p = DEPS_DIR / _sub
        if _p.is_dir():
            sys.path.append(str(_p))
            try:
                os.add_dll_directory(str(_p))
            except AttributeError:
                pass

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:  # 缺少 tkinterdnd2 时仍可用按钮添加文件
    DND_FILES = None
    TkinterDnD = None
    HAS_DND = False

# ----------------------------------------------------------------------------
# 格式定义
# ----------------------------------------------------------------------------
IMAGE_FORMATS = {"png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff", "ico"}
AUDIO_FORMATS = {"mp3", "wav", "flac", "aac", "m4a", "ogg", "wma", "amr"}
VIDEO_FORMATS = {"mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v", "ts", "mpg", "mpeg"}
DOC_FORMATS = {"txt", "md", "markdown", "html", "htm", "pdf", "docx", "doc", "rtf"}
ARCHIVE_FORMATS = {"zip", "tar", "gz", "tgz", "bz2", "tbz2", "7z", "rar", "xz", "txz"}

CATEGORY_LABELS = {
    "image": "图像",
    "audio": "音频",
    "video": "视频",
    "doc": "文档",
    "archive": "压缩包",
}

TARGET_CHOICES = {
    "image": ["不转换", "png", "jpg", "webp", "bmp", "tif", "gif", "ico", "pdf"],
    "audio": ["不转换", "mp3", "wav", "flac", "aac", "m4a", "ogg"],
    "video": ["不转换", "mp4", "mkv", "mov", "webm", "avi", "gif", "mp3", "wav", "flac"],
    "doc": ["不转换", "pdf", "docx", "txt", "md", "html", "png", "jpg"],
    "archive": ["不转换", "zip", "tar", "tar.gz", "tar.bz2"],
}

DEFAULT_TARGETS = {"image": "png", "audio": "mp3", "video": "mp4", "doc": "pdf", "archive": "zip"}

EXT_ALIAS = {"jpeg": "jpg", "tiff": "tif", "markdown": "md", "htm": "html"}

IMAGE_EXT_MAP = {
    "jpg": "JPEG", "png": "PNG", "webp": "WEBP", "bmp": "BMP",
    "gif": "GIF", "tif": "TIFF", "ico": "ICO",
}

AUDIO_CODECS = {
    "mp3": ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
    "wav": ["-vn", "-c:a", "pcm_s16le"],
    "flac": ["-vn", "-c:a", "flac"],
    "aac": ["-vn", "-c:a", "aac", "-b:a", "192k"],
    "m4a": ["-vn", "-c:a", "aac", "-b:a", "192k"],
    "ogg": ["-vn", "-c:a", "libvorbis", "-q:a", "5"],
}

VIDEO_CODECS = {
    "mp4": ["-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"],
    "mkv": ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-c:a", "aac", "-b:a", "160k"],
    "mov": ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-c:a", "aac", "-b:a", "160k"],
    "avi": ["-c:v", "mpeg4", "-q:v", "4", "-c:a", "libmp3lame", "-q:a", "4"],
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", "libopus", "-b:a", "128k"],
}

GIF_FILTER = ("fps=10,scale=480:-1:flags=lanczos,split[s0][s1];"
              "[s0]palettegen[p];[s1][p]paletteuse")


# ----------------------------------------------------------------------------
# 异常
# ----------------------------------------------------------------------------
class ConvertError(Exception):
    """转换失败，可向用户展示的提示。"""


class SkipError(Exception):
    """文件被跳过（例如目标格式与源格式相同）。"""


# ----------------------------------------------------------------------------
# 通用工具
# ----------------------------------------------------------------------------
def normalize_target(target):
    t = target.strip().lower().lstrip(".")
    return EXT_ALIAS.get(t, t)


def detect_format(path):
    """返回 (类别, 规范化格式)。格式不识别时返回 (None, None)。"""
    name = Path(path).name.lower()
    for compound in ("tar.gz", "tar.bz2", "tar.xz"):
        if name.endswith("." + compound):
            return "archive", compound
    for short, compound in (("tgz", "tar.gz"), ("tbz2", "tar.bz2"), ("tbz", "tar.bz2"),
                            ("txz", "tar.xz")):
        if name.endswith("." + short):
            return "archive", compound

    ext = Path(name).suffix.lstrip(".")
    ext = EXT_ALIAS.get(ext, ext)
    if ext in IMAGE_FORMATS:
        return "image", ext
    if ext in AUDIO_FORMATS:
        return "audio", ext
    if ext in VIDEO_FORMATS:
        return "video", ext
    if ext in DOC_FORMATS:
        return "doc", ext
    if ext in ARCHIVE_FORMATS:
        return "archive", ext
    return None, None


def base_name(path):
    name = Path(path).name
    lowered = name.lower()
    for compound in ("tar.gz", "tar.bz2", "tar.xz"):
        if lowered.endswith("." + compound):
            return name[: -(len(compound) + 1)]
    for short in ("tgz", "tbz2", "tbz", "txz"):
        if lowered.endswith("." + short):
            return name[: -(len(short) + 1)]
    return Path(name).stem


def target_path(src, target, out_dir, overwrite, suffix=""):
    """生成输出路径；同名文件自动加序号。"""
    out_root = Path(out_dir) if out_dir else Path(src).parent
    out_root.mkdir(parents=True, exist_ok=True)
    stem = f"{base_name(src)}{suffix}"
    candidate = out_root / f"{stem}.{target}"
    if overwrite:
        return candidate
    index = 1
    while candidate.exists():
        candidate = out_root / f"{stem} ({index}).{target}"
        index += 1
    return candidate


# ----------------------------------------------------------------------------
# 图像转换
# ----------------------------------------------------------------------------
def _flatten_for_opaque(image):
    """把带透明通道的图片铺到白底上，便于保存为 jpg/bmp。"""
    from PIL import Image

    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    return image.convert("RGB")


def convert_image(src, target, out_dir, overwrite, note):
    from PIL import Image, ImageOps

    src = Path(src)
    out = target_path(src, "pdf" if target == "pdf" else target, out_dir, overwrite)

    if target == "pdf":
        convert_image_to_pdf(src, out)
        return [out]

    with Image.open(src) as image:
        frames = getattr(image, "n_frames", 1)
        image = ImageOps.exif_transpose(image)

        if target == "gif" and frames > 1:
            images = []
            durations = []
            for i in range(frames):
                image.seek(i)
                images.append(image.convert("RGBA").convert("P", palette=Image.ADAPTIVE))
                durations.append(image.info.get("duration", 100))
            images[0].save(out, save_all=True, append_images=images[1:],
                           duration=durations, loop=0, optimize=True)
            return [out]

        if frames > 1:
            note(f"源文件含 {frames} 帧，已取第一帧转换")

        if target in ("jpg", "bmp"):
            image = _flatten_for_opaque(image)
        elif target == "ico":
            image = image.convert("RGBA")
            image.thumbnail((256, 256))
        elif image.mode in ("RGBA", "LA"):
            if target not in ("png", "webp", "tif", "gif", "ico"):
                image = image.convert("RGB")

        save_kwargs = {}
        if target == "jpg":
            save_kwargs = {"quality": 90, "optimize": True, "progressive": True}
        elif target == "webp":
            save_kwargs = {"quality": 90, "method": 6}
        elif target == "png":
            save_kwargs = {"optimize": True}
        elif target == "tif":
            save_kwargs = {"compression": "tiff_lzw"}
        elif target == "ico":
            save_kwargs = {"sizes": [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]}
        image.save(out, IMAGE_EXT_MAP[target], **save_kwargs)

    return [out]


def convert_image_to_pdf(src, out):
    try:
        import fitz
    except ImportError as exc:
        raise ConvertError("缺少 PyMuPDF，无法转 PDF") from exc
    from PIL import Image

    with Image.open(src) as image:
        image = image.convert("RGBA")
        width, height = image.size

    with tempfile.TemporaryDirectory() as tmp:
        tmp_png = Path(tmp) / "page.png"
        with Image.open(src) as image:
            image.convert("RGBA").save(tmp_png, "PNG")

        doc = fitz.open()
        page = doc.new_page(width=width * 0.75, height=height * 0.75)
        page.insert_image(page.rect, filename=str(tmp_png))
        doc.save(str(out))
        doc.close()


def convert_pdf_to_images(src, target, out_dir, overwrite, note):
    try:
        import fitz
    except ImportError as exc:
        raise ConvertError("缺少 PyMuPDF，无法读取 PDF") from exc

    outputs = []
    with fitz.open(str(src)) as doc:
        total = doc.page_count
        if total == 0:
            raise ConvertError("PDF 没有可渲染的页面")
        if total > 1:
            note(f"共 {total} 页，将逐页导出图片")
        for index, page in enumerate(doc):
            out = target_path(src, target, out_dir, overwrite, suffix=f"_p{index + 1}")
            pix = page.get_pixmap(dpi=150)
            pix.save(str(out))
            outputs.append(out)
    return outputs


# ----------------------------------------------------------------------------
# 音视频转换
# ----------------------------------------------------------------------------
_FFMPEG_CACHE = None


def find_ffmpeg():
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE:
        return _FFMPEG_CACHE

    candidates = []
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        candidates.append(Path(env_path))
    try:
        import imageio_ffmpeg
        candidates.append(Path(imageio_ffmpeg.get_ffmpeg_exe()))
    except Exception:
        pass
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(Path(found))

    for candidate in candidates:
        if candidate.is_file():
            _FFMPEG_CACHE = str(candidate)
            return _FFMPEG_CACHE
    raise ConvertError("未找到 ffmpeg。请重新运行“安装依赖.bat”，或设置环境变量 FFMPEG_PATH")


def convert_media(src, src_format, target, out_dir, overwrite):
    src = Path(src)
    out = target_path(src, target, out_dir, overwrite)
    ffmpeg = find_ffmpeg()

    if target in AUDIO_CODECS:
        if src_format in AUDIO_FORMATS and target in AUDIO_FORMATS:
            extra = AUDIO_CODECS[target]
        elif src_format in AUDIO_FORMATS:
            extra = AUDIO_CODECS[target]
        else:  # 视频提取音频
            extra = AUDIO_CODECS[target]
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(src)] + extra + [str(out)]
    elif target in VIDEO_CODECS:
        if src_format in AUDIO_FORMATS:
            raise ConvertError("音频文件无法转换成视频")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(src)] + VIDEO_CODECS[target] + [str(out)]
    elif target == "gif":
        if src_format in AUDIO_FORMATS:
            raise ConvertError("音频文件无法转换成 GIF")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(src), "-vf", GIF_FILTER, "-an", "-loop", "0", str(out)]
    else:
        raise ConvertError(f"不支持的目标格式：{target}")

    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        detail = detail[-1] if detail else "未知错误"
        raise ConvertError(f"ffmpeg 转换失败：{detail}")
    return [out]


# ----------------------------------------------------------------------------
# 文档转换
# ----------------------------------------------------------------------------
class _HTMLTextExtractor(HTMLParser):
    SKIP_TAGS = {"script", "style", "head"}
    BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5",
                  "h6", "section", "article", "table", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)

    def text(self):
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in raw.splitlines()]
        merged = []
        for line in lines:
            if line or (merged and merged[-1]):
                merged.append(line)
        return "\n".join(merged).strip()


def _decode_text(data):
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _strip_rtf(text):
    text = re.sub(r"\\'[0-9a-fA-F]{2}", "", text)
    text = re.sub(r"\\u(-?\d+)\s?\?" , "", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", text)
    return text.replace("{", "").replace("}", "").strip()


_WORD_OK = None
_WORD_LOCK = threading.Lock()


def word_available():
    global _WORD_OK
    if _WORD_OK is not None:
        return _WORD_OK
    try:
        import win32com.client  # noqa: F401
        _WORD_OK = True
    except Exception:
        _WORD_OK = False
    return _WORD_OK


def word_save_as(src, out, word_format, retries=2):
    """用本机 Word 转换，保留原始版式。word_format: 17=PDF, 16=docx, 2=txt。

    转换在后台线程中执行，COM 需要按线程初始化，因此这里自行 CoInitialize。
    """
    import pythoncom
    import win32com.client

    last_error = None
    for attempt in range(retries):
        pythoncom.CoInitialize()
        app = None
        try:
            with _WORD_LOCK:
                app = win32com.client.DispatchEx("Word.Application")
                app.Visible = False
                try:
                    app.DisplayAlerts = 0
                except Exception:
                    pass
                try:
                    app.Options.ConfirmConversions = False
                except Exception:
                    pass
                doc = app.Documents.Open(str(src), ReadOnly=True, AddToRecentFiles=False)
                try:
                    if word_format == 2:
                        doc.SaveAs2(str(out), FileFormat=2, Encoding=65001)
                    else:
                        doc.SaveAs2(str(out), FileFormat=word_format)
                finally:
                    doc.Close(False)
            return
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(1.0)
        finally:
            if app is not None:
                try:
                    app.Quit()
                except Exception:
                    pass
            pythoncom.CoUninitialize()
    raise ConvertError(f"Word 调用失败：{last_error}")


def read_text(src, src_format):
    src = Path(src)
    if src_format in ("txt", "md", "markdown"):
        return _decode_text(src.read_bytes())
    if src_format in ("html", "htm"):
        parser = _HTMLTextExtractor()
        parser.feed(_decode_text(src.read_bytes()))
        return parser.text()
    if src_format == "docx":
        try:
            import docx
        except ImportError as exc:
            raise ConvertError("缺少 python-docx，无法读取 docx") from exc
        document = docx.Document(str(src))
        lines = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                lines.append("\t".join(cell.text.strip() for cell in row.cells))
        return "\n".join(lines).strip()
    if src_format == "pdf":
        try:
            import fitz
        except ImportError as exc:
            raise ConvertError("缺少 PyMuPDF，无法读取 PDF") from exc
        with fitz.open(str(src)) as doc:
            return "\n\n".join(page.get_text() for page in doc).strip()
    if src_format in ("doc", "rtf"):
        if word_available():
            with tempfile.TemporaryDirectory() as tmp:
                tmp_txt = Path(tmp) / "out.txt"
                try:
                    word_save_as(src, tmp_txt, 2)
                    return _decode_text(tmp_txt.read_bytes())
                except Exception:
                    pass
        if src_format == "rtf":
            return _strip_rtf(_decode_text(src.read_bytes()))
        raise ConvertError("读取 .doc 需要本机安装 Word")
    raise ConvertError(f"无法读取格式：{src_format}")


def write_text_docx(text, out):
    try:
        import docx
        from docx.oxml.ns import qn
    except ImportError as exc:
        raise ConvertError("缺少 python-docx，无法生成 docx") from exc

    document = docx.Document()
    normal = document.styles["Normal"]
    normal.font.name = "微软雅黑"
    normal.font.size = docx.shared.Pt(10.5)
    try:
        normal.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    except Exception:
        pass
    for line in text.splitlines():
        document.add_paragraph(line)
    document.save(str(out))


def _char_width(ch, font_size):
    return font_size if ord(ch) > 0x2E80 else font_size * 0.55


def _wrap_lines(text, font_size, max_width):
    lines = []
    for paragraph in text.splitlines():
        if not paragraph.strip():
            lines.append("")
            continue
        current = ""
        width = 0.0
        for ch in paragraph:
            w = _char_width(ch, font_size)
            if width + w > max_width and current:
                lines.append(current)
                current = ch
                width = w
            else:
                current += ch
                width += w
        if current:
            lines.append(current)
    return lines


def write_text_pdf(text, out):
    try:
        import fitz
    except ImportError as exc:
        raise ConvertError("缺少 PyMuPDF，无法生成 PDF") from exc

    margin = 56
    font_size = 10.5
    leading = 15
    page_width, page_height = 595, 842
    lines = _wrap_lines(text, font_size, page_width - 2 * margin)
    per_page = int((page_height - 2 * margin) / leading)
    per_page = max(per_page, 1)

    doc = fitz.open()
    try:
        if not lines:
            lines = [""]
        for start in range(0, len(lines), per_page):
            page = doc.new_page(width=page_width, height=page_height)
            y = margin + font_size
            for line in lines[start:start + per_page]:
                if line:
                    page.insert_text((margin, y), line, fontname="china-s",
                                     fontsize=font_size, color=(0, 0, 0))
                y += leading
        doc.save(str(out))
    finally:
        doc.close()


def write_text_html(text, out):
    body = "\n".join(f"<p>{escape(line)}</p>" if line.strip() else "<p>&nbsp;</p>"
                     for line in text.splitlines())
    html = ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
            f"<title>{escape(Path(out).stem)}</title>\n"
            "<style>body{font-family:'Microsoft YaHei',sans-serif;line-height:1.7;"
            "max-width:820px;margin:40px auto;padding:0 16px;color:#222;}"
            "p{margin:0 0 .6em;}</style>\n</head>\n<body>\n"
            f"{body}\n</body>\n</html>\n")
    Path(out).write_text(html, encoding="utf-8")


def _short_error(exc, limit=80):
    text = str(exc).strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


def convert_document(src, src_format, target, out_dir, overwrite, note):
    src = Path(src)

    if target in ("png", "jpg"):
        if src_format != "pdf":
            raise ConvertError("文档转图片目前仅支持 PDF 源文件")
        return convert_pdf_to_images(src, target, out_dir, overwrite, note)

    out = target_path(src, target, out_dir, overwrite)

    if target == "pdf" and src_format in ("docx", "doc", "rtf"):
        if word_available():
            try:
                word_save_as(src, out, 17)
                return [out]
            except Exception as exc:
                note(f"Word 转换失败（{_short_error(exc)}），已改用纯文本排版")
        write_text_pdf(read_text(src, src_format), out)
        note("已按纯文本生成 PDF，原版式未保留")
        return [out]

    if target == "docx" and src_format == "pdf":
        if word_available():
            try:
                word_save_as(src, out, 16)
                note("已用 Word 转换，保留原版式")
                return [out]
            except Exception as exc:
                note(f"Word 转换失败（{_short_error(exc)}），已改用纯文本方式")
        write_text_docx(read_text(src, src_format), out)
        note("仅保留文字内容，图片与版式未保留")
        return [out]

    text = read_text(src, src_format)

    if target == "txt":
        out.write_text(text, encoding="utf-8")
    elif target == "md":
        out.write_text(text, encoding="utf-8")
    elif target == "html":
        write_text_html(text, out)
    elif target == "docx":
        write_text_docx(text, out)
    elif target == "pdf":
        write_text_pdf(text, out)
        note("已按纯文本排版生成 PDF")
    else:
        raise ConvertError(f"不支持的目标格式：{target}")
    return [out]


# ----------------------------------------------------------------------------
# 压缩包转换
# ----------------------------------------------------------------------------
def _safe_extract_zip(src, dest):
    with zipfile.ZipFile(src) as zf:
        for info in zf.infolist():
            name = info.filename
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in Path(name).parts:
                raise ConvertError("压缩包中包含不安全的路径，已拒绝解压")
        zf.extractall(dest)


def _safe_extract_tar(src, dest):
    with tarfile.open(src, "r:*") as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:  # 旧版本 Python 没有 filter 参数
            for member in tf.getmembers():
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise ConvertError("压缩包中包含不安全的路径，已拒绝解压")
            tf.extractall(dest)


def _pack_archive(root, target, out):
    files = sorted(p for p in root.rglob("*") if p.is_file())
    if target == "zip":
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                zf.write(path, path.relative_to(root).as_posix())
    elif target in ("tar", "tar.gz", "tar.bz2"):
        mode = {"tar": "w", "tar.gz": "w:gz", "tar.bz2": "w:bz2"}[target]
        with tarfile.open(out, mode) as tf:
            for path in files:
                tf.add(path, arcname=path.relative_to(root).as_posix())
    else:
        raise ConvertError(f"不支持的目标格式：{target}")


def convert_archive(src, src_format, target, out_dir, overwrite):
    src = Path(src)
    if src_format in ("7z", "rar"):
        raise ConvertError("暂不支持 7z / rar，请先解压后再转换")
    out = target_path(src, target, out_dir, overwrite)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "content"
        root.mkdir()
        if src_format == "zip":
            _safe_extract_zip(src, root)
        elif src_format in ("tar", "tar.gz", "tar.bz2", "tar.xz"):
            _safe_extract_tar(src, root)
        else:
            raise ConvertError(f"暂不支持读取 {src_format}")
        _pack_archive(root, target, out)
    return [out]


# ----------------------------------------------------------------------------
# 统一入口
# ----------------------------------------------------------------------------
def convert_file(src, target, out_dir=None, overwrite=False, note=None):
    """转换单个文件，返回输出文件路径列表。"""
    note = note or (lambda _msg: None)
    src = Path(src)
    target = normalize_target(target)
    if not src.is_file():
        raise ConvertError("文件不存在")

    category, src_format = detect_format(src)
    if category is None:
        raise ConvertError("不支持的文件格式")

    if target == src_format:
        raise SkipError("目标格式与源格式相同")

    if category == "image":
        if target in IMAGE_FORMATS or target == "pdf":
            return convert_image(src, target, out_dir, overwrite, note)
        raise ConvertError(f"图像无法转换为 {target}")

    if category == "audio":
        if target in AUDIO_FORMATS:
            return convert_media(src, src_format, target, out_dir, overwrite)
        raise ConvertError(f"音频无法转换为 {target}")

    if category == "video":
        if target in AUDIO_FORMATS or target in VIDEO_FORMATS or target == "gif":
            return convert_media(src, src_format, target, out_dir, overwrite)
        raise ConvertError(f"视频无法转换为 {target}")

    if category == "doc":
        return convert_document(src, src_format, target, out_dir, overwrite, note)

    if category == "archive":
        if target in ("zip", "tar", "tar.gz", "tar.bz2"):
            return convert_archive(src, src_format, target, out_dir, overwrite)
        raise ConvertError(f"压缩包无法转换为 {target}")

    raise ConvertError("不支持的文件类型")


# ----------------------------------------------------------------------------
# 图形界面
# ----------------------------------------------------------------------------
class ConverterApp:
    BG = "#eef2f7"
    CARD = "#ffffff"
    ACCENT = "#2563eb"
    TEXT = "#1f2937"
    MUTED = "#6b7280"

    def __init__(self, root, ui_scale=1.0):
        self.root = root
        self.scale = ui_scale
        self.items = {}          # 绝对路径 -> 类别
        self.target_vars = {}
        self.event_queue = queue.Queue()
        self.running = False

        self.root.title("批量文件格式转换")
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1240, screen_w - 80)
        height = min(1350, screen_h - 120)
        x = max(10, (screen_w - width) // 2)
        y = max(10, (screen_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(1000, width), min(900, height))
        self.root.configure(bg=self.BG)

        self._setup_style()
        self._build_ui()
        self._setup_dnd()
        self.root.after(120, self._drain_events)

    # ---------- 样式 ----------
    def _setup_style(self):
        self.font = ("Microsoft YaHei UI", 10)
        self.font_bold = ("Microsoft YaHei UI", 11, "bold")
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("TLabel", background=self.BG, foreground=self.TEXT, font=self.font)
        style.configure("Card.TFrame", background=self.CARD)
        style.configure("TButton", font=self.font)
        style.configure("TCombobox", font=self.font)
        style.configure("TCheckbutton", background=self.BG, font=self.font)
        style.configure("Treeview", font=self.font, rowheight=int(26 * self.scale))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

    # ---------- 界面 ----------
    def _build_ui(self):
        outer = tk.Frame(self.root, bg=self.BG)
        outer.pack(fill="both", expand=True, padx=16, pady=14)

        header = tk.Frame(outer, bg=self.BG)
        header.pack(fill="x")
        tk.Label(header, text="批量文件格式转换", bg=self.BG, fg=self.TEXT,
                 font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        tk.Label(header, text="拖入文件或文件夹，选择目标格式，一键批量转换",
                 bg=self.BG, fg=self.MUTED, font=self.font).pack(side="left", padx=12, pady=(6, 0))

        # 拖拽区
        self.drop_label = tk.Label(
            outer,
            text="把文件或文件夹拖到这里\n也可以点击此处选择文件",
            bg="#f8fafc", fg=self.MUTED, font=("Microsoft YaHei UI", 11),
            relief="groove", bd=2, height=3, cursor="hand2",
            justify="center", anchor="center",
        )
        self.drop_label.pack(fill="x", pady=(12, 8))
        self.drop_label.bind("<Button-1>", lambda _e: self.add_files())

        drop_hint = ("支持拖拽添加" if HAS_DND else
                     "当前环境未启用拖拽，请用下方按钮添加文件（安装 tkinterdnd2 后可用拖拽）")
        tk.Label(outer, text=drop_hint, bg=self.BG, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w")

        # 队列
        queue_card = tk.Frame(outer, bg=self.CARD, bd=0, highlightthickness=1,
                              highlightbackground="#dbdbdb")
        queue_card.pack(fill="both", expand=True, pady=(6, 10))

        toolbar = tk.Frame(queue_card, bg=self.CARD)
        toolbar.pack(fill="x", padx=10, pady=8)
        self.btn_add_files = ttk.Button(toolbar, text="添加文件", command=self.add_files)
        self.btn_add_files.pack(side="left")
        self.btn_add_folder = ttk.Button(toolbar, text="添加文件夹", command=self.add_folder)
        self.btn_add_folder.pack(side="left", padx=6)
        self.btn_remove = ttk.Button(toolbar, text="移除选中", command=self.remove_selected)
        self.btn_remove.pack(side="left")
        self.btn_clear = ttk.Button(toolbar, text="清空列表", command=self.clear_all)
        self.btn_clear.pack(side="left", padx=6)
        self.count_label = tk.Label(toolbar, text="共 0 个文件", bg=self.CARD,
                                    fg=self.MUTED, font=self.font)
        self.count_label.pack(side="right")

        table_wrap = tk.Frame(queue_card, bg=self.CARD)
        table_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tree = ttk.Treeview(table_wrap, columns=("cat", "target"), show="tree headings",
                                 selectmode="extended")
        self.tree.heading("#0", text="文件")
        self.tree.heading("cat", text="类别")
        self.tree.heading("target", text="目标格式")
        self.tree.column("#0", width=620, anchor="w")
        self.tree.column("cat", width=90, anchor="center", stretch=False)
        self.tree.column("target", width=110, anchor="center", stretch=False)
        scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # 目标格式
        format_card = tk.Frame(outer, bg=self.CARD, bd=0, highlightthickness=1,
                               highlightbackground="#dbdbdb")
        format_card.pack(fill="x", pady=(0, 10))
        tk.Label(format_card, text="目标格式", bg=self.CARD, fg=self.TEXT,
                 font=self.font_bold).pack(anchor="w", padx=12, pady=(10, 2))
        format_row = tk.Frame(format_card, bg=self.CARD)
        format_row.pack(fill="x", padx=8, pady=(0, 10))
        for index, (category, label) in enumerate(CATEGORY_LABELS.items()):
            cell = tk.Frame(format_row, bg=self.CARD)
            cell.grid(row=0, column=index, padx=4, sticky="w")
            tk.Label(cell, text=label, bg=self.CARD, fg=self.MUTED,
                     font=("Microsoft YaHei UI", 9)).pack(anchor="w")
            var = tk.StringVar(value=DEFAULT_TARGETS[category])
            combo = ttk.Combobox(cell, textvariable=var, values=TARGET_CHOICES[category],
                                 width=8, state="readonly")
            combo.pack()
            combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_targets())
            self.target_vars[category] = var
        tk.Label(format_card, text="列表中的文件会按所属类别使用对应目标格式；选“不转换”则跳过该类别。",
                 bg=self.CARD, fg=self.MUTED, font=("Microsoft YaHei UI", 9)).pack(
            anchor="w", padx=12, pady=(0, 10))

        # 输出设置
        option_card = tk.Frame(outer, bg=self.CARD, bd=0, highlightthickness=1,
                               highlightbackground="#dbdbdb")
        option_card.pack(fill="x", pady=(0, 10))
        option_row = tk.Frame(option_card, bg=self.CARD)
        option_row.pack(fill="x", padx=12, pady=10)
        tk.Label(option_row, text="输出目录", bg=self.CARD, fg=self.TEXT,
                 font=self.font).pack(side="left")
        self.out_dir_var = tk.StringVar(value="")
        self.out_entry = ttk.Entry(option_row, textvariable=self.out_dir_var)
        self.out_entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(option_row, text="浏览", command=self.browse_out_dir).pack(side="left")
        ttk.Button(option_row, text="清空", command=lambda: self.out_dir_var.set("")).pack(
            side="left", padx=6)

        option_row2 = tk.Frame(option_card, bg=self.CARD)
        option_row2.pack(fill="x", padx=12, pady=(0, 10))
        tk.Label(option_row2, text="输出目录留空时，转换结果保存在源文件旁边。",
                 bg=self.CARD, fg=self.MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.overwrite_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(option_row2, text="覆盖同名文件", variable=self.overwrite_var).pack(
            side="right")
        self.recursive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(option_row2, text="文件夹包含子文件夹",
                        variable=self.recursive_var).pack(side="right", padx=12)

        # 进度与日志
        bottom = tk.Frame(outer, bg=self.BG)
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x", pady=(2, 4))
        self.status_label = tk.Label(bottom, text="就绪", bg=self.BG, fg=self.MUTED,
                                     font=self.font, anchor="w")
        self.status_label.pack(fill="x")

        self.log = ScrolledText(outer, height=5, font=("Consolas", 9), relief="groove",
                                bd=1, bg="#fbfbfb", fg="#374151", state="disabled")
        self.log.pack(fill="both", expand=False, pady=(8, 10))

        action_row = tk.Frame(outer, bg=self.BG)
        action_row.pack(fill="x")
        self.start_button = tk.Button(
            action_row, text="开始转换", command=self.start, bg=self.ACCENT, fg="white",
            activebackground="#1d4ed8", activeforeground="white", relief="flat",
            font=("Microsoft YaHei UI", 11, "bold"), padx=26, pady=8, cursor="hand2",
        )
        self.start_button.pack(side="right")
        tk.Button(action_row, text="打开输出目录", command=self.open_output_dir,
                  relief="groove", font=self.font, padx=14, pady=8, cursor="hand2").pack(
            side="right", padx=10)

        self._refresh_targets()

    # ---------- 拖拽 ----------
    def _setup_dnd(self):
        if not HAS_DND:
            return
        try:
            for widget in (self.root, self.drop_label, self.tree):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
        except Exception as exc:
            self.log_line(f"拖拽功能初始化失败：{exc}", "error")

    def _on_drop(self, event):
        try:
            paths = self.root.tk.splitlist(event.data)
        except tk.TclError:
            paths = [event.data]
        self.add_paths(paths)
        return "break"

    # ---------- 队列管理 ----------
    def add_files(self):
        paths = filedialog.askopenfilenames(title="选择要转换的文件")
        if paths:
            self.add_paths(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title="选择文件夹")
        if folder:
            self.add_paths([folder])

    def add_paths(self, paths):
        added = 0
        skipped = 0
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                pattern = "**/*" if self.recursive_var.get() else "*"
                files = sorted(p for p in path.glob(pattern) if p.is_file())
                for file_path in files:
                    if self._add_single(file_path):
                        added += 1
                    else:
                        skipped += 1
            elif path.is_file():
                if self._add_single(path):
                    added += 1
                else:
                    skipped += 1
            else:
                skipped += 1

        self._refresh_targets()
        if skipped:
            self.log_line(f"已忽略 {skipped} 个不支持的文件", "note")
        if added:
            self.log_line(f"已添加 {added} 个文件", "info")
        self.update_count()

    def _add_single(self, path):
        category, _fmt = detect_format(path)
        if category is None:
            return False
        key = str(path.resolve())
        if key in self.items:
            return False
        self.items[key] = category
        self.tree.insert("", "end", iid=key, text=str(path), values=(CATEGORY_LABELS[category], ""))
        return True

    def remove_selected(self):
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.items.pop(iid, None)
        self.update_count()

    def clear_all(self):
        if self.running:
            return
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self.update_count()

    def update_count(self):
        self.count_label.config(text=f"共 {len(self.items)} 个文件")

    def _refresh_targets(self):
        for iid, category in self.items.items():
            if not self.tree.exists(iid):
                continue
            target = self.target_vars[category].get()
            shown = "跳过" if target == "不转换" else target
            self.tree.set(iid, "target", shown)

    def browse_out_dir(self):
        folder = filedialog.askdirectory(title="选择输出目录")
        if folder:
            self.out_dir_var.set(folder)

    def open_output_dir(self):
        folder = self.out_dir_var.get().strip()
        if not folder:
            messagebox.showinfo("提示", "输出目录为空时，结果保存在源文件旁边。")
            return
        path = Path(folder)
        if not path.is_dir():
            messagebox.showwarning("提示", "输出目录不存在")
            return
        os.startfile(str(path))

    # ---------- 日志 ----------
    def log_line(self, text, level="info"):
        tag_color = {"info": "#374151", "ok": "#15803d",
                     "error": "#b91c1c", "note": "#a16207"}.get(level, "#374151")
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        line_start = self.log.index("end-2l")
        self.log.tag_configure(level, foreground=tag_color)
        self.log.tag_add(level, line_start, "end-1c")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---------- 转换 ----------
    def start(self):
        if self.running:
            return
        jobs = []
        for iid, category in self.items.items():
            target = self.target_vars[category].get()
            if target == "不转换":
                continue
            jobs.append((iid, target, category))

        if not jobs:
            messagebox.showinfo("提示", "请先添加文件，并至少为一个类别选择目标格式。")
            return

        out_dir = self.out_dir_var.get().strip() or None
        if out_dir and not Path(out_dir).is_dir():
            messagebox.showwarning("提示", "输出目录不存在，请重新选择。")
            return

        overwrite = self.overwrite_var.get()
        self.running = True
        self._set_controls_enabled(False)
        self.progress.configure(maximum=len(jobs), value=0)
        self.status_label.config(text=f"正在转换 0/{len(jobs)}")
        self.log_line(f"开始转换，共 {len(jobs)} 个文件", "info")

        thread = threading.Thread(target=self._worker, args=(jobs, out_dir, overwrite),
                                  daemon=True)
        thread.start()

    def _worker(self, jobs, out_dir, overwrite):
        success = 0
        skipped = 0
        failed = 0
        for index, (src, target, _category) in enumerate(jobs, 1):
            name = Path(src).name
            try:
                outputs = convert_file(
                    src, target, out_dir, overwrite,
                    note=lambda msg, n=name: self.event_queue.put(("log", f"    {n}：{msg}", "note")),
                )
                success += 1
                result = "、".join(p.name for p in outputs)
                self.event_queue.put(("log", f"[成功] {name} -> {result}", "ok"))
            except SkipError:
                skipped += 1
                self.event_queue.put(("log", f"[跳过] {name}：目标格式与源格式相同", "note"))
            except ConvertError as exc:
                failed += 1
                self.event_queue.put(("log", f"[失败] {name}：{exc}", "error"))
            except Exception:  # 未预期的异常也不中断整批任务
                failed += 1
                detail = traceback.format_exc().strip().splitlines()[-1]
                self.event_queue.put(("log", f"[失败] {name}：{detail}", "error"))
            self.event_queue.put(("progress", index, len(jobs)))
        self.event_queue.put(("done", success, skipped, failed))

    def _drain_events(self):
        try:
            while True:
                event = self.event_queue.get_nowait()
                kind = event[0]
                if kind == "log":
                    self.log_line(event[1], event[2])
                elif kind == "progress":
                    done, total = event[1], event[2]
                    self.progress.configure(value=done)
                    self.status_label.config(text=f"正在转换 {done}/{total}")
                elif kind == "done":
                    success, skipped, failed = event[1], event[2], event[3]
                    self.running = False
                    self._set_controls_enabled(True)
                    self.status_label.config(
                        text=f"完成：成功 {success}，跳过 {skipped}，失败 {failed}")
                    self.log_line(
                        f"全部结束：成功 {success}，跳过 {skipped}，失败 {failed}", "info")
                    messagebox.showinfo(
                        "转换完成",
                        f"成功 {success} 个，跳过 {skipped} 个，失败 {failed} 个。")
        except queue.Empty:
            pass
        self.root.after(120, self._drain_events)

    def _set_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for widget in (self.btn_add_files, self.btn_add_folder, self.btn_remove, self.btn_clear):
            widget.configure(state=state)
        if enabled:
            self.start_button.configure(state="normal", bg=self.ACCENT)
        else:
            self.start_button.configure(state="disabled", bg="#93a3b8")


def enable_dpi_awareness():
    """让窗口按物理像素渲染，避免高分屏缩放后窗口超出屏幕。返回缩放倍数。"""
    if sys.platform != "win32":
        return 1.0
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
    except Exception:
        dpi = 96
    return max(1.0, dpi / 96.0)


def main():
    ui_scale = min(enable_dpi_awareness(), 1.6)
    if TkinterDnD is not None:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.3333 * ui_scale)
    except tk.TclError:
        pass
    app = ConverterApp(root, ui_scale)
    app.log_line("准备就绪。图像用 Pillow，音视频用 ffmpeg，文档/压缩包用内置模块。", "note")
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        detail = traceback.format_exc()
        try:
            messagebox.showerror("启动失败", detail)
        except Exception:
            pass
        raise
