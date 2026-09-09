"""Build a standalone, printable HTML guide from the canonical Markdown."""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication


def build_guide(source: Path, destination: Path) -> None:
    parts = re.split(r"(?m)^## ", source.read_text(encoding="utf-8"))
    navigation, articles = [], []
    for index, part in enumerate(parts[1:], 1):
        title = part.split("\n", 1)[0]
        document = QTextDocument()
        document.setMarkdown("## " + part)
        body = re.search(r"<body[^>]*>(.*)</body>", document.toHtml(), re.S).group(1)
        anchor = f"topic-{index}"
        navigation.append(f'<a href="#{anchor}">{html.escape(title)}</a>')
        articles.append(f'<article id="{anchor}">{body}</article>')
    title = html.escape(parts[0].splitlines()[0].lstrip("# "))
    output = '''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>''' + title + '''</title><style>
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:#253E48;background:#F3F5F7;
font:16px/1.8 "Malgun Gothic","Apple SD Gothic Neo",sans-serif;word-break:keep-all;overflow-wrap:anywhere}
header{background:#173F3A;color:white;padding:32px 4vw}header p{color:#D2E4DF;margin:4px 0}
h1{font-size:28px;margin:0}h2{font-size:24px;color:#205B4E}h3{font-size:19px;color:#36545B}
.layout{display:grid;grid-template-columns:245px minmax(0,1fr);gap:24px;padding:28px 4vw}
nav{position:sticky;top:24px;align-self:start}nav a{display:block;padding:9px 12px;color:#36545B;
border-bottom:1px solid #DFE7E9;text-decoration:none}nav a:hover{background:#DFEEE7;border-radius:8px}
main{min-width:0}article{background:white;border:1px solid #DFE7E9;border-radius:12px;padding:24px 32px;margin-bottom:24px;scroll-margin-top:16px}
p,li{font-size:16px!important;line-height:1.8!important}p{margin:12px 0!important;white-space:normal!important}
span{font-size:inherit!important}li{margin-bottom:7px}table{width:100%!important;border-collapse:collapse;margin:16px 0}
td,th{border:1px solid #DDE6E8;padding:10px 14px;vertical-align:top}th{background:#EDF4F1}td p,th p{margin:0!important}
pre{white-space:pre-wrap}code{font-size:14px;background:#F0F4F6}a{color:#17645A}
@media(max-width:800px){.layout{grid-template-columns:1fr;padding:18px}nav{position:static}article{padding:18px}}
@media print{body{background:white;font-size:11pt}.layout{display:block;padding:0}nav{display:none}header{background:white;color:#173F3A;padding:0}header p{color:#36545B}article{border:0;padding:10px 0;break-before:page}table{break-inside:avoid}a{color:inherit;text-decoration:none}}
</style></head><body><header><h1>''' + title + '''</h1>
<p>상품 확인부터 포장 확정, 작업 복구까지.</p><p>Ctrl+F 검색 · Ctrl+P 인쇄 · 인터넷 없이 이용</p>
</header><div class="layout"><nav aria-label="목차">''' + "".join(navigation) + '''</nav><main>''' + "".join(articles) + '''</main></div></body></html>'''
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    root = Path(__file__).resolve().parents[1]
    build_guide(root / "docs/USER_MANUAL.md", root / "docs/USER_MANUAL.html")
