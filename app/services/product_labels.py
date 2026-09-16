"""Secure product scan labels — opaque QR payload + Code128 barcode (SKU only)."""

from __future__ import annotations

import base64
import io
import re

import qrcode
from barcode import Code128
from barcode.writer import ImageWriter

PRODUCT_QR_PREFIX = "DUKA+SKU:"

# Code128 charset — reject control chars; max length for retail scanners
_SKU_SAFE = re.compile(r"^[\x20-\x7E]{1,80}$")


def normalize_scan_sku(raw: str) -> str:
    sku = (raw or "").strip()
    if sku.startswith("\ufeff"):
        sku = sku[1:]
    return sku


def product_qr_payload(sku: str) -> str:
    return f"{PRODUCT_QR_PREFIX}{normalize_scan_sku(sku)}"


def barcode_value(sku: str, explicit_barcode: str | None = None) -> str:
    value = normalize_scan_sku(explicit_barcode or sku)
    if not value or not _SKU_SAFE.match(value):
        raise ValueError("Invalid SKU/barcode for label generation")
    return value


def _png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_qr_png_base64(payload: str, box_size: int = 8) -> str:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box_size, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1E213D", back_color="#FFFFFF")
    return base64.b64encode(_png_bytes(img)).decode("ascii")


def generate_barcode_png_base64(value: str) -> str:
    code = Code128(value, writer=ImageWriter())
    buf = io.BytesIO()
    code.write(
        buf,
        options={
            "module_width": 0.35,
            "module_height": 12.0,
            "font_size": 10,
            "text_distance": 4,
            "quiet_zone": 4,
        },
    )
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_product_label_assets(*, sku: str, barcode: str | None = None) -> dict:
    sku_norm = normalize_scan_sku(sku)
    if not sku_norm:
        raise ValueError("Product SKU is required")
    bc = barcode_value(sku_norm, barcode)
    qr_payload = product_qr_payload(sku_norm)
    return {
        "sku": sku_norm,
        "barcode_value": bc,
        "qr_payload": qr_payload,
        "qr_png_base64": generate_qr_png_base64(qr_payload),
        "barcode_png_base64": generate_barcode_png_base64(bc),
        "format": "duka_plus_v1",
    }
