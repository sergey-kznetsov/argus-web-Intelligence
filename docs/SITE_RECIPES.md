# Жизненный цикл SiteRecipe

ARGUS хранит детерминированные browser recipes как operational state. Recipe сам по себе не является Evidence; фактические данные появляются только после успешного source fetch/extraction.

## Текущее состояние

В текущем production service graph AGENT не подключён (`agent=None`). Поэтому существующие active recipes могут использоваться для deterministic BROWSER replay, но автоматическое создание новых agent-generated recipes сейчас не является рабочим production path.

Ниже сохранён lifecycle contract, который действует для существующих recipes и обязателен при повторном включении AGENT.

## Состояния

`SiteRecipe` имеет:

- `candidate` — in-memory candidate, ожидающий verification;
- `active` — verified recipe, разрешённый для normal replay;
- `invalidated` — expired/repeatedly failing version, которую нельзя replay'ить.

Legacy recipes без lifecycle fields читаются как `active` для backward compatibility.

## Verified promotion

При активном AGENT допустимый путь:

1. AGENT возвращает bounded action sequence.
2. `AgentRecipeCompiler` преобразует только supported deterministic subset в `RecipeStep`.
3. `RecipeManager.candidate()` создаёт новую version в памяти.
4. BROWSER replay'ит candidate на public page.
5. Только successful non-blocked replay позволяет `RecipeManager.mark_success()` и persistence как `active`.

`RecipeManager.save()` отклоняет unverified `candidate`. Unsupported actions, failed replay, unsafe URL, CAPTCHA/access challenge и blocked replay не создают active recipe.

Пока AGENT не подключён, этот путь остаётся dormant и не должен описываться как фактически выполняемый.

## Failure и invalidation

Active recipes считают cumulative successes, failures и consecutive failures. По умолчанию 3 consecutive replay failures invalidates current version. Successful replay сбрасывает только consecutive failure counter.

Expired recipe invalidates при read. Default max age — 30 дней от последнего successful verification/use, если он известен.

Invalidated versions сохраняются как immutable lifecycle history. Новое исследование создаёт новую candidate/version, а не reactivates старую.

## Version retention

Repository хранит bounded число versions на `(domain, goal)`, default `10`. Cleanup поддерживают SQLite/PostgreSQL.

В PostgreSQL mutation/cleanup recipe state во время worker execution защищается lease fencing: stale worker после lease loss не должен менять recipe state.

## Security boundary

SiteRecipe не обходит authentication, CAPTCHA, paywall, rate limit или access control. BROWSER продолжает использовать `UrlGuard`, redirect checks, request limits и domain restrictions.

Если в будущем AGENT снова включён, reusable operational artifact остаётся только deterministic verified recipe, а не model output.

## Provenance

Recipe-backed BROWSER response может сохранять `recipe_id`, `recipe_version` и bounded lifecycle metadata. Эти данные входят в provenance/telemetry и не являются factual Evidence.
