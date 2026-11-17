"""Export a stored private report snapshot as CSV or a small paginated PDF."""
import csv
from io import StringIO
import json
from textwrap import wrap


def report_rows(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from report_rows(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for i, child in enumerate(value, 1):
            yield from report_rows(child, f"{path}.{i}")
    else:
        yield path, value


def _safe_cell(value):
    if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@"))
                                  or value.startswith(("\t", "\r", "\n"))):
        return "'" + value
    return value


def csv_bytes(report):
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("campo", "valor"))
    writer.writerows((_safe_cell(path), _safe_cell(value)) for path, value in report_rows(report))
    return output.getvalue().encode("utf-8-sig")


def _literal(line):
    data = line.encode("cp1252", errors="replace")
    return b"(" + b"".join(bytes([b]) if 32 <= b < 127 and b not in (40, 41, 92)
                           else f"\\{b:03o}".encode() for b in data) + b")"


def pdf_bytes(report):
    # WinAnsi is bounded and broadly available. CSV preserves all Unicode.
    lines = ["MarketPredict Enterprise - Reporte privado"]
    for path, value in report_rows(report):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        lines.extend(wrap(f"{path}: {text}", width=105) or [""])
    chunks = [lines[i:i + 50] for i in range(0, len(lines), 50)]
    ids = [4 + 2 * i for i in range(len(chunks))]
    kids = " ".join(f"{i} 0 R" for i in ids)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(ids)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for i, page in zip(ids, chunks):
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {i + 1} 0 R >>".encode())
        stream = b"BT /F1 9 Tf 12 TL 36 756 Td\n" + b"\n".join(_literal(line) + b" Tj T*" for line in page) + b"\nET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, item in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{i} 0 obj\n".encode() + item + b"\nendobj\n")
    start = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    result.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(result)
