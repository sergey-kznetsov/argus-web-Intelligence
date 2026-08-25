# SiteRecipe lifecycle

ARGUS stores deterministic browser recipes only after successful replay through the BROWSER runtime. Agent actions are never trusted as a persisted recipe by themselves.

## Lifecycle

A `SiteRecipe` has one of three states:

- `candidate` — in-memory recipe compiled from agent actions and awaiting verification;
- `active` — verified recipe eligible for normal replay;
- `invalidated` — expired or repeatedly failing historical version that must not be replayed.

Legacy recipes created before lifecycle fields were introduced remain readable as `active` for backward compatibility.

## Verified promotion

The promotion path is:

1. AGENT returns a bounded action sequence.
2. `AgentRecipeCompiler` converts only the supported deterministic subset into `RecipeStep` objects.
3. `RecipeManager.candidate()` creates a new version in memory.
4. BROWSER replays the candidate against the public page.
5. Only a successful, non-blocked replay may call `RecipeManager.mark_success()` and persist the recipe as `active`.

`RecipeManager.save()` rejects unverified `candidate` objects. Unsupported agent actions, failed replay, unsafe URLs, CAPTCHA/access-control challenges, and blocked replay never produce an active recipe.

## Failure and invalidation

Active recipes track cumulative successes, cumulative failures and consecutive failures. Three consecutive replay failures invalidate the current version by default. A successful replay resets only the consecutive failure counter.

Expired recipes are invalidated when read. The default maximum age is 30 days, measured from the latest successful verification/use when available.

Invalidated versions are immutable lifecycle history. Recovery creates a new candidate/version instead of reactivating an old version.

## Version retention

Repository cleanup keeps a bounded number of recipe versions per `(domain, goal)`. The default is 10 versions. Cleanup is supported by SQLite and PostgreSQL.

PostgreSQL recipe mutation and cleanup are protected by worker lease fencing. A stale worker that has lost the collection lease cannot save, invalidate, or prune recipe state after another worker takes ownership.

## Security boundary

SiteRecipe does not bypass authentication, CAPTCHA, paywalls, rate limits or other access controls. Browser navigation remains subject to `UrlGuard`, redirect validation, browser subrequest filtering, robots rules and configured request/concurrency budgets.

Successful agent navigation is useful to ARGUS only after deterministic BROWSER replay. This preserves the architecture boundary: AGENT may discover a path, but the reusable operational artifact is a verified SiteRecipe.

## Provenance and telemetry

Recipe-backed BROWSER responses expose `recipe_id` and `recipe_version`. Lifecycle-aware fetches also include bounded lifecycle metadata such as status, verification state, success/failure counts and invalidation policy. The factual Observation keeps this runtime metadata in provenance/data without treating recipe state itself as Evidence.
