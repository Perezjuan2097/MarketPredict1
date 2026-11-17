"""Bounded readers for tabular UTF-8 CSV and single-sheet OOXML workbooks."""
import csv
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import PurePosixPath
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile
from zlib import error as ZipDataError

from app.security import InvalidInput

MAX_COLUMNS = 64
MAX_CELL_LENGTH = 512
SHEET = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


@dataclass(frozen=True)
class ExcelNumber:
    value: str
    epoch_1904: bool


def _xml(archive, path):
    raw = archive.read(path)
    text = raw.decode("utf-8-sig")
    if text.count("<") > 500_000:
        raise InvalidInput("El documento XML supera el límite de elementos.")
    if "\x00" in text or re.search(r"<!DOCTYPE|<!ENTITY", text, re.I):
        raise InvalidInput("El archivo Excel contiene declaraciones XML no permitidas.")
    return ET.fromstring(text)


def _xlsx(data, max_rows):
    with ZipFile(BytesIO(data)) as archive:
        files = archive.infolist()
        names = {part.filename for part in files}
        if (len(files) > 128 or len(names) != len(files)
                or sum(part.file_size for part in files) > 8 * 1024 * 1024
                or any(part.flag_bits & 1 for part in files)
                or any(n.startswith(("/", "\\")) or "\\" in n or ".." in PurePosixPath(n).parts
                       or any(x in n.lower() for x in ("vbaproject", "externallinks/", "embeddings/")) for n in names)):
            raise InvalidInput("El contenedor Excel supera los límites o tiene contenido no permitido.")
        types = _xml(archive, "[Content_Types].xml")
        if not any(part.get("PartName") == "/xl/workbook.xml" and
                   part.get("ContentType") == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
                   for part in types):
            raise InvalidInput("El archivo debe ser un libro XLSX sin macros.")
        for name in names:
            if name.endswith(".rels") and any(rel.get("TargetMode", "").lower() == "external"
                                             for rel in _xml(archive, name)):
                raise InvalidInput("Los libros con enlaces externos no se admiten.")
        book = _xml(archive, "xl/workbook.xml")
        sheets = book.findall(f"{SHEET}sheets/{SHEET}sheet")
        if len(sheets) != 1 or sheets[0].get("state", "visible") != "visible":
            raise InvalidInput("El libro debe contener una sola hoja visible.")
        relations = _xml(archive, "xl/_rels/workbook.xml.rels")
        relationship = next((rel for rel in relations if rel.get("Id") == sheets[0].get(REL_ID)
                             and rel.get("Type", "").endswith("/worksheet")), None)
        if relationship is None or not relationship.get("Target"):
            raise InvalidInput("No se pudo localizar la hoja de cálculo.")
        target = relationship.get("Target")
        path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
        if not path.startswith("xl/worksheets/") or not path.endswith(".xml"):
            raise InvalidInput("La ubicación de la hoja no es válida.")
        props = book.find(f"{SHEET}workbookPr")
        epoch_1904 = props is not None and props.get("date1904") in ("1", "true")
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = ["".join(t.text or "" for t in entry.iter(SHEET + "t"))
                      for entry in _xml(archive, "xl/sharedStrings.xml").findall(SHEET + "si")]
        sheet = _xml(archive, path)
        if (sheet.tag != SHEET + "worksheet" or next(sheet.iter(SHEET + "f"), None) is not None
                or sheet.find(SHEET + "mergeCells") is not None):
            raise InvalidInput("La hoja contiene fórmulas, celdas combinadas o un formato no permitido.")
        previous = 0
        for row in sheet.findall(f"{SHEET}sheetData/{SHEET}row"):
            number = int(row.attrib["r"])
            if number <= previous or number > max_rows + 1:
                raise InvalidInput("La hoja supera el límite de filas o su orden es inválido.")
            previous = number
            cells = {}
            for cell in row.findall(SHEET + "c"):
                match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", cell.get("r", ""))
                if not match or int(match[2]) != number:
                    raise InvalidInput("Coordenada de celda inválida.")
                column = 0
                for letter in match[1]:
                    column = column * 26 + ord(letter) - 64
                if column > MAX_COLUMNS or column in cells:
                    raise InvalidInput("Columna duplicada o fuera del límite.")
                kind = cell.get("t", "n")
                value = cell.findtext(SHEET + "v", "")
                if kind == "inlineStr":
                    value = "".join(t.text or "" for t in cell.iter(SHEET + "t"))
                elif kind == "s":
                    index = int(value)
                    if index < 0 or index >= len(shared):
                        raise InvalidInput("Referencia de texto inválida.")
                    value = shared[index]
                elif kind == "n" and value:
                    value = ExcelNumber(value, epoch_1904)
                elif kind not in ("n", "str", "d"):
                    raise InvalidInput("Las celdas con errores o valores booleanos no se admiten.")
                cells[column] = value
            yield number, [cells.get(col, "") for col in range(1, max(cells, default=0) + 1)]


def read_table(data, extension, max_rows):
    if extension not in (".csv", ".xlsx") or not data:
        raise InvalidInput("Selecciona un CSV UTF-8 o un XLSX no vacío.")
    try:
        if extension == ".xlsx":
            if not data.startswith(b"PK\x03\x04"):
                raise InvalidInput("El contenido del archivo no corresponde a XLSX.")
            rows = _xlsx(data, max_rows)
        else:
            text = data.decode("utf-8-sig")
            if "\x00" in text or data.startswith(b"PK\x03\x04"):
                raise InvalidInput("El contenido del archivo no corresponde a CSV.")
            first = text.split("\n", 1)[0]
            delimiter = ";" if first.count(";") > first.count(",") else ","
            rows = enumerate(csv.reader(StringIO(text, newline=""), delimiter=delimiter, strict=True), 1)
        number, header = next(rows, (0, []))
        if number != 1 or not 2 <= len(header) <= MAX_COLUMNS:
            raise InvalidInput("La primera fila debe contener entre 2 y 64 encabezados.")
        if any(not isinstance(value, str) or not value.strip() or not value.isprintable()
               or len(value) > 100 for value in header):
            raise InvalidInput("Los encabezados deben ser textos no vacíos de hasta 100 caracteres.")
        columns = [value.strip() for value in header]
        if len({value.casefold() for value in columns}) != len(columns):
            raise InvalidInput("Hay encabezados duplicados.")
        records = []
        for number, row in rows:
            if number > max_rows + 1:
                raise InvalidInput("El archivo supera el límite de filas.")
            if extension == ".xlsx" and len(row) < len(columns):
                row += [""] * (len(columns) - len(row))
            if len(row) != len(columns) or any(len(v.value if isinstance(v, ExcelNumber) else v) > MAX_CELL_LENGTH
                                              for v in row):
                raise InvalidInput(f"Fila {number}: columnas incompatibles o celda demasiado larga.")
            if any(value != "" for value in row):
                records.append((number, row))
        if not records:
            raise InvalidInput("El archivo no contiene registros.")
        return columns, records
    except (BadZipFile, csv.Error, ET.ParseError, UnicodeError, KeyError, ValueError, RuntimeError,
            NotImplementedError, ZipDataError, OverflowError) as exc:
        if isinstance(exc, InvalidInput):
            raise
        raise InvalidInput("El contenido del archivo está dañado o tiene un formato no admitido.") from exc
