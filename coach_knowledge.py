"""The JST coaching knowledge base, read from Google Drive at runtime.

This is the same material as the Coaching Q&A Claude Project: the project
instructions, the "why" references (physiology, mindset, bias tracks, olympic
lifting, bodyweight), the business and avatar context, the tone of voice, and
the service reference. Roughly 55k tokens, small enough to hand over whole
rather than retrieve from.

It lives in Drive rather than this repo on purpose. The repo is public, so
proprietary coaching material cannot be committed to it — the same reason the
tone of voice document moved to a Sheet. Editing a document in Drive changes
what the next day's drafts are written from, with no deploy.
"""
import config

_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
_TEXT_TYPES = ("text/plain", "text/markdown", "application/json")
_cache = {"text": None, "files": []}


def _drive():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def load(force=False):
    """Return the whole knowledge base as one string. Cached per process."""
    if _cache["text"] is not None and not force:
        return _cache["text"]
    folder = str(getattr(config, "KNOWLEDGE_FOLDER_ID", "") or "").strip()
    if not folder:
        _cache["text"] = ""
        return ""
    try:
        drive = _drive()
        listing = drive.files().list(
            q=f"'{folder}' in parents and trashed=false",
            fields="files(id,name,mimeType)", pageSize=200,
        ).execute().get("files", [])
    except Exception as exc:
        print(f"  ! coaching knowledge unavailable: {type(exc).__name__}: {exc}")
        _cache["text"] = ""
        return ""

    parts, names = [], []
    for f in sorted(listing, key=lambda x: x.get("name", "")):
        name, mime = f.get("name", ""), f.get("mimeType", "")
        try:
            if mime == "application/vnd.google-apps.document":
                body = drive.files().export(
                    fileId=f["id"], mimeType="text/plain").execute()
            elif mime in _TEXT_TYPES or name.lower().endswith((".txt", ".md")):
                body = drive.files().get_media(fileId=f["id"]).execute()
            else:
                continue  # skip binaries: PDFs, images, spreadsheets
            text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
        except Exception as exc:
            print(f"  ! couldn't read knowledge file {name!r}: {exc}")
            continue
        if text.strip():
            parts.append(f"===== {name} =====\n{text.strip()}")
            names.append(name)

    _cache["text"] = "\n\n".join(parts)
    _cache["files"] = names
    return _cache["text"]


def summary():
    """One line on what was loaded, for the sync log."""
    text = _cache["text"]
    if text is None:
        return "coaching knowledge not loaded"
    if not text:
        return "coaching knowledge EMPTY (check the Drive folder is shared and populated)"
    return (f"coaching knowledge: {len(_cache['files'])} files, "
            f"~{len(text) // 4:,} tokens")


def files_loaded():
    return list(_cache["files"])
