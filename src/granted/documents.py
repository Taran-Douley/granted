"""Documents in and out, in the formats a charity actually keeps.

Past applications live in Word and PDF, not markdown, so the archive reader has
to open both or it learns nothing from the files that matter most. And what the
agent writes for a bid has to open in Word, because that is where a trustee
edits it and where it gets pasted from into a funder's portal.

Word files are read and written with the standard library alone: a .docx is a
zip of XML, and the parts needed here are small. PDF text needs `pypdf`; without
it a PDF is still counted (its name carries the outcome) but its text is not read.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{W}}}"

# Characters XML 1.0 forbids. A pasted form field can carry them, and one is
# enough for Word to refuse the whole file.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


# ---------------------------------------------------------------------- reading

def read_text(p: Path) -> str:
    """Plain text of a past application, or "" when there is none to read.

    Never raises: one corrupt or password-protected file must not stop the
    archive scan, and its name still tells the archive who it went to.
    """
    suffix = p.suffix.lower()
    try:
        if suffix in (".md", ".txt"):
            return p.read_text(encoding="utf-8", errors="ignore")
        if suffix == ".docx":
            return _docx_text(p)
        if suffix == ".pdf":
            return _pdf_text(p)
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError, ValueError):
        return ""
    return ""


def _docx_text(p: Path) -> str:
    with zipfile.ZipFile(p) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    paragraphs = []
    for para in root.iter(f"{_W}p"):
        parts = []
        for node in para.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n\n".join(t for t in paragraphs if t.strip())


def _pdf_text(p: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(p))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception:                                            # noqa: BLE001
        # pypdf raises its own family for encrypted and malformed files.
        return ""


# ---------------------------------------------------------------------- writing

def write_docx(path: Path, blocks: list[tuple[str, object]]) -> Path:
    """Write a Word document from simple blocks.

        ("title", text)  ("h1", text)  ("h2", text)  ("p", text)
        ("bullet", text)  ("quote", text)  ("table", [[header, ...], [cell, ...], ...])

    A4, Calibri, UK English. Enough structure for a trustee to edit and a
    coordinator to paste from; nothing a funder's portal will choke on.
    """
    body = "".join(_block(kind, content) for kind, content in blocks)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("word/document.xml", document)
    return path


_STYLE_OF = {"title": "Title", "h1": "Heading1", "h2": "Heading2",
             "quote": "Quote", "bullet": "Bullet", "p": None}


def _block(kind: str, content: object) -> str:
    if kind == "table":
        return _table(content)                                   # type: ignore[arg-type]
    text = str(content)
    if kind == "bullet":
        text = "•\t" + text
    return _para(text, _STYLE_OF.get(kind))


def _run(text: str) -> str:
    pieces = []
    for i, line in enumerate(_CONTROL.sub("", str(text)).split("\n")):
        if i:
            pieces.append("<w:br/>")
        pieces.append(f'<w:t xml:space="preserve">{escape(line)}</w:t>')
    return "<w:r>" + "".join(pieces) + "</w:r>"


def _para(text: str, style: str | None = None) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}{_run(text)}</w:p>"


def _table(rows: list[list[object]]) -> str:
    if not rows:
        return ""
    edge = 'w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"'
    borders = "".join(f"<w:{side} {edge}/>" for side in
                      ("top", "left", "bottom", "right", "insideH", "insideV"))
    width = max(len(r) for r in rows)
    trs = []
    for r, row in enumerate(rows):
        cells = list(row) + [""] * (width - len(row))
        tcs = "".join(
            '<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>'
            f'{_para(str(c), "TableHeader" if r == 0 else None)}</w:tc>'
            for c in cells)
        trs.append(f"<w:tr>{tcs}</w:tr>")
    grid = "".join("<w:gridCol/>" for _ in range(width))
    # A paragraph after the table: Word expects the body to end on one.
    return (f'<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/>'
            f"<w:tblBorders>{borders}</w:tblBorders></w:tblPr>"
            f"<w:tblGrid>{grid}</w:tblGrid>{''.join(trs)}</w:tbl><w:p/>")


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    "</Types>"
)

_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)

_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    "</Relationships>"
)

_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles xmlns:w="{W}">'
    "<w:docDefaults><w:rPrDefault><w:rPr>"
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/>'
    '<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-GB"/>'
    "</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>"
    '<w:spacing w:after="120" w:line="276" w:lineRule="auto"/>'
    "</w:pPr></w:pPrDefault></w:docDefaults>"
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
    '<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="240"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="40"/><w:szCs w:val="40"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
    '<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/>'
    '<w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/><w:szCs w:val="30"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
    '<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/>'
    '<w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/>'
    '<w:qFormat/><w:pPr><w:ind w:left="567"/></w:pPr><w:rPr><w:i/><w:color w:val="595959"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Bullet"><w:name w:val="Granted Bullet"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:ind w:left="360" w:hanging="360"/></w:pPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="TableHeader"><w:name w:val="Granted Table Header"/>'
    '<w:basedOn w:val="Normal"/><w:rPr><w:b/></w:rPr></w:style>'
    "</w:styles>"
)
