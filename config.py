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
# Comma-separated athlete names to restrict sync to (e.g. for testing). Empty = all athletes.
TEST_ATHLETES = [n.strip() for n in _get("TEST_ATHLETES", "").split(",") if n.strip()]

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

# Benchmark name normalisation — maps lowercase Fitr variant → canonical display name.
# Add new mappings here whenever Fitr returns a name inconsistently.
BENCHMARK_NAME_MAP = {
    # Running
    "5k run": "5K Run",
    "5k": "5K Run",
    "5km run": "5K Run",
    "5km": "5K Run",
    "5k raw": "5K Run",
    "1k run": "1K Run",
    "1km run": "1K Run",
    "1km": "1K Run",
    "400m run": "400m Run",
    "400m": "400m Run",
    "800m run": "800m Run",
    "800m": "800m Run",
    "1 mile run": "Mile Run",
    "1 mile": "Mile Run",
    "mile run": "Mile Run",
    # Rowing
    "500m row": "500m Row",
    "row 500m": "500m Row",
    "1000m row": "1000m Row",
    "row 1000m": "1000m Row",
    "2000m row": "2000m Row",
    "row 2000m": "2000m Row",
    "2k row": "2000m Row",
    # Weightlifting
    "clean & jerk": "Clean & Jerk",
    "clean and jerk": "Clean & Jerk",
    "c&j": "Clean & Jerk",
    "snatch": "Snatch",
    "power clean": "Power Clean",
    "power snatch": "Power Snatch",
    "hang power clean": "Hang Power Clean",
    "hang power snatch": "Hang Power Snatch",
    "clean": "Clean",
    "jerk": "Jerk",
    # Strength
    "back squat": "Back Squat",
    "backsquat": "Back Squat",
    "front squat": "Front Squat",
    "frontsquat": "Front Squat",
    "overhead squat": "Overhead Squat",
    "ohs": "Overhead Squat",
    "deadlift": "Deadlift",
    "bench press": "Bench Press",
    "strict press": "Strict Press",
    "shoulder press": "Strict Press",
    "overhead press": "Strict Press",
    "push press": "Push Press",
    "push jerk": "Push Jerk",
    # Gymnastics
    "pull-up": "Pull-up",
    "pullup": "Pull-up",
    "pull up": "Pull-up",
    "strict pull-up": "Strict Pull-up",
    "strict pull up": "Strict Pull-up",
    "muscle-up": "Muscle-up",
    "muscle up": "Muscle-up",
    "muscleup": "Muscle-up",
    "ring muscle-up": "Ring Muscle-up",
    "ring muscle up": "Ring Muscle-up",
    "rmu": "Ring Muscle-up",
    "bar muscle-up": "Bar Muscle-up",
    "bar muscle up": "Bar Muscle-up",
    "bmu": "Bar Muscle-up",
    "handstand push-up": "Handstand Push-up",
    "handstand push up": "Handstand Push-up",
    "hspu": "Handstand Push-up",
    "toes-to-bar": "Toes-to-Bar",
    "toes to bar": "Toes-to-Bar",
    "t2b": "Toes-to-Bar",
    "ttb": "Toes-to-Bar",
    "double unders": "Double Unders",
    "double under": "Double Unders",
    "du": "Double Unders",
    "pistol squat": "Pistol Squat",
    "pistol": "Pistol Squat",
    # Named WODs
    "fran": "Fran",
    "grace": "Grace",
    "cindy": "Cindy",
    "diane": "Diane",
    "helen": "Helen",
    "karen": "Karen",
    "isabel": "Isabel",
    "linda": "Linda",
    "annie": "Annie",
    "amanda": "Amanda",
    "jackie": "Jackie",
    "elizabeth": "Elizabeth",
}

# 90-day milestone — 1:1 consultation call booking link.
CONSULTATION_BOOKING_URL = _get("CONSULTATION_BOOKING_URL", "https://calendar.app.google/NmzLTiP8Ypo7Yrff7")
# 180-day milestone — t-shirt reward Typeform (address + size collection).
TSHIRT_FORM_URL = _get("TSHIRT_FORM_URL", "https://jstcompete.typeform.com/to/UszYYXgk")
# Google Sheet ID of the Typeform t-shirt responses (set via env var or Streamlit secret).
TSHIRT_FORM_SHEET_ID = _get("TSHIRT_FORM_SHEET_ID", "")
TSHIRT_FORM_TAB = _get("TSHIRT_FORM_TAB", "Sheet1")
# Column headers — must match the Typeform question text exactly as it appears in the Sheet.
TSHIRT_FORM_NAME_COL = "Full name"
TSHIRT_FORM_SIZE_COL = "T-shirt size"
TSHIRT_FORM_ADDRESS1_COL = "Address line 1"
TSHIRT_FORM_ADDRESS2_COL = "Address line 2"
TSHIRT_FORM_CITY_COL = "City"
TSHIRT_FORM_POSTCODE_COL = "Postcode"
TSHIRT_FORM_COUNTRY_COL = "Country"

# Grandslam retention: athletes who joined on/before this date are Founding Members.
# Format: YYYY-MM-DD. Override via FOUNDING_MEMBER_CUTOFF env var.
FOUNDING_MEMBER_CUTOFF = _get("FOUNDING_MEMBER_CUTOFF", "2024-12-31")

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
