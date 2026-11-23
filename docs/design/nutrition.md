# Nutrition: the plan for now

Status: a plan, not built. Written 2026-11-23.

## The idea

Nutrition is logged by telling the coach, the same way everything else is done. There is no nutrition app behind it and no new screen to learn. The coach keeps a short list of the foods you actually eat, and code adds up the numbers.

Built in OpenStride rather than connected to an existing tracker, because:

- You eat from roughly 100 foods and meals. A tracker's database of millions and its barcode scanner are not needed.
- The coach is already the logging screen.
- The well-known trackers do not appear to offer an API for personal use (not checked recently).

## How you log

| You do | The coach does |
|---|---|
| Write a meal: "100 grams of the normal oats, 100 grams banana, two eggs and black coffee for breakfast" | Finds each food in your list, logs the amounts, replies with the meal's total |
| Attach a photo of a label, for a food not in the list yet | Reads the label, adds the food under the name you use for it, logs it |
| Attach a photo of a plate when eating out | Makes a rough estimate, logs it as one entry marked as an estimate |
| Say "same breakfast as yesterday", or correct something | Copies or changes the entries |

Photos use the + button that is already in the app.

## What is stored

Two new things in the workspace, both written by the coach:

- **The food list** (`nutrition/foods.json`): one entry per food.
  - The names you use for it ("the normal oats"), so the same words always mean the same product.
  - Calories, carbohydrate, protein and fat per 100 g.
  - The weight of one piece where that makes sense, so "two eggs" works.
  - Where the numbers came from: a label photo, or the coach's general knowledge.
- **The daily log** (`nutrition/log/YYYY-MM-DD.json`): one entry per thing eaten.
  - The meal (breakfast, lunch, dinner, snack) and the time.
  - The food and the amount, or for an eating-out photo a description and the estimated numbers.
  - Whether it is measured or an estimate.

## What is computed

In code, never by the model, like the plan's weekly km today:

- Each entry's calories and macros from the food list and the amount.
- The day's totals, and how much of the total is estimated.
- The day against a target, if one is set.

## Where you see it

- **The day view** gets a Nutrition section: the meals, the day's totals, and the share that is estimated.
- **The coach** reads the log like any other file, so it can relate intake to training: fuelling before a long run, a shortfall in a heavy week.

No charts, in line with the rest of the app.

## What gets built

| Piece | What |
|---|---|
| Storage | The food list and the daily log, with their shapes checked on write |
| Commands for the coach | Add or change a food, log a meal, change or remove an entry, show a day's totals |
| Totals | The arithmetic above |
| Day view | The Nutrition section |
| Coach instructions | A skill for the three cases: a meal in words, a label photo, an eating-out photo |
| Tests | For the arithmetic, the commands and the day view |

Rough size: 300 to 400 lines including tests.

## Known limits

- **Eating-out estimates are rough.** Portion size and added fat can't be seen in a photo. They are marked so the day's total shows how much is a guess.
- **Every logged meal is a coach turn.** It costs a little and takes a moment, though there is no need to wait for the reply. If it proves slow, food logging can run on a smaller, faster model than coaching does.
- **The first week has more label photos**, because the list starts empty.

## Still to decide

1. **Targets.** Where the daily calorie and macro targets come from: you set them, the coach proposes them from your weight and training load, or there are none at first and the log is only a record.
2. **Calories burned.** Whether the day shows intake against what training burned, using the provider's numbers.
3. **Sending the day's total to intervals.icu**, so it sits beside training load there. Its wellness record seems to have a field for it; to be confirmed.
4. **Water, alcohol, supplements.** In or out of the first version.
