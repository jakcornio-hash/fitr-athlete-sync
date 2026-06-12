"""
Fitr API client.

Endpoints below were captured by inspecting the live Fitr coach app
(app.fitr.training) — they are private/undocumented, so treat them as
liable to change. Auth is a bearer token, obtained either from a pasted
token (testing) or an OAuth password-grant login (automation).
"""
import datetime as dt
import time
import requests

import config


class FitrError(RuntimeError):
    pass


class FitrClient:
    def __init__(self):
        self.base = config.FITR_BASE.rstrip("/")
        self.session = requests.Session()
        self.token = None

    # ------------------------------------------------------------------ auth
    def authenticate(self):
        """Get a bearer token. Prefer a pasted token; fall back to login."""
        if config.FITR_ACCESS_TOKEN:
            self.token = config.FITR_ACCESS_TOKEN.strip()
            if not self._token_works():
                raise FitrError(
                    "FITR_ACCESS_TOKEN is set but rejected by Fitr (expired?). "
                    "Grab a fresh one, or use the email/password login instead."
                )
            return

        if config.FITR_EMAIL and config.FITR_PASSWORD and config.FITR_CLIENT_ID:
            self._login()
            return

        raise FitrError(
            "No Fitr credentials. Set FITR_ACCESS_TOKEN, or "
            "FITR_EMAIL + FITR_PASSWORD + FITR_CLIENT_ID + FITR_CLIENT_SECRET."
        )

    def _login(self):
        # OAuth resource-owner password grant. The Fitr SPA posts to this
        # endpoint with its own client_id/client_secret (see HANDOFF.md).
        payload = {
            "email": config.FITR_EMAIL,
            "password": config.FITR_PASSWORD,
            "client_id": config.FITR_CLIENT_ID,
            "client_secret": config.FITR_CLIENT_SECRET,
        }
        r = self.session.post(
            f"{self.base}/api/users/sign_in",
            json=payload,
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if r.status_code != 200:
            raise FitrError(f"Login failed ({r.status_code}): {r.text[:200]}")
        data = r.json()
        token = data.get("access_token") or data.get("token") or (
            data.get("user", {}).get("access_token")
        )
        if not token:
            raise FitrError(f"Login succeeded but no token in response: {list(data)}")
        self.token = token

    def _headers(self):
        return {"Authorization": f"bearer {self.token}", "Accept": "application/json"}

    def _token_works(self):
        r = self.session.get(
            f"{self.base}/api/chat/rooms?page=1", headers=self._headers(), timeout=30
        )
        return r.status_code == 200

    # --------------------------------------------------------------- requests
    def _get(self, path, params=None, retries=3):
        url = f"{self.base}{path}"
        for attempt in range(retries):
            r = self.session.get(url, headers=self._headers(), params=params, timeout=45)
            if r.status_code == 200:
                if not r.text:
                    return None
                return r.json()
            if r.status_code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise FitrError(f"GET {path} -> {r.status_code}: {r.text[:160]}")
        return None

    # ------------------------------------------------------------- benchmarks
    def benchmarks(self, user_id):
        """All benchmarks for one athlete. Returns list of dicts:
        {name, code, measure, units, last_value:{date, value, symbol, note, ...}}"""
        out, page = [], 1
        while True:
            data = self._get("/api/coach/benchmarks", {"user_id": user_id, "page": page})
            if not data:
                break
            items = data.get("items", [])
            out.extend(items)
            pag = data.get("pagination", {})
            if page >= pag.get("total_pages", 1) or not items:
                break
            page += 1
            time.sleep(0.3)
        return out

    # ------------------------------------------------------------- challenges
    def challenges(self, pages=3, per_page=15):
        """Recent challenges (the coach library, newest first)."""
        out = []
        for page in range(1, pages + 1):
            data = self._get(
                "/api/score",
                {
                    "kind": "challenge",
                    "page": page,
                    "per_page": per_page,
                    "q[s]": "created_at desc",
                },
            )
            if not data:
                break
            batch = data if isinstance(data, list) else data.get("items", [])
            if not batch:
                break
            out.extend(batch)
            time.sleep(0.3)
        return out

    def challenge_scores(self, score_id, max_pages=10):
        """Per-athlete scores on a challenge leaderboard."""
        out, page = [], 1
        while page <= max_pages:
            data = self._get(
                f"/api/score/{score_id}/items",
                {"per_page": 25, "page": page, "sort": "asc", "order": "best"},
            )
            if not data:
                break
            items = data.get("items", [])
            out.extend(items)
            pag = data.get("pagination", {})
            if page >= pag.get("total_pages", 1) or not items:
                break
            page += 1
            time.sleep(0.3)
        return out

    def challenge_comments(self, score_id):
        data = self._get(
            "/api/score/comments/",
            {"resource_type": "score/score", "resource_id": score_id},
        )
        if not data:
            return []
        return data if isinstance(data, list) else data.get("items", [])

    # ------------------------------------------------------------------ inbox
    def chat_rooms(self, max_pages=20):
        """Inbox conversations, newest activity first.

        Each room is enriched with two extra keys:
          last_message_date  — datetime.date parsed from last_message.created_at (or None)
          last_message_text  — human-readable string built from text + performance attachments
        """
        out, page = [], 1
        while page <= max_pages:
            data = self._get("/api/chat/rooms", {"page": page})
            if not data:
                break
            items = data.get("items", [])
            for room in items:
                lm = room.get("last_message") or {}
                room["last_message_date"] = _parse_unix_date(lm.get("created_at"))
                room["last_message_text"] = _format_last_message(lm)
            out.extend(items)
            if not items:
                break
            page += 1
            time.sleep(0.3)
        return out

    # --------------------------------------------------------------- clients
    def clients(self, max_pages=30):
        """Coach's athletes (for name <-> fitr id reconciliation)."""
        out, page = [], 1
        while page <= max_pages:
            data = self._get("/api/coach/clients", {"page": page})
            if not data:
                break
            items = data if isinstance(data, list) else data.get("items", [])
            if not items:
                break
            out.extend(items)
            page += 1
            time.sleep(0.3)
        return out


# ------------------------------------------------------------------ helpers

def _parse_unix_date(ts):
    """Convert a Unix timestamp integer to datetime.date, or None."""
    if not ts:
        return None
    try:
        return dt.datetime.fromtimestamp(int(ts)).date()
    except (ValueError, OSError):
        return None


def _format_last_message(lm):
    """
    Build a plain-text description of a last_message object.

    Handles both text messages and performance-log attachments (the
    latter have text=null but carry workout section + athlete notes).
    Returns empty string if there is nothing useful to extract.
    """
    parts = []
    text = (lm.get("text") or "").strip()
    if text:
        parts.append(text)

    for att in lm.get("attachments") or []:
        if att.get("attachment_type") != "performance":
            continue
        res = att.get("resource") or {}
        section = res.get("section") or {}
        section_title = (section.get("title") or "").strip()
        workout_desc = (section.get("description") or section.get("preview_description") or "").strip()
        athlete_note = (res.get("text") or "").strip()
        date_str = (res.get("date") or "").strip()

        block = []
        if date_str:
            block.append(f"[Workout logged {date_str}]")
        if section_title:
            block.append(f"Section: {section_title}")
        if workout_desc:
            # Trim long descriptions to first 200 chars
            block.append(f"Workout: {workout_desc[:200]}")
        if athlete_note:
            block.append(f"Athlete note: {athlete_note}")
        if block:
            parts.append("\n".join(block))

    return "\n\n".join(parts)
