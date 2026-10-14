"""Strict bounded screenshot uploads. Contents are never executed or served inline."""
from __future__ import annotations

import hashlib
import re
import struct

from control_center.domain import ControlCenterValidationError

MAX_ATTACHMENT_BYTES = 1024 * 1024
MAX_TICKET_ATTACHMENTS = 3


def validate_attachment(name: str, content_type: str, data: bytes) -> tuple[str, str, bytes, str]:
    """Validate declared type against image headers; not a full image decoder."""
    if not isinstance(name, str) or not 1 <= len(name) <= 120:
        raise ControlCenterValidationError("Nome do anexo invalido.")
    if name != name.strip() or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_. ()-]*", name) or ".." in name:
        raise ControlCenterValidationError("Nome do anexo invalido.")
    extension = name.rsplit(".", 1)[-1].lower()
    if name.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ControlCenterValidationError("Nome do anexo invalido.")
    types = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
    if extension not in types or content_type != types[extension]:
        raise ControlCenterValidationError("Use uma imagem PNG, JPEG ou WebP.")
    if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_ATTACHMENT_BYTES:
        raise ControlCenterValidationError("O anexo deve ter no maximo 1 MiB.")
    valid = False
    if content_type == "image/png":
        valid = (len(data) >= 45 and data.startswith(b"\x89PNG\r\n\x1a\n")
                 and data[8:16] == b"\x00\x00\x00\rIHDR"
                 and data[-12:] == b"\x00\x00\x00\x00IEND\xaeB`\x82")
        if valid:
            width, height = struct.unpack(">II", data[16:24])
            valid = 0 < width <= 16384 and 0 < height <= 16384 and width * height <= 40_000_000
    elif content_type == "image/jpeg":
        valid = len(data) >= 12 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
    elif content_type == "image/webp":
        valid = (len(data) >= 20 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
                 and data[12:16] in {b"VP8 ", b"VP8L", b"VP8X"}
                 and int.from_bytes(data[4:8], "little") == len(data) - 8)
    if not valid:
        raise ControlCenterValidationError("Conteudo do anexo nao corresponde a uma imagem suportada.")
    return name, content_type, data, hashlib.sha256(data).hexdigest()
