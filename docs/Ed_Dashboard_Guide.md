# JST Compete — Coach Dashboard Guide
### For Ed Cook

---

## What is this?

The JST Compete coaching dashboard pulls live data from Fitr and Google Sheets, flags athletes who need attention, and drafts messages for you — so you can do your coaching work in one place without constantly switching between Fitr, email, Slack, and a spreadsheet.

It runs on a live sync that updates throughout the day. You don't need to manage the sync — it happens automatically.

---

## What the system handles automatically

You don't need to do anything for these. They happen on the daily sync:

| What | When | Example |
|------|------|---------|
| New athlete welcome message | When someone is added to Fitr | "Hey Sam, you've been added to the JST Compete coaching system — good to have you in..." |
| First log message | Athlete's first training log | "Hey Sam — first log is in. Good start..." |
| New PB / result congrats | When a benchmark result is logged | "Hey Sam — Back Squat 1RM: 120kg. New PB (was 110kg). Good work." |
| North Star goal achieved | When a logged result hits their goal | "Hey Sam — you just hit your North Star Goal. Back Squat 1RM: 130kg. That's what we've been building towards." |
| Competition countdown messages | 10 weeks, 3 weeks, race week, day before | "Hey Sam — three weeks to CrossFit Regionals. We're in the final stretch..." |
| Post-competition check-in | Day after a competition | "Hey Sam — hope you're recovering well after CrossFit Regionals. When you get a chance, can you let me know..." |
| Training anniversary | 90 days, 6 months, 1 year etc. | "Hey Sam — 6 months since your first log on 01 Jan 2026. That consistency is exactly what makes the difference." |
| 60-day inactive check-in | If an athlete hasn't logged for 60 days | "Hey Sam — it's been a while since I've seen you in the logs. Hope everything's okay..." |
| Weekly progress emails | Every Monday | Summary of results, consistency, and competition progress |

**Bespoke athletes** — athletes on a Bespoke subscription get **no** automated messages. Those relationships need a personal touch, so every message comes from you.

---

## What Ed needs to do

The system handles volume. Your job is the stuff that needs a human — flagged athletes, recovery issues, relationships.

**Daily (5–10 minutes):**

1. Open the dashboard
2. Go to the **✅ Actions** tab — this is your to-do list for the day
3. Work through the cards top to bottom. Each one has a pre-drafted message
4. Copy the message, tweak it if needed, send it (Fitr, email, or WhatsApp)
5. Hit **Mark Done** on each card as you go

That's it for most days.

---

## Your daily interface: ✅ Actions tab

This is the first tab when you open the dashboard. It shows everything that needs a message from you personally — the system has not sent anything for these athletes.

Each card shows:
- **Priority** — what type of contact this is (Recovery flag, Check-in, Performance concern etc.)
- **Athlete name** and their programme
- **Reason** — exactly why they've been flagged
- **Pre-drafted message** — ready to copy, or edit before sending

Below the message there are three quick buttons:
- **Email** — opens your email client with the message pre-filled
- **WhatsApp** — opens WhatsApp with the message pre-filled
- **Fitr** — if the Fitr messaging toggle is on in the sidebar, you can send directly from the dashboard

Click **Mark Done** on each card when you've sent it. A progress bar at the top tracks how many you've cleared. They come back if you refresh in a new browser session, but the data will have updated by then anyway.

---

## Priority types — what each one means

| Priority | What it means | What to do |
|----------|---------------|------------|
| 🔴 Contact Today | Recovery survey flagged high soreness or stress | Message today. Ask what's going on. Adjust training if needed. |
| ✅ Positive | Athlete has logged X consecutive weeks | Quick acknowledgement — consistency is worth recognising. |
| ⚠️ Check In | 28–44 days without logging | Friendly nudge. Life happens. Keep it light. |
| ⚠️ Re-engage | 45+ days without logging | More direct outreach. They might be about to cancel. |
| 📉 Performance | A benchmark is declining or well below peak | Check in on what's going on. Might be life load, injury, or need a programme tweak. |
| 🚨 Programme Switch | A-race is 10 weeks out — needs a programme change | Confirm you've updated their programme in Fitr. |
| 📝 Remind to Log | Athlete is in contact but not logging results | Ask them to record their sessions. Data is how you coach. |

**Note:** Things like PR congrats, competition prep messages, and post-comp check-ins are handled automatically. You'll see them in the Outreach List tab under "Auto-sent by system" for your awareness, but you don't need to action them.

---

## Other tabs worth knowing

**📋 Outreach List** — the full overview table. Same data as the Actions tab but in a sortable table format with a bulk export. Useful for reviewing the squad at a glance or downloading a list for planning.

**🚨 Alerts** — aggregate view of all flags across the squad. Recovery, performance, and consistency alerts in one place.

**👥 Athletes** — individual athlete profiles. Click any athlete to see their full history, benchmarks, competition calendar, archetype, coaching notes, and conversation history.

**🏁 Competitions** — full competition schedule across the squad. Upcoming races, phases, and what action each needs.

**📚 Playbook** — coaching reference hub. Scenarios, example messages, and notes you can add to over time. If you've got a good way of handling a particular situation, add it here so it informs the AI drafts.

---

## The Coaching Playbook

The Playbook is a living reference. It currently has notes on:
- How to respond to missed sessions, recovery flags, and mindset questions
- Competition scenarios (taper, post-comp debrief, below-expectation results)
- Weightlifting technique cues (snatch, clean, jerk)
- Gymnastics skill cues (kipping pull-ups, toes-to-bar, handstand walk)
- Conditioning principles (pacing, zone 2, recovery management)
- The three athlete avatars (Grit, Grunt, Grime)

You can add to it at any time using the form at the bottom of the Playbook tab, or by adding rows directly to the **Coaching Playbook** tab in the Google Sheet.

The more you add, the better the AI message drafts get.

---

## What the automated messages sound like

The system uses a strict tone guide for everything it sends. Here's what that looks like:

**New athlete (added to Fitr):**
> Hey Sam, you've been added to the JST Compete coaching system — good to have you in.
>
> Two things to get started:
>
> 1. Athlete intake form (3 minutes — tells me everything I need to set your programming up properly): [link]
>
> 2. Weekly recovery check-in — same link. Once you're training, do this each week. It takes 2 minutes and is how I manage your load week to week.
>
> Message me here anytime.

**New PB:**
> Hey Sam — Back Squat 1RM: 120kg. New PB (was 110kg). Good work.

**60-day inactive:**
> Hey Sam — it's been a while since I've seen you in the logs. Hope everything's okay.
>
> If life's got in the way or you want to adjust something with the programme, just drop me a message. No pressure.

No emojis, no "Let's go!", no sign-off (because it's automated, not personally from you). Short and direct.

---

## Questions?

Anything not covered here: ask Jak. The dashboard is built on top of your Google Sheets data, so everything you see reflects what's in the sheets.

If a message looks wrong or an athlete shouldn't be getting automated messages, let Jak know — it's a quick fix.
