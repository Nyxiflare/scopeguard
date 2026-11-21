# ScopeGuard evals

`cases.json` holds hand-checked cases: an agreement, a client message, and the verdict each request should get. They test Claude's judgement, which the unit tests in `tests/` can't cover.

**How to run:** give Claude the skill plus one case's agreement and message, then compare its verdicts with `expected`. The things that matter most:

1. **False extras:** included work called extra. This is the worst mistake, because it can cost the freelancer a client. Target: zero.
2. **Missed extras:** extra work called included or a revision.
3. **Evidence:** every verdict quotes the right line, or says the agreement is silent.
4. **Rounds:** one batch of feedback = one round, and changes past the cap become extra.
5. **Uncertainty:** silent or unwritten agreements give grey or provisional verdicts, not confident ones.

Case 01 is the acceptance test. Every release should still produce its expected verdicts.

**Next:** grow this to 100 cases across professions, with real (anonymised) agreements from freelancers. Pull requests with new cases are welcome.
