document.querySelectorAll('[data-sidebar-toggle]').forEach((button) => {
  button.addEventListener('click', () => document.body.classList.toggle('sidebar-open'));
});

const dateTarget = document.querySelector('[data-current-date]');
if (dateTarget) {
  dateTarget.textContent = new Intl.DateTimeFormat('pt-BR', {
    weekday: 'long', day: '2-digit', month: 'long'
  }).format(new Date());
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
