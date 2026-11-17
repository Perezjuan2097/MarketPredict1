"""Versioned sales data contract and pure import validation."""
from datetime import date, timedelta
from decimal import Decimal, DecimalException, ROUND_HALF_UP
from hashlib import sha256
import json
import re
from unicodedata import normalize

from itsdangerous import BadData, URLSafeTimedSerializer

from app.security import InvalidInput
from app.services.dataset_files import ExcelNumber, read_table

CONTRACT = "sales-daily-v1"
TRANSACTION_CONTRACT = "sales-daily-v2"
FIELDS = ("date", "product", "quantity", "price", "revenue", "category", "transaction_id")

REQUIRED = {"date", "product", "quantity"}
SCALE = 10000
MAX_SCALED = 10**14
PREVIEW_SECONDS = 900



def contract_for(mapping):
    return TRANSACTION_CONTRACT if mapping and "transaction_id" in mapping else CONTRACT


def _signer(secret):
    return URLSafeTimedSerializer(secret, salt="marketpredict.dataset-preview.v1",
                                  signer_kwargs={"key_derivation": "hmac", "digest_method": sha256})


def sign_preview(secret, scope, mapping, digest):
    return _signer(secret).dumps({"scope": scope, "mapping": mapping, "digest": digest, "contract": contract_for(mapping)})


def verify_preview(secret, token):
    if not isinstance(token, str) or len(token) > 8192:
        raise InvalidInput("Confirma una vista previa válida.")
    try:
        return _signer(secret).loads(token, max_age=PREVIEW_SECONDS)
    except BadData as exc:
        raise InvalidInput("La vista previa no es válida o caducó.") from exc


def _text(value):
    return value.value if isinstance(value, ExcelNumber) else value


def _day(value):
    if isinstance(value, ExcelNumber):
        serial = Decimal(value.value)
        if not serial.is_finite() or not 0 <= serial <= 2958465 or serial != serial.to_integral_value():
            raise ValueError()
        days = int(serial)
        if value.epoch_1904:
            return date(1904, 1, 1) + timedelta(days=days)
        if days in (0, 60):
            raise ValueError()
        return date(1899, 12, 31) + timedelta(days=days - (days >= 61))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError()
    return date.fromisoformat(value)


def _scaled(value, nonnegative=False):
    number = Decimal(_text(value))
    if not number.is_finite() or abs(number) > MAX_SCALED // SCALE or (nonnegative and number < 0):
        raise ValueError()
    scaled = number * SCALE
    if scaled != scaled.to_integral_value():
        raise ValueError()
    return int(scaled)


def amount(scaled):
    return format(Decimal(scaled) / SCALE, "f") if scaled is not None else None


def _value(field, raw):
    if field == "date":
        return _day(raw if isinstance(raw, ExcelNumber) else raw.strip())
    if field in ("quantity", "price", "revenue"):
        return _scaled(raw, nonnegative=field == "price")
    text = normalize("NFC", _text(raw).strip())
    if len(text) > 160 or not text.isprintable():
        raise ValueError()
    return text


def _preview_record(row):
    return {"date": row["date"].isoformat(), "product": row["product"], "category": row["category"],
            **{key: amount(row[key]) for key in ("quantity", "price", "revenue")},
            **({"transaction_id": row["transaction_id"]} if row["transaction_id"] is not None else {})}


def parse_import(data, extension, max_rows, mapping=None):
    columns, source = read_table(data, extension, max_rows)
    if mapping is None:
        return {"columns": columns, "sample": [[_text(value) for value in row] for _, row in source[:5]]}
    if (not isinstance(mapping, dict) or not REQUIRED <= mapping.keys()
            or not mapping.keys() <= set(FIELDS) or not {"price", "revenue"}.intersection(mapping)
            or any(not isinstance(column, str) or column not in columns for column in mapping.values())
            or len(set(mapping.values())) != len(mapping)):
        raise InvalidInput("Mapea fecha, producto y cantidad, más precio o ingresos, usando columnas distintas.")
    indices = {field: columns.index(column) for field, column in mapping.items()}
    quality = {"source_rows": len(source), "valid_rows": 0, "invalid_rows": 0, "duplicate_rows": 0,
               "missing_cells": {field: 0 for field in indices}, "revenue_discrepancies": 0}
    records, errors, seen = [], [], set()
    for number, values in source:
        row = dict.fromkeys(FIELDS)
        issues = []
        for field, index in indices.items():
            raw = values[index]
            if not _text(raw).strip():
                quality["missing_cells"][field] += 1
                if field in REQUIRED or field == "transaction_id":
                    issues.append((field, "missing"))
                continue
            try:
                row[field] = _value(field, raw)
            except (ValueError, DecimalException, OverflowError):
                issues.append((field, "type_or_range"))
        if not issues:
            if row["price"] is None and row["revenue"] is None:
                issues.append(("revenue", "missing"))
            elif row["price"] is not None:
                derived = int((Decimal(row["quantity"] * row["price"]) / SCALE)
                              .to_integral_value(rounding=ROUND_HALF_UP))
                if row["revenue"] is None:
                    if abs(derived) > MAX_SCALED:
                        issues.append(("revenue", "type_or_range"))
                    else:
                        row["revenue"] = derived
                elif row["revenue"] != derived:
                    quality["revenue_discrepancies"] += 1
        if not issues:
            key = tuple(row[field] for field in FIELDS)
            if key in seen:
                quality["duplicate_rows"] += 1
                issues.append(("row", "duplicate"))
            else:
                seen.add(key)
        if issues:
            quality["invalid_rows"] += 1
            errors.extend({"row": number, "field": field, "code": code}
                          for field, code in issues[:max(0, 30 - len(errors))])
        else:
            records.append({"row_number": number, **row})
    quality["valid_rows"] = len(records)
    return {"columns": columns, "sample": [_preview_record(row) for row in records[:10]],
            "quality": quality, "errors": errors, "records": records}


def import_identity(data, extension, mapping):
    source = sha256(data).hexdigest()
    fingerprint = sha256(json.dumps([contract_for(mapping), extension, source, mapping], sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()
    return source, fingerprint
