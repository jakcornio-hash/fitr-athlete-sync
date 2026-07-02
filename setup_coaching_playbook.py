"""
Create the 'Coaching Playbook' tab in the main Google Sheet and populate it
with initial coaching reference content from JST documents.

Run once:
    python3 setup_coaching_playbook.py

The tab has five columns:
    Scenario | Subject | Notes | Example | Source

Scenario values must match coaching_voice.SCENARIOS so the dashboard can
filter by them.
"""
import sys
import os

# ── Bootstrap (same pattern as sync.py) ──────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

import gspread
from google.oauth2.service_account import Credentials

import config

SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

TAB_NAME = "Coaching Playbook"

HEADERS = ["Scenario", "Subject", "Notes", "Example", "Source"]

# ── Initial content ──────────────────────────────────────────────────────────
# Columns: [Scenario, Subject, Notes, Example, Source]
# Keep Notes under ~300 chars so they fit in a prompt injection block.
# Example = an actual short message the coach could send (optional).

ROWS = [
    # ── JST Coaching Method ──────────────────────────────────────────────────
    [
        "general",
        "The JST way of answering athlete questions",
        "1. Start with something specific and positive. 2. Give one or two clear focus points — not ten. "
        "3. Explain WHY behind any instruction (bought-in athletes train better). "
        "4. End with one clear next action. Never three.",
        "",
        "JST Community Coaching Reference",
    ],
    [
        "general",
        "16-Year-Old Rule",
        "Before sending any message: would a smart 16-year-old understand this without Googling it? "
        "If not, simplify. No jargon, no coaching textbook language.",
        "",
        "JST Tone of Voice Guidelines",
    ],
    [
        "general",
        "Minimum Effective Dose (MED) principle",
        "Find the smallest dose of training stimulus that produces the desired result. "
        "More is not better — better is better. Athletes often need permission to do less.",
        "",
        "JST Mindset Reference Guide",
    ],
    [
        "general",
        "Purpose behind the programme",
        "Communicate the WHY behind training blocks, not just the WHAT. "
        "Athletes who understand why they're training a certain way are more compliant and more resilient.",
        "",
        "JST Mindset Reference Guide",
    ],

    # ── Mindset & Motivation ─────────────────────────────────────────────────
    [
        "mindset",
        "Identity-based habits (Atomic Habits)",
        "Every rep is a vote for the person you want to be. "
        "Don't focus on the outcome — focus on the identity. 'I'm someone who trains consistently.' "
        "When an athlete is struggling with motivation, anchor it back to identity, not goals.",
        "Hey [Name] — consistency is what builds the athlete you want to be. "
        "Every session, even a bad one, is a vote cast. Keep voting.\n\nJak",
        "JST Mindset Reference Guide (Atomic Habits)",
    ],
    [
        "mindset",
        "Chimp Paradox — managing emotional brain",
        "Athletes have a 'Chimp' (emotional, impulsive) and a 'Human' (rational, goal-focused). "
        "After a bad session, the Chimp often catastrophises. "
        "Coach response: acknowledge the feeling first, then redirect to rational perspective.",
        "Hey [Name] — one session doesn't define a block. The data over time does. "
        "How's the rest of the week looking?\n\nJak",
        "JST Mindset Reference Guide (Chimp Paradox)",
    ],
    [
        "mindset",
        "Conscious Coaching — connecting with the person",
        "Coaches must build trust before they can build athletes. "
        "Use what you know about the athlete's life context, not just their training data, "
        "when messaging. A result is always secondary to how the person is doing.",
        "",
        "JST Mindset Reference Guide (Conscious Coaching)",
    ],
    [
        "missed_session",
        "Responding to a missed session",
        "Don't guilt-trip. Acknowledge life happens. "
        "Offer to adjust the programme if load is the issue. "
        "Focus on what's still possible this week, not what was missed.",
        "Hey [Name] — no stress on missing [session]. Life happens. "
        "Let me know if the loading's felt heavy lately — happy to adjust.\n\nJak",
        "JST Community Coaching Reference",
    ],
    [
        "missed_session",
        "Athlete hasn't logged in 7–14 days (soft check-in)",
        "Short check-in, no pressure. Ask how they're getting on. "
        "Don't assume they've quit — life gets busy. "
        "Leave the door open without making them feel guilty.",
        "Hey [Name] — haven't seen you in the logs for a bit. "
        "Hope everything's good — just checking in. "
        "Drop me a message if anything's come up.\n\nJak",
        "JST Community Coaching Reference",
    ],

    # ── Competition ──────────────────────────────────────────────────────────
    [
        "comp_10wk",
        "10-week competition prep — setting expectations",
        "This is the turning point where everything in training gets pointed at the competition. "
        "Communicate the phase shift clearly: volume may drop, intensity rises, "
        "recovery becomes non-negotiable. Don't leave the athlete guessing why their programme changed.",
        "",
        "JST Annual Programming Context 26/27",
    ],
    [
        "comp_race_week",
        "Race week — taper mindset",
        "Athletes often feel flat or anxious in taper week. This is normal — the body is storing up. "
        "Remind them that feeling 'off' in taper is usually a sign the taper is working. "
        "Keep communication brief and reassuring, not detailed.",
        "Hey [Name] — feeling flat or weird this week is completely normal in taper. "
        "It means the training is sitting. Trust it.\n\nJak",
        "JST Community Coaching Reference",
    ],
    [
        "post_comp",
        "Post-comp debrief — what to ask",
        "Get four things: result/placing, what went well, what they'd do differently, "
        "and how the body feels. Don't ask everything at once in one long list — "
        "keep it conversational. The debrief feeds the next training block.",
        "",
        "JST Community Coaching Reference",
    ],
    [
        "comp_result",
        "Delivering post-comp feedback when result was below expectation",
        "Acknowledge the result without dismissing it. "
        "Find something specific and genuine that went well. "
        "Then one forward-looking observation. "
        "Never lead with what went wrong.",
        "Hey [Name] — [specific positive] was genuinely strong. "
        "That's the foundation we'll build on. "
        "Let's talk through the debrief when you're ready.\n\nJak",
        "JST Community Coaching Reference",
    ],

    # ── New PB / Goal Achievement ─────────────────────────────────────────────
    [
        "new_pr",
        "Acknowledging a new PB",
        "Be specific about the number and what it means. "
        "Tie it back to something they've worked on — it shows you've been watching. "
        "Keep it short. Genuine > effusive.",
        "",
        "JST Community Coaching Reference",
    ],
    [
        "goal_achieved",
        "North Star Goal achieved — setting the next one",
        "Celebrate it properly, then immediately look forward. "
        "Ask: 'What's the next big thing you want to chase?' "
        "Athletes without a North Star goal lose momentum fast.",
        "Hey [Name] — that's it, you've done it. [Benchmark: value]. "
        "That's what we've been building toward.\n\n"
        "Time to set the next one — what's the big thing you want to chase?\n\nJak",
        "JST Community Coaching Reference",
    ],

    # ── Onboarding ───────────────────────────────────────────────────────────
    [
        "onboarding",
        "Setting expectations for new athletes",
        "In the first week: confirm they've filled in the intake form, "
        "explain the recovery check-in and why it matters (it's how load is managed), "
        "and tell them to message with anything. "
        "Don't dump everything in one message — keep it to two actions max.",
        "",
        "JST Community Coaching Reference",
    ],
    [
        "onboarding",
        "Athlete avatar — Grit Track (most common)",
        "Age 25-40, full-time work, 3-5 days training, wants to compete. "
        "Pain: wasted sessions, no progression, can't seem to peak. "
        "Dream: compete at a high level, be the fittest in the gym. "
        "Coach with: clear purpose behind programming, specific feedback on benchmarks.",
        "",
        "JST Avatar Reference",
    ],
    [
        "onboarding",
        "Athlete avatar — Grunt Track",
        "Strength-focused. Wants a big squat/deadlift, not necessarily CrossFit competition. "
        "Pain: CrossFit gyms don't take lifting seriously enough. "
        "Dream: a serious lifting number that earns respect. "
        "Coach with: specificity on loading, %-based work, respect for technique over speed.",
        "",
        "JST Avatar Reference",
    ],
    [
        "onboarding",
        "Athlete avatar — Grime Track",
        "Engine/endurance focus. Wants to dominate long workouts, rowing benchmarks, running. "
        "Pain: always gassing out or overtrained. "
        "Dream: unbreakable engine. "
        "Coach with: recovery management, zone 2 education, pacing strategy.",
        "",
        "JST Avatar Reference",
    ],

    # ── Weightlifting Cues ───────────────────────────────────────────────────
    [
        "weightlifting",
        "Snatch setup — hips/bum position",
        "Common issue: bum too low (squat stance) = bar drifts forward, loss of back tension. "
        "Fix: hips higher than knees at setup, bar over mid-foot, lats tight. "
        "Cue: 'Push the floor away, don't squat the bar up.'",
        "Hey [Name] — at the start of the snatch your hips are sitting lower than they need to be. "
        "Try setting up with hips slightly higher, lats engaged, "
        "and think about pushing the floor away rather than squatting it up. "
        "Makes a big difference to where the bar travels.\n\nJak",
        "JST Community Coaching Reference",
    ],
    [
        "weightlifting",
        "Snatch — bar path off the floor",
        "Bar should stay close and travel in a slight S-curve. "
        "Common fault: bar swings away from body on the way up. "
        "Cause: early arm pull or hips rising faster than shoulders. "
        "Cue: 'Keep your shoulders over the bar longer off the floor.'",
        "",
        "JST Community Coaching Reference",
    ],
    [
        "weightlifting",
        "Clean — receiving position",
        "In the catch, elbows must be high and fast. "
        "Common fault: bar landing on hands instead of shoulders, elbows dropping. "
        "Cue: 'Elbows up as fast as possible — punch them through the bar.'",
        "",
        "JST Community Coaching Reference",
    ],
    [
        "weightlifting",
        "Jerk — footwork",
        "Front foot should land first, then back foot. "
        "Common fault: both feet landing simultaneously = unstable catch. "
        "Cue: 'Punch up, split the feet — front heel, then back toe.'",
        "",
        "JST Community Coaching Reference",
    ],

    # ── Gymnastics / Bodyweight ───────────────────────────────────────────────
    [
        "gymnastics",
        "Kipping pull-up — hollow/arch rhythm",
        "The kip is a controlled swing, not a flail. "
        "Hollow position (pike at top) drives the kip. "
        "Common fault: athlete pulling with arms only, no body swing. "
        "Cue: 'Lead with your chest going forward, then snap hips up.'",
        "",
        "JST Community Coaching Reference",
    ],
    [
        "gymnastics",
        "Toes-to-bar — hip flexor vs lat engagement",
        "Most athletes pull with hip flexors only. "
        "Using lats to keep the bar close = more reps with less energy. "
        "Cue: 'Pull the bar into your hips on every rep — feel the lats load.'",
        "",
        "JST Community Coaching Reference",
    ],
    [
        "gymnastics",
        "Handstand walk progressions",
        "Don't skip shoulder prep. "
        "Drill: wall-facing shoulder taps → kick up with spot → short freestanding holds → walking. "
        "Wrist flexibility is often the limiter — address it daily, not just before skill work.",
        "",
        "JST Community Coaching Reference",
    ],

    # ── Conditioning ─────────────────────────────────────────────────────────
    [
        "conditioning",
        "Pacing strategy — start slower than you think",
        "Most athletes blow up in the first minute of a long workout. "
        "Rule of thumb: if you feel comfortable at minute 2, you started right. "
        "Cue athletes to pick a pace they can hold for the whole piece, then build.",
        "",
        "JST Community Coaching Reference",
    ],
    [
        "conditioning",
        "Zone 2 training — why and how much",
        "Zone 2 (conversational pace) builds the aerobic base that all performance sits on. "
        "Most athletes don't do enough of it. 30-60 mins 2-3x per week. "
        "Should feel easy. If you can't hold a conversation, you're too hard.",
        "",
        "JST Annual Programming Context 26/27",
    ],
    [
        "conditioning",
        "Recovery management — the non-negotiables",
        "Sleep, nutrition, and stress are training variables — not separate from it. "
        "If an athlete is chronically under-recovered, volume should go DOWN, not up. "
        "Recovery survey is the primary tool for identifying this week-to-week.",
        "",
        "JST Annual Programming Context 26/27",
    ],

    # ── Recovery Flags ────────────────────────────────────────────────────────
    [
        "recovery_flag",
        "High stress + low sleep score in recovery survey",
        "When an athlete flags high life stress and poor sleep: acknowledge it, "
        "don't push harder training. Recommend a deload session or rest day. "
        "The job is to protect long-term training capacity, not extract performance today.",
        "Hey [Name] — the recovery survey is flagging high stress and poor sleep this week. "
        "I'm going to pull back the volume a bit today — "
        "protecting your body now means better training next week.\n\nJak",
        "JST Annual Programming Context 26/27",
    ],
    [
        "recovery_flag",
        "Persistent soreness / niggle reported",
        "Take it seriously and ask for specifics (where, when, scale 1-10, any swelling). "
        "Don't diagnose — refer to physio for anything structural. "
        "Modify the session to work around it: there's always something they can do.",
        "Hey [Name] — tell me more about what you're feeling — "
        "where is it, when does it come on, and how sore are we talking? "
        "I'll adjust things so we're working around it, not through it.\n\nJak",
        "JST Community Coaching Reference",
    ],
]


def main():
    creds = Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPE
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(config.SHEET_ID)

    # Check if tab already exists
    existing = [ws.title for ws in sh.worksheets()]
    if TAB_NAME in existing:
        print(f"Tab '{TAB_NAME}' already exists. Updating content...")
        ws = sh.worksheet(TAB_NAME)
        ws.clear()
    else:
        print(f"Creating tab '{TAB_NAME}'...")
        ws = sh.add_worksheet(title=TAB_NAME, rows=200, cols=len(HEADERS))

    # Write headers
    ws.update("A1", [HEADERS])

    # Write data rows
    if ROWS:
        ws.append_rows(ROWS, value_input_option="RAW")

    # Format header row: bold
    ws.format("A1:E1", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.2, "green": 0.4, "blue": 0.6},
    })
    ws.format("A1:E1", {
        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
    })

    # Freeze header row
    sh.batch_update({
        "requests": [{
            "updateSheetProperties": {
                "properties": {
                    "sheetId": ws.id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        }]
    })

    print(f"Done. {len(ROWS)} rows written to '{TAB_NAME}'.")
    print("\nCoaches can add rows at any time — the dashboard and sync.py will pick them up.")
    print("Scenario values must match coaching_voice.SCENARIOS to be filterable.")


if __name__ == "__main__":
    main()
