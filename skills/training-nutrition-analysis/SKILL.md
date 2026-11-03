---
name: training-nutrition-analysis
description: Analyze the user's strength training (Hevy) together with their eating (Fitatu) through the hevy-fitatu MCP tools. Use when the user asks how their training or diet is going, whether they eat enough protein or calories for their training, why a lift stalled, how their weight trend matches their intake, or for a weekly or monthly fitness review.
---

# Training and nutrition analysis

The `hevy-fitatu` MCP server reads Hevy (workouts) and Fitatu (food diary, goals, weigh-ins). It
is read-only.

## Which tool

1. `get_today` first when the user speaks of today, this week or "lately".
2. `analyze_training_and_nutrition` for any "how is it going" question. It returns the training
   load, intake on training vs rest days, protein g/kg, the weight trend and, when the data
   allows, an estimated maintenance intake. Read its `notes`: they say what the data does not cover.
3. Go deeper only where the answer needs it:
   - `analyze_training`: sets per muscle group per week, gaps, top exercises.
   - `analyze_exercise_progress`: one lift's e1RM per session, PRs and the trend.
   - `get_daily_log`: the days one by one, e.g. around a stall or a bad week.
   - Fitatu tools (`get_day`, `get_report`, `get_top_foods`, `get_targets`, `get_profile`) for
     what was eaten and the user's goals.

## Reading the numbers

- A day with no food logged is unknown, not zero. Say how many days the averages cover. A day
  logged only in part (e.g. breakfast only) drags averages down: if one looks implausibly low,
  say so instead of concluding the user undereats.
- Protein: about 1.6–2.2 g per kg of body weight a day supports muscle gain; below about 1.2
  is worth flagging.
- Weekly sets per muscle group: about 10–20 hard sets is a common range for growth; under
  about 6 is low. Secondary muscles count half a set.
- Maintenance kcal is estimated from intake and the weight trend (7700 kcal per kg). It is
  rough, and only given with 14+ logged days and weigh-ins spanning 14+ days.
- e1RM is the Epley estimate from sets of 1–15 reps. Compare trends, not single sessions.

## Answer

Lead with the answer, then the 2–4 numbers that support it, then concrete next steps. Do not
give medical advice. Point the user to a professional for health conditions or very low intake.
