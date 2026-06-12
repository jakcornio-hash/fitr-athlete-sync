# JST Weekly Recovery Check-in — Typeform build spec

A 60-second weekly pulse. Short enough that athletes actually do it, structured
enough that the sync script can merge it into their profile automatically.
Build it at typeform.com (same account as your athlete onboarding form).

---

## Welcome screen

> **JST Weekly Check-in 🛠**
> 60 seconds. Be honest — this calibrates your training for the week.
> No judgement, no wrong answers. Let's get after it.

Button: **Start**

---

## Questions

**1. What's your email?**
- Type: *Email* · Required
- Description: "The one you use on Fitr — it matches you to your athlete file."
- ⚠️ This is the join key. Without it the response can't be matched.

**2. How many hours of sleep did you average this week?**
- Type: *Number* · Required · Min 0, Max 14
- Description: "Rough average per night is fine."

**3. How sore are you right now?**
- Type: *Opinion scale* 1–10 · Required
- Labels: 1 = "Fresh as a daisy" · 10 = "Can't sit on the toilet"

**4. How heavy is life load this week?**
- Type: *Opinion scale* 1–10 · Required
- Description: "Work, family, everything outside the gym."
- Labels: 1 = "Cruising" · 10 = "Drowning"

**5. How's the fire? (motivation to train)**
- Type: *Opinion scale* 1–10 · Required
- Labels: 1 = "Can't face the gym" · 10 = "Ready to eat the programme alive"

**6. Bodyweight (kg)?**
- Type: *Number* · Optional
- Description: "Only if you track it — useful for the lifts."

**7. Any niggles or pain we should know about?**
- Type: *Short text* · Optional
- Description: "Where, when it bites, and what movement sets it off. 'None' is a great answer."

**8. What does your training week realistically look like?**
- Type: *Multiple choice* · Required
  - Full normal week
  - Reduced — 3–4 sessions
  - Minimal — 1–2 sessions
  - Away / travelling
  - Carrying an injury — need adjustments

---

## Thank-you screen

> **Done. Keep grafting. 🔨**
> Your coaches see this before the week starts — if anything needs adjusting, we're on it.

---

## Wiring it to the sheet

1. In Typeform: **Connect → Google Sheets**, authorise your Google account.
2. Typeform writes responses to a spreadsheet it manages. Two ways to get them
   into the Athlete Profiles sheet's `Recovery` tab:
   - **Easiest:** in the `Recovery` tab, cell A1, bridge with:
     `=IMPORTRANGE("<typeform sheet URL>", "Sheet1!A:I")`
     (approve access when prompted once).
   - **Alternative:** point the sync script straight at the Typeform sheet —
     ask Claude Code to add a `RECOVERY_SHEET_ID` setting (5-minute change).
3. Make the `Recovery` tab's header row match what `recovery.py` expects
   (or edit `RECOVERY_COLS` in `recovery.py` to match Typeform's headers):

   | recovery.py key | Expected header |
   |---|---|
   | timestamp | Submitted At |
   | email | Email |
   | sleep | Sleep (hrs) |
   | soreness | Soreness |
   | stress | Stress |
   | motivation | Motivation |
   | bodyweight | Bodyweight |
   | niggles | Niggles/Injuries |
   | availability | Availability this week |

---

## Getting athletes to fill it in

- Drop the link in the WhatsApp group every **Sunday evening** with the same
  two-line message, so it becomes a ritual: *"Weekly check-in — 60 seconds,
  shapes your week. Go: <link>"*
- Pin the link in the group description as a backup.
- The Sunday-evening timing means responses land before the Sunday 18:00 sync…
  realistically many will come in later, so they'll be picked up the following
  week. If that lag bothers you, move the sync to Monday morning instead —
  one-line change in the workflow cron.

## Reading the results (coach's cheat-sheet)

- **Soreness ≥ 7, stress ≥ 7, or motivation ≤ 3** → worth a proactive message
  (feeds straight into Ed's Monday check-in list logic).
- **Same niggle two weeks running** → programme adjustment, don't wait for week three.
- **"Carrying an injury" availability** → flag to the coach that day, not at the weekly review.
