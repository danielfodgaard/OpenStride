---
name: plan-change
description: Change the running plan: move, swap, skip, shorten, add or remove sessions, adjust for illness, injury, travel or missed training, or restore an old version. Use for any change to plan/, whether the athlete asked or you think it is needed.
---

# Changing the plan

Every change goes the same way, however small: a draft, the athlete's approval, then the plan changes. `GUIDE.md` has the mechanics under "Changing the plan"; this is the judgment.

## When the athlete asks

Do exactly what they asked. Moving a long run to Saturday for one week or putting two hard days together is theirs to choose. Name the coaching cost in a sentence if there is one, do not work around the request, and do not add changes they did not ask for. A vague request ("I'm away next week") gets one question first: which days, and can they run there.

## When you think it is needed

Be conservative. One bad or missed session is not a reason to change anything. Reasons: pain or injury, illness, a run of missed or failed sessions, sessions consistently much easier or harder than planned, a life constraint they mentioned. Look at the last one to two weeks (`openstride status last`, the reviews in `notes/`, wellness) and your notes about how they respond. Ask how they feel before proposing if you have not heard. "No change" is a good outcome; say why.

## Making the draft

- Change the smallest number of weeks that covers it. Never silently drop a session: move it or make it lighter, and if it has to go, give it its own entry in `changes` with the reason.
- Keep the long run on the long-run day and hard days apart unless the athlete chose otherwise. Strength never the day before the long run or race.
- To skip a session, set its `status` to `skipped` and put the reason in `edit_note`; do not delete it. "I did something else" is `status: replaced`.
- To go back to an earlier version: `openstride plan history`, then `openstride plan export --version <commit> -o scratch/draft.json`, and draft that. Sessions that have already happened stay as they are.
- Validate before drafting. Fix errors; tell the athlete about warnings that matter.

## In your reply

Say what changes and why in plain words, in two to four sentences, and what does not change. The card next to your reply shows the detail and has the buttons. Do not say the plan has changed. If the provider has a calendar, approval also updates their watch; you do not need to do anything.
