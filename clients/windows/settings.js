const field = document.getElementById('server');
const error = document.getElementById('error');
window.settings.read().then(value => { field.value = value; field.focus(); }).catch(e => { error.textContent = e.message; });
field.addEventListener('blur', () => { error.textContent = field.validity.valid ? '' : '请输入完整的 http:// 或 https:// 地址'; });
document.getElementById('form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = document.querySelector('button');
  button.disabled = true; button.textContent = '正在连接…';
  try { await window.settings.save(field.value); }
  catch (e) { error.textContent = e.message; button.disabled = false; button.textContent = '保存并连接'; }
});
