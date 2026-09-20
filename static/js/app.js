document.querySelectorAll('[data-sidebar-toggle]').forEach((button) => {
  button.addEventListener('click', () => document.body.classList.toggle('sidebar-open'));
});

const dateTarget = document.querySelector('[data-current-date]');
if (dateTarget) {
  dateTarget.textContent = new Intl.DateTimeFormat('pt-BR', {
    weekday: 'long', day: '2-digit', month: 'long'
  }).format(new Date());
}

const syncStatus = document.querySelector('[data-sync-status]');
const syncStatusText = document.querySelector('[data-sync-status-text]');
const refreshSyncStatus = async () => {
  if (!syncStatus || !syncStatusText) return;
  try {
    const response = await fetch('/sync/status', {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    if (!response.ok) throw new Error('sync-status');
    const snapshot = await response.json();
    const state = ['offline', 'online', 'degraded', 'unknown'].includes(snapshot.state)
      ? snapshot.state : 'unknown';
    const waiting = Number.isInteger(snapshot.waiting) ? snapshot.waiting : 0;
    const sending = Number.isInteger(snapshot.sending) ? snapshot.sending : 0;
    const dead = Number.isInteger(snapshot.dead_letter) ? snapshot.dead_letter : 0;
    syncStatus.classList.remove('is-offline', 'is-online', 'is-degraded', 'is-unknown');
    syncStatus.classList.add(`is-${state}`);
    if (state === 'offline') syncStatusText.textContent = `Offline · ${waiting} item(ns) aguardando sincronização`;
    else if (state === 'degraded') syncStatusText.textContent = `Conexão instável · ${waiting} item(ns) pendente(s)`;
    else if (state === 'online' && waiting) syncStatusText.textContent = `Online · Sincronizando ${Math.max(waiting, sending)} item(ns)`;
    else if (state === 'online') syncStatusText.textContent = 'Online · Tudo sincronizado';
    else syncStatusText.textContent = `Conectividade desconhecida · ${waiting} item(ns) local(is)`;
    if (dead > 0) syncStatusText.textContent += ` · ${dead} requer(em) revisão`;
  } catch (_error) {
    syncStatus.classList.remove('is-offline', 'is-online', 'is-degraded');
    syncStatus.classList.add('is-unknown');
    syncStatusText.textContent = 'Sincronização local indisponível';
  }
};
refreshSyncStatus();
if (syncStatus) window.setInterval(refreshSyncStatus, 30000);

const adminRecoveryPoll = document.querySelector('[data-admin-recovery-poll]');
if (adminRecoveryPoll) {
  let polling = false;
  const refreshAdminRecovery = async () => {
    if (polling || document.hidden) return;
    polling = true;
    try {
      const response = await fetch(adminRecoveryPoll.dataset.statusUrl, {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      if (!response.ok) return;
      const snapshot = await response.json();
      if (snapshot.authorized === true) window.location.reload();
    } catch (_error) {
      // Offline is an expected state; the durable Outbox keeps the request.
    } finally {
      polling = false;
    }
  };
  refreshAdminRecovery();
  window.setInterval(refreshAdminRecovery, 5000);
}

document.querySelectorAll('.main-nav a, .tabs a').forEach((link) => {
  link.addEventListener('click', () => document.body.classList.remove('sidebar-open'));
});

document.querySelectorAll('[data-modal-open]').forEach((button) => {
  button.addEventListener('click', () => {
    const modal = document.getElementById(button.dataset.modalOpen);
    if (modal) modal.hidden = false;
  });
});
document.querySelectorAll('[data-modal-close]').forEach((button) => {
  button.addEventListener('click', () => {
    const modal = document.getElementById(button.dataset.modalClose);
    if (modal) modal.hidden = true;
  });
});
document.querySelectorAll('.modal-backdrop').forEach((modal) => {
  modal.addEventListener('click', (event) => { if (event.target === modal) modal.hidden = true; });
});

document.querySelectorAll('[data-customer-form]').forEach((form) => {
  const refreshType = () => {
    const company = form.querySelector('[name="type"]:checked')?.value === 'COMPANY';
    form.querySelectorAll('[data-company-only]').forEach((element) => { element.hidden = !company; });
    form.querySelectorAll('[data-person-only]').forEach((element) => { element.hidden = company; });
    form.querySelectorAll('[data-person-text]').forEach((element) => {
      element.textContent = company ? element.dataset.companyText : element.dataset.personText;
    });
    form.querySelectorAll('[data-person-label]').forEach((element) => {
      element.textContent = company ? element.dataset.companyLabel : element.dataset.personLabel;
    });
  };
  form.querySelectorAll('[name="type"]').forEach((input) => input.addEventListener('change', refreshType));
  refreshType();
});

document.querySelectorAll('[data-customer-picker]').forEach((picker) => {
  const search = picker.querySelector('[data-customer-search]');
  const select = picker.querySelector('[data-customer-select]');
  const status = picker.querySelector('[data-customer-status]');
  const endpoint = picker.dataset.customerEndpoint;
  if (!search || !select || !status || !endpoint) return;

  let timer = null;
  let requestController = null;

  const selectedSnapshot = () => {
    const option = select.selectedOptions[0];
    return option?.value ? { value: option.value, label: option.textContent } : null;
  };

  const render = (items) => {
    const selected = selectedSnapshot();
    const emptyLabel = select.options[0]?.textContent || 'Todos os clientes';
    const fragment = document.createDocumentFragment();
    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = emptyLabel;
    fragment.append(empty);
    const seen = new Set();
    items.forEach((item) => {
      const value = String(item.id);
      if (seen.has(value)) return;
      const option = document.createElement('option');
      option.value = value;
      option.textContent = `${item.name}${item.is_active ? '' : ' (inativo)'}`;
      fragment.append(option);
      seen.add(value);
    });
    if (selected && !seen.has(selected.value)) {
      const option = document.createElement('option');
      option.value = selected.value;
      option.textContent = selected.label;
      fragment.append(option);
    }
    select.replaceChildren(fragment);
    if (selected) select.value = selected.value;
    status.textContent = items.length
      ? `${items.length} cliente(s) encontrado(s). Selecione na lista.`
      : 'Nenhum cliente encontrado. Tente outro termo.';
  };

  const load = async () => {
    requestController?.abort();
    const controller = new AbortController();
    requestController = controller;
    const url = new URL(endpoint, window.location.origin);
    url.searchParams.set('q', search.value.trim());
    url.searchParams.set('active_only', picker.dataset.customerActiveOnly || '0');
    const selected = selectedSnapshot();
    if (selected) url.searchParams.set('selected_id', selected.value);
    picker.setAttribute('aria-busy', 'true');
    status.textContent = 'Buscando clientes…';
    try {
      const response = await fetch(url, {
        headers: { Accept: 'application/json' },
        credentials: 'same-origin',
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      render(Array.isArray(payload.items) ? payload.items.slice(0, 20) : []);
    } catch (error) {
      if (error.name !== 'AbortError') {
        status.textContent = 'Não foi possível buscar clientes. Tente novamente.';
      }
    } finally {
      if (requestController === controller) {
        requestController = null;
        picker.removeAttribute('aria-busy');
      }
    }
  };

  search.addEventListener('input', () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(load, 220);
  });
  search.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      window.clearTimeout(timer);
      load();
    }
  });
  select.addEventListener('change', () => {
    if (select.value) status.textContent = `Cliente selecionado: ${select.selectedOptions[0].textContent}.`;
  });
});

document.querySelectorAll('[data-price-input]').forEach((input) => {
  input.addEventListener('input', () => {
    const preview = input.closest('form')?.querySelector('[data-price-preview]');
    if (preview) preview.textContent = input.value.trim() ? `R$ ${input.value.trim()}` : 'Novo preço';
  });
});

// Evita reenvio por duplo clique em qualquer formulário de escrita local.
document.querySelectorAll('form[method="post"]').forEach((form) => {
  form.addEventListener('submit', (event) => {
    if (event.defaultPrevented) return;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    if (form.dataset.submitting === 'true') {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = 'true';
    form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach((button) => {
      button.disabled = true;
      button.setAttribute('aria-disabled', 'true');
    });
  });
});

window.addEventListener('pageshow', () => {
  document.querySelectorAll('form[data-submitting="true"]').forEach((form) => {
    delete form.dataset.submitting;
    form.querySelectorAll('[aria-disabled="true"]').forEach((button) => {
      button.disabled = false;
      button.removeAttribute('aria-disabled');
    });
  });
});

const parseCashMoney = (value) => {
  let raw = String(value ?? '').trim();
  if (raw.startsWith('R$')) raw = raw.slice(2).trim();
  if (raw.includes('R$')) return null;
  raw = raw.replace(/\s/g, '');
  let normalized = raw;
  if (raw.includes(',')) {
    if (!/^-?(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?$/.test(raw)) return null;
    normalized = raw.replace(/\./g, '').replace(',', '.');
  } else if (/^-?\d{1,3}(?:\.\d{3})+$/.test(raw)) {
    normalized = raw.replace(/\./g, '');
  } else if (!/^-?\d+(?:\.\d+)?$/.test(raw)) {
    return null;
  }
  const negative = normalized.startsWith('-');
  if (negative) return null;
  const unsigned = negative ? normalized.slice(1) : normalized;
  const [wholeRaw = '0', fractionRaw = ''] = unsigned.split('.');
  const whole = BigInt(wholeRaw || '0');
  if (whole > 999999999999n) return null;
  if (whole === 999999999999n && /^99[0-9]*[1-9][0-9]*$/.test(fractionRaw.padEnd(2, '0'))) return null;
  const fraction = (fractionRaw + '00').slice(0, 2);
  let cents = (whole * 100n) + BigInt(fraction);
  if ((fractionRaw[2] || '0') >= '5') cents += 1n;
  if (cents > 99999999999999n) return null;
  return cents;
};

const formatCashMoney = (valueInCents) => {
  const safeCents = valueInCents < 0n ? 0n : valueInCents;
  const whole = (safeCents / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  const cents = (safeCents % 100n).toString().padStart(2, '0');
  return `R$ ${whole},${cents}`;
};

document.querySelectorAll('[data-cash-form]').forEach((form) => {
  const typeInputs = [...form.querySelectorAll('[data-cash-type]')];
  const category = form.querySelector('[data-cash-category]');
  const feeSection = form.querySelector('[data-entry-fee-section]');
  const feeToggle = form.querySelector('[data-fee-toggle]');
  const feeFields = form.querySelector('[data-fee-fields]');
  const grossInput = form.querySelector('[data-gross-amount]');
  const feeInput = form.querySelector('[data-fee-amount]');
  const netTarget = form.querySelector('[data-net-amount]');
  const payment = form.querySelector('[data-payment-method]');
  const boletoNote = form.querySelector('[data-boleto-note]');

  const currentType = () => typeInputs.find((item) => item.checked)?.value
    || form.querySelector('input[name="movement_type"][type="hidden"]')?.value
    || 'ENTRY';

  const refreshPreview = () => {
    if (!netTarget) return;
    const gross = parseCashMoney(grossInput?.value);
    const fee = currentType() === 'ENTRY' && feeToggle?.checked ? parseCashMoney(feeInput?.value) : 0n;
    if (gross === null || fee === null) {
      netTarget.textContent = 'Valor inválido';
      netTarget.classList.add('cash-value--exit');
      return;
    }
    netTarget.textContent = formatCashMoney(gross - fee);
    netTarget.classList.toggle('cash-value--exit', fee > gross);
  };

  const refreshPaymentNote = () => {
    if (!boletoNote || !payment) return;
    boletoNote.hidden = payment.options[payment.selectedIndex]?.text.replace(/\s*\(inativa\)$/, '') !== 'Boleto';
  };

  const refreshType = () => {
    const entry = currentType() === 'ENTRY';
    if (feeSection) feeSection.hidden = !entry;
    if (!entry && feeToggle) feeToggle.checked = false;
    if (feeInput) feeInput.disabled = !entry;
    if (feeFields) feeFields.hidden = !entry || !feeToggle?.checked;
    if (category) {
      [...category.options].forEach((option) => {
        const accepted = !option.value || !option.dataset.categoryType
          || option.dataset.categoryType === 'BOTH'
          || option.dataset.categoryType === currentType();
        option.hidden = !accepted;
        option.disabled = !accepted;
      });
      if (category.selectedOptions[0]?.disabled) category.value = '';
    }
    refreshPreview();
  };

  typeInputs.forEach((input) => input.addEventListener('change', refreshType));
  feeToggle?.addEventListener('change', () => {
    if (feeFields) feeFields.hidden = !feeToggle.checked;
    refreshPreview();
  });
  grossInput?.addEventListener('input', refreshPreview);
  feeInput?.addEventListener('input', refreshPreview);
  payment?.addEventListener('change', refreshPaymentNote);
  form.addEventListener('submit', () => {
    const submit = form.querySelector('button[type="submit"]');
    if (submit) {
      submit.disabled = true;
      submit.textContent = 'Salvando…';
    }
  });
  refreshType();
  refreshPaymentNote();
});

document.querySelectorAll('[data-custom-period-toggle]').forEach((container) => {
  const select = container.querySelector('[name="period"]');
  const fields = container.querySelector('[data-custom-period-fields]');
  const refresh = () => { if (fields && select) fields.hidden = select.value !== 'custom'; };
  select?.addEventListener('change', refresh);
  refresh();
});

document.querySelectorAll('[data-row-href]').forEach((row) => {
  const openRow = (event) => {
    if (event.target.closest('a, button, input, select, textarea, form')) return;
    if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
    if (event.type === 'keydown') event.preventDefault();
    window.location.assign(row.dataset.rowHref);
  };
  row.addEventListener('click', openRow);
  row.addEventListener('keydown', openRow);
});

document.querySelectorAll('[data-print-page]').forEach((button) => {
  button.addEventListener('click', () => window.print());
});

document.querySelectorAll('[data-payment-form]').forEach((form) => {
  const method = form.querySelector('[data-payment-method]');
  const cardFields = form.querySelector('[data-card-payment-fields]');
  const cardInputs = [...form.querySelectorAll('[data-card-payment-input]')];
  const cardMode = form.querySelector('[data-card-mode]');
  const installments = form.querySelector('[name="installments"]');
  const refreshCard = () => {
    const card = method?.selectedOptions[0]?.dataset.methodKind === 'CARD';
    if (cardFields) cardFields.hidden = !card;
    cardInputs.forEach((input) => {
      input.disabled = !card;
      input.required = card;
    });
    if (card && cardMode?.value === 'DEBIT' && installments) installments.value = '1';
  };
  method?.addEventListener('change', refreshCard);
  cardMode?.addEventListener('change', refreshCard);
  refreshCard();
});

document.querySelectorAll('[data-fee-rule-form]').forEach((form) => {
  const method = form.querySelector('[data-fee-method]');
  const fields = [...form.querySelectorAll('[data-fee-card-field]')];
  const refresh = () => {
    const card = method?.selectedOptions[0]?.dataset.methodKind === 'CARD';
    fields.forEach((field) => {
      field.hidden = !card;
      field.querySelectorAll('select, input').forEach((input) => { input.disabled = !card; });
    });
  };
  method?.addEventListener('change', refresh);
  refresh();
});

const parseNoteDecimal = (value, maxDecimalPlaces) => {
  const raw = String(value ?? '').trim();
  if (!/^[0-9]+(?:[.,][0-9]+)?$/.test(raw)) return null;
  const [whole = '0', fractionRaw = ''] = raw.replace(',', '.').split('.');
  const fraction = fractionRaw.replace(/0+$/, '');
  if (fraction.length > maxDecimalPlaces) return null;
  return {
    coefficient: BigInt(`${whole}${fraction}` || '0'),
    scale: fraction.length,
  };
};

const notePowerOfTen = (exponent) => 10n ** BigInt(exponent);
const noteRoundPositive = (numerator, denominator) => (
  (numerator + (denominator / 2n)) / denominator
);

const parseNoteMoney = (value) => {
  let raw = String(value ?? '').trim();
  if (raw.startsWith('R$')) raw = raw.slice(2).trim();
  if (raw.includes('R$')) return null;
  raw = raw.replace(/\s/g, '');
  let normalized = raw;
  if (raw.includes(',')) {
    if (!/^(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?$/.test(raw)) return null;
    normalized = raw.replace(/\./g, '').replace(',', '.');
  } else if (/^\d{1,3}(?:\.\d{3})+$/.test(raw)) {
    normalized = raw.replace(/\./g, '');
  } else if (!/^\d+(?:\.\d+)?$/.test(raw)) {
    return null;
  }
  const [wholeRaw = '0', fractionRaw = ''] = normalized.split('.');
  if (wholeRaw.length > 17) return null;
  let cents = (BigInt(wholeRaw || '0') * 100n) + BigInt((fractionRaw + '00').slice(0, 2));
  if ((fractionRaw[2] || '0') >= '5') cents += 1n;
  return cents <= 9223372036854775807n ? cents : null;
};

document.querySelectorAll('[data-note-form]').forEach((form) => {
  const debtPanel = form.querySelector('[data-note-receivables]');
  const debtList = debtPanel?.querySelector('[data-note-receivables-list]');
  const customerSelect = form.querySelector('[data-customer-select]');
  if (debtPanel && debtList && customerSelect) {
    let debtController = null;
    const refreshDebts = async () => {
      debtController?.abort();
      const controller = new AbortController();
      debtController = controller;
      debtList.replaceChildren();
      debtPanel.hidden = true;
      const customerId = customerSelect.value;
      if (!customerId) return;
      const url = new URL(debtPanel.dataset.endpoint, window.location.origin);
      url.searchParams.set('customer_id', customerId);
      try {
        const response = await fetch(url, {
          headers: { Accept: 'application/json' },
          credentials: 'same-origin',
          signal: controller.signal,
        });
        if (!response.ok) return;
        const data = await response.json();
        if (customerSelect.value !== customerId) return;
        data.items.forEach((debt) => {
          const label = document.createElement('label');
          label.className = 'checkbox-row';
          const input = document.createElement('input');
          input.type = 'checkbox';
          input.name = 'receivable_ids';
          input.value = String(debt.id);
          label.append(input, document.createTextNode(` Incluir saldo da Nota #${debt.source_note_id} · ${debt.amount_display}`));
          debtList.append(label);
        });
        debtPanel.hidden = data.items.length === 0;
      } catch (error) {
        if (error.name !== 'AbortError') debtPanel.hidden = true;
      }
    };
    customerSelect.addEventListener('change', refreshDebts);
  }
  const itemsBody = form.querySelector('[data-note-items]');
  const emptyState = form.querySelector('[data-note-items-empty]');
  const itemCount = form.querySelector('[data-note-item-count]');
  const serviceSearch = form.querySelector('[data-note-service-search]');
  const servicePicker = form.querySelector('[data-note-service-picker]');
  const serviceResult = form.querySelector('[data-note-service-result]');
  const pickerMessage = form.querySelector('[data-note-picker-message]');
  const addService = form.querySelector('[data-note-add-service]');
  const deliveryToggle = form.querySelector('[data-note-delivery-toggle]');
  const deliveryFields = form.querySelector('[data-note-delivery-fields]');
  const deliveryAmount = form.querySelector('[data-note-delivery-amount]');
  const discountType = form.querySelector('[data-note-discount-type]');
  const discountField = form.querySelector('[data-note-discount-field]');
  const discountLabel = form.querySelector('[data-note-discount-label]');
  const discountInput = form.querySelector('[data-note-discount-input]');
  const servicesSubtotalTarget = form.querySelector('[data-note-services-subtotal]');
  const discountTarget = form.querySelector('[data-note-discount-total]');
  const deliveryTarget = form.querySelector('[data-note-delivery-total]');
  const totalTarget = form.querySelector('[data-note-total]');
  const summaryMessage = form.querySelector('[data-note-summary-message]');
  const initialPayment = form.querySelector('[data-note-initial-payment]');
  const initialPaymentToggle = initialPayment?.querySelector('[data-note-initial-payment-toggle]');
  const initialPaymentFields = initialPayment?.querySelector('[data-note-initial-payment-fields]');
  const initialPaymentInputs = [...(initialPayment?.querySelectorAll('[data-initial-payment-input]') || [])];
  const initialPaymentAmount = initialPayment?.querySelector('[data-initial-payment-amount]');
  const initialPaymentMethod = initialPayment?.querySelector('[data-payment-method]');
  const initialPaymentTotal = initialPayment?.querySelector('[data-initial-payment-total]');
  const initialPaymentReceived = initialPayment?.querySelector('[data-initial-payment-received]');
  const initialPaymentBalance = initialPayment?.querySelector('[data-initial-payment-balance]');
  const initialPaymentStatus = initialPayment?.querySelector('[data-initial-payment-status]');
  let quantitySequence = itemsBody?.querySelectorAll('[data-note-item]').length || 0;

  const showPickerMessage = (message) => {
    if (!pickerMessage) return;
    pickerMessage.textContent = message;
    pickerMessage.hidden = !message;
  };

  const configureQuantity = (row) => {
    const input = row.querySelector('[data-note-quantity]');
    if (!input) return;
    const behavior = row.dataset.quantityBehavior;
    const decimalPlaces = Math.max(0, Number.parseInt(row.dataset.decimalPlaces || '0', 10) || 0);
    if (behavior === 'FIXED_ONE') {
      input.value = '1';
      input.readOnly = true;
      input.setAttribute('aria-readonly', 'true');
      input.inputMode = 'numeric';
      input.step = '1';
      input.min = '1';
    } else if (behavior === 'INTEGER') {
      input.readOnly = false;
      input.removeAttribute('aria-readonly');
      input.inputMode = 'numeric';
      input.step = '1';
      input.min = '1';
    } else {
      input.readOnly = false;
      input.removeAttribute('aria-readonly');
      input.inputMode = 'decimal';
      input.step = decimalPlaces > 0 ? `0.${'0'.repeat(decimalPlaces - 1)}1` : '1';
      input.min = input.step;
    }
  };

  const updateItemCount = () => {
    const count = itemsBody?.querySelectorAll('[data-note-item]').length || 0;
    if (itemCount) itemCount.textContent = `${count} ${count === 1 ? 'item' : 'itens'}`;
    if (emptyState) emptyState.hidden = count > 0;
  };

  const calculateItemSubtotal = (row) => {
    const input = row.querySelector('[data-note-quantity]');
    const target = row.querySelector('[data-note-item-subtotal]');
    const behavior = row.dataset.quantityBehavior || 'DECIMAL';
    const decimalPlaces = Math.max(0, Number.parseInt(row.dataset.decimalPlaces || '0', 10) || 0);
    const quantity = parseNoteDecimal(input?.value, decimalPlaces);
    const priceCents = BigInt(row.dataset.priceCents || '0');
    let valid = quantity !== null && quantity.coefficient > 0n;
    if (valid && behavior === 'INTEGER' && quantity.scale !== 0) valid = false;
    if (valid && behavior === 'FIXED_ONE') valid = quantity.scale === 0 && quantity.coefficient === 1n;
    if (!valid) {
      if (target) target.textContent = 'Quantidade inválida';
      row.classList.add('note-item--invalid');
      return null;
    }
    const denominator = notePowerOfTen(quantity.scale);
    const subtotal = noteRoundPositive(priceCents * quantity.coefficient, denominator);
    if (target) target.textContent = formatCashMoney(subtotal);
    row.classList.remove('note-item--invalid');
    return subtotal;
  };

  const updateInitialPaymentSummary = (total, previewValid) => {
    if (!initialPayment) return;
    const enabled = Boolean(initialPaymentToggle?.checked);
    const parsed = enabled && initialPaymentAmount?.value.trim()
      ? parseNoteMoney(initialPaymentAmount.value)
      : 0n;
    const received = parsed === null ? 0n : parsed;
    const balance = previewValid && received <= total ? total - received : total;
    if (initialPaymentTotal) initialPaymentTotal.textContent = previewValid ? formatCashMoney(total) : '—';
    if (initialPaymentReceived) initialPaymentReceived.textContent = parsed === null ? 'Valor inválido' : formatCashMoney(received);
    if (initialPaymentBalance) initialPaymentBalance.textContent = previewValid ? formatCashMoney(balance) : '—';
    if (initialPaymentStatus) {
      if (!enabled || received === 0n) initialPaymentStatus.textContent = 'Não pago';
      else if (parsed === null) initialPaymentStatus.textContent = 'Revise o valor';
      else if (received > total) initialPaymentStatus.textContent = 'Acima do saldo';
      else if (received === total) initialPaymentStatus.textContent = 'Pago';
      else initialPaymentStatus.textContent = 'Parcialmente pago';
    }
  };

  const updateSummary = () => {
    const rows = [...(itemsBody?.querySelectorAll('[data-note-item]') || [])];
    let servicesSubtotal = 0n;
    let itemsValid = true;
    rows.forEach((row) => {
      const subtotal = calculateItemSubtotal(row);
      if (subtotal === null) itemsValid = false;
      else servicesSubtotal += subtotal;
    });

    let delivery = 0n;
    let deliveryValid = true;
    if (deliveryToggle?.checked && deliveryAmount?.value.trim()) {
      const parsed = parseNoteMoney(deliveryAmount.value);
      if (parsed === null) deliveryValid = false;
      else delivery = parsed;
    }

    let discount = 0n;
    let discountValid = true;
    if (discountType?.value === 'VALOR') {
      const parsed = parseNoteMoney(discountInput?.value);
      if (parsed === null) discountValid = false;
      else discount = parsed;
    } else if (discountType?.value === 'PERCENTUAL') {
      const percentage = parseNoteDecimal(discountInput?.value, 4);
      if (percentage === null || percentage.coefficient > (100n * notePowerOfTen(percentage.scale))) {
        discountValid = false;
      } else {
        discount = noteRoundPositive(
          servicesSubtotal * percentage.coefficient,
          100n * notePowerOfTen(percentage.scale),
        );
      }
    }

    if (discount > servicesSubtotal) discountValid = false;
    const previewValid = rows.length > 0 && itemsValid && deliveryValid && discountValid;
    const total = previewValid ? servicesSubtotal - discount + delivery : 0n;
    if (servicesSubtotalTarget) servicesSubtotalTarget.textContent = formatCashMoney(servicesSubtotal);
    if (discountTarget) discountTarget.textContent = `− ${formatCashMoney(discountValid ? discount : 0n)}`;
    if (deliveryTarget) deliveryTarget.textContent = formatCashMoney(deliveryValid ? delivery : 0n);
    if (totalTarget) totalTarget.textContent = previewValid ? formatCashMoney(total) : '—';
    if (summaryMessage) {
      if (!rows.length) summaryMessage.textContent = 'Adicione serviços para calcular a prévia.';
      else if (!itemsValid) summaryMessage.textContent = 'Revise as quantidades para calcular o total.';
      else if (!discountValid) summaryMessage.textContent = 'Revise o desconto informado.';
      else if (!deliveryValid) summaryMessage.textContent = 'Revise o valor da entrega.';
      else if (total === 0n) summaryMessage.textContent = 'Total zero: a Nota ficará financeiramente paga, sem gerar recebimento.';
      else summaryMessage.textContent = 'Prévia pronta. Os valores serão confirmados pelo servidor.';
    }
    updateInitialPaymentSummary(total, previewValid);
  };

  const appendText = (parent, tagName, textValue, className = '') => {
    const element = document.createElement(tagName);
    element.textContent = textValue;
    if (className) element.className = className;
    parent.append(element);
    return element;
  };

  const addSelectedService = () => {
    const option = servicePicker?.selectedOptions[0];
    if (!option?.value) {
      showPickerMessage('Selecione um serviço antes de adicionar.');
      servicePicker?.focus();
      return;
    }
    const alreadyAdded = [...itemsBody.querySelectorAll('[data-note-item]')]
      .some((row) => row.dataset.serviceId === option.value);
    if (alreadyAdded) {
      showPickerMessage('Este serviço já foi adicionado. Ajuste a quantidade no item existente.');
      return;
    }

    const row = document.createElement('tr');
    row.dataset.noteItem = '';
    row.dataset.serviceId = option.value;
    row.dataset.priceCents = option.dataset.priceCents || '0';
    row.dataset.quantityBehavior = option.dataset.quantityBehavior || 'DECIMAL';
    row.dataset.decimalPlaces = option.dataset.decimalPlaces || '0';

    const serviceCell = document.createElement('td');
    const itemId = document.createElement('input');
    itemId.type = 'hidden';
    itemId.name = 'item_id';
    itemId.value = '';
    const serviceId = document.createElement('input');
    serviceId.type = 'hidden';
    serviceId.name = 'service_id';
    serviceId.value = option.value;
    serviceCell.append(itemId, serviceId);
    appendText(serviceCell, 'strong', option.dataset.serviceName || option.textContent.trim());
    appendText(serviceCell, 'small', `${option.dataset.serviceCode || 'Sem código'}${option.dataset.category ? ` · ${option.dataset.category}` : ''}`);
    row.append(serviceCell);

    const unitCell = document.createElement('td');
    appendText(unitCell, 'strong', option.dataset.unitSymbol || '—');
    appendText(unitCell, 'small', option.dataset.unitName || 'Unidade');
    row.append(unitCell);

    const quantityCell = document.createElement('td');
    const quantityId = `note-quantity-added-${quantitySequence += 1}`;
    const quantityInput = document.createElement('input');
    quantityInput.id = quantityId;
    quantityInput.className = 'note-quantity-input';
    quantityInput.name = 'quantity';
    quantityInput.value = '1';
    quantityInput.maxLength = 64;
    quantityInput.required = true;
    quantityInput.dataset.noteQuantity = '';
    quantityInput.setAttribute('aria-label', `Quantidade de ${option.dataset.serviceName || 'serviço'}`);
    quantityCell.append(quantityInput);
    const behavior = option.dataset.quantityBehavior;
    const decimalPlaces = option.dataset.decimalPlaces || '0';
    appendText(
      quantityCell,
      'small',
      behavior === 'FIXED_ONE' ? 'Sempre 1' : (behavior === 'INTEGER' ? 'Inteira e positiva' : `Até ${decimalPlaces} casa(s) decimal(is)`),
    );
    row.append(quantityCell);

    const priceCell = document.createElement('td');
    priceCell.className = 'note-money-cell';
    priceCell.append(document.createTextNode(formatCashMoney(BigInt(option.dataset.priceCents || '0'))));
    appendText(priceCell, 'small', option.dataset.unitSymbol === '—' ? 'preço fixo' : `/ ${option.dataset.unitSymbol || '—'}`);
    row.append(priceCell);

    const subtotalCell = document.createElement('td');
    subtotalCell.className = 'note-money-cell';
    const subtotal = appendText(subtotalCell, 'strong', '—');
    subtotal.dataset.noteItemSubtotal = '';
    appendText(subtotalCell, 'small', 'prévia');
    row.append(subtotalCell);

    const actionCell = document.createElement('td');
    const removeButton = appendText(actionCell, 'button', 'Remover', 'link-button note-remove-item');
    removeButton.type = 'button';
    removeButton.dataset.noteRemoveItem = '';
    removeButton.setAttribute('aria-label', `Remover ${option.dataset.serviceName || 'serviço'}`);
    row.append(actionCell);

    itemsBody.append(row);
    configureQuantity(row);
    servicePicker.value = '';
    showPickerMessage('');
    updateItemCount();
    updateSummary();
    quantityInput.focus();
  };

  const filterServices = () => {
    const query = (serviceSearch?.value || '').trim().toLocaleLowerCase('pt-BR');
    let visible = 0;
    [...(servicePicker?.options || [])].forEach((option, index) => {
      if (index === 0) return;
      const searchable = [option.dataset.serviceName, option.dataset.serviceCode, option.dataset.category, option.dataset.unitName, option.dataset.unitSymbol]
        .filter(Boolean).join(' ').toLocaleLowerCase('pt-BR');
      const matches = !query || searchable.includes(query);
      option.hidden = !matches;
      option.disabled = !matches;
      if (matches) visible += 1;
    });
    if (servicePicker?.selectedOptions[0]?.disabled) servicePicker.value = '';
    if (serviceResult) serviceResult.textContent = `${visible} serviço(s) encontrado(s).`;
  };

  const refreshDelivery = () => {
    if (deliveryFields) deliveryFields.hidden = !deliveryToggle?.checked;
    if (deliveryAmount) deliveryAmount.disabled = !deliveryToggle?.checked;
    updateSummary();
  };

  const refreshDiscount = () => {
    const enabled = discountType?.value === 'VALOR' || discountType?.value === 'PERCENTUAL';
    if (discountField) discountField.hidden = !enabled;
    if (discountInput) {
      discountInput.disabled = !enabled;
      discountInput.placeholder = discountType?.value === 'PERCENTUAL' ? '0,00' : '0,00';
    }
    if (discountLabel) discountLabel.textContent = discountType?.value === 'PERCENTUAL' ? 'Percentual *' : 'Valor *';
    updateSummary();
  };

  const refreshInitialPayment = () => {
    if (!initialPayment) return;
    const enabled = Boolean(initialPaymentToggle?.checked);
    if (initialPaymentFields) initialPaymentFields.hidden = !enabled;
    initialPaymentInputs.forEach((input) => { input.disabled = !enabled; });
    ['initial_payment_amount', 'initial_payment_method_id', 'initial_payment_paid_at'].forEach((name) => {
      const input = initialPayment.querySelector(`[name="${name}"]`);
      if (input) input.required = enabled;
    });
    if (enabled) initialPaymentMethod?.dispatchEvent(new Event('change'));
    updateSummary();
  };

  itemsBody?.querySelectorAll('[data-note-item]').forEach(configureQuantity);
  itemsBody?.addEventListener('input', (event) => {
    if (event.target.matches('[data-note-quantity]')) updateSummary();
  });
  itemsBody?.addEventListener('click', (event) => {
    const removeButton = event.target.closest('[data-note-remove-item]');
    if (!removeButton) return;
    removeButton.closest('[data-note-item]')?.remove();
    updateItemCount();
    updateSummary();
  });
  addService?.addEventListener('click', addSelectedService);
  serviceSearch?.addEventListener('input', filterServices);
  serviceSearch?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    const firstMatch = [...(servicePicker?.options || [])].find((option, index) => index > 0 && !option.disabled);
    if (firstMatch) servicePicker.value = firstMatch.value;
    addSelectedService();
  });
  deliveryToggle?.addEventListener('change', refreshDelivery);
  deliveryAmount?.addEventListener('input', updateSummary);
  discountType?.addEventListener('change', refreshDiscount);
  discountInput?.addEventListener('input', updateSummary);
  initialPaymentToggle?.addEventListener('change', refreshInitialPayment);
  initialPaymentAmount?.addEventListener('input', updateSummary);
  form.addEventListener('submit', (event) => {
    if (itemsBody?.querySelector('[data-note-item]')) return;
    event.preventDefault();
    delete form.dataset.submitting;
    form.querySelectorAll('[aria-disabled="true"]').forEach((button) => {
      button.disabled = false;
      button.removeAttribute('aria-disabled');
    });
    showPickerMessage('Adicione ao menos um serviço antes de salvar a Nota.');
    servicePicker?.focus();
  }, { capture: true });

  filterServices();
  refreshDelivery();
  refreshDiscount();
  refreshInitialPayment();
  updateItemCount();
  updateSummary();
});
