---
guardrails-version: 0.3.15
---

# Engineering guardrails

Applies to all work in this repository, whoever or whatever is writing it.

## Never invent intent

If a requirement is ambiguous, missing, or contradicted by the code, **surface it — do not resolve
it by guessing**. Write the question on the issue and ask.

This is the most important rule here. A plausible invented requirement is expensive precisely
because it looks like a real one: it gets implemented, reviewed, merged, and discovered months later
by someone who assumes it was deliberate.

## Evidence, not inference

State what the code does, not what its names suggest it does. A function called `validateInput` may
validate nothing. When you assert behavior, cite `file:line`. When you cannot confirm something,
say that you cannot, and say what you would need to read to decide.

"I checked X and it does Y" and "I assume X does Y" are different claims. Never present the second
as the first.

## Branches and history

- Work happens on a branch. The default branch is reached through a pull request.
- Never force-push a shared branch. `--force-with-lease` when you genuinely must overwrite your own.
- Every branch and pull request names the issue it advances.

## Secrets

Credentials never enter the repository — not in source, not in fixtures, not in test data, not in
a commit message. Commit a `.env.example` with the keys and empty values. If a secret is ever
committed, rotating it is the fix; removing the commit is not.

## Errors

Do not swallow exceptions. A caught-and-ignored error converts a visible failure into a wrong
result that surfaces somewhere else, attributed to something else.

Do not add defensive null checks or catch blocks to make a symptom go away. Find out why the
unexpected state exists first — otherwise you have hidden a bug rather than fixed one.

## Bug fixes

Every bug fix carries a regression test that fails against the old code and passes against the new.
If it would have passed before the fix, it does not prove anything.

## Scope

A change does what its issue says and nothing else. Unrelated improvements — however small, however
obviously correct — become separate work. Mixed diffs are what make review shallow.

## Dependencies

Adding a dependency is a decision, not a detail. Say why in the pull request: what it does, why the
standard library or existing dependencies are not enough, and what its maintenance status is.
Lockfiles are committed.

## When you are stuck

Say so, early, and say what you have ruled out. Two hours of visible uncertainty is cheaper than a
day of confident wrong direction.
