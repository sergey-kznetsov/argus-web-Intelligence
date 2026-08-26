INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ARGUS Web Intelligence</title>
  <link rel="stylesheet" href="/assets/style.css">
</head>
<body>
  <main class="shell">
    <header>
      <div>
        <p class="eyebrow">AI web-intelligence backend</p>
        <h1>ARGUS Web Intelligence</h1>
        <p class="lead">Отдельный операторский контур поверх того же ARGUS API. Запросы из этого интерфейса проходят через тот же Research Planner, FAST → BROWSER → AGENT, Evidence и storage, что и запросы аналитических модулей.</p>
      </div>
      <div id="health" class="status">API: проверка...</div>
    </header>

    <section class="panel">
      <h2>Тестовый профиль потребителя</h2>
      <p class="hint">Кнопки только заполняют CollectionRequest. Они не включают скрытую логику в ARGUS и нужны для проверки того, какой стек фактов реально собирается под запрос конкретного модуля.</p>
      <div id="test-profiles" class="profile-actions"><button type="button" class="secondary" disabled>Загрузка профилей...</button></div>
      <p id="profile-description" class="profile-description">Свободный режим: параметры запроса задаются вручную.</p>
    </section>

    <section class="panel">
      <h2>Новое исследование</h2>
      <form id="collection-form">
        <div class="grid">
          <label>Потребитель<input id="consumer" value="standalone.web" required maxlength="128"></label>
          <label>ID анализа<input id="analysis-id" placeholder="web-..." maxlength="128"></label>
          <label>Город<input id="city" placeholder="Пермь" maxlength="256"></label>
          <label>Адрес<input id="address" placeholder="Комсомольский проспект, 27" maxlength="512"></label>
          <label class="wide">Цели исследования<input id="intents" value="public_mentions,reviews,local_news,incidents,discussions" required></label>
          <label>Максимум страниц<input id="max-pages" type="number" value="30" min="1" max="500"></label>
          <label>Глубина исследования<input id="max-depth" type="number" value="2" min="0" max="5"></label>
          <label>Язык представления<input id="output-language" value="ru" readonly></label>
          <label class="wide">Дополнительный пул сайтов<textarea id="source-pool-urls" rows="4" placeholder="https://example.org/page&#10;https://example.net/source"></textarea><span class="field-help">По одному URL на строку. Эти сайты не получают приоритет и дополняют автоматический поиск ARGUS.</span></label>
        </div>
        <div class="actions">
          <button type="submit">Запустить исследование</button>
          <button type="button" id="cancel" class="secondary" disabled>Отменить</button>
        </div>
      </form>
    </section>

    <section class="panel">
      <div class="row"><h2>Состояние</h2><code id="collection-id">—</code></div>
      <pre id="status-output">Исследование ещё не запускалось.</pre>
    </section>

    <section class="panel">
      <div class="row"><h2>Русскоязычный отчёт</h2><span class="hint">Производный слой, не Evidence</span></div>
      <p id="presentation-summary" class="report-summary">Отчёт появится после завершения исследования.</p>
      <div class="table-wrap">
        <table id="presentation-table">
          <thead><tr><th>Категория</th><th>Заголовок</th><th>Факт</th><th>Источник</th></tr></thead>
          <tbody id="presentation-body"><tr><td colspan="4">—</td></tr></tbody>
        </table>
      </div>
      <p class="field-help">Русский текст связан с исходными Observation/Evidence. Оригинальные цитаты не переводятся и остаются доступны ниже для проверки.</p>
    </section>

    <section class="panel split">
      <div><h2>Оригинальные наблюдения</h2><pre id="observations-output">—</pre></div>
      <div><h2>Оригинальные доказательства</h2><pre id="evidence-output">—</pre></div>
    </section>
  </main>
  <script src="/assets/app.js" defer></script>
</body>
</html>
"""

STYLE_CSS = """:root{font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#17202a;background:#f4f6f8}*{box-sizing:border-box}body{margin:0}.shell{max-width:1180px;margin:0 auto;padding:32px 20px 60px}header{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;margin-bottom:24px}.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:12px;font-weight:700;color:#52606d}.lead{max-width:780px;color:#52606d;line-height:1.55}h1{margin:.2rem 0;font-size:34px}h2{margin:0 0 16px;font-size:18px}.panel{background:#fff;border:1px solid #dfe3e8;border-radius:12px;padding:20px;margin-top:16px;box-shadow:0 1px 2px rgba(16,24,40,.04)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.wide{grid-column:1/-1}label{display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:650;color:#344054}input,textarea{font:inherit;padding:10px 12px;border:1px solid #cfd6dd;border-radius:8px;background:#fff;color:#17202a}input[readonly]{background:#f8fafb;color:#52606d}textarea{resize:vertical}.actions,.profile-actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px}button{border:0;border-radius:8px;padding:10px 16px;background:#182230;color:#fff;font:inherit;font-weight:700;cursor:pointer}button.secondary{background:#e8edf2;color:#25313c}button:disabled{opacity:.45;cursor:not-allowed}.status{padding:8px 12px;border:1px solid #d4dbe2;border-radius:999px;font-size:13px;background:#fff}.row{display:flex;justify-content:space-between;gap:16px;align-items:center}.hint,.profile-description,.field-help{color:#667085;line-height:1.5}.hint{margin:0}.profile-description{margin:12px 0 0}.field-help{font-size:12px;font-weight:400}.report-summary{line-height:1.6;margin:0 0 14px}.table-wrap{overflow:auto;border:1px solid #e2e8ee;border-radius:8px}table{width:100%;border-collapse:collapse;min-width:760px}th,td{text-align:left;vertical-align:top;padding:10px 12px;border-bottom:1px solid #e8edf2;font-size:13px;line-height:1.45}th{background:#f8fafb;color:#344054;font-weight:700}tbody tr:last-child td{border-bottom:0}a{color:#155eef;word-break:break-all}pre{white-space:pre-wrap;word-break:break-word;margin:0;background:#f8fafb;border-radius:8px;padding:14px;max-height:460px;overflow:auto;font-size:12px;line-height:1.5}.split{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:760px){header{display:block}.status{display:inline-block;margin-top:12px}.grid,.split{grid-template-columns:1fr}.wide{grid-column:auto}}
"""

APP_JS = """(() => {
  const $ = (id) => document.getElementById(id);
  const terminal = new Set(['completed','partial','blocked','failed','cancelled']);
  const statusLabels = {
    queued: 'в очереди',
    running: 'в работе',
    completed: 'завершено',
    partial: 'частичный результат',
    blocked: 'источник заблокировал доступ',
    failed: 'ошибка',
    cancelled: 'отменено'
  };
  let collectionId = null;
  let pollTimer = null;

  const show = (id, value) => { $(id).textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2); };
  const csv = (value) => value.split(',').map(v => v.trim()).filter(Boolean);
  const lines = (value) => value.split(/\r?\n/).map(v => v.trim()).filter(Boolean);
  const request = async (path, options = {}) => {
    const response = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...options});
    const payload = await response.json().catch(() => ({detail: 'Некорректный JSON-ответ'}));
    if (!response.ok) throw new Error(JSON.stringify(payload));
    return payload;
  };

  async function checkHealth() {
    try {
      const payload = await request('/api/health');
      const status = statusLabels[payload.status] || payload.status || 'неизвестно';
      $('health').textContent = `API: ${status}`;
    } catch (error) {
      $('health').textContent = 'API: недоступен';
    }
  }

  async function loadProfiles() {
    const root = $('test-profiles');
    try {
      const profiles = await request('/api/test-profiles');
      root.replaceChildren();
      Object.entries(profiles).forEach(([profileId, profile]) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'secondary';
        button.textContent = profile.label || profileId;
        button.addEventListener('click', () => applyProfile(profile));
        root.appendChild(button);
      });
    } catch (error) {
      root.replaceChildren();
      const message = document.createElement('span');
      message.textContent = 'Не удалось загрузить тестовые профили.';
      root.appendChild(message);
    }
  }

  function applyProfile(profile) {
    if (profile.consumer) $('consumer').value = profile.consumer;
    if (Array.isArray(profile.intents)) $('intents').value = profile.intents.join(',');
    if (profile.max_pages) $('max-pages').value = String(profile.max_pages);
    if (profile.max_depth !== undefined) $('max-depth').value = String(profile.max_depth);
    $('profile-description').textContent = profile.description || 'Тестовый профиль применён.';
  }

  function buildPayload() {
    const city = $('city').value.trim();
    const address = $('address').value.trim();
    if (!city && !address) throw new Error('Укажите город или адрес');
    return {
      consumer: $('consumer').value.trim(),
      analysis_id: $('analysis-id').value.trim() || `web-${Date.now()}`,
      territory: {city: city || null, address: address || null},
      intents: csv($('intents').value),
      constraints: {
        max_pages: Number($('max-pages').value || 30),
        max_depth: Number($('max-depth').value || 2),
        output_language: $('output-language').value.trim() || 'ru',
        source_pool_urls: lines($('source-pool-urls').value)
      },
      allow_partial: true
    };
  }

  function resetPresentation(message = 'Ожидание результата...') {
    $('presentation-summary').textContent = message;
    const body = $('presentation-body');
    body.replaceChildren();
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 4;
    cell.textContent = '—';
    row.appendChild(cell);
    body.appendChild(row);
  }

  function renderPresentation(payload) {
    $('presentation-summary').textContent = payload.summary_ru || 'Русскоязычная сводка не сформирована.';
    const body = $('presentation-body');
    body.replaceChildren();
    const rows = Array.isArray(payload.rows) ? payload.rows : [];
    if (!rows.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 4;
      cell.textContent = 'Нет строк для отображения.';
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }
    rows.forEach((item) => {
      const row = document.createElement('tr');
      const category = document.createElement('td');
      category.textContent = item.category_ru || 'Факт';
      const title = document.createElement('td');
      title.textContent = item.title_ru || '—';
      const fact = document.createElement('td');
      fact.textContent = item.fact_ru || '—';
      const source = document.createElement('td');
      const link = document.createElement('a');
      link.href = item.source_url || '#';
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = item.source_url || '—';
      source.appendChild(link);
      row.append(category, title, fact, source);
      body.appendChild(row);
    });
  }

  async function submit(event) {
    event.preventDefault();
    try {
      const payload = buildPayload();
      const accepted = await request('/api/collections', {method: 'POST', body: JSON.stringify(payload)});
      collectionId = accepted.collection_id;
      $('collection-id').textContent = collectionId;
      $('cancel').disabled = false;
      show('status-output', accepted);
      resetPresentation();
      show('observations-output', 'Ожидание результата...');
      show('evidence-output', 'Ожидание результата...');
      schedulePoll(0);
    } catch (error) {
      show('status-output', String(error));
    }
  }

  function schedulePoll(delay = 2000) {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, delay);
  }

  async function poll() {
    if (!collectionId) return;
    try {
      const status = await request(`/api/collections/${encodeURIComponent(collectionId)}`);
      show('status-output', status);
      if (terminal.has(status.status)) {
        $('cancel').disabled = true;
        await loadResult();
        return;
      }
      schedulePoll();
    } catch (error) {
      show('status-output', String(error));
      schedulePoll(4000);
    }
  }

  async function loadResult() {
    if (!collectionId) return;
    const id = encodeURIComponent(collectionId);
    try {
      const [summary, presentation, observations, evidence] = await Promise.all([
        request(`/api/collections/${id}/result/summary`),
        request(`/api/collections/${id}/presentation`),
        request(`/api/collections/${id}/result/observations?limit=50`),
        request(`/api/collections/${id}/result/evidence?limit=50`)
      ]);
      show('status-output', summary);
      renderPresentation(presentation);
      show('observations-output', observations);
      show('evidence-output', evidence);
    } catch (error) {
      show('status-output', String(error));
      resetPresentation('Не удалось сформировать русскоязычное представление.');
    }
  }

  async function cancel() {
    if (!collectionId) return;
    try {
      const payload = await request(`/api/collections/${encodeURIComponent(collectionId)}/cancel`, {method: 'POST'});
      show('status-output', payload);
      $('cancel').disabled = true;
      schedulePoll(0);
    } catch (error) {
      show('status-output', String(error));
    }
  }

  $('collection-form').addEventListener('submit', submit);
  $('cancel').addEventListener('click', cancel);
  checkHealth();
  loadProfiles();
})();
"""
