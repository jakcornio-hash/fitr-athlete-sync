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
SHEET_ID = _get("SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_FILE = _get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
# CRM sheet used for bespoke athlete → coach mapping
CRM_SHEET_ID = _get("CRM_SHEET_ID", "1LA58Pnvgte5HliXXTSvioB1RKnwGGWumkxsXasm7nSo")

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
TAB_COACHES = "Coaches"  # programme → Slack channel mapping for per-coach notifications

# Analytics thresholds (overridable via env)
ENGAGEMENT_THRESHOLD_DAYS = int(_get("ENGAGEMENT_THRESHOLD_DAYS", "28"))
CHAT_LOOKBACK_DAYS = int(_get("CHAT_LOOKBACK_DAYS", "14"))

# Notifications (Slack + email)
SLACK_WEBHOOK_URL = _get("SLACK_WEBHOOK_URL", "")
# Bot token for per-coach channel routing (xoxb-...). When set, sync.py will
# send one Slack message per athlete to the channel mapped in the Coaches tab.
SLACK_BOT_TOKEN = _get("SLACK_BOT_TOKEN", "")
SMTP_FROM = _get("SMTP_FROM", "")
SMTP_PASSWORD = _get("SMTP_PASSWORD", "")
SMTP_TO = _get("SMTP_TO", "")

# Competition planner Typeform — athletes submit once per competition
# Set COMP_FORM_SHEET_ID to the Google Sheet ID of the Typeform responses sheet.
COMP_FORM_SHEET_ID = _get("COMP_FORM_SHEET_ID", "")
COMP_FORM_TAB = _get("COMP_FORM_TAB", "Sheet1")
# Column headers — must match Typeform question text exactly
COMP_FORM_FULL_NAME_COL = "Your full name"
COMP_FORM_EMAIL_COL = "Email address"
COMP_FORM_COMP_NAME_COL = "What is the name of this competition?"
COMP_FORM_DATE_COL = "What is the competition date? (DD/MM/YYYY)"
COMP_FORM_TYPE_COL = "What type of competition is this?"
COMP_FORM_NOTES_COL = "Any notes for your coach about this competition? (optional)"

# Athlete intake Typeform — new athlete onboarding form
# Set INTAKE_FORM_SHEET_ID to the Google Sheet ID of the intake Typeform responses.
INTAKE_FORM_SHEET_ID = _get("INTAKE_FORM_SHEET_ID", "")
INTAKE_FORM_TAB = _get("INTAKE_FORM_TAB", "Sheet1")
# Column headers — must match Typeform question text exactly
INTAKE_FORM_FULL_NAME_COL = "Your full name"
INTAKE_FORM_EMAIL_COL = "Email address"
INTAKE_FORM_GOAL_COL = "What is your main goal?"
INTAKE_FORM_TIER_COL = "What is your current competition level?"
INTAKE_FORM_OCCUPATION_COL = "What is your occupation?"
INTAKE_FORM_EQUIPMENT_COL = "What equipment do you have access to?"
INTAKE_FORM_NOTES_COL = "Anything else your coach should know? (optional)"

# Competitions tab in the main athlete sheet
TAB_COMPETITIONS = "Competitions"
TAB_CHURN_HISTORY = "Churn History"
TAB_MESSAGE_LOG = "Message Log"
TAB_DRAFT_REPLIES = "Draft Replies"
TAB_TRAINING_LOAD = "Training Load"

# Recovery survey (Typeform sheet — separate from main athlete sheet)
RECOVERY_SHEET_ID = _get("RECOVERY_SHEET_ID", "")
RECOVERY_TAB = "New form"
# Column header in the Typeform response sheet for the programme question.
# Must match the question text exactly as it appears in the Google Sheet.
RECOVERY_PROGRAMME_COL = "Which programme are you currently following?"

# Athlete competitive tier labels (shown in profile dropdown and Athletes table)
JST_TIERS = ["Open", "Quarterfinals", "Semifinals", "Games"]

# JST Athlete programme tracks (used in dashboard dropdown and sync validation)
JST_TRACKS = [
    "JST Athlete - 2 Sessions Per Day",
    "JST Athlete - 1 Session Per Day",
    "Strength Bias - 2 Sessions Per Day",
    "Strength Bias - 1 Session Per Day",
    "Gymnastic Bias - 2 Sessions Per Day",
    "Gymnastics Bias - 1 Session Per Day",
    "Engine Bias - 2 Sessions Per Day",
    "Engine Bias - 1 Session Per Day",
    "Competition Ready - 2 Sessions Per Day",
    "Competition Ready - 1 Session Per Day",
]

# Monthly subscription prices by plan name — update to match your actual plans/prices.
# Keys must exactly match the values stored in the "Subscription Plan" column of _DATA.
# Leave a plan out or set to 0 to exclude it from MRR calculations.
SUBSCRIPTION_PRICES = {
    "Bespoke": 300,
    "JST Athlete": 97,
    "Strength Bias": 97,
    "Engine Bias": 97,
    "Gymnastics Bias": 97,
    "Competition Ready": 97,
}
