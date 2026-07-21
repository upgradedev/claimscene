# ClaimScene extraction-accuracy scoreboard

- Date: 2026-07-21T22:21:41.347651+00:00
- Scenarios: 7 (synthetic toy-diorama photo sets, 3 views + context note each; ground truth authored with the generation prompts — self-consistent by construction)
- Prompt version: `sha256:30304091d3fee485`
- Headline: **google/gemma-4-31b-it — 100.0% weighted field accuracy**

## Per model

| model | overall | approach | damage_clock | impact_clock | maneuver | road_layout | signal | vehicle_color | vehicle_count | vehicle_kind |
|---|---|---|---|---|---|---|---|---|---|---|
| `google/gemma-4-31b-it` | **100.0%** | 14/14 | 14/14 | 14/14 | 14/14 | 7/7 | 7/7 | 14/14 | 7/7 | 14/14 |
| `google/gemini-3.5-flash` | **71.4%** | 10/14 | 10/14 | 10/14 | 10/14 | 5/7 | 5/7 | 10/14 | 5/7 | 10/14 |

## Per scenario (headline model)

| scenario | score | misses |
|---|---|---|
| s01_rear_end | 100.0% | — |
| s02_left_cross | 100.0% | — |
| s03_parking_reverse | 100.0% | — |
| s04_roundabout_sideswipe | 100.0% | — |
| s05_t_intersection | 100.0% | — |
| s06_lane_change | 100.0% | — |
| s07_intersection_truck | 100.0% | — |

## Token usage

| model | calls | prompt tokens | completion tokens |
|---|---|---|---|
| `google/gemma-4-31b-it` | 7 | 11703 | 1427 |
| `google/gemini-3.5-flash` | 5 | 20722 | 9729 |
