#!/usr/bin/env python3
"""
PDF生成工具 v2 — 使用 weasyprint + 明确指定中文字体
用法：python3.11 gen_pdf_v2.py <input.md> <output.pdf> [title]
"""

import sys, os, re, subprocess, tempfile
from pathlib import Path


FONT_CSS = """
@font-face {
    font-family: "NotoSansCJK";
    src: url("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc");
    font-weight: normal;
}
@font-face {
    font-family: "NotoSansCJK";
    src: url("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc");
    font-weight: bold;
}
"""

def md_to_html(md_path: str, title: str = "") -> str:
    """Markdown → HTML（紧凑排版，适合PDF）"""
    with open(md_path, "r", encoding="utf-8") as f:
        md = f.read()

    lines = md.split("\n")
    html_parts = []

    in_table = False
    table_lines = []

    def flush_table():
        nonlocal table_lines, in_table, html_parts
        if not table_lines:
            return
        # 跳过分隔行
        data_rows = []
        for tl in table_lines:
            cells = [c.strip() for c in tl.split("|")[1:-1]]
            if all(re.match(r"^-+$", c) for c in cells if c):
                continue
            data_rows.append(cells)
        if data_rows:
            th = data_rows[0]
            tr_rest = data_rows[1:]
            table_html = "<table>\n<thead><tr>"
            table_html += "".join(f"<th>{c}</th>" for c in th)
            table_html += "</tr></thead>\n<tbody>\n"
            for row in tr_rest:
                table_html += "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>\n"
            table_html += "</tbody></table>\n"
            html_parts.append(table_html)
        table_lines = []
        in_table = False

    for ln in lines:
        stripped = ln.strip()

        # 表格检测
        if re.match(r"^\|.*\|$", stripped):
            in_table = True
            table_lines.append(stripped)
            continue
        else:
            if in_table:
                flush_table()

        if not stripped:
            html_parts.append("")
            continue

        # 分隔线
        if re.match(r"^---+$", stripped):
            html_parts.append("<hr>")
            continue

        # 图片
        if m := re.match(r"^!\[([^\]]*)\]\(([^\)]+)\)$", stripped):
            img_rel = m.group(2)
            # 解析为绝对路径
            md_dir = os.path.dirname(md_path)
            img_abs = os.path.join(md_dir, img_rel)
            if os.path.exists(img_abs):
                img_src = f"file://{img_abs}"
                html_parts.append(f'<p style="text-align:center"><img src="{img_src}" style="max-width:90%;height:auto;"/></p>')
            else:
                html_parts.append(f"<p>[图片: {img_rel}]</p>")
            continue

        # 标题
        if m := re.match(r"^#### (.+)$", stripped):
            html_parts.append(f"<h4>{m.group(1)}</h4>")
        elif m := re.match(r"^### (.+)$", stripped):
            html_parts.append(f"<h3>{m.group(1)}</h3>")
        elif m := re.match(r"^## (.+)$", stripped):
            html_parts.append(f"<h2>{m.group(1)}</h2>")
        elif m := re.match(r"^# (.+)$", stripped):
            html_parts.append(f"<h1>{m.group(1)}</h1>")
        # 列表
        elif m := re.match(r"^[-*] (.+)$", stripped):
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", m.group(1))
            html_parts.append(f"<p class='li'>• {text}</p>")
        # 引用
        elif stripped.startswith("> "):
            text = stripped[2:]
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
            html_parts.append(f"<blockquote>{text}</blockquote>")
        # 普通段落
        else:
            text = stripped
            text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)  # 去链接
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
            text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
            html_parts.append(f"<p>{text}</p>")

    flush_table()

    body_html = "\n".join(html_parts)
    page_title = title or Path(md_path).stem

    full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
{FONT_CSS}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: "NotoSansCJK", "WenQuanYi Zen Hei", "SimSun", serif;
    font-size: 12px;
    line-height: 1.8;
    color: #222;
}}
h1 {{
    font-size: 20px;
    font-weight: bold;
    text-align: center;
    margin: 24px 0 16px;
    border-bottom: 2px solid #333;
    padding-bottom: 8px;
}}
h2 {{
    font-size: 16px;
    font-weight: bold;
    margin: 20px 0 10px;
    border-bottom: 1px solid #ccc;
    padding-bottom: 4px;
}}
h3 {{
    font-size: 14px;
    font-weight: bold;
    margin: 16px 0 8px;
}}
h4 {{
    font-size: 13px;
    font-weight: bold;
    margin: 12px 0 6px;
}}
p {{
    text-indent: 2em;
    margin-bottom: 6px;
    text-align: justify;
}}
p.li {{
    text-indent: 2em;
    margin-left: 1em;
}}
blockquote {{
    margin: 10px 2em;
    padding: 8px 16px;
    background: #f9f9f9;
    border-left: 4px solid #999;
    color: #555;
    font-size: 11px;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    margin: 12px 0;
    font-size: 11px;
}}
th {{
    background: #f0f0f0;
    font-weight: bold;
    padding: 6px 10px;
    border: 1px solid #ccc;
    text-align: left;
}}
td {{
    padding: 5px 10px;
    border: 1px solid #ccc;
}}
tr:nth-child(even) td {{
    background: #f9f9f9;
}}
hr {{
    border: none;
    border-top: 1px solid #ddd;
    margin: 16px 0;
}}
@page {{
    size: A4;
    margin: 2.5cm 2.5cm;
}}
</style>
</head>
<body>
{body_html}
</body>
</html>"""
    return full_html


def md_to_pdf(md_path: str, pdf_path: str, title: str = ""):
    """MD → HTML → weasyprint → PDF"""
    html = md_to_html(md_path, title)
    tmp = tempfile.mktemp(suffix=".html")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(html)
        result = subprocess.run(
            ["weasyprint", tmp, pdf_path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  ⚠ weasyprint 错误: {result.stderr[:300]}")
            raise RuntimeError(result.stderr)
        print(f"  ✅ PDF: {pdf_path}")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python3.11 gen_pdf_v2.py <input.md> <output.pdf> [title]")
        sys.exit(1)
    md_path = sys.argv[1]
    pdf_path = sys.argv[2]
    title    = sys.argv[3] if len(sys.argv) > 3 else ""
    md_to_pdf(md_path, pdf_path, title)
