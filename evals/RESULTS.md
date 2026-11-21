# Eval results

## v2.1, 2026-11-21

One run of all 12 cases in `cases.json` by a fresh Claude agent using the skill. It wrote its verdicts before reading the expected answers.

| Measure | Result |
|---|---|
| Expected requests | 20 |
| Matching verdicts | 20 / 20 |
| False extras (included work called extra) | 0 |
| Missed extras | 0 |
| Traps fallen into | 0 |

Notes from the run, now fixed:
- SKILL.md had two rules that could disagree on new formats vs. new asset types. A single rule now separates them.
- Cases 05, 09 and 11 now state their assumptions and accept the reasonable alternatives (`also_accept`, `assumes`).

**Limits of this result:** it's one run, on 12 hand-written cases, by one model. It shows the skill's rules are coherent. It doesn't yet show accuracy on real agreements. The next step is more cases (target 100) and real anonymised projects from freelancers.
