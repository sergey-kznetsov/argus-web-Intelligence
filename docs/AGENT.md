# AGENT: рабочий контракт

AGENT — активный резервный слой навигации ARGUS для публичных страниц, когда детерминированные FAST/BROWSER-механизмы не получили достаточное представление. AGENT не является factual source: текст модели, навигационная история и выбранные действия никогда не становятся Observation или Evidence.

## Рабочая цепочка

Обычная эскалация выполняется последовательно:

```text
verified SiteRecipe replay
-> FAST
-> BROWSER, если статического ответа недостаточно
-> AGENT, если BROWSER завершился ошибкой либо вернул недостаточный незаблокированный DOM
-> deterministic Playwright replay
-> factual extraction
```

CAPTCHA, login, paywall, access-control, robots/rate-limit challenge останавливают ветку. Другой AGENT backend не используется как средство обхода.

В режиме `ARGUS_AGENT_BACKEND=auto` порядок фиксирован:

1. `ollama-recipe` выбирает только заранее извлечённые безопасные controls и подготовленные ARGUS значения публичного GET/search/filter;
2. `stagehand` анализирует инертный snapshot уже полученной страницы и возвращает только безопасные click selectors;
3. `browser-use` выполняет ограниченную публичную навигацию в отдельном Python-окружении.

Все backends используют один глобальный LLM semaphore с параллелизмом 1. Timeout, отсутствие optional dependency или недоступность Ollama дают структурированную деградацию и следующий безопасный fallback.

## SiteRecipe

Модель не получает произвольное прямое управление production Playwright. Предложенный путь:

1. проходит allowlist действий и проверку опасных маркеров;
2. компилируется в bounded `SiteRecipe`;
3. повторно выполняется обычным BROWSER runtime с `UrlGuard`;
4. становится активным только после source-backed проверки исследовательской цели.

Unknown actions, неоднозначные action objects, произвольные keyboard shortcuts, login/payment/upload/download/state-changing controls отклоняются.

## Граница Evidence

AGENT помогает получить публичную страницу, но не доказывает предметный факт. Факт извлекается только из независимо fetched source и должен иметь Observation/Evidence/Provenance. Для семантического Evidence точная цитата обязана буквально присутствовать в тексте источника.

Полный ресурсный профиль, разделение окружений Stagehand/Browser Use и health-семантика описаны в [LLM_AGENT_RUNTIME.md](LLM_AGENT_RUNTIME.md).
