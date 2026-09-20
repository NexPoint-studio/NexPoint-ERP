(() => {
  const panel = document.getElementById('nexa-panel');
  const launchers = [...document.querySelectorAll('[data-nexa-open]')];
  if (!panel || !launchers.length) return;

  const backdrop = document.querySelector('.nexa-backdrop');
  const context = panel.querySelector('[data-nexa-context]');
  const risks = panel.querySelector('[data-nexa-risk]');
  const messages = panel.querySelector('[data-nexa-messages]');
  const form = panel.querySelector('[data-nexa-form]');
  const status = panel.querySelector('[data-nexa-status]');
  const input = form.querySelector('textarea');
  const submit = form.querySelector('button[type="submit"]');
  const primaryLaunch = launchers.find((item) => item.querySelector('[data-nexa-badge]')) || launchers[0];
  const badge = primaryLaunch.querySelector('[data-nexa-badge]');
  const recoveryAvailability = document.querySelector('[data-nexa-recovery-availability]');
  let previousFocus = null;

  function publicHttpsSource(raw) {
    try {
      const url = new URL(raw);
      const host = url.hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase();
      if (url.protocol !== 'https:' || url.username || url.password || !host || host.includes(':')) return null;
      if (host === 'localhost' || !host.includes('.') || /\.(localhost|local|localdomain|internal|home|lan)$/.test(host)) return null;
      const ipv4 = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
      if (ipv4) {
        const octets = ipv4.slice(1).map(Number);
        if (octets.some((value) => value > 255)) return null;
        const [a, b] = octets;
        if (a === 0 || a === 10 || a === 127 || a >= 224 ||
            (a === 100 && b >= 64 && b <= 127) ||
            (a === 169 && b === 254) ||
            (a === 172 && b >= 16 && b <= 31) ||
            (a === 192 && (b === 0 || b === 168)) ||
            (a === 198 && (b === 18 || b === 19)) ||
            (a === 198 && b === 51) || (a === 203 && b === 0)) return null;
      } else if (/^[0-9a-f.x]+$/.test(host)) return null;
      return url;
    } catch (_) { return null; }
  }

  function addMessage(value, kind, sources = []) {
    const message = document.createElement('p');
    message.className = `nexa-message nexa-message--${kind}`;
    message.textContent = String(value || 'Não foi possível obter uma resposta. Tente novamente.');
    const safeSources = kind === 'assistant' && Array.isArray(sources)
      ? sources.slice(0, 5).map((source) => ({ source, url: publicHttpsSource(source?.url) })).filter((item) => item.url)
      : [];
    if (safeSources.length) {
      const sourceList = document.createElement('span');
      sourceList.className = 'nexa-panel__sources';
      sourceList.append(document.createTextNode('Fontes: '));
      safeSources.forEach(({ source, url }, index) => {
        if (index) sourceList.append(document.createTextNode(' · '));
        const link = document.createElement('a');
        link.href = url.href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = String(source.title || url.hostname);
        sourceList.append(link);
      });
      message.append(sourceList);
    }
    messages.append(message);
    messages.scrollTop = messages.scrollHeight;
  }

  function renderContext(data) {
    const moduleName = typeof data.module === 'string' ? data.module : data.module?.name;
    context.textContent = moduleName ? `Contexto: ${moduleName} · ${data.screen || location.pathname}` : 'Ajuda contextual desta tela.';
    risks.replaceChildren();
    const relevant = Array.isArray(data.risks) ? data.risks.filter((risk) => ['high', 'critical', 'alto', 'critico', 'crítico'].includes(String(risk.level || risk.severity).toLowerCase())) : [];
    risks.hidden = relevant.length === 0;
    if (badge && relevant.length === 0) badge.hidden = true;
    else if (badge && Array.isArray(data.new_alerts) && data.new_alerts.length && panel.hidden) badge.hidden = false;
    relevant.slice(0, 3).forEach((risk) => {
      const title = document.createElement('strong');
      title.textContent = `Risco ${risk.level || risk.severity}: ${risk.summary || 'Atenção ao módulo atual'}`;
      risks.append(title);
      if (Array.isArray(risk.evidence) && risk.evidence.length) {
        const detail = document.createElement('small');
        detail.textContent = `Evidências: ${risk.evidence.slice(0, 3).map(String).join(' · ')}`;
        risks.append(detail);
      }
      [
        ['Causa provável', risk.probable_cause],
        ['Impacto', risk.impact],
        ['Recomendação', risk.recommendation],
      ].forEach(([label, value]) => {
        if (typeof value !== 'string' || !value) return;
        const detail = document.createElement('small');
        detail.textContent = `${label}: ${value}`;
        risks.append(detail);
      });
    });
  }

  async function loadContext() {
    context.textContent = 'Consultando contexto seguro do ERP…';
    try {
      const url = new URL('/nexa/context', location.origin);
      url.searchParams.set('screen', location.pathname);
      const response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error('context unavailable');
      const data = await response.json();
      renderContext(data);
      if (recoveryAvailability) {
        recoveryAvailability.textContent = data.available === true
          ? 'Nexa disponível para orientação segura. A senha continua somente no ERP local.'
          : 'Nexa indisponível sem conexão ou configuração ativa. Use “Solicitar recuperação à NexPoint” na tela de acesso à Administração.';
      }
    } catch (_) {
      context.textContent = 'Contexto indisponível no momento. Você ainda pode descrever sua dúvida.';
      risks.hidden = true;
      if (badge) badge.hidden = true;
      if (recoveryAvailability) recoveryAvailability.textContent = 'Nexa offline. Use “Solicitar recuperação à NexPoint” na tela de acesso à Administração.';
    }
  }

  launchers.forEach((launch) => launch.addEventListener('click', () => {
      previousFocus = document.activeElement;
      panel.hidden = false;
      backdrop.hidden = false;
      launchers.forEach((item) => item.setAttribute('aria-expanded', 'true'));
      if (badge) badge.hidden = true;
      const contextualPrompt = launch.dataset.nexaPrompt;
      if (contextualPrompt) input.value = contextualPrompt.slice(0, 2000);
      input.focus();
      loadContext();
    }));

  // Deterministic local watch; it never calls an LLM until the user sends a message.
  loadContext();
  window.setInterval(() => { if (!document.hidden) loadContext(); }, 60_000);

  function close() {
    panel.hidden = true;
    backdrop.hidden = true;
    launchers.forEach((item) => item.setAttribute('aria-expanded', 'false'));
    (previousFocus || primaryLaunch).focus();
  }
  document.querySelectorAll('[data-nexa-close]').forEach((button) => button.addEventListener('click', close));
  document.addEventListener('keydown', (event) => {
    if (panel.hidden) return;
    if (event.key === 'Escape') close();
    if (event.key === 'Tab') {
      const focusable = [...panel.querySelectorAll('button, textarea, a[href]')].filter((item) => !item.disabled);
      if (!focusable.length) return;
      if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable[focusable.length - 1].focus(); }
      else if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) { event.preventDefault(); focusable[0].focus(); }
    }
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message || submit.disabled) return;
    addMessage(message, 'user');
    input.value = '';
    submit.disabled = true;
    status.textContent = 'Nexa está analisando…';
    try {
      const response = await fetch('/nexa/chat', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ message, screen: location.pathname }),
      });
      if (!response.ok) throw new Error('chat unavailable');
      const data = await response.json();
      addMessage(data.reply || data.answer || data.error, 'assistant', data.sources);
      const diagnosisReference = document.querySelector('form[action="/admin/suporte/chamados"] input[name="nexa_request_id"]');
      if (diagnosisReference && typeof data.request_id === 'string') diagnosisReference.value = data.request_id;
      status.textContent = '';
    } catch (_) {
      addMessage('A Nexa está indisponível no momento. O ERP continua funcionando; tente novamente mais tarde.', 'assistant');
      status.textContent = 'Não foi possível conectar à Nexa.';
    } finally {
      submit.disabled = false;
      input.focus();
    }
  });
})();
