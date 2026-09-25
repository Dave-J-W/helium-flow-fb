"""Render a Markdown file to PDF: markdown-it -> styled HTML -> headless Edge print."""
import subprocess
import sys
from pathlib import Path

from markdown_it import MarkdownIt

src = Path(sys.argv[1])
pdf = src.with_suffix(".pdf")
html_path = Path(sys.argv[2]) / (src.stem + ".html")

md = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")
body = md.render(src.read_text(encoding="utf-8"))

css = """
@page { size: Letter; margin: 16mm 14mm; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 10pt; line-height: 1.38; color: #1a1a1a; }
h1 { font-size: 18pt; border-bottom: 2px solid #333; padding-bottom: 4px; }
h2 { font-size: 14pt; border-bottom: 1px solid #999; padding-bottom: 2px; margin-top: 22px; page-break-after: avoid; }
h3 { font-size: 11.5pt; margin-top: 16px; page-break-after: avoid; }
h4 { font-size: 10.5pt; page-break-after: avoid; }
code { font-family: Consolas, monospace; font-size: 9pt; background: #f2f2f2; padding: 0 2px; }
pre { background: #f6f6f6; border: 1px solid #ddd; padding: 6px 8px; font-size: 8.5pt;
      line-height: 1.25; white-space: pre; overflow: hidden; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; margin: 8px 0; font-size: 8.8pt; width: 100%; page-break-inside: auto; }
th, td { border: 1px solid #bbb; padding: 3px 5px; vertical-align: top; text-align: left; }
th { background: #e9eef5; }
tr { page-break-inside: avoid; }
"""
html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{src.stem}</title>
<style>{css}</style></head><body>{body}</body></html>"""
html_path.write_text(html, encoding="utf-8")

edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
subprocess.run([edge, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                f"--print-to-pdf={pdf}", html_path.as_uri()], check=True, timeout=120)
print(pdf, pdf.stat().st_size, "bytes")
