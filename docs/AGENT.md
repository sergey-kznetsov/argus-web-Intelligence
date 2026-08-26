# AGENT fallback contract

ARGUS AGENT is an optional last-resort navigation layer. It is not a factual source and its generated text is never stored as Evidence.

The production escalation order is:

`verified SiteRecipe -> FAST -> BROWSER -> AGENT planning -> verified SiteRecipe replay`

AGENT is disabled by default. The operational backend is `ollama-recipe`, implemented inside ARGUS with the existing local Ollama HTTP interface and the normal protected Playwright runtime. Browser Use is intentionally unavailable while its pinned `pypdf` version conflicts with ARGUS's patched PDF security baseline. Stagehand remains an explicit unavailable boundary until local-LLM integration is validated.

## Purpose

AGENT is allowed to discover a difficult public navigation path when deterministic FAST/BROWSER access reaches a public page but does not expose the factual view needed by the research goal. A successful path is useful only if ARGUS can convert it into a deterministic `SiteRecipe` and replay it successfully through the normal BROWSER runtime.

The model does not control Playwright directly. ARGUS first extracts a bounded set of safe controls from an already fetched public page. Ollama may select only those control IDs and a bounded scroll amount. It cannot return a free-form selector or arbitrary URL that becomes executable.

ARGUS does not trust the model's answer as a fact.

## Execution budgets

`ollama-recipe` is bounded before model output can affect navigation:

- one planning step per escalation;
- maximum 120 candidate controls extracted from the fetched page;
- maximum 300,000 characters of page HTML scanned for controls;
- maximum 40,000 characters in the planning prompt;
- maximum 6 selected actions;
- scroll clamped to plus/minus 8,000 pixels;
- Ollama request timeout capped at 30 seconds;
- same-domain or explicit collection `allowed_domains` boundary;
- every candidate navigation URL passes `UrlGuard` before it is exposed to the model;
- every model-selected action is compiled through `AgentRecipeCompiler` and replayed by BROWSER before use.

If the model invents an unknown control ID, returns unsupported action data, or no safe control is available, the AGENT path fails closed and no recipe is created.

## Control restrictions

The native agent does not expose forms, text entry, JavaScript execution, file operations or arbitrary Playwright methods to the model.

Candidate controls are limited to public same-domain links and selected non-form click controls. ARGUS excludes form-associated buttons, submit/reset controls, generic role-buttons that are not disclosure/tab controls, and labels associated with login, registration, purchase, checkout, upload/download, deletion, subscription, sending, saving, confirmation, voting/liking and equivalent Russian actions.

The task contract also prohibits CAPTCHA/access-control bypass, login/account creation, paywall/rate-limit/robots bypass, purchases, state-changing submissions, entry of personal/secret/payment data and attempts to evade public-source challenges.

These application controls are not a substitute for deployment-level process/OS/network restrictions.

## Domain boundary

If a collection supplies `allowed_domains`, candidate links must stay within that boundary using the same parent/subdomain semantics as ARGUS. Without an explicit boundary, candidate links are restricted to the current page host.

Every candidate HTTP(S) link is additionally checked by `UrlGuard`, so private/internal destinations and forbidden ports remain subject to the normal SSRF policy.

## CAPTCHA and access challenges

A CAPTCHA, human-verification page, access-denied page, robot check or HTTP-rate-limit challenge is not a research target. ARGUS does not use AGENT to bypass it. A blocked deterministic replay is rejected and no alternate path is used to evade the challenge.

## Verified path promotion

For a successful AGENT plan:

1. Ollama chooses from ARGUS-issued control IDs only;
2. ARGUS converts those IDs into bounded action objects;
3. `AgentRecipeCompiler` rejects any unsupported action;
4. a candidate `SiteRecipe` is created;
5. BROWSER replays the candidate against the original public URL;
6. blocked or failed replay rejects the candidate;
7. only successful non-blocked replay activates and persists the recipe;
8. factual extraction runs on the replayed public page, not on model output.

A successful action-free legacy AgentResult may still expose a bounded direct public URL path for compatibility tests, but the URL is always re-fetched with BROWSER before it can become factual input.

## Semantic goal escalation

A technically successful page fetch does not prove that the research goal was achieved. Public map pages illustrate this: the application shell can load while reviews remain behind an interactive tab.

For supported public-map review tasks, ARGUS may invoke `ollama-recipe` when no source-declared `Review` fact was extracted. The resulting replay is accepted only when it improves factual extraction. Navigation telemetry records the attempt, but model output is never Evidence.

## Failure behavior

AGENT is a last-resort capability. Ollama unavailability or an internal agent-runtime failure is recorded as bounded diagnostic metadata and fails open to the surrounding collection logic rather than becoming an uncontrolled exception. `UnsafeUrlError` and other security-boundary failures are not swallowed.

## Telemetry

`AgentResult.metadata` records backend, status/reason code, control/action limits and replay policy. Lifecycle-aware fetches may copy this into Observation provenance.

Telemetry explains how a source was reached. It is not Evidence and consumers must not interpret it as source confidence.
