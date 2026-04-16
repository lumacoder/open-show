#!/usr/bin/env python3
"""
OpenShow — 将 Markdown / Word / HTML / URL 转换为单个可播放 HTML 幻灯片
"""

import argparse
import base64
import io
import json
import mimetypes
import os
import re
import sys
import textwrap
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup, NavigableString, Tag

# ---------------------------------------------------------------------------
# 依赖适配
# ---------------------------------------------------------------------------
try:
    import markdown
except ImportError:
    markdown = None  # type: ignore

try:
    import requests
except ImportError:
    requests = None  # type: ignore

try:
    import docx
except ImportError:
    docx = None  # type: ignore


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class Block:
    type: str  # heading, paragraph, image, list, code, quote, other
    html: str
    level: int = 0  # for heading
    text: str = ""


@dataclass
class Slide:
    blocks: List[Block] = field(default_factory=list)
    layout: str = "text"
    idx: int = 0


# ---------------------------------------------------------------------------
# 内容提取
# ---------------------------------------------------------------------------
def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _to_data_uri(src: str, base_path: Optional[str] = None) -> str:
    """将本地图片或远程图片转为 data URI，失败则返回原 src。"""
    if src.startswith("data:"):
        return src

    # 远程 URL
    if src.startswith("http://") or src.startswith("https://"):
        if requests is None:
            return src
        try:
            r = requests.get(src, timeout=15)
            r.raise_for_status()
            mime = r.headers.get("Content-Type", mimetypes.guess_type(src)[0] or "image/png")
            b64 = base64.b64encode(r.content).decode("ascii")
            return f"data:{mime};base64,{b64}"
        except Exception:
            return src

    # 本地路径
    if base_path:
        local = Path(base_path).parent / src
        if not local.exists():
            local = Path(src)
        if local.exists():
            mime = mimetypes.guess_type(str(local))[0] or "application/octet-stream"
            with open(local, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f"data:{mime};base64,{b64}"
    return src


def _soup_to_blocks(soup: BeautifulSoup, base_path: Optional[str] = None) -> List[Block]:
    """将 BeautifulSoup 中的正文元素提取为 Block 列表。"""
    blocks: List[Block] = []

    # 清理导航/广告
    for tag_name in ["script", "style", "nav", "footer", "header", "aside", "noscript"]:
        for t in soup.find_all(tag_name):
            t.decompose()

    # 先把所有图片转 data-uri
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if src:
            img["src"] = _to_data_uri(src, base_path)

    # 块级元素名单
    BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "ul", "ol", "pre", "img", "blockquote", "table"}

    def _is_block_tag(node) -> bool:
        return isinstance(node, Tag) and node.name in BLOCK_TAGS

    def _walk(node):
        if isinstance(node, NavigableString):
            txt = str(node).strip()
            if txt:
                blocks.append(Block(type="paragraph", html=f"<p>{txt}</p>", text=txt))
            return
        if not isinstance(node, Tag):
            return
        name = node.name
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(name[1])
            text = node.get_text(strip=True)
            blocks.append(Block(type="heading", html=str(node), level=level, text=text))
        elif name == "img":
            blocks.append(Block(type="image", html=str(node)))
        elif name in ("ul", "ol"):
            text = node.get_text(strip=True)
            blocks.append(Block(type="list", html=str(node), text=text))
        elif name == "pre":
            text = node.get_text(strip=True)
            blocks.append(Block(type="code", html=str(node), text=text))
        elif name == "blockquote":
            text = node.get_text(strip=True)
            blocks.append(Block(type="quote", html=str(node), text=text))
        elif name in ("p", "div", "span", "a", "strong", "em", "b", "i"):
            # 如果内部含有块级子元素，递归处理；否则整体作为段落
            has_block_child = any(_is_block_child(c) for c in node.children)
            if has_block_child:
                for child in node.children:
                    _walk(child)
            else:
                text = node.get_text(strip=True)
                if text:
                    inner = "".join(str(c) for c in node.contents)
                    blocks.append(Block(type="paragraph", html=f"<p>{inner}</p>", text=text))
        elif name in ("section", "article", "main", "figure", "figcaption"):
            for child in node.children:
                _walk(child)
        else:
            # 其他标签若含有块级子元素，递归；否则作为 other
            has_block_child = any(_is_block_child(c) for c in node.children)
            if has_block_child:
                for child in node.children:
                    _walk(child)
            else:
                text = node.get_text(strip=True)
                if text:
                    blocks.append(Block(type="other", html=str(node), text=text))

    def _is_block_child(child) -> bool:
        return isinstance(child, Tag) and child.name in BLOCK_TAGS

    body = soup.find("body") or soup
    for child in body.children:
        _walk(child)

    # 合并相邻的同类型短段落
    merged: List[Block] = []
    for b in blocks:
        if b.type == "paragraph" and merged and merged[-1].type == "paragraph":
            merged[-1].html = f"<p>{merged[-1].text}<br><br>{b.text}</p>"
            merged[-1].text += "\n\n" + b.text
        else:
            merged.append(b)
    return merged


def _extract_main_content(soup: BeautifulSoup) -> BeautifulSoup:
    """启发式提取正文容器（最大文本密度的 div/article/main/section）。"""
    candidates = []
    for tag in soup.find_all(["article", "main", "div", "section"]):
        text_len = len(tag.get_text(strip=True))
        link_text = sum(len(a.get_text(strip=True)) for a in tag.find_all("a"))
        score = text_len - link_text * 2
        if text_len > 200:
            candidates.append((score, tag))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return BeautifulSoup(str(candidates[0][1]), "html.parser")
    return soup


def parse_markdown(path: str) -> List[Block]:
    if markdown is None:
        raise RuntimeError("缺少依赖: markdown。请运行 pip install markdown")
    text = _read_text(path)
    html = markdown.markdown(text, extensions=["tables", "fenced_code"])
    soup = BeautifulSoup(html, "html.parser")
    return _soup_to_blocks(soup, base_path=path)


def parse_docx(path: str) -> List[Block]:
    if docx is None:
        raise RuntimeError("缺少依赖: python-docx。请运行 pip install python-docx")
    document = docx.Document(path)
    blocks: List[Block] = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style_name = para.style.name if para.style else ""
        if style_name.startswith("Heading"):
            level = 1
            try:
                level = int(style_name.replace("Heading", "").strip())
            except ValueError:
                level = 1
            blocks.append(
                Block(type="heading", html=f"<h{level}>{text}</h{level}>", level=level, text=text)
            )
        else:
            blocks.append(Block(type="paragraph", html=f"<p>{text}</p>", text=text))

    # 尝试提取图片
    try:
        import zipfile

        docx_zip = zipfile.ZipFile(path)
        media_files = [n for n in docx_zip.namelist() if n.startswith("word/media/")]
        for mf in media_files[:5]:
            data = docx_zip.read(mf)
            mime = mimetypes.guess_type(mf)[0] or "image/png"
            b64 = base64.b64encode(data).decode("ascii")
            blocks.append(Block(type="image", html=f'<img src="data:{mime};base64,{b64}" alt="">'))
    except Exception:
        pass
    return blocks


def parse_html(path: str) -> List[Block]:
    text = _read_text(path)
    soup = BeautifulSoup(text, "html.parser")
    main = _extract_main_content(soup)
    return _soup_to_blocks(main, base_path=path)


def parse_text(path: str) -> List[Block]:
    text = _read_text(path)
    blocks: List[Block] = []
    # 简单按空行分块，每段作为一个 paragraph
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        # 若整行是标题格式（# 开头），作为 heading
        if para.startswith("# "):
            blocks.append(Block(type="heading", html=f"<h1>{para[2:]}</h1>", level=1, text=para[2:]))
        elif para.startswith("## "):
            blocks.append(Block(type="heading", html=f"<h2>{para[3:]}</h2>", level=2, text=para[3:]))
        elif para.startswith("### "):
            blocks.append(Block(type="heading", html=f"<h3>{para[4:]}</h3>", level=3, text=para[4:]))
        else:
            blocks.append(Block(type="paragraph", html=f"<p>{para}</p>", text=para))
    return blocks


def parse_url(url: str) -> List[Block]:
    if requests is None:
        raise RuntimeError("缺少依赖: requests。请运行 pip install requests")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; OpenShow/1.0)"}
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
    except Exception:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = requests.get(url, headers=headers, timeout=20, verify=False)
        r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "html.parser")
    main = _extract_main_content(soup)
    for img in main.find_all("img"):
        src = img.get("src") or ""
        if src and not src.startswith(("http://", "https://", "data:")):
            img["src"] = urllib.parse.urljoin(url, src)
    return _soup_to_blocks(main, base_path=None)


def parse_pdf(path: str) -> List[Block]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise RuntimeError("缺少依赖: PyMuPDF。请运行 pip install pymupdf")

    doc = fitz.open(path)
    blocks: List[Block] = []
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        b64 = base64.b64encode(img_bytes).decode("ascii")
        blocks.append(
            Block(
                type="image",
                html=f'<img src="data:image/png;base64,{b64}" alt="Page {page_num + 1}">',
                text=f"Page {page_num + 1}",
            )
        )
    return blocks


def parse_input(src: str) -> Tuple[List[Block], str]:
    """返回 blocks 和来源标题（用于文件名）。"""
    src = src.strip()
    # 处理 file:// 前缀
    if src.startswith("file://"):
        src = src[7:]

    if src.startswith(("http://", "https://")):
        title = urllib.parse.urlparse(src).netloc.replace(".", "_")
        return parse_url(src), title

    p = Path(src)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {src}")

    suffix = p.suffix.lower()
    title = p.stem
    if suffix in (".md", ".markdown"):
        return parse_markdown(str(p)), title
    elif suffix == ".docx":
        return parse_docx(str(p)), title
    elif suffix in (".html", ".htm"):
        return parse_html(str(p)), title
    elif suffix == ".pdf":
        return parse_pdf(str(p)), title
    elif suffix == ".txt":
        return parse_text(str(p)), title
    else:
        return parse_markdown(str(p)), title


# ---------------------------------------------------------------------------
# 分页算法
# ---------------------------------------------------------------------------
def _count_words(blocks: List[Block]) -> int:
    return sum(len(b.text) for b in blocks)


def _count_images(blocks: List[Block]) -> int:
    return sum(1 for b in blocks if b.type == "image")


def _split_long_paragraphs(blocks: List[Block], max_words: int = 300) -> List[Block]:
    """将超过 max_words 的单个 paragraph 按句子拆分。"""
    result: List[Block] = []
    for b in blocks:
        if b.type == "paragraph" and len(b.text) > max_words:
            # 按句子分割（中文句号、英文句号、换行等）
            pattern = r"(?<=[。！？.!?])\s*|\n"
            sentences = re.split(pattern, b.text)
            sentences = [s.strip() for s in sentences if s.strip()]
            chunk_text = ""
            chunk_html = ""
            for s in sentences:
                if chunk_text and len(chunk_text) + len(s) > max_words:
                    result.append(Block(type="paragraph", html=f"<p>{chunk_html}</p>", text=chunk_text))
                    chunk_text = s
                    chunk_html = s
                else:
                    if chunk_text:
                        chunk_text += "\n\n" + s
                        chunk_html += "<br><br>" + s
                    else:
                        chunk_text = s
                        chunk_html = s
            if chunk_text:
                result.append(Block(type="paragraph", html=f"<p>{chunk_html}</p>", text=chunk_text))
        else:
            result.append(b)
    return result


def _split_blocks(blocks: List[Block], max_words: int = 300, max_images: int = 3, max_items: int = 6) -> List[List[Block]]:
    """把一组长内容块按容量拆分成多页。"""
    pages: List[List[Block]] = []
    current: List[Block] = []
    cur_words = 0
    cur_images = 0
    cur_items = 0

    for b in blocks:
        w = len(b.text)
        img = 1 if b.type == "image" else 0
        if current and (cur_words + w > max_words or cur_images + img > max_images or cur_items >= max_items):
            pages.append(current)
            current = [b]
            cur_words = w
            cur_images = img
            cur_items = 1
        else:
            current.append(b)
            cur_words += w
            cur_images += img
            cur_items += 1
    if current:
        pages.append(current)
    return pages


def paginate(blocks: List[Block], title: str = "Deck") -> List[Slide]:
    """
    先按 heading 分节，再对每节做容量拆分，最后选 layout。
    """
    if not blocks:
        return []

    # 先拆分超长段落
    blocks = _split_long_paragraphs(blocks)

    # 按 heading 分 section
    sections: List[List[Block]] = []
    current_sec: List[Block] = []
    for b in blocks:
        if b.type == "heading" and b.level <= 3 and current_sec:
            sections.append(current_sec)
            current_sec = [b]
        else:
            current_sec.append(b)
    if current_sec:
        sections.append(current_sec)

    # 若第一节没有 H1 标题，补一个封面
    has_cover = False
    if sections and sections[0] and sections[0][0].type == "heading" and sections[0][0].level == 1:
        has_cover = True
    elif sections and sections[0]:
        # 在最前面插入一个封面 section
        sections.insert(0, [Block(type="heading", html=f"<h1>{title}</h1>", level=1, text=title)])
        has_cover = True

    slides: List[Slide] = []
    for sec in sections:
        pages = _split_blocks(sec)
        for page_blocks in pages:
            slides.append(Slide(blocks=page_blocks))

    # 后处理：避免标题独自占页（除非是最后一页）
    i = 0
    while i < len(slides) - 1:
        s = slides[i]
        if len(s.blocks) == 1 and s.blocks[0].type == "heading":
            if slides[i + 1].blocks:
                s.blocks.append(slides[i + 1].blocks.pop(0))
                if not slides[i + 1].blocks:
                    slides.pop(i + 1)
                    continue
        i += 1

    # 布局判定
    for idx, slide in enumerate(slides):
        page_blocks = slide.blocks
        is_first_slide = idx == 0
        is_last_slide = idx == len(slides) - 1

        headings = [b for b in page_blocks if b.type == "heading"]
        images = [b for b in page_blocks if b.type == "image"]
        lists = [b for b in page_blocks if b.type == "list"]
        quotes = [b for b in page_blocks if b.type == "quote"]
        codes = [b for b in page_blocks if b.type == "code"]
        non_heading = [b for b in page_blocks if b.type != "heading"]
        text_blocks = [b for b in page_blocks if b.type in ("paragraph", "list")]

        # Cover: first slide with H1
        if is_first_slide and headings and headings[0].level == 1:
            slide.layout = "cover"
        # Title divider: slide with only a heading (not first/last)
        elif len(page_blocks) == 1 and headings and not is_first_slide and not is_last_slide:
            slide.layout = "title"
        # Closing
        elif is_last_slide and len(slides) > 2:
            slide.layout = "closing"
        # Quote: dominant quote block
        elif quotes and len(quotes) >= len(non_heading) * 0.5 and not images:
            slide.layout = "quote"
        # Code: dominant code block
        elif codes and len(codes) >= len(non_heading) * 0.5 and not images:
            slide.layout = "code"
        # Comparison: exactly 2 lists and little else
        elif len(lists) == 2 and len(text_blocks) <= 3 and not images:
            slide.layout = "comparison"
        # Grid: many small blocks (>=5) with no large paragraphs
        elif len(page_blocks) >= 5 and not images and all(len(b.text) < 80 for b in page_blocks):
            slide.layout = "grid"
        # Image + text split
        elif images and len([b for b in page_blocks if b.type in ("paragraph", "list", "code")]) > 0:
            if len(images) == 1:
                slide.layout = "split"
            else:
                slide.layout = "split-top"
        # List focused
        elif lists and not images:
            slide.layout = "list"
        # Single image
        elif len(page_blocks) == 1 and page_blocks[0].type == "image":
            slide.layout = "image"
        # Two-column text: many paragraphs without images/lists
        elif len([b for b in page_blocks if b.type == "paragraph"]) >= 3 and not images and not lists:
            slide.layout = "text-2col"
        else:
            slide.layout = "text"

    if not slides:
        slides.append(Slide(blocks=[Block(type="paragraph", html="<p>无内容</p>", text="无内容")], layout="text"))

    for i, s in enumerate(slides):
        s.idx = i
    return slides


# ---------------------------------------------------------------------------
# HTML 模板与渲染
# ---------------------------------------------------------------------------
CSS = """
/* ================= OpenShow Design System ================= */
:root {
  --bg: #0b0c0f;
  --bg-soft: #111318;
  --surface: #15171d;
  --surface-2: #1c1f28;
  --border: rgba(255,255,255,.08);
  --border-strong: rgba(255,255,255,.14);
  --text-1: #f2f4f8;
  --text-2: #b8bfc8;
  --text-3: #7a8490;
  --accent: #3b82f6;
  --accent-2: #8b5cf6;
  --accent-3: #ec4899;
  --good: #22c55e;
  --warn: #f59e0b;
  --bad: #ef4444;
  --grad: linear-gradient(135deg,#3b82f6 0%,#8b5cf6 55%,#ec4899 100%);
  --grad-soft: linear-gradient(135deg,rgba(59,130,246,.12),rgba(139,92,246,.08) 55%,rgba(236,72,153,.06));
  --radius: 18px;
  --radius-sm: 10px;
  --radius-lg: 24px;
  --shadow: 0 10px 30px rgba(0,0,0,.25), 0 2px 8px rgba(0,0,0,.18);
  --shadow-lg: 0 24px 60px rgba(0,0,0,.35), 0 8px 20px rgba(0,0,0,.22);
  --font-sans: "PingFang SC","Microsoft YaHei",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --font-mono: "SF Mono","Fira Code",Consolas,"Courier New",monospace;
  --ease: cubic-bezier(.22,1,.36,1);
}

[data-theme="light"] {
  --bg: #ffffff;
  --bg-soft: #f7f7f8;
  --surface: #ffffff;
  --surface-2: #f2f2f4;
  --border: rgba(0,0,0,.08);
  --border-strong: rgba(0,0,0,.14);
  --text-1: #111216;
  --text-2: #55596a;
  --text-3: #8a8f9e;
  --accent: #3b6cff;
  --accent-2: #7a5cff;
  --accent-3: #ff5c8a;
  --grad: linear-gradient(135deg,#3b6cff,#7a5cff 55%,#ff5c8a);
  --shadow: 0 10px 30px rgba(18,24,40,.08), 0 2px 6px rgba(18,24,40,.04);
  --shadow-lg: 0 24px 60px rgba(18,24,40,.14), 0 6px 16px rgba(18,24,40,.06);
}

[data-theme="tech"] {
  --bg: #0b0f19;
  --bg-soft: #0f1522;
  --surface: #131b2e;
  --surface-2: #1a243d;
  --text-1: #e8ebf4;
  --text-2: #9aa3b2;
  --text-3: #5e6a7c;
  --accent: #7ee787;
  --accent-2: #38bdf8;
  --accent-3: #c084fc;
  --grad: linear-gradient(135deg,#7ee787 0%,#38bdf8 55%,#c084fc 100%);
}

[data-theme="pitch"] {
  --bg: #070a1f;
  --bg-soft: #0c1030;
  --surface: #111642;
  --surface-2: #1a1f5c;
  --text-1: #ffffff;
  --text-2: #bfc7ea;
  --text-3: #7a85b8;
  --accent: #6366f1;
  --accent-2: #8b5cf6;
  --accent-3: #f43f5e;
  --grad: linear-gradient(135deg,#6366f1 0%,#8b5cf6 55%,#f43f5e 100%);
}

[data-theme="academic"] {
  --bg: #faf9f7;
  --bg-soft: #f5f3ef;
  --surface: #ffffff;
  --surface-2: #edeae3;
  --border: rgba(0,0,0,.06);
  --border-strong: rgba(0,0,0,.12);
  --text-1: #1a1a1a;
  --text-2: #4a4a4a;
  --text-3: #8a8a8a;
  --accent: #8b4513;
  --accent-2: #a0522d;
  --accent-3: #cd853f;
  --grad: linear-gradient(135deg,#8b4513,#cd853f);
  --shadow: 0 4px 12px rgba(0,0,0,.06);
  --shadow-lg: 0 12px 30px rgba(0,0,0,.1);
}

[data-theme="sunset"] {
  --bg: #1a0f14;
  --bg-soft: #24141b;
  --surface: #2e1922;
  --surface-2: #3d222e;
  --text-1: #fff0f5;
  --text-2: #e6c2cd;
  --text-3: #a67c85;
  --accent: #fb7185;
  --accent-2: #f472b6;
  --accent-3: #fb923c;
  --grad: linear-gradient(135deg,#fb7185 0%,#f472b6 55%,#fb923c 100%);
}

*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:var(--bg);color:var(--text-1);font-family:var(--font-sans);font-size:clamp(15px,1.5vw,26px);line-height:1.65;-webkit-font-smoothing:antialiased;letter-spacing:-.01em}
img,svg,video{max-width:100%;display:block}
a{color:var(--accent);text-decoration:none}
code,kbd,pre,samp{font-family:var(--font-mono)}

/* ================= DECK ================= */
#deck{position:relative;width:100vw;height:100vh;overflow:hidden;background:var(--bg)}
.slide{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;padding:6vh 7vw;opacity:0;pointer-events:none;transform:scale(.96) translateY(20px);transition:opacity .55s var(--ease),transform .55s var(--ease);overflow:hidden}
.slide.active{opacity:1;pointer-events:auto;transform:scale(1) translateY(0);z-index:2}
.slide.prev{transform:scale(.96) translateY(-20px)}
.slide-inner{width:100%;max-width:1500px;display:flex;flex-direction:column;gap:1.1em}

/* ================= TYPOGRAPHY ================= */
.eyebrow{font-size:.7rem;font-weight:600;letter-spacing:.2em;text-transform:uppercase;color:var(--text-3)}
.kicker{font-size:.75rem;font-weight:700;color:var(--accent);letter-spacing:.12em;text-transform:uppercase}
h1.title,.h1{font-size:clamp(2.6rem,5.2vw,4.8rem);line-height:1.08;font-weight:800;letter-spacing:-.03em;margin:0;color:var(--text-1)}
h2.title,.h2{font-size:clamp(1.8rem,3.6vw,3.2rem);line-height:1.12;font-weight:700;letter-spacing:-.02em;margin:0}
h3,.h3{font-size:clamp(1.3rem,2.4vw,2rem);line-height:1.25;font-weight:600;margin:0}
.lede{font-size:clamp(1.05rem,1.8vw,1.45rem);line-height:1.6;color:var(--text-2);font-weight:400;max-width:62ch}
.dim{color:var(--text-2)}.dim2{color:var(--text-3)}
.mono{font-family:var(--font-mono)}
.gradient-text{background:var(--grad);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}

/* ================= PRIMITIVES ================= */
.stack>*+*{margin-top:.9em}
.row{display:flex;gap:2vw;align-items:center}.row.wrap{flex-wrap:wrap}
.grid{display:grid;gap:2vw}
.g2{grid-template-columns:repeat(2,1fr)}.g3{grid-template-columns:repeat(3,1fr)}.g4{grid-template-columns:repeat(4,1fr)}
.center{display:flex;align-items:center;justify-content:center;text-align:center}
.fill{flex:1}
.mt-s{margin-top:.4em}.mt-m{margin-top:.9em}.mt-l{margin-top:1.6em}
.mb-s{margin-bottom:.4em}.mb-m{margin-bottom:.9em}.mb-l{margin-bottom:1.6em}

/* ================= CARDS & BADGES ================= */
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1.4em 1.6em;box-shadow:var(--shadow)}
.card-soft{background:var(--surface-2);border:1px solid var(--border)}
.card-outline{background:transparent;border:1.5px solid var(--border-strong);box-shadow:none}
.card-accent{background:var(--surface);border-left:4px solid var(--accent)}
.pill{display:inline-block;padding:.25em .8em;border-radius:999px;font-size:.65rem;font-weight:600;background:var(--surface-2);color:var(--text-2);border:1px solid var(--border)}
.pill-accent{background:rgba(59,130,246,.15);color:var(--accent);border-color:rgba(59,130,246,.35)}

/* ================= DIVIDERS ================= */
.divider{height:1px;background:var(--border);width:100%}
.divider-accent{height:3px;width:64px;background:var(--accent);border-radius:2px}

/* ================= CHROME ================= */
.deck-header{position:absolute;top:2.2vh;left:3vw;right:3vw;display:flex;align-items:center;justify-content:space-between;font-size:.65rem;color:var(--text-3);letter-spacing:.14em;text-transform:uppercase;z-index:10;pointer-events:none}
.deck-footer{position:absolute;bottom:2.2vh;left:3vw;right:3vw;display:flex;align-items:center;justify-content:space-between;font-size:.65rem;color:var(--text-3);z-index:10;pointer-events:none}
.progress-bar{position:fixed;left:0;right:0;bottom:0;height:3px;background:transparent;z-index:20}
.progress-bar > span{display:block;height:100%;width:0;background:var(--accent);transition:width .35s var(--ease)}

/* ================= MEDIA ================= */
img{border-radius:var(--radius-sm);box-shadow:var(--shadow)}
.img-frame{border:1px solid var(--border);padding:.4em;background:var(--surface)}
.img-clean{box-shadow:none;border-radius:var(--radius-sm)}

/* ================= CODE ================= */
.code-block{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1.2em 1.4em;overflow:auto;max-height:52vh;font-size:.82em;line-height:1.6}
.code-block pre{margin:0;padding:0;background:transparent;border-radius:0}

/* ================= QUOTE ================= */
.quote-block{position:relative;padding:1.2em 1.6em;background:var(--grad-soft);border-radius:var(--radius);border:1px solid var(--border)}
.quote-block::before{content:"\"";position:absolute;top:.2em;left:.4em;font-size:3.5rem;line-height:1;color:var(--accent);opacity:.35;font-family:Georgia,serif}
.quote-block p{position:relative;z-index:1;font-size:1.25rem;line-height:1.7;font-style:italic;color:var(--text-1);margin:0}
.quote-block cite{display:block;margin-top:.8em;font-size:.85rem;color:var(--text-3);font-style:normal}

/* ================= LAYOUTS ================= */
[data-layout="cover"] .slide-inner{align-items:center;text-align:center;gap:.7em}
[data-layout="cover"] h1{font-size:clamp(2.8rem,6vw,5.2rem);letter-spacing:-.03em}
[data-layout="cover"] .subtitle{color:var(--text-2);font-size:clamp(1.1rem,2.2vw,1.6rem)}
[data-layout="cover"] .lede{max-width:52ch;text-align:center}

[data-layout="title"] .slide-inner{align-items:center;text-align:center;gap:.6em}
[data-layout="title"] h1{font-size:clamp(2.6rem,5.6vw,4.8rem)}

[data-layout="closing"] .slide-inner{align-items:center;text-align:center;gap:.9em}
[data-layout="closing"] h1{font-size:clamp(2.2rem,4.6vw,3.8rem)}

[data-layout="text"] .slide-inner{align-items:flex-start;text-align:left;gap:.9em}
[data-layout="text-2col"] .slide-inner{display:grid;grid-template-columns:1fr 1fr;gap:3vw;align-items:start;text-align:left}

[data-layout="list"] .slide-inner{align-items:flex-start;text-align:left;gap:1em}
[data-layout="list"] ul,[data-layout="list"] ol{padding-left:1.3em;font-size:clamp(1.05rem,1.9vw,1.55rem)}
[data-layout="list"] li{margin:.55em 0;line-height:1.55}

[data-layout="split"] .slide-inner{display:grid;grid-template-columns:1fr 1fr;align-items:center;gap:3.5vw}
[data-layout="split-top"] .slide-inner{display:grid;grid-template-columns:1fr;grid-template-rows:auto 1fr;gap:1.8em;text-align:center;align-items:center}
[data-layout="split-top"] img{max-height:42vh;margin:0 auto}

[data-layout="image"] .slide-inner{align-items:center;justify-content:center}
[data-layout="image"] img{max-height:74vh;box-shadow:var(--shadow-lg)}

[data-layout="quote"] .slide-inner{align-items:center;justify-content:center}
[data-layout="quote"] .quote-block{max-width:72ch;width:100%}

[data-layout="code"] .slide-inner{align-items:flex-start;justify-content:center}
[data-layout="code"] .code-block{width:100%}

[data-layout="comparison"] .slide-inner{display:grid;grid-template-columns:1fr 1fr;gap:3vw;align-items:start}
[data-layout="comparison"] .col{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1.4em}

[data-layout="grid"] .slide-inner{display:grid;grid-template-columns:repeat(2,1fr);gap:1.5vw}
[data-layout="grid"] .cell{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1.2em 1.4em}

/* ================= ANIMATIONS ================= */
.anim-in{opacity:0;transform:translateY(18px);transition:opacity .5s var(--ease),transform .5s var(--ease)}
.slide.active .anim-in{opacity:1;transform:translateY(0)}
.slide.active .anim-in:nth-child(1){transition-delay:.06s}
.slide.active .anim-in:nth-child(2){transition-delay:.12s}
.slide.active .anim-in:nth-child(3){transition-delay:.18s}
.slide.active .anim-in:nth-child(4){transition-delay:.24s}
.slide.active .anim-in:nth-child(5){transition-delay:.30s}
.slide.active .anim-in:nth-child(6){transition-delay:.36s}
.slide.active .anim-in:nth-child(7){transition-delay:.42s}
.slide.active .anim-in:nth-child(8){transition-delay:.48s}

/* ================= TIMER & PROGRESS DOTS ================= */
#timer{position:fixed;top:18px;left:22px;z-index:999;font-variant-numeric:tabular-nums;font-size:.75rem;color:var(--text-2);background:var(--surface);border:1px solid var(--border);padding:6px 14px;border-radius:999px;cursor:pointer;user-select:none;transition:opacity .3s}
#timer.hidden{opacity:0;pointer-events:none}
#timer.paused{color:var(--warn)}

#progress-dots{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);display:flex;align-items:center;gap:10px;z-index:999;padding:6px 14px;background:var(--surface);border:1px solid var(--border);border-radius:999px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--border-strong);transition:background .3s}
.dot.active{background:var(--accent)}

#page-num{position:fixed;bottom:18px;right:22px;font-size:.7rem;color:var(--text-3);z-index:999}

/* ================= THEME SWITCHER ================= */
#theme-switcher{position:fixed;top:18px;right:22px;z-index:999;font-size:.7rem;color:var(--text-2);background:var(--surface);border:1px solid var(--border);padding:6px 12px;border-radius:999px;cursor:pointer}
#theme-switcher:hover{background:var(--surface-2)}

/* ================= ZONES ================= */
.zone{position:fixed;top:0;bottom:0;width:20%;z-index:99;cursor:pointer}
.zone:hover{background:rgba(255,255,255,.02)}
.zone-left{left:0}.zone-right{right:0;width:80%}

/* ================= MOBILE ================= */
@media (max-width: 768px) {
  .slide{padding:5vh 6vw}
  [data-layout="split"] .slide-inner,[data-layout="text-2col"] .slide-inner,[data-layout="comparison"] .slide-inner,[data-layout="grid"] .slide-inner{grid-template-columns:1fr}
  [data-layout="split-top"] .slide-inner{grid-template-rows:auto auto}
  img{max-height:40vh}
  #timer,#theme-switcher{font-size:.65rem;top:10px}
}
""".strip()


JS = """
(function(){
  const slides = Array.from(document.querySelectorAll('.slide'));
  let idx = 0;
  const themes = ['dark','light','tech','pitch','academic','sunset'];
  let themeIdx = 0;

  function update(){
    slides.forEach((s,i)=>{
      s.classList.remove('active','prev');
      if(i===idx){ s.classList.add('active'); }
      else if(i<idx){ s.classList.add('prev'); }
    });
    document.querySelectorAll('.dot').forEach((d,i)=>{ d.classList.toggle('active', i===idx); });
    document.getElementById('page-text').textContent = (idx+1) + ' / ' + slides.length;
    const bar = document.querySelector('.progress-bar > span');
    if(bar){ bar.style.width = ((idx+1)/slides.length*100) + '%'; }
    const hCurrent = document.querySelector('.deck-header .current');
    if(hCurrent){ hCurrent.textContent = 'SLIDE ' + (idx+1); }
  }

  function next(){ if(idx < slides.length-1){ idx++; update(); } }
  function prev(){ if(idx > 0){ idx--; update(); } }
  function cycleTheme(){
    themeIdx = (themeIdx+1) % themes.length;
    document.documentElement.setAttribute('data-theme', themes[themeIdx]);
  }

  document.addEventListener('keydown', e=>{
    if(e.key==='ArrowRight' || e.key==='ArrowDown' || e.key===' ' || e.key==='PageDown'){
      e.preventDefault(); next();
    } else if(e.key==='ArrowLeft' || e.key==='ArrowUp' || e.key==='PageUp'){
      e.preventDefault(); prev();
    } else if(e.key==='f' || e.key==='F'){
      e.preventDefault();
      if(!document.fullscreenElement) document.documentElement.requestFullscreen().catch(()=>{});
      else document.exitFullscreen().catch(()=>{});
    } else if(e.key==='t' || e.key==='T'){
      e.preventDefault(); document.getElementById('timer').classList.toggle('hidden');
    } else if(e.key==='a' || e.key==='A'){
      // toggle animation classes by forcing a tiny reflow
      slides.forEach(s=>{
        s.querySelectorAll('.anim-in').forEach(el=>{
          el.style.transition='none'; el.style.opacity='0'; el.style.transform='translateY(18px)';
          setTimeout(()=>{ el.style.transition=''; el.style.opacity=''; el.style.transform=''; }, 50);
        });
      });
    }
  });

  document.querySelector('.zone-left').addEventListener('click', prev);
  document.querySelector('.zone-right').addEventListener('click', next);

  let startX = 0;
  document.addEventListener('touchstart', e=>{ startX = e.touches[0].clientX; }, {passive:true});
  document.addEventListener('touchend', e=>{
    const endX = e.changedTouches[0].clientX;
    const diff = startX - endX;
    if(Math.abs(diff) > 40){ diff > 0 ? next() : prev(); }
  }, {passive:true});

  document.body.addEventListener('click', e=>{
    const a = e.target.closest('a');
    if(a){ const href = a.getAttribute('href')||''; if(!href.startsWith('#')){ e.preventDefault(); } }
  });

  const timerEl = document.getElementById('timer');
  let seconds = 0, paused = false, timerStarted = false;
  function fmt(n){ return n.toString().padStart(2,'0'); }
  function updateTimer(){ timerEl.textContent = fmt(Math.floor(seconds/60)) + ':' + fmt(seconds%60); }
  setInterval(()=>{ if(!paused && timerStarted){ seconds++; updateTimer(); } }, 1000);
  timerEl.addEventListener('click', ()=>{ paused = !paused; timerEl.classList.toggle('paused', paused); });
  setTimeout(()=>{ timerStarted = true; }, 1000);

  update();
})();
""".strip()


def _render_slide_content(slide: Slide) -> str:
    """把 Slide 的 blocks 渲染成 HTML，同时做 layout 微调。"""
    def _anim(html: str, tag: str = "div") -> str:
        # 给外层容器加进入动画类，内容本身保持
        return f'<{tag} class="anim-in">{html}</{tag}>'

    if slide.layout == "cover":
        parts = []
        eyebrow = ""
        for b in slide.blocks:
            if b.type == "heading" and b.level == 1:
                parts.append(f'<h1 class="title gradient-text anim-in">{b.text}</h1>')
            elif b.type == "heading":
                parts.append(f'<div class="subtitle anim-in">{b.text}</div>')
            else:
                # wrap paragraph in lede for better style
                if b.type == "paragraph":
                    parts.append(f'<p class="lede anim-in">{b.text}</p>')
                else:
                    parts.append(_anim(b.html))
        if len(parts) > 1 and slide.blocks[0].type == "heading" and slide.blocks[0].level == 1:
            eyebrow = '<div class="eyebrow anim-in">OPENSHOW</div>'
        return eyebrow + "\n".join(parts)

    if slide.layout == "title":
        h = slide.blocks[0]
        return f'<div class="eyebrow anim-in">SECTION</div>\n<h1 class="title gradient-text anim-in">{h.text}</h1>'

    if slide.layout == "closing":
        parts = []
        for b in slide.blocks:
            if b.type == "heading":
                parts.append(f'<h1 class="title anim-in">{b.text}</h1>')
            elif b.type == "paragraph":
                parts.append(f'<p class="lede anim-in">{b.text}</p>')
            else:
                parts.append(_anim(b.html))
        return "\n".join(parts)

    if slide.layout == "quote":
        q = slide.blocks[0]
        cite = ""
        if len(slide.blocks) > 1 and slide.blocks[1].type == "paragraph":
            cite = f'<cite class="anim-in">{slide.blocks[1].text}</cite>'
        return f'<div class="quote-block anim-in">{q.html}{cite}</div>'

    if slide.layout == "code":
        parts = []
        heading = ""
        for b in slide.blocks:
            if b.type == "heading":
                heading = f'<h3 class="anim-in">{b.text}</h3>'
            elif b.type == "code":
                parts.append(f'<div class="code-block anim-in">{b.html}</div>')
            else:
                parts.append(_anim(b.html))
        return heading + "\n".join(parts)

    if slide.layout == "comparison":
        lists = [b for b in slide.blocks if b.type == "list"]
        heading = ""
        for b in slide.blocks:
            if b.type == "heading":
                heading = f'<h2 class="anim-in">{b.text}</h2>'
        cols = ""
        for i, lst in enumerate(lists[:2]):
            title = "A" if i == 0 else "B"
            cols += f'<div class="col anim-in">{lst.html}</div>'
        return heading + f'<div class="row fill anim-in" style="width:100%">{cols}</div>'

    if slide.layout == "grid":
        heading = ""
        cells = []
        for b in slide.blocks:
            if b.type == "heading":
                heading = f'<h2 class="anim-in">{b.text}</h2>'
            else:
                content = b.text if b.type == "paragraph" else b.html
                cells.append(f'<div class="cell anim-in">{content}</div>')
        grid = "\n".join(cells)
        return heading + f'<div class="grid g2">{grid}</div>'

    if slide.layout == "split":
        images = [b.html for b in slide.blocks if b.type == "image"]
        texts = []
        heading = ""
        for b in slide.blocks:
            if b.type == "heading":
                heading = f'<h2 class="anim-in">{b.text}</h2>'
            elif b.type != "image":
                texts.append(_anim(b.html))
        img_html = f'<div class="anim-in">{"".join(images[:1])}</div>'
        text_html = f'<div class="stack">{heading}{"".join(texts)}</div>'
        return img_html + "\n" + text_html

    if slide.layout == "split-top":
        images = [b.html for b in slide.blocks if b.type == "image"]
        texts = []
        heading = ""
        for b in slide.blocks:
            if b.type == "heading":
                heading = f'<h2 class="anim-in">{b.text}</h2>'
            elif b.type != "image":
                texts.append(_anim(b.html))
        return f'<div class="anim-in">{"".join(images)}</div>' + "\n" + f'<div>{heading}{"".join(texts)}</div>'

    if slide.layout == "image":
        return "\n".join(f'<div class="anim-in">{b.html}</div>' for b in slide.blocks)

    if slide.layout == "list":
        heading = ""
        body = []
        for b in slide.blocks:
            if b.type == "heading":
                heading = f'<h2 class="anim-in">{b.text}</h2>'
            else:
                body.append(_anim(b.html))
        return heading + "\n" + "\n".join(body)

    if slide.layout == "text-2col":
        heading = ""
        paras = []
        for b in slide.blocks:
            if b.type == "heading":
                heading = f'<h2 class="anim-in">{b.text}</h2>'
            else:
                paras.append(b.html)
        mid = (len(paras) + 1) // 2
        col1 = "\n".join(paras[:mid])
        col2 = "\n".join(paras[mid:])
        return heading + f'<div class="text-2col row fill anim-in" style="align-items:flex-start;gap:3vw;width:100%"><div class="stack">{col1}</div><div class="stack">{col2}</div></div>'

    # text (default)
    parts = []
    for b in slide.blocks:
        if b.type == "heading":
            parts.append(f'<h2 class="anim-in">{b.text}</h2>')
        else:
            parts.append(_anim(b.html))
    return "\n".join(parts)


def build_html(slides: List[Slide], title: str = "OpenShow", logo_svg: str = "") -> str:
    dots = "\n".join(f'<div class="dot{" active" if i == 0 else ""}"></div>' for i in range(len(slides)))
    slide_html = "\n".join(
        f'<section class="slide" data-layout="{s.layout}"><div class="slide-inner">{_render_slide_content(s)}</div></section>'
        for s in slides
    )
    safe_title = title.replace("<", "&lt;").replace(">", "&gt;")

    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_title}</title>
<style>
{CSS}
</style>
</head>
<body>
<div class="deck-header">
  <span>{safe_title}</span>
  <span class="current">SLIDE 1</span>
</div>
<div id="deck">
{slide_html}
</div>
<div class="zone zone-left"></div>
<div class="zone zone-right"></div>
<div id="timer">00:00</div>
<div id="theme-switcher" title="按 T 隐藏/显示计时器">
  <span style="opacity:.7">主题</span> <span id="theme-name" style="font-weight:700">DARK</span>
</div>
<div id="progress-dots">
{dots}
</div>
<div class="progress-bar"><span></span></div>
<div id="page-num"><span id="page-text">1 / {len(slides)}</span></div>
<script>
{JS}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="OpenShow — 将文档/网页转为可播放 HTML 幻灯片")
    parser.add_argument("-i", "--input", required=True, help="输入文件路径或 URL")
    parser.add_argument("-o", "--output", default=".", help="输出目录（默认当前目录）")
    parser.add_argument("--open", action="store_true", help="生成后用系统默认浏览器自动打开")
    parser.add_argument("--openclaw", action="store_true", help="生成后用 openclaw browser 打开")
    args = parser.parse_args()

    blocks, title = parse_input(args.input)
    slides = paginate(blocks, title=title)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"openshow_{title}_{ts}.html"
    out_path = out_dir / filename

    logo_svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="60" cy="60" r="54" opacity="0.25"/>
  <circle cx="60" cy="60" r="36" opacity="0.18"/>
  <polygon points="48,45 48,75 81,60" fill="currentColor" stroke="none" opacity="0.35"/>
</svg>"""
    html = build_html(slides, title=title, logo_svg=logo_svg)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"生成完成：{out_path.resolve()}")
    print(f"共 {len(slides)} 页幻灯片")

    file_url = f"file://{out_path.resolve()}"
    if args.openclaw:
        import subprocess
        try:
            subprocess.run(["openclaw", "browser", "open", file_url], check=False)
            print(f"已通过 openclaw 打开：{file_url}")
        except Exception as e:
            print(f"openclaw 打开失败：{e}")
    elif args.open:
        import webbrowser
        try:
            webbrowser.open(file_url)
            print(f"已用系统默认浏览器打开：{file_url}")
        except Exception as e:
            print(f"浏览器打开失败：{e}")


if __name__ == "__main__":
    main()