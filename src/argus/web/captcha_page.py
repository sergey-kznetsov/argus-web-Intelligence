CAPTCHA_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ARGUS CAPTCHA</title>
  <link rel="stylesheet" href="/assets/captcha.css">
</head>
<body>
<main>
  <div class="row"><div><h1>CAPTCHA ARGUS</h1><p class="muted">Ожидающие ручного вмешательства проверки.</p></div><button id="refresh" class="secondary">Обновить</button></div>
  <div id="root" class="panel">Загрузка...</div>
</main>
<script src="/assets/captcha.js" defer></script>
</body>
</html>
"""

CAPTCHA_STYLE_CSS = """body{font-family:Inter,system-ui,sans-serif;background:#f4f6f8;color:#17202a;margin:0}main{max-width:900px;margin:0 auto;padding:28px 18px 60px}.panel{background:#fff;border:1px solid #dfe3e8;border-radius:12px;padding:18px;margin-top:16px}.row{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}button{border:0;border-radius:8px;padding:10px 14px;background:#182230;color:#fff;font:inherit;font-weight:700;cursor:pointer}button.secondary{background:#e8edf2;color:#25313c}button:disabled{opacity:.45;cursor:not-allowed}input{font:inherit;padding:10px 12px;border:1px solid #cfd6dd;border-radius:8px;min-width:260px}img{max-width:100%;border:1px solid #dfe3e8;border-radius:8px;margin-top:12px}.interactive-shot{cursor:crosshair}.interactive-shot.busy{cursor:wait;opacity:.75}code{word-break:break-all}.muted{color:#667085}.danger{color:#b42318}.ok{color:#067647}"""

CAPTCHA_APP_JS = """(() => {
  const root = document.getElementById('root');
  const request = async (path, options={}) => {
    const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
    const payload = await response.json().catch(() => ({detail:'Некорректный ответ'}));
    if (!response.ok) throw new Error(JSON.stringify(payload));
    return payload;
  };
  async function submitText(id, input, message) {
    try {
      await request(`/api/captcha/${encodeURIComponent(id)}/answer`, {method:'POST', body:JSON.stringify({answer:input.value})});
      message.textContent = 'Ответ отправлен. ARGUS продолжает текущую браузерную сессию.';
      message.className = 'ok';
      input.disabled = true;
    } catch (error) {
      message.textContent = String(error);
      message.className = 'danger';
    }
  }
  async function submitInteraction(id, payload, message, image=null) {
    try {
      if (image) image.classList.add('busy');
      await request(`/api/captcha/${encodeURIComponent(id)}/interaction`, {method:'POST', body:JSON.stringify(payload)});
      message.textContent = payload.action === 'click'
        ? 'Клик передан в ту же браузерную сессию. Ожидаю новый снимок или продолжение сбора.'
        : 'Запрошен новый снимок текущей браузерной сессии.';
      message.className = 'ok';
    } catch (error) {
      message.textContent = String(error);
      message.className = 'danger';
    } finally {
      if (image) image.classList.remove('busy');
    }
  }
  async function load() {
    try {
      const payload = await request('/api/captcha');
      root.replaceChildren();
      const items = Array.isArray(payload.items) ? payload.items : [];
      if (!items.length) { root.textContent = 'Ожидающих CAPTCHA нет.'; return; }
      items.forEach((item) => {
        const card = document.createElement('section');
        card.className = 'panel';
        const title = document.createElement('h2');
        title.textContent = item.kind === 'text' ? 'Текстовая CAPTCHA' : 'Интерактивная проверка';
        const meta = document.createElement('p');
        meta.className = 'muted';
        meta.textContent = `${item.status} · действий: ${item.interaction_count || 0} · ${item.url || ''}`;
        const prompt = document.createElement('p');
        prompt.textContent = item.prompt || 'Требуется ручное действие.';
        card.append(title, meta, prompt);
        const message = document.createElement('p');
        let image = null;
        if (item.has_screenshot) {
          image = document.createElement('img');
          image.src = `/api/captcha/${encodeURIComponent(item.challenge_id)}/screenshot?v=${encodeURIComponent(item.updated_at || Date.now())}`;
          image.alt = 'Снимок CAPTCHA';
          if (item.interactive_input_supported && item.status === 'interactive_required') {
            image.className = 'interactive-shot';
            image.title = 'Кликните по нужному элементу проверки';
            image.addEventListener('click', (event) => {
              const rect = image.getBoundingClientRect();
              if (!rect.width || !rect.height) return;
              const xRatio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
              const yRatio = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
              submitInteraction(item.challenge_id, {action:'click', x_ratio:xRatio, y_ratio:yRatio}, message, image);
            });
          }
          card.appendChild(image);
        }
        if (item.manual_input_supported && item.status === 'pending') {
          const row = document.createElement('div');
          row.className = 'row';
          const input = document.createElement('input');
          input.autocomplete = 'off';
          input.placeholder = 'Введите ответ CAPTCHA';
          const button = document.createElement('button');
          button.textContent = 'Отправить';
          button.addEventListener('click', () => submitText(item.challenge_id, input, message));
          row.append(input, button);
          card.appendChild(row);
        } else if (item.interactive_input_supported) {
          const row = document.createElement('div');
          row.className = 'row';
          const refreshButton = document.createElement('button');
          refreshButton.className = 'secondary';
          refreshButton.textContent = 'Обновить снимок';
          refreshButton.disabled = item.status !== 'interactive_required';
          refreshButton.addEventListener('click', () => submitInteraction(item.challenge_id, {action:'refresh'}, message, image));
          row.appendChild(refreshButton);
          card.appendChild(row);
          if (item.status === 'interactive_required') {
            message.textContent = 'Кликните прямо по нужному элементу на снимке. ARGUS перенесёт координату клика в сохранённую браузерную сессию.';
            message.className = 'muted';
          } else {
            message.textContent = 'ARGUS применяет последнее действие в браузере.';
            message.className = 'muted';
          }
        }
        card.appendChild(message);
        root.appendChild(card);
      });
    } catch (error) {
      root.textContent = String(error);
    }
  }
  document.getElementById('refresh').addEventListener('click', load);
  load();
  setInterval(load, 3000);
})();
"""
