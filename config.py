"""Central configuration, loaded from environment / .env file."""
import os
from dotenv import load_dotenv

load_dotenv()


def _get(name, default=None, required=False):
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required env var: {name} (see .env.example)")
    return val


# Google Sheet
SHEET_ID = _get("SHEET_ID", required=True)
GOOGLE_SERVICE_ACCOUNT_FILE = _get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")

# Fitr
FITR_BASE = "https://app.fitr.training"
FITR_ACCESS_TOKEN = _get("FITR_ACCESS_TOKEN", "")
FITR_EMAIL = _get("FITR_EMAIL", "")
FITR_PASSWORD = _get("FITR_PASSWORD", "")
FITR_CLIENT_ID = _get("FITR_CLIENT_ID", "")
FITR_CLIENT_SECRET = _get("FITR_CLIENT_SECRET", "")

# Claude
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = _get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

# Behaviour
LOOKBACK_DAYS = int(_get("LOOKBACK_DAYS", "7"))
MAX_CHAT_SUMMARIES = int(_get("MAX_CHAT_SUMMARIES", "40"))
DRY_RUN = _get("DRY_RUN", "0") == "1"

# Tab names in the sheet
TAB_PR_LOG = "PR Log"
TAB_BENCHMARKS = "Benchmarks"
TAB_DATA = "_DATA"
TAB_RECOVERY = "Recovery"
TAB_SYNC_LOG = "Sync Log"
TAB_COACH_ALERTS = "Coach Alerts"

# Analytics thresholds (overridable via env)
ENGAGEMENT_THRESHOLD_DAYS = int(_get("ENGAGEMENT_THRESHOLD_DAYS", "21"))
CHAT_LOOKBACK_DAYS = int(_get("CHAT_LOOKBACK_DAYS", "14"))

# Recovery survey (Typeform sheet — separate from main athlete sheet)
RECOVERY_SHEET_ID = _get("RECOVERY_SHEET_ID", "")
RECOVERY_TAB = "New form"
