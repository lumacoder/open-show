<div align="center">

<h1>
  <img src="https://raw.githubusercontent.com/lumacoder/open-show/main/assets/logo.svg" width="80" alt="OpenShow">
  <br>
  OpenShow
</h1>

<p><strong>将任意文档转换为可全屏播放的 HTML 幻灯片</strong></p>
<p><em>支持 Markdown · Word · PDF · 纯文本 · HTML · 网页链接</em></p>

<p>
  <a href="https://github.com/lumacoder/open-show/stargazers"><img src="https://img.shields.io/github/stars/lumacoder/open-show?style=for-the-badge&logo=github&color=ffd700&labelColor=1a1a1a"></a>
  <a href="https://github.com/lumacoder/open-show/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-00d26a?style=for-the-badge&labelColor=1a1a1a"></a>
  <a href="#"><img src="https://img.shields.io/badge/Python-3.9%2B-3776ab?style=for-the-badge&logo=python&labelColor=1a1a1a"></a>
  <a href="#"><img src="https://img.shields.io/badge/%E6%97%A0%20CDN-%E7%A6%BB%E7%BA%BF%E5%8F%AF%E7%94%A8-ff6b6b?style=for-the-badge&labelColor=1a1a1a"></a>
</p>

<p>
  <img src="https://img.shields.io/badge/%E2%86%90_%E2%86%92-%E9%94%AE%E7%9B%98%E7%BF%BB%E9%A1%B5-3b82f6?style=flat-square&labelColor=1a1a1a">
  <img src="https://img.shields.io/badge/F-%E5%85%A8%E5%B1%8F-f59e0b?style=flat-square&labelColor=1a1a1a">
  <img src="https://img.shields.io/badge/T-%E8%AE%A1%E6%97%B6%E5%99%A8-10b981?style=flat-square&labelColor=1a1a1a">
  <img src="https://img.shields.io/badge/%E2%86%94%EF%B8%8E-%E8%A7%A6%E6%91%B8%E6%BB%91%E5%8A%A8-ec4899?style=flat-square&labelColor=1a1a1a">
</p>

<br>

<p>
  <a href="#-%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B"><strong>快速开始</strong></a> ·
  <a href="#-%E7%89%B9%E6%80%A7"><strong>特性</strong></a> ·
  <a href="#-%E6%94%AF%E6%8C%81%E6%A0%BC%E5%BC%8F"><strong>支持格式</strong></a> ·
  <a href="#-%E5%AE%89%E8%A3%85"><strong>安装</strong></a> ·
  <a href="#-%E5%91%BD%E4%BB%A4%E8%A1%8C"><strong>命令行</strong></a> ·
  <a href="#-%E6%9E%B6%E6%9E%84"><strong>架构</strong></a>
</p>

</div>

---

## 快速开始

```bash
# 克隆仓库
git clone https://github.com/lumacoder/open-show.git
cd open-show
python3 -m pip install markdown python-docx requests beautifulsoup4 pymupdf

# 将网页转为幻灯片
python3 scripts/openshow.py -i "https://example.com" -o ~/openshow_outputs --open

# 将 PDF 转为幻灯片
python3 scripts/openshow.py -i "presentation.pdf" -o ~/openshow_outputs --open
```

> 提示：将 `--open` 替换为 `--openclaw`，可直接在 OpenClaw 浏览器中打开。

---

## 特性

- **智能分页** — 根据标题层级、字数、图片数量自动拆分页面，避免单页内容过多。
- **自动布局** — 根据每页内容自动选择封面、纯文字、图文分栏、列表、单图、结尾页等布局。
- **内置计时器** — 加载后自动开始计时，支持暂停/继续，按 `T` 显示或隐藏。
- **图片内联** — 本地及远程图片自动转为 Base64 `data URI`，生成单个可离线使用的 HTML 文件。
- **一键打开** — 生成后可通过 `--open`（系统浏览器）或 `--openclaw`（OpenClaw）直接播放。
- **零 CDN** — CSS、JS、资源全部内嵌，无需网络即可播放。

---

## 支持格式

| 格式 | 扩展名 | 处理方式 | 依赖 |
|:---:|:---:|:---|:---|
| Markdown | `.md`、`.markdown` | 完整解析，支持表格与代码块 | `markdown` |
| Word | `.docx` | 提取段落、标题、嵌入图片 | `python-docx` |
| PDF | `.pdf` | 逐页渲染为 PNG 图片 | `pymupdf` |
| 纯文本 | `.txt` | 按空行分段，识别 `#` 标题 | 无 |
| HTML | `.html`、`.htm` | 启发式提取正文内容 | `beautifulsoup4` |
| 网页链接 | `http://`、`https://` | 抓取网页并清洗提取 | `requests`、`beautifulsoup4` |

---

## 安装

### 环境要求

```bash
python3 --version  # >= 3.9
```

### 安装依赖

```bash
python3 -m pip install markdown python-docx requests beautifulsoup4 pymupdf
```

### OpenClaw（可选）

若已安装 [OpenClaw](https://openclaw.ai)，可使用 `--openclaw` 参数直接调用其浏览器打开生成的幻灯片。

---

## 命令行

```
usage: openshow.py [-h] -i INPUT [-o OUTPUT] [--open] [--openclaw]

OpenShow — 将文档/网页转为可播放 HTML 幻灯片

options:
  -h, --help           显示帮助信息
  -i, --input INPUT    输入文件路径或 URL
  -o, --output OUTPUT  输出目录（默认当前目录）
  --open               生成后用系统默认浏览器自动打开
  --openclaw           生成后用 openclaw browser 打开
```

### 常用示例

```bash
# Markdown 文章
python3 scripts/openshow.py -i article.md -o ~/openshow_outputs --open

# Word 报告
python3 scripts/openshow.py -i report.docx -o ~/openshow_outputs --open

# PDF 文件
python3 scripts/openshow.py -i deck.pdf -o ~/openshow_outputs --open

# 纯文本笔记
python3 scripts/openshow.py -i notes.txt -o ~/openshow_outputs --open

# 网页文章
python3 scripts/openshow.py -i "https://blog.example.com/post" -o ~/openshow_outputs --openclaw
```

---

## 操作方式

| 操作 | 桌面端 | 移动端 |
|:---|:---|:---|
| 下一页 | `→` `↓` `空格` `PageDown` | 左滑 · 点击屏幕右侧 2/3 |
| 上一页 | `←` `↑` `PageUp` | 右滑 · 点击屏幕左侧 1/3 |
| 切换全屏 | `F` | 旋转设备 |
| 显示/隐藏计时器 | `T` | — |
| 暂停/继续计时 | 点击左上角计时器 | — |

---

## 架构

```
┌─────────────┐     ┌─────────────┐     ┌───────────────┐     ┌─────────────┐
│   INPUT     │────▶│   PARSER    │────▶│  BLOCK PIPELINE│────▶│  PAGINATE   │
│ (md/pdf/..) │     │(format spec)│     │(clean · inline)│     │(smart split)│
└─────────────┘     └─────────────┘     └───────────────┘     └──────┬──────┘
                                                                      │
                                                                      ▼
┌─────────────┐     ┌─────────────┐     ┌───────────────┐     ┌─────────────┐
│  SINGLE     │◀────│   RENDER    │◀────│  LAYOUT ENGINE │◀────│   SLIDES    │
│  HTML FILE  │     │(css+js inline)│    │(auto layout)   │    │(page list)  │
└─────────────┘     └─────────────┘     └───────────────┘     └─────────────┘
```

### 设计原则

1. **单文件输出** — 所有内容（CSS、JS、图片）打包进一个 HTML，可直接邮件发送或在静态服务器托管。
2. **内容优先分页** — 先按语义标题分节，再按容量（字数、图片数、块数）拆分。
3. **自动布局选择** — 根据每页内容特征自动匹配最优布局，无需手动配置。
4. **零配置** — 不需要 YAML 前置信息、模板选择或参数调优，传文件即出幻灯片。

---

## 开发计划

- [x] Markdown 支持
- [x] Word (.docx) 支持
- [x] PDF 支持
- [x] 纯文本支持
- [x] HTML 与 URL 支持
- [x] OpenClaw 浏览器集成
- [ ] PPTX 支持
- [ ] 自定义主题/皮肤
- [ ] 演讲者备注模式
- [ ] 导出视频（MP4/WebM）

---

## 参与贡献

欢迎提交 PR。以下方向尤为需要：

- PDF 扫描件的文字提取（OCR）
- 更多布局模板
- 主题/CSS 自定义系统
- 超大文档的性能优化

---

## 许可证

MIT © [lumacoder](https://github.com/lumacoder)

---

<div align="center">

如果觉得有用，欢迎 Star 支持。

</div>
