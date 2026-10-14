document.querySelectorAll('[data-payment-preview-url]').forEach((form) => {
  const preview = form.querySelector('[data-payment-preview]');
  const status = form.querySelector('[data-payment-preview-status]');
  let timer;
  let controller;
  const refresh = async () => {
    controller?.abort();
    controller = new AbortController();
    const signal = controller.signal;
    preview.hidden = true;
    if (!form.querySelector('[data-payment-method]').value) {
      status.textContent = 'Selecione a forma de pagamento para calcular os valores.';
      return;
    }
    status.textContent = 'Calculando recebimento…';
    try {
      const response = await fetch(form.dataset.paymentPreviewUrl, {
        method: 'POST', body: new FormData(form), credentials: 'same-origin', signal,
      });
      const result = await response.json();
      if (signal.aborted) return;
      if (!response.ok) {
        status.textContent = result.error || 'Revise os campos para calcular o recebimento.';
        return;
      }
      preview.querySelectorAll('[data-quote]').forEach((field) => {
        field.textContent = result[field.dataset.quote];
      });
      preview.hidden = false;
      status.textContent = 'Prévia calculada. Confira o total cobrado do cliente antes de confirmar.';
    } catch (error) {
      if (error.name !== 'AbortError') status.textContent = 'Não foi possível calcular a prévia. Revise os dados antes de confirmar.';
    }
  };
  const schedule = () => {
    controller?.abort();
    preview.hidden = true;
    status.textContent = 'Atualizando prévia…';
    clearTimeout(timer);
    timer = setTimeout(refresh, 250);
  };
  form.addEventListener('input', schedule);
  form.addEventListener('change', schedule);
  refresh();
});
