"""
Safe handling of patient / doctor file uploads.

Defences: extension allow-list, magic-byte check (extension alone can lie), a size
cap, random storage names (the client's filename never touches the filesystem) and
a content type decided by us, never by the client.
"""

import os
import uuid

from flask import current_app
from werkzeug.utils import secure_filename

# extension -> (content type, magic bytes the file must start with)
ALLOWED = {
    "pdf": ("application/pdf", (b"%PDF-",)),
    "png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    "jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    "jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
}


class UploadError(Exception):
    """A user-facing problem with the uploaded file."""


def upload_dir() -> str:
    path = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(path, exist_ok=True)
    return path


def save_upload(file_storage) -> dict:
    """
    Validate and store an uploaded file. Returns the metadata to persist:
    original_name, stored_name, content_type, size. Raises UploadError.
    """
    original = secure_filename(file_storage.filename or "")
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED:
        raise UploadError("Only PDF, PNG and JPG files are allowed.")

    max_bytes = current_app.config["MAX_UPLOAD_BYTES"]
    data = file_storage.stream.read(max_bytes + 1)
    if not data:
        raise UploadError("That file is empty.")
    if len(data) > max_bytes:
        raise UploadError(f"Files can be at most {max_bytes // (1024 * 1024)} MB.")

    content_type, magics = ALLOWED[ext]
    if not data.startswith(magics):
        raise UploadError("That file's contents don't match its type.")

    stored = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(upload_dir(), stored), "wb") as fh:
        fh.write(data)
    return {"original_name": original[:200], "stored_name": stored,
            "content_type": content_type, "size": len(data)}


def delete_stored(stored_name: str) -> None:
    """Remove a stored file; a missing file is not an error."""
    try:
        os.remove(os.path.join(upload_dir(), stored_name))
    except FileNotFoundError:
        pass
