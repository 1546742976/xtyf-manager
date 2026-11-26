(function () {
  'use strict';

  const byId = id => document.getElementById(id);
  const box = byId('personalPayment');
  const form = byId('personalPaymentForm');
  const owner = byId('personalPaymentOwner');
  const reason = byId('personalPaymentReason');
  const choices = byId('personalPaymentChoices');
  const save = byId('personalPaymentSave');
  const revoke = byId('personalPaymentRevoke');
  const refresh = byId('personalPaymentReload');
  const feedback = byId('personalPaymentError');
  const actions = {confirm:'确认个人报销', correct:'更正报销登记', backfill:'历史报销补录', revoke:'撤销报销登记'};
  const formatCents = cents => Money.format(cents / 100n + '.' + String(cents % 100n).padStart(2, '0'));
  let active;

  async function request(url, options) {
    const response = await BusinessUI.fetch(url, options);
    const body = await response.json();
    if (!response.ok || (body.code != null && body.code !== 0)) throw new Error(body.msg || '无法完成请求');
    return body;
  }

  function actionButtons(record) {
    if (record.record_type !== '出') return '';
    const button = (operation, label) => '<button type="button" class="btn btn-outline-primary btn-sm mt-1" '+
      'data-payment-record="'+record.id+'" data-payment-operation="'+operation+'">'+label+'</button>';
    if (record.invoice_selection && record.review_status === '待审核') return '';
    if (record.invoice_selection && !record.personal_paid_at) return '<div>'+button('confirm','登记报销')+'</div>';
    if (record.review_status === '待审核') return record.self_funded ? '<div>'+button('confirm','核对发票')+'</div>' : '';
    if (record.personal_paid_at) return '<div>'+button('correct','更正或撤销')+'</div>';
    if (!record.self_funded) return '<div>'+button('backfill','历史补录')+'</div>';
    return '<div class="d-flex flex-wrap gap-1">'+button('confirm','登记报销')+button('backfill','历史补录')+'</div>';
  }

  function updateSelected() {
    if (!active?.record) return;
    const selected = [...choices.querySelectorAll('input:checked')];
    const cents = selected.reduce((total, item) => total + BigInt(item.dataset.cents), 0n);
    const target = BigInt(active.record.amount_cents);
    byId('personalPaymentTotal').textContent = '已选 '+selected.length+' 张 · ¥'+formatCents(cents);
    save.disabled = active.saving || !active.generation || active.record.review_status === '待审核'
      || cents !== target || selected.length > 100;
  }

  async function loadChoices(session) {
    if (!session.record) return;
    const requestId = ++session.query;
    session.generation = null;
    choices.replaceChildren();
    feedback.textContent = '';
    updateSelected();
    const member = session.record.applicant_id || owner.value;
    if (!member) {
      choices.textContent = '请选择申请人后查找发票';
      return;
    }
    choices.textContent = '正在查找发票…';
    try {
      const data = await request(session.path+'/personal-invoices?'+new URLSearchParams({applicant_id:member}));
      if (active !== session || session.query !== requestId) return;
      if (data.version !== session.record.version) {
        revoke.disabled = true;
        throw new Error('资金记录已更新，请关闭后重新打开');
      }
      session.generation = data.generation;
      choices.replaceChildren();
      if (!data.data.length) choices.textContent = '暂无可用发票';
      for (const invoice of data.data) {
        if (session.record.invoice_selection && session.operation === 'confirm' && !invoice.selected) continue;
        const line = document.createElement('div');
        line.className = 'd-flex align-items-center gap-2 py-1';
        const label = document.createElement('label');
        label.className = 'd-flex align-items-center gap-2 flex-grow-1';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'form-check-input mt-0';
        checkbox.value = invoice.id;
        checkbox.checked = invoice.selected;
        checkbox.disabled = session.record.invoice_selection && session.operation === 'confirm';
        checkbox.dataset.cents = invoice.amount_cents;
        label.append(checkbox, ['¥'+formatCents(BigInt(invoice.amount_cents)), invoice.date,
          invoice.vendor, invoice.invoice_number || invoice.filename].filter(Boolean).join(' · '));
        const preview = document.createElement('a');
        preview.href = '/invoices/file/'+invoice.id.split('/').map(encodeURIComponent).join('/');
        preview.target = '_blank';
        preview.rel = 'noopener';
        preview.className = 'text-nowrap';
        preview.textContent = '查看原件';
        line.append(label, preview);
        choices.append(line);
      }
      updateSelected();
    } catch (error) {
      if (active !== session || session.query !== requestId) return;
      choices.textContent = '';
      feedback.textContent = error.message;
    }
  }

  function historyText(state) {
    const person = state.applicant_id ? state.applicant+' #'+state.applicant_id : state.applicant || '未绑定申请人';
    if (!state.paid_at) return person+' · 未个人报销';
    const invoices = state.invoices.map(invoice => (invoice.label || '原关联发票')+' ¥'+Money.format(invoice.amount));
    return [person, '已个人报销', ...invoices].join(' · ');
  }

  async function open(button) {
    const operation = button.dataset.paymentOperation;
    const session = {operation, path:API+'/'+button.dataset.paymentRecord, query:0};
    active = session;
    form.reset();
    save.disabled = revoke.disabled = refresh.disabled = true;
    save.hidden = false;
    revoke.hidden = operation !== 'correct';
    byId('personalPaymentHistory').hidden = true;
    byId('personalPaymentHistory').open = false;
    byId('personalPaymentEvents').replaceChildren();
    byId('personalPaymentOwnerField').hidden = true;
    byId('personalPaymentReasonField').hidden = operation === 'confirm';
    reason.required = operation !== 'confirm';
    byId('personalPaymentTitle').textContent = actions[operation];
    save.textContent = operation === 'confirm' ? '确认已付款' : '保存';
    byId('personalPaymentRecord').textContent = '正在读取支出记录…';
    byId('personalPaymentTotal').textContent = '';
    feedback.textContent = '';
    choices.replaceChildren();
    bootstrap.Modal.getOrCreateInstance(box).show(button);
    try {
      const result = await request(session.path);
      if (active !== session) return;
      const record = session.record = result.data;
      byId('personalPaymentRecord').textContent = '记录 #'+record.id+' · '+record.applicant+' · ¥'+Money.format(record.amount);
      if (record.review_status === '待审核') {
        save.hidden = true;
        byId('personalPaymentRecord').append(' · 审核通过后可登记付款');
      }
      revoke.disabled = false;
      refresh.disabled = false;
      const tasks = [request(session.path+'/personal-reimbursement/history').then(result => {
        if (active !== session || !result.data.length) return;
        const events = byId('personalPaymentEvents');
        for (const event of result.data) {
          const entry = document.createElement('li');
          const details = document.createElement('details');
          const title = document.createElement('summary');
          title.textContent = event.occurred_at+' · '+event.actor_name+' · '+actions[event.operation];
          const body = document.createElement('p');
          body.className = 'mb-1';
          body.textContent = [event.reason, '操作前  '+historyText(event.before), '操作后  '+historyText(event.after)].filter(Boolean).join(' / ');
          details.append(title, body);
          entry.append(details);
          events.append(entry);
        }
        byId('personalPaymentHistory').hidden = false;
      })];
      if (!record.applicant_id) {
        tasks.push(request('/invoices/api/owners').then(result => {
          if (active !== session) return;
          const applicant = (record.applicant || '').trim();
          const matches = result.members.filter(member => applicant && member.name === applicant);
          owner.replaceChildren(new Option('请选择成员', ''), ...result.members.map(member =>
            new Option(member.name+' · '+member.username+' (#'+member.id+')', String(member.id))));
          owner.value = matches.length === 1 ? String(matches[0].id) : '';
          byId('personalPaymentOwnerField').hidden = false;
        }));
      }
      await Promise.all(tasks);
      if (active === session) await loadChoices(session);
    } catch (error) {
      if (active === session) feedback.textContent = error.message;
    }
  }

  async function submit(operation) {
    const session = active;
    if (!session?.record || session.saving) return;
    if (operation === 'revoke') {
      if (!reason.reportValidity() || !confirm('撤销这笔报销登记并释放关联发票？')) return;
    } else if (save.disabled) return;

    session.saving = true;
    save.disabled = revoke.disabled = refresh.disabled = true;
    owner.disabled = true;
    feedback.textContent = '';
    try {
      const result = await request(session.path+'/personal-reimbursement', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
          operation, version:session.record.version, generation:session.generation,
          invoice_ids:[...choices.querySelectorAll('input:checked')].map(input => input.value),
          applicant_id:session.record.applicant_id || owner.value, reason:reason.value
        })
      });
      fundsToast(result.msg, result.ledger_sync === 'failed' ? 'error' : 'success');
      session.saving = false;
      bootstrap.Modal.getInstance(box).hide();
      const refreshCatalog = byId('refreshPersonalInvoices');
      if (personalCatalog && refreshCatalog) await refreshPersonalInvoices(refreshCatalog);
      else await reload();
      loadStats();
    } catch (error) {
      if (active === session) feedback.textContent = error.message;
    } finally {
      session.saving = false;
      if (active === session) {
        revoke.disabled = refresh.disabled = owner.disabled = false;
        updateSelected();
      }
    }
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-payment-record]');
    if (button) open(button);
  });
  box.addEventListener('hidden.bs.modal', () => { active = null; owner.disabled = false; });
  box.addEventListener('hide.bs.modal', event => {
    if (active?.saving) event.preventDefault();
  });
  choices.addEventListener('change', updateSelected);
  owner.addEventListener('change', () => { if (active) loadChoices(active); });
  refresh.addEventListener('click', () => { if (active) loadChoices(active); });
  revoke.addEventListener('click', () => submit('revoke'));
  form.addEventListener('submit', event => { event.preventDefault(); if (active) submit(active.operation); });
  window.FundsReimbursement = {actions:actionButtons};
})();
