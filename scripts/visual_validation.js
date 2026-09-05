// Ferramenta de teste, não dependência do ERP. A instalação local é informada pelo ambiente.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const base = 'http://127.0.0.1:8876';
const output = path.resolve(__dirname, '../artifacts/visual');
const assert = (ok, message) => { if (!ok) throw Error(message); };

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: 'msedge' });
  try {
    const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
    const external = [], errors = [], checks = [];
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== base) {
        external.push(route.request().url());
        return route.abort();
      }
      return route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    page.on('dialog', dialog => dialog.accept());
    const goto = async route => {
      const response = await page.goto(base + route);
      assert(response.status() === 200, `${route}: HTTP ${response.status()}`);
    };
    const post = async (button, pattern) => {
      const waiting = page.waitForResponse(response => response.request().method() === 'POST');
      await button.click();
      const response = await waiting;
      assert(response.status() === 303, `POST ${response.url()}: ${response.status()}`);
      await page.waitForURL(pattern);
      await page.waitForLoadState('load');
    };
    await goto('/login');
    await page.locator('[name=email]').fill('adm');
    await page.locator('[name=password]').fill('adm');
    await post(page.getByRole('button', { name: 'Entrar', exact: true }), /clientes\/lista$/);

    await goto('/clientes/novo');
    await page.locator('[name=name]').fill('Cliente visual Árvore');
    await page.locator('[name=phone]').fill('5511987654321');
    await page.locator('[name=notes]').fill('Observação local. <script> não é código executável.');
    await post(page.getByRole('button', { name: 'Salvar cliente', exact: true }), /clientes\/\d+\?saved=1$/);
    const customerPath = new URL(page.url()).pathname;
    await page.locator('[data-modal-open=visit-modal]').click();
    await page.locator('#visit-modal [name=note]').fill('Visita da validação visual');
    await post(page.locator('#visit-modal button[type=submit]'), new RegExp(customerPath + '$'));
    assert((await page.locator('body').textContent()).includes('Visita da validação visual'), 'Timeline de visita');
    await post(page.getByRole('button', { name: 'Inativar cliente', exact: true }), new RegExp(customerPath + '$'));
    await post(page.getByRole('button', { name: 'Reativar cliente', exact: true }), new RegExp(customerPath + '$'));
    await goto(customerPath + '/editar');
    assert((await page.locator('[name=phone]').inputValue()).replace(/\D/g, '') === '5511987654321', 'Telefone perdeu DDI ao editar');

    await goto('/servicos/novo');
    await page.locator('[name=name]').fill('Serviço visual local');
    await page.locator('[name=code]').fill('VISUAL-001');
    await page.locator('[name=initial_price]').fill('30,00');
    await post(page.getByRole('button', { name: 'Salvar serviço', exact: true }), /servicos\/\d+\?saved=1$/);
    const servicePath = new URL(page.url()).pathname;
    for (const amount of ['35,00', '40,00']) {
      await goto('/servicos/precos');
      await page.locator('button[data-modal-open]').first().click();
      const modal = page.locator('.modal-backdrop:not([hidden])');
      await modal.locator('[name=amount]').fill(amount);
      await modal.locator('[name=reason]').fill('Reajuste de teste isolado');
      await post(modal.locator('button[type=submit]'), /price_saved=1$/);
    }
    await goto('/servicos/precos?history=' + servicePath.split('/').pop());
    const priceHistory = await page.locator('#history-modal').textContent();
    for (const amount of ['30,00', '35,00', '40,00']) assert(priceHistory.includes(amount), 'Histórico de preço ' + amount);

    await goto('/caixa/resumo');
    assert((await page.locator('.primary-banner__value').textContent()).includes('R$ 0,00'), 'Caixa de teste deve iniciar vazio');
    await goto('/caixa/novo-lancamento');
    await page.locator('[data-modal-open=cash-categories-modal]').click();
    const category = page.locator('#cash-categories-modal');
    await category.locator('input[name=name]').fill('Operacional');
    await category.locator('select[name=movement_type]').selectOption('BOTH');
    await post(category.getByRole('button', { name: 'Criar categoria', exact: true }), /categories=1$/);

    async function movement(type, gross, description, fee) {
      await goto('/caixa/novo-lancamento');
      await page.locator(`.cash-type-option--${type === 'ENTRY' ? 'entry' : 'exit'}`).click();
      await page.locator('[data-cash-form] [name=gross_amount]').fill(gross);
      await page.locator('[data-cash-form] [name=description]').fill(description);
      await page.locator('[data-cash-form] select[name=category_id]').selectOption({ label: 'Operacional · Ambos' });
      await page.locator('[name=payment_method_id]').selectOption({ label: fee ? 'Cartão' : 'Dinheiro' });
      if (fee) {
        await page.locator('[name=has_fee]').check();
        await page.locator('[name=fee_amount]').fill(fee);
        assert((await page.locator('[data-net-amount]').textContent()).includes('485,00'), 'Prévia líquida');
      }
      await post(page.getByRole('button', { name: 'Registrar lançamento', exact: true }), /caixa\/novo-lancamento$/);
      assert((await page.locator('.alert--success').textContent()).includes('registrada com sucesso'), 'Confirmação após commit');
    }
    await movement('ENTRY', '1.000,00', 'Entrada visual');
    await movement('ENTRY', '500,00', 'Entrada visual com taxa', '15,00');
    await movement('EXIT', '200,00', 'Saída visual');
    await movement('EXIT', '75,50', 'Saída complementar');
    await movement('ENTRY', '100,00', 'Para cancelar');
    await goto('/caixa/historico');
    await page.locator('tbody tr', { hasText: 'Para cancelar' }).getByRole('button', { name: 'Cancelar', exact: true }).click();
    const cancel = page.locator('.modal-backdrop:not([hidden])');
    await cancel.locator('[name=reason]').fill('Validação descartável');
    await post(cancel.getByRole('button', { name: 'Cancelar lançamento', exact: true }), /caixa\/movimentos\/\d+$/);
    const movementPath = new URL(page.url()).pathname;
    for (const route of ['resumo', 'historico', 'relatorios']) {
      await goto('/caixa/' + route);
      assert((await page.locator('body').textContent()).includes('R$ 1.209,50'), 'Reconciliação visual ' + route);
    }
    await goto('/caixa/novo-lancamento');
    // Duplo submit no DOM: o segundo deve ser cancelado antes de outra requisição.
    const duplicateGuard = await page.locator('[data-cash-form]').evaluate(form => {
      const first = new Event('submit', { bubbles: true, cancelable: true });
      const second = new Event('submit', { bubbles: true, cancelable: true });
      form.dispatchEvent(first); form.dispatchEvent(second);
      return !first.defaultPrevented && second.defaultPrevented;
    });
    assert(duplicateGuard, 'Proteção de duplo submit');

    const routes = ['/clientes/lista', '/clientes/novo', '/clientes/historico', customerPath, customerPath + '/editar',
      '/servicos/catalogo', '/servicos/novo', '/servicos/categorias', '/servicos/precos', servicePath,
      '/caixa/resumo', '/caixa/novo-lancamento', '/caixa/historico', '/caixa/relatorios', movementPath,
      '/admin/usuarios', '/admin/permissoes', '/admin/configuracoes', '/admin/sistema'];
    for (const [width, height] of [[1920,1080], [1366,768], [1280,720], [390,844]]) {
      await page.setViewportSize({ width, height });
      for (const route of routes) {
        await goto(route);
        const dimensions = await page.evaluate(() => ({ width: window.innerWidth, scroll: document.documentElement.scrollWidth }));
        if (dimensions.scroll > dimensions.width + 1) {
          const offenders = await page.evaluate(() => Array.from(document.querySelectorAll('body *')).map(element => {
            const rect = element.getBoundingClientRect();
            return { tag: element.tagName.toLowerCase(), id: element.id, className: String(element.className || ''),
              left: Math.round(rect.left), right: Math.round(rect.right), width: Math.round(rect.width),
              clientWidth: element.clientWidth, scrollWidth: element.scrollWidth };
          }).filter(item => item.right > window.innerWidth + 1).slice(0, 20));
          throw Error(`Overflow de página ${width} ${route}: ${dimensions.scroll}; elementos=${JSON.stringify(offenders)}`);
        }
        assert(await page.locator('h1').count() > 0, 'Tela sem título ' + route);
        checks.push({ width, height, route, status: 'PASS' });
        if ([1366,390].includes(width) && ['/clientes/lista','/clientes/novo','/servicos/precos','/caixa/resumo','/caixa/novo-lancamento','/caixa/historico','/caixa/relatorios'].includes(route)) {
          await page.screenshot({ path: path.join(output, `${width}-${route.replaceAll('/','_')}.png`), fullPage: true });
        }
      }
      if (width === 390) {
        await page.locator('button[data-sidebar-toggle]').click();
        await page.locator('.main-nav a', { hasText: 'Caixa' }).click();
        assert(!(await page.locator('body').getAttribute('class') || '').includes('sidebar-open'), 'Menu mobile deve recolher');
      }
    }
    await goto('/caixa/resumo');
    await page.reload();
    assert((await page.locator('.primary-banner__value').textContent()).includes('1.209,50'), 'Refresh não deve duplicar lançamento');
    assert(external.length === 0, 'Tentativas externas: ' + external.join(', '));
    assert(errors.length === 0, 'Erros de console/JS: ' + errors.join(', '));
    const results = { checks, external_requests: external.length, console_errors: errors.length,
      balance: '1209.50', workflows: ['login', 'cliente', 'visita', 'inativar/reativar', 'telefone DDI', 'serviço', 'preços 30/35/40', 'categoria caixa', 'entrada', 'taxa', 'saída', 'cancelamento', 'relatórios', 'duplo submit', 'refresh', 'menu mobile'],
      offline_method: 'Toda requisição fora da origem loopback foi bloqueada; nenhuma foi solicitada.' };
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
    console.log(JSON.stringify({ checks: checks.length, external_requests: 0, console_errors: 0, balance: results.balance }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
