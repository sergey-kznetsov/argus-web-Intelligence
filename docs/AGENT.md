# AGENT fallback contract

ARGUS AGENT is an optional last-resort navigation layer. It is not a factual source and its generated text is never stored as Evidence.

The production escalation order is:

`verified SiteRecipe -> FAST -> BROWSER -> deterministic public view -> bounded AGENT planning -> verified SiteRecipe replay`

AGENT is disabled by default. The operational backend is `ollama-recipe`, implemented inside ARGUS with the existing local Ollama HTTP interface and the normal protected Playwright runtime. Browser Use is intentionally unavailable while its pinned `pypdf` version conflicts with ARGUS's patched PDF security baseline. Stagehand remains an explicit unavailable boundary until local-LLM integration is validated.

## Purpose

AGENT is allowed to discover a difficult public navigation path when deterministic FAST/BROWSER access reaches a public page but does not expose the factual view needed by the research goal. A successful path is useful only if ARGUS can convert it into a deterministic `SiteRecipe` and replay it successfully through the normal BROWSER runtime.

The model does not control Playwright directly. ARGUS first extracts a bounded set of safe controls from an already fetched public page. Ollama may select only those control IDs and a bounded scroll amount. It cannot return a free-form selector or arbitrary URL that becomes executable.

ARGUS does not trust the model's answer as a fact.

## Execution budgets

`ollama-recipe` is bounded before model output can affect navigation:

- one planning step per AGENT round;
- at most two semantic AGENT rounds for one public-map source evaluation;
- maximum 120 candidate controls extracted from a fetched page;
- maximum 300,000 characters of page HTML scanned for controls;
- maximum 40,000 characters in the planning prompt;
- maximum 6 selected actions per planning round;
- combined verified AGENT-generated recipe path capped at 40 steps;
- scroll clamped to plus/minus 8,000 pixels;
- Ollama request timeout capped at 30 seconds;
- same-domain or explicit collection `allowed_domains` boundary;
- every candidate navigation URL passes `UrlGuard` before it is exposed to the model;
- every model-selected action is compiled through `AgentRecipeCompiler` and replayed by BROWSER before use.

If the model invents an unknown control ID, returns unsupported action data, exceeds a recipe budget, or no safe control is available, the AGENT path fails closed and no unverified recipe is persisted.

## Control restrictions

The native agent does not expose forms, text entry, JavaScript execution, file operations or arbitrary Playwright methods to the model.

Candidate controls are limited to public same-domain links and selected non-form click controls. ARGUS excludes form-associated buttons, submit/reset controls, generic role-buttons that are not disclosure/tab controls, and labels associated with login, registration, purchase, checkout, upload/download, deletion, subscription, sending, saving, confirmation, voting/liking and equivalent Russian actions.

The task contract also prohibits CAPTCHA/access-control bypass, login/account creation, paywall/rate-limit/robots bypass, purchases, state-changing submissions, entry of personal/secret/payment data and attempts to evade public-source challenges.

These application controls are not a substitute for deployment-level process/OS/network restrictions.

## Domain boundary

If a collection supplies `allowed_domains`, candidate links must stay within that boundary using the same parent/subdomain semantics as ARGUS. Without an explicit boundary, candidate links are restricted to the current page host.

Every candidate HTTP(S) link is additionally checked by `UrlGuard`, so private/internal destinations and forbidden ports remain subject to the normal SSRF policy.

## CAPTCHA and access challenges

A CAPTCHA, human-verification page, access-denied page, robot check or HTTP-rate-limit challenge is not a research target. ARGUS does not use AGENT to bypass it. A blocked deterministic public view or blocked recipe replay stops that escalation path; ARGUS does not try an alternate AGENT route to evade the challenge.

## Verified path promotion

For a successful AGENT plan:

1. Ollama chooses from ARGUS-issued control IDs only;
2. ARGUS converts those IDs into bounded action objects;
3. `AgentRecipeCompiler` rejects any unsupported action;
4. a candidate `SiteRecipe` is created;
5. BROWSER replays the candidate against the public URL from which that recipe starts;
6. blocked or failed replay rejects the candidate;
7. only successful non-blocked replay activates and persists the recipe;
8. factual extraction runs on the replayed public page, not on model output.

A successful action-free legacy AgentResult may still expose a bounded direct public URL path for compatibility tests, but the URL is always re-fetched with BROWSER before it can become factual input.

## Verified iterative extension

Some SPAs expose the next useful control only after the first interaction, for example `Отзывы -> Показать ещё`. ARGUS supports a bounded second semantic round without trusting intermediate model output.

If a replayed page still does not satisfy the semantic goal, ARGUS may let the agent inspect that verified DOM once more. Before extending the path, ARGUS checks `recipe_id` from BROWSER metadata against the currently active `SiteRecipe` for the same URL and goal.

Only when those identities match are the existing verified steps prepended to the newly compiled steps. The combined candidate is then replayed again from the recipe's normal start URL. A mismatched or missing recipe identity is never silently spliced into another path, and the combined path may not exceed 40 steps.

This produces a versioned deterministic recipe such as:

`click Reviews -> click Show more`

rather than storing the second click as if it were valid on the original unmodified DOM.

## Semantic goal escalation

A technically successful page fetch does not prove that the research goal was achieved. Public map pages illustrate this: the application shell can load while reviews remain behind an interactive tab.

For supported public-map goals (`reviews`, `comments`, `discussions`, `complaints`), ARGUS first prefers a deterministic public review view when the provider exposes one. Yandex/2GIS public card URLs may be normalized to their public review surfaces. Google review URLs are not guessed without sufficient place identity.

If the public view is non-blocked but still hides the requested factual content, ARGUS may run up to two bounded semantic AGENT rounds. Every replayed result goes through the normal extractor and source-backed intent coverage evaluator. A round is accepted only when factual coverage improves. Navigation telemetry records the attempts, but model output is never Evidence.

`incidents` remains a generic web-research intent rather than a reason to force public-map navigation.

## Failure behavior

AGENT is a last-resort capability. Ollama unavailability or an internal agent-runtime failure is recorded as bounded diagnostic metadata and fails open to the surrounding collection logic rather than becoming an uncontrolled exception. `UnsafeUrlError` and other security-boundary failures are not swallowed.

## Telemetry

`AgentResult.metadata` records backend, status/reason code, control/action limits and replay policy. Lifecycle-aware fetches may copy this into Observation provenance. Verified recipe extension additionally records the base recipe identity/version, original/new/combined step counts and the acceptance decision.

Telemetry explains how a source was reached. It is not Evidence and consumers must not interpret it as source confidence.
