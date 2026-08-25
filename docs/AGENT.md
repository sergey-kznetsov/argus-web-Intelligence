# AGENT fallback contract

ARGUS AGENT is an optional last-resort navigation layer. It is not a factual source and its generated final text is never stored as Evidence.

The production escalation order is:

`verified SiteRecipe -> FAST -> BROWSER -> AGENT -> verified SiteRecipe replay`

AGENT is disabled by default. The supported implementation for this stage is Browser Use with Ollama. Stagehand remains an explicit disabled boundary and is not treated as an operational backend.

## Purpose

AGENT is allowed to discover a difficult public navigation path when deterministic FAST/BROWSER access cannot reach the required public view. A successful action path is useful to ARGUS only if it can be converted into a deterministic `SiteRecipe` and replayed successfully by the normal BROWSER runtime.

ARGUS does not trust the agent's final answer as a fact.

## Execution budgets

Browser Use execution is bounded by ARGUS before it can affect the factual pipeline:

- maximum 25 agent steps;
- maximum 3 browser actions per model step;
- maximum 2 consecutive Browser Use internal failures;
- maximum 6 history items retained by the agent;
- total execution timeout derived from the configured browser/fetch timeouts and capped at 180 seconds;
- LLM and individual step timeouts capped at 60 seconds;
- maximum 40 returned actions eligible for deterministic replay processing;
- maximum 20 visited URLs retained;
- maximum 20,000 characters of final agent text retained for diagnostics only;
- bounded action nesting, node count and string length before actions enter ARGUS.

If an action/count/payload budget is exceeded, the AGENT run fails closed and no recipe is created.

## Tool restrictions

ARGUS constructs Browser Use with a restricted tool registry. The agent cannot use Browser Use external search or local file operations (`read_file`, `write_file`, `replace_file`, uploads/downloads and related file actions).

The task prompt additionally prohibits:

- CAPTCHA or access-control bypass;
- login/account creation;
- paywall/rate-limit/robots bypass;
- purchases or state-changing form submissions;
- entry of personal, secret or payment data;
- file/javascript/chrome/about/extension navigation.

Browser Use is configured with domain restrictions, IP-address navigation blocking and default extensions disabled. ARGUS also validates every returned URL through `UrlGuard` before it can be replayed.

These application controls are not a substitute for the deployment-level process/OS/network sandbox required by the security-hardening stage. AGENT remains disabled by default until that production boundary is deliberately enabled.

## Domain boundary

If a collection supplies `allowed_domains`, the target must be within that boundary. ARGUS mirrors its own subdomain semantics into Browser Use by emitting both the parent domain and its explicit `*.domain` pattern. IP literals are not accepted as AGENT allowed domains.

If no allowed-domain constraint exists, AGENT is restricted to the current target host.

## CAPTCHA and access challenges

A CAPTCHA, human-verification page, access-denied page, Cloudflare challenge, robot check or HTTP-rate-limit diagnostic is treated as `blocked`. ARGUS stops the AGENT path and does not try alternate visited URLs to evade the challenge.

## Verified path promotion

For successful AGENT runs that contain actions:

1. the bounded returned actions are passed to the conservative `AgentRecipeCompiler`;
2. any unsupported action rejects the whole path;
3. a candidate `SiteRecipe` is created only in memory;
4. BROWSER replays the candidate against the original public URL;
5. blocked or failed replay rejects the candidate;
6. only a successful non-blocked replay activates and persists the recipe.

ARGUS never falls back to arbitrary visited URLs when a successful agent run performed actions that cannot be compiled or verified.

For a successful action-free AGENT run, ARGUS may re-fetch a directly discovered public URL using the normal BROWSER runtime. At most two visited URLs are attempted. The agent's own extracted text is still not factual input.

## Telemetry

`AgentResult.metadata` records bounded execution telemetry including backend, status/reason code, step/action/history limits, timeout values, visited-URL truncation and output truncation. Lifecycle-aware web fetches copy that telemetry into normal fetch metadata, which can appear in Observation provenance/data.

Telemetry explains how the source was reached. It is explicitly not Evidence and must not be used as source confidence by consumers.
