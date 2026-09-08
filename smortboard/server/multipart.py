"""minimal multipart/form-data parsing for the single attachment-upload endpoint

stdlib dropped cgi.FieldStorage's parsing helpers; this covers exactly the one shape we need
(a single file field) rather than pulling in a dependency for it
"""

from dataclasses import dataclass


@dataclass
class UploadedFile:
    filename: str
    content_type: str
    data: bytes


class MultipartError(Exception):
    """the request body was not a well-formed multipart/form-data payload"""


def parse_boundary(content_type_header: str) -> bytes:
    for part in content_type_header.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary=") :].strip('"')
            return boundary.encode()
    raise MultipartError("missing boundary in content-type")


def parse_first_file(body: bytes, boundary: bytes) -> UploadedFile:
    """the first file part in the body — sufficient for a single-attachment upload"""
    delimiter = b"--" + boundary
    sections = body.split(delimiter)
    for section in sections:
        section = section.strip(b"\r\n")
        if not section or section == b"--":
            continue
        if b"\r\n\r\n" not in section:
            continue
        headers_blob, _, content = section.partition(b"\r\n\r\n")
        content = content.rstrip(b"\r\n")
        headers = _parse_headers(headers_blob.decode("utf-8", errors="replace"))
        disposition = headers.get("content-disposition", "")
        if "filename=" not in disposition:
            continue
        filename = _extract_param(disposition, "filename")
        content_type = headers.get("content-type", "application/octet-stream")
        return UploadedFile(filename=filename, content_type=content_type, data=content)
    raise MultipartError("no file part found in multipart body")


def _parse_headers(blob: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in blob.split("\r\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    return headers


def _extract_param(header_value: str, param: str) -> str:
    for part in header_value.split(";"):
        part = part.strip()
        if part.startswith(f"{param}="):
            return part[len(f"{param}=") :].strip('"')
    raise MultipartError(f"missing {param} in header: {header_value!r}")
