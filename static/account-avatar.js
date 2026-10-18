"use strict";

// Crop geometry uses the browser's EXIF-oriented image dimensions, just like the server.
const AvatarCrop = (() => {
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  function normalize(state) {
    const size = Math.min(state.width, state.height) / state.scale;
    return {...state, cx:clamp(state.cx, size / 2, state.width - size / 2),
      cy:clamp(state.cy, size / 2, state.height - size / 2)};
  }
  function initial(width, height) {
    if (![width, height].every(value => Number.isInteger(value) && value > 0)) {
      throw new Error("图片尺寸无效");
    }
    return {width, height, cx:width / 2, cy:height / 2, scale:1};
  }
  function zoom(state, value) {
    if (!Number.isFinite(value)) return state;
    return normalize({...state, scale:clamp(value, 1, 4)});
  }
  // Positive offsets move the crop in source pixels; dragging the image uses the inverse.
  function move(state, dx, dy) {
    if (!Number.isFinite(dx) || !Number.isFinite(dy)) return state;
    return normalize({...state, cx:state.cx + dx, cy:state.cy + dy});
  }
  function rectangle(state) {
    const size = Math.max(1, Math.min(state.width, state.height,
      Math.round(Math.min(state.width, state.height) / state.scale)));
    return {x:clamp(Math.round(state.cx - size / 2), 0, state.width - size),
      y:clamp(Math.round(state.cy - size / 2), 0, state.height - size), size};
  }
  return {initial, zoom, move, rectangle};
})();
if (typeof module !== "undefined" && module.exports) module.exports = AvatarCrop;
if (typeof window !== "undefined") window.AvatarCrop = AvatarCrop;

if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", () => {
  const editor = document.getElementById("avatar-editor");
  if (!editor || typeof window.XTYF?.invoke === "function") return;
  editor.hidden = false;
  const $ = id => document.getElementById(id);
  const fileInput = $("avatar-file"), dialog = $("avatar-crop-dialog");
  const canvas = $("avatar-crop-canvas"), preview = $("avatar-preview");
  const zoomInput = $("avatar-zoom"), save = $("avatar-save"), remove = $("avatar-remove");
  const resume = $("avatar-resume"), result = $("avatar-result"), cropResult = $("avatar-crop-result");
  let draft = null, busy = false, pointer = null, returnFocus = null;
  let currentUrl = document.querySelector("[data-account-avatar]")?.dataset.avatarUrl || null;

  function message(host, text, error = false) {
    host.textContent = text;
    host.classList.toggle("error", error);
  }
  function setBusy(value) {
    busy = value;
    [...editor.querySelectorAll("button,input"), ...dialog.querySelectorAll("button,input")]
      .forEach(control => { control.disabled = value; });
    canvas.setAttribute("aria-disabled", String(value));
    dialog.setAttribute("aria-busy", String(value));
    editor.setAttribute("aria-busy", String(value));
    remove.disabled = value || !currentUrl;
  }
  function draw() {
    if (!draft) return;
    const crop = AvatarCrop.rectangle(draft.state);
    for (const target of [canvas, preview]) {
      const context = target.getContext("2d");
      context.clearRect(0, 0, target.width, target.height);
      context.drawImage(draft.image, crop.x, crop.y, crop.size, crop.size, 0, 0, target.width, target.height);
    }
    zoomInput.value = String(draft.state.scale);
    const scaleText = draft.state.scale.toFixed(2) + " 倍";
    $("avatar-zoom-value").textContent = scaleText;
    zoomInput.setAttribute("aria-valuetext", scaleText);
  }
  function open() {
    if (!draft || busy) return;
    // File decoding disables the picker button and moves focus to the body.
    // Keep the external opener captured before that, including when reselecting inside the dialog.
    if (!returnFocus) returnFocus = $("avatar-change");
    draw();
    if (!dialog.open) dialog.showModal();
    canvas.focus();
  }
  function close() {
    if (busy) return;
    dialog.close();
    pointer = null;
    resume.hidden = !draft;
    if (draft) message(result, "尚未保存，裁剪草稿已保留；可继续调整头像。");
    const target = returnFocus?.getClientRects().length ? returnFocus : $("avatar-change");
    target?.focus({preventScroll:true});
  }
  function clearDraft() {
    if (draft) URL.revokeObjectURL(draft.url);
    draft = null;
    fileInput.value = "";
    resume.hidden = true;
  }
  async function readFile(file) {
    if (!file) return;
    const feedback = dialog.open ? cropResult : result;
    if (file.size > 10 * 1024 * 1024) {
      message(feedback, "图片不能超过 10 MiB，请选择较小的图片。", true); return;
    }
    if (!file.size || (file.type && !["image/jpeg", "image/png", "image/webp"].includes(file.type))) {
      message(feedback, "请选择 JPG、PNG 或 WebP 静态图片。", true); return;
    }
    setBusy(true);
    message(feedback, "正在读取图片…");
    const url = URL.createObjectURL(file);
    try {
      const image = await new Promise((resolve, reject) => {
        const source = new Image();
        source.onload = () => resolve(source);
        source.onerror = () => reject(new Error("无法读取这张图片，请重新选择 JPG、PNG 或 WebP 图片。"));
        source.src = url;
      });
      const width = image.naturalWidth, height = image.naturalHeight;
      if (width > 10000 || height > 10000 || width * height > 20000000) {
        throw new Error("图片尺寸过大：单边不能超过 10000 像素，总像素不能超过 2000 万。");
      }
      const state = AvatarCrop.initial(width, height);
      if (draft) URL.revokeObjectURL(draft.url);
      draft = {file, image, url, state};
      resume.hidden = false;
      message(result, "调整后点击保存头像，当前头像暂不改变。");
      message(cropResult, "");
      save.textContent = "保存头像";
    } catch (error) {
      URL.revokeObjectURL(url);
      message(feedback, error.message || "读取失败，请重新选择图片。", true);
      return;
    } finally { setBusy(false); }
    open();
  }
  function choose() {
    if (busy) return;
    if (!dialog.open) returnFocus = $("avatar-change");
    // Keep the File in the draft, but allow selecting the same file again.
    fileInput.value = "";
    fileInput.click();
  }
  $("avatar-change").addEventListener("click", choose);
  $("avatar-reselect").addEventListener("click", choose);
  fileInput.addEventListener("change", () => {
    if (!busy) readFile(fileInput.files[0]);
  });
  resume.addEventListener("click", () => { returnFocus = resume; open(); });
  $("avatar-cancel").addEventListener("click", close);
  dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });

  zoomInput.addEventListener("input", () => {
    if (!draft || busy) return;
    draft.state = AvatarCrop.zoom(draft.state, Number(zoomInput.value));
    draw();
  });
  function shiftImage(dx, dy) {
    if (!draft || busy) return;
    draft.state = AvatarCrop.move(draft.state, dx, dy);
    draw();
  }
  const directions = {left:[1,0], right:[-1,0], up:[0,1], down:[0,-1]};
  dialog.querySelectorAll("[data-avatar-move]").forEach(button => {
    button.addEventListener("click", () => {
      if (!draft || busy) return;
      const [dx, dy] = directions[button.dataset.avatarMove];
      const step = Math.max(1, AvatarCrop.rectangle(draft.state).size / 25);
      shiftImage(dx * step, dy * step);
    });
  });
  canvas.addEventListener("keydown", event => {
    const direction = directions[event.key.replace("Arrow", "").toLowerCase()];
    if (!direction || !draft || busy) return;
    event.preventDefault();
    const step = Math.max(1, AvatarCrop.rectangle(draft.state).size / 100) * (event.shiftKey ? 10 : 1);
    shiftImage(direction[0] * step, direction[1] * step);
  });
  canvas.addEventListener("pointerdown", event => {
    if (busy || !draft || event.button !== 0 || pointer) return;
    event.preventDefault();
    canvas.focus();
    pointer = {id:event.pointerId, x:event.clientX, y:event.clientY};
    // A detached/cancelled pointer may already have lost capture.
    try { canvas.setPointerCapture(event.pointerId); } catch (_) { /* Pointer events still work inside the canvas. */ }
  });
  canvas.addEventListener("pointermove", event => {
    if (!pointer || pointer.id !== event.pointerId || !draft || busy) return;
    const bounds = canvas.getBoundingClientRect();
    if (!bounds.width) return;
    const ratio = AvatarCrop.rectangle(draft.state).size / bounds.width;
    shiftImage((pointer.x - event.clientX) * ratio, (pointer.y - event.clientY) * ratio);
    pointer.x = event.clientX;
    pointer.y = event.clientY;
  });
  const endPointer = event => {
    if (pointer?.id !== event.pointerId) return;
    pointer = null;
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  };
  canvas.addEventListener("pointerup", endPointer);
  canvas.addEventListener("pointercancel", endPointer);
  canvas.addEventListener("lostpointercapture", () => { pointer = null; });

  async function request(options) {
    const response = await BusinessUI.fetch("/api/account/avatar", {credentials:"same-origin", ...options});
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || "头像保存失败，请重试。");
    return payload;
  }
  function applySaved(payload) {
    currentUrl = payload.avatar_url || null;
    window.AvatarDisplay.setUrl(currentUrl);
    remove.disabled = !currentUrl;
  }
  save.addEventListener("click", async () => {
    if (!draft || busy) return;
    const body = new FormData();
    body.append("avatar", draft.file, draft.file.name);
    body.append("crop", JSON.stringify(AvatarCrop.rectangle(draft.state)));
    setBusy(true);
    message(cropResult, "正在保存头像…");
    try {
      const payload = await request({method:"POST", body});
      applySaved(payload);
      clearDraft();
      setBusy(false);
      close();
      message(result, "头像已更新。");
    } catch (error) {
      message(cropResult, (error.message || "保存失败") + " 当前头像未在页面替换，裁剪草稿已保留。", true);
      save.textContent = "重试保存头像";
    } finally { setBusy(false); }
  });
  remove.addEventListener("click", async () => {
    if (busy || !currentUrl) return;
    setBusy(true);
    message(result, "正在恢复默认头像…");
    try {
      const payload = await request({method:"DELETE"});
      applySaved(payload);
      message(result, "已恢复姓名首字默认头像。");
    } catch (error) {
      message(result, (error.message || "恢复失败") + " 当前头像已保留，可重试。", true);
    } finally { setBusy(false); }
  });
  window.addEventListener("pagehide", () => { if (draft) URL.revokeObjectURL(draft.url); });
});
