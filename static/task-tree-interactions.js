"use strict";

// View state belongs to this page, never to the task data or a different server.
let taskTreeViewState = null;
function treeClampZoom(value) {
  return Math.max(.25, Math.min(2, Number(value) || 1));
}
function treeZoomAnchor(scroll, anchor, previousScale, nextScale) {
  return Math.max(0, (scroll + anchor) / previousScale * nextScale - anchor);
}
function treeFilterKey() {
  return JSON.stringify(state.filters);
}
function treeFocusKey(element) {
  if (!element) return null;
  if (element.dataset.edgeKey) return {kind: "edge", key: element.dataset.edgeKey};
  if (element.dataset.treeAction) return {kind: "action", key: element.dataset.treeAction};
  if (element.dataset.taskId) return {kind: "task", key: element.dataset.taskId};
  if (element.matches("[data-tree-isolated-toggle]")) return {kind: "isolated"};
  if (element.matches(".dependency-scroll")) return {kind: "viewport"};
  return null;
}
function treeFocusTarget(root, focus) {
  if (!focus) return null;
  const choices = {edge: ".dependency-edge-hit", action: "[data-tree-action]", task: "[data-task-id]"};
  const keys = {edge: "edgeKey", action: "treeAction", task: "taskId"};
  if (choices[focus.kind]) return [...root.querySelectorAll(choices[focus.kind])].find(el => el.dataset[keys[focus.kind]] === focus.key);
  return root.querySelector(focus.kind === "isolated" ? "[data-tree-isolated-toggle]" : ".dependency-scroll");
}
function captureTreeView(target) {
  target._treeController?.capture();
  target._treeController?.dispose();
  target._treeController = null;
}

function mountTreeView(target) {
  const root = target.querySelector(".task-tree-view");
  if (!root) return;
  const filterKey = treeFilterKey();
  const saved = taskTreeViewState?.filterKey === filterKey ? taskTreeViewState : null;
  const view = saved || {filterKey, scale: 1, left: 0, top: 0, isolatedOpen: false, pinned: null, focus: null};
  const savedPin = saved?.pinned;
  taskTreeViewState = view;
  const scroll = root.querySelector(".dependency-scroll");
  const graph = root.querySelector(".dependency-graph");
  const spacer = root.querySelector(".dependency-spacer");
  const isolated = root.querySelector(".tree-isolated");
  const tooltip = root.querySelector("[data-tree-tooltip]");
  const output = root.querySelector("[data-tree-zoom]");
  const listeners = [], pointers = new Map();
  let disposed = false, hideTimer, resizeFrame, activeEdge = null, pinned = false;
  let gesture = null, suppressClickUntil = 0, observer;
  const listen = (el, type, handler, options) => {
    if (!el) return;
    el.addEventListener(type, handler, options);
    listeners.push(() => el.removeEventListener(type, handler, options));
  };
  const edgeByKey = key => [...root.querySelectorAll(".dependency-edge-hit")].find(el => el.dataset.edgeKey === key);
  const clearHighlights = () => root.querySelectorAll(".is-highlighted").forEach(el => el.classList.remove("is-highlighted"));
  function hideTooltip() {
    clearTimeout(hideTimer);
    activeEdge = null;
    pinned = false;
    view.pinned = null;
    if (tooltip) tooltip.hidden = true;
    clearHighlights();
  }
  function positionTooltip(point) {
    if (!activeEdge || !tooltip) return;
    const frame = root.querySelector(".dependency-viewport").getBoundingClientRect();
    const left = Math.max(8, frame.left + 8), right = Math.min(window.innerWidth - 8, frame.right - 8);
    const top = Math.max(8, frame.top + 8), bottom = Math.min(window.innerHeight - 8, frame.bottom - 8);
    if (right <= left || bottom <= top) { tooltip.hidden = true; return; }
    tooltip.hidden = false;
    tooltip.style.maxWidth = Math.max(100, Math.min(360, right - left)) + "px";
    if (!point) {
      const mid = activeEdge.getPointAtLength(activeEdge.getTotalLength() / 2);
      const matrix = activeEdge.getScreenCTM();
      point = matrix ? {x: matrix.a * mid.x + matrix.c * mid.y + matrix.e,
        y: matrix.b * mid.x + matrix.d * mid.y + matrix.f} : {x: left, y: top};
    }
    const box = tooltip.getBoundingClientRect();
    const x = Math.max(left, Math.min(right - box.width, point.x + 12));
    let y = point.y + 14;
    if (y + box.height > bottom) y = point.y - box.height - 12;
    tooltip.style.left = x + "px";
    tooltip.style.top = Math.max(top, Math.min(bottom - box.height, y)) + "px";
  }
  function showTooltip(edge, pin = false, point) {
    if (!edge || !tooltip) return;
    clearTimeout(hideTimer);
    if (pinned && activeEdge !== edge && !pin) return;
    activeEdge = edge;
    pinned = pin || (pinned && activeEdge === edge);
    view.pinned = pinned ? edge.dataset.edgeKey : null;
    tooltip.textContent = edge.dataset.edgeLabel;
    tooltip.hidden = false;
    clearHighlights();
    const key = edge.dataset.edgeKey;
    root.querySelectorAll(".dependency-edge").forEach(el => el.classList.toggle("is-highlighted", el.dataset.edgeKey === key));
    root.querySelectorAll(".dependency-node").forEach(el => el.classList.toggle("is-highlighted",
      [edge.dataset.edgeFrom, edge.dataset.edgeTo].includes(el.dataset.nodeId)));
    positionTooltip(point);
  }
  function delayHide() {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      if (!pinned && document.activeElement !== activeEdge && !tooltip?.matches(":hover")) hideTooltip();
    }, 160);
  }
  function updateScale(next, anchor) {
    if (!graph || !scroll) return;
    const previous = view.scale;
    next = treeClampZoom(next);
    anchor = anchor || {x: scroll.clientWidth / 2, y: scroll.clientHeight / 2};
    const left = treeZoomAnchor(scroll.scrollLeft, anchor.x, previous, next);
    const top = treeZoomAnchor(scroll.scrollTop, anchor.y, previous, next);
    view.scale = next;
    graph.style.transform = `scale(${next})`;
    spacer.style.width = Number(graph.dataset.graphWidth) * next + "px";
    spacer.style.height = Number(graph.dataset.graphHeight) * next + "px";
    // The visible stroke stays thin; only the invisible hit area grows at low zoom.
    root.querySelectorAll(".dependency-edge-hit").forEach(edge => edge.style.strokeWidth = 24 / next + "px");
    if (output) output.textContent = Math.round(next * 100) + "%";
    root.querySelector('[data-tree-action="out"]').disabled = next <= .25;
    root.querySelector('[data-tree-action="in"]').disabled = next >= 2;
    scroll.scrollLeft = left;
    scroll.scrollTop = top;
    if (pinned) positionTooltip(); else hideTooltip();
  }
  function fit() {
    if (!graph) return;
    updateScale(Math.min(1, (scroll.clientWidth - 2) / Number(graph.dataset.graphWidth)), {x: 0, y: 0});
    scroll.scrollLeft = scroll.scrollTop = 0;
  }
  if (isolated) isolated.open = view.isolatedOpen;
  if (graph) {
    const scale = view.scale;
    view.scale = 1;
    updateScale(saved ? scale : Math.min(1, (scroll.clientWidth - 2) / Number(graph.dataset.graphWidth)), {x: 0, y: 0});
    scroll.scrollLeft = saved ? view.left : 0;
    scroll.scrollTop = saved ? view.top : 0;
  }
  listen(isolated, "toggle", () => { view.isolatedOpen = isolated.open; });
  listen(root, "click", event => {
    if (Date.now() < suppressClickUntil) { event.preventDefault(); event.stopPropagation(); return; }
    const action = event.target.closest("[data-tree-action]");
    if (action) {
      if (action.dataset.treeAction === "fit") fit();
      else updateScale(action.dataset.treeAction === "reset" ? 1 : view.scale + (action.dataset.treeAction === "in" ? .1 : -.1));
      return;
    }
    const edge = event.target.closest(".dependency-edge-hit");
    if (edge) {
      if (activeEdge === edge && pinned) hideTooltip();
      else showTooltip(edge, true);
    } else if (!event.target.closest("[data-tree-tooltip]")) hideTooltip();
  }, true);
  listen(root, "pointerover", event => {
    const edge = event.target.closest(".dependency-edge-hit");
    if (edge && !gesture && event.pointerType !== "touch") showTooltip(edge, false, {x: event.clientX, y: event.clientY});
    if (event.target.closest("[data-tree-tooltip]")) clearTimeout(hideTimer);
  });
  listen(root, "pointerout", event => {
    if (event.target.closest(".dependency-edge-hit,[data-tree-tooltip]") &&
        !event.relatedTarget?.closest?.(".dependency-edge-hit,[data-tree-tooltip]")) delayHide();
  });
  listen(root, "focusin", event => {
    const edge = event.target.closest(".dependency-edge-hit");
    if (edge) showTooltip(edge);
  });
  listen(root, "focusout", event => {
    if (event.target.closest(".dependency-edge-hit")) delayHide();
  });
  listen(root, "keydown", event => {
    if (event.key === "Escape" && activeEdge) { event.preventDefault(); hideTooltip(); return; }
    const edge = event.target.closest(".dependency-edge-hit");
    if (edge && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      if (pinned && activeEdge === edge) hideTooltip(); else showTooltip(edge, true);
    }
  });
  listen(document, "keydown", event => {
    if (event.key === "Escape" && activeEdge) hideTooltip();
  });
  listen(document, "click", event => {
    if (activeEdge && !root.contains(event.target) && !event.target.closest("button,a,input,select,textarea,[role=button],[role=dialog]")) hideTooltip();
  });
  listen(scroll, "wheel", event => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const rect = scroll.getBoundingClientRect();
    updateScale(view.scale * Math.exp(-event.deltaY * .002), {x: event.clientX - rect.left, y: event.clientY - rect.top});
  }, {passive: false});

  function point(event) { return {x: event.clientX, y: event.clientY}; }
  function pinchStart() {
    const [a, b] = [...pointers.values()];
    const rect = scroll.getBoundingClientRect();
    gesture = {type: "pinch", distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), scale: view.scale,
      x: (scroll.scrollLeft + (a.x + b.x) / 2 - rect.left) / view.scale,
      y: (scroll.scrollTop + (a.y + b.y) / 2 - rect.top) / view.scale};
    suppressClickUntil = Date.now() + 500;
    hideTooltip();
  }
  listen(scroll, "pointerdown", event => {
    if (event.button !== 0) return;
    if (event.pointerType !== "touch" && event.target.closest("[data-task-id],.dependency-edge-hit")) return;
    pointers.set(event.pointerId, point(event));
    if (pointers.size >= 2) {
      pinchStart();
      for (const id of pointers.keys()) scroll.setPointerCapture(id);
    } else {
      gesture = {type: "pan", start: point(event), left: scroll.scrollLeft, top: scroll.scrollTop,
        moved: false, canPan: !event.target.closest("[data-task-id]")};
    }
  });
  listen(scroll, "pointermove", event => {
    if (!pointers.has(event.pointerId) || !gesture) return;
    pointers.set(event.pointerId, point(event));
    if (pointers.size >= 2) {
      if (gesture.type !== "pinch") pinchStart();
      const [a, b] = [...pointers.values()], rect = scroll.getBoundingClientRect();
      const next = treeClampZoom(gesture.scale * Math.hypot(a.x - b.x, a.y - b.y) / gesture.distance);
      updateScale(next, {x: 0, y: 0});
      scroll.scrollLeft = gesture.x * next - ((a.x + b.x) / 2 - rect.left);
      scroll.scrollTop = gesture.y * next - ((a.y + b.y) / 2 - rect.top);
      suppressClickUntil = Date.now() + 500;
    } else if (gesture.type === "pan" && gesture.canPan) {
      const dx = event.clientX - gesture.start.x, dy = event.clientY - gesture.start.y;
      if (!gesture.moved && Math.hypot(dx, dy) < 5) return;
      gesture.moved = true;
      scroll.setPointerCapture(event.pointerId);
      scroll.classList.add("is-panning");
      scroll.scrollLeft = gesture.left - dx;
      scroll.scrollTop = gesture.top - dy;
      suppressClickUntil = Date.now() + 500;
      hideTooltip();
    }
  });
  function endPointer(event) {
    if (!pointers.has(event.pointerId)) return;
    pointers.delete(event.pointerId);
    if (scroll.hasPointerCapture(event.pointerId)) scroll.releasePointerCapture(event.pointerId);
    if (gesture?.type === "pinch" || gesture?.moved) suppressClickUntil = Date.now() + 500;
    if (pointers.size === 1) {
      gesture = {type: "pan", start: [...pointers.values()][0], left: scroll.scrollLeft,
        top: scroll.scrollTop, moved: true, canPan: true};
    } else gesture = null;
    scroll.classList.remove("is-panning");
  }
  listen(window, "pointerup", endPointer);
  listen(window, "pointercancel", endPointer);
  const movedViewport = () => {
    if (pinned || (activeEdge && document.activeElement === activeEdge)) positionTooltip();
    else hideTooltip();
  };
  listen(scroll, "scroll", movedViewport, {passive: true});
  listen(window, "scroll", movedViewport, {passive: true, capture: true});
  // A resize repacks whole relationship blocks. It does not reset user zoom.
  let width = Math.round(target.clientWidth);
  if (typeof ResizeObserver !== "undefined") {
    observer = new ResizeObserver(() => {
      const nextWidth = Math.round(target.clientWidth);
      if (disposed || !root.isConnected || state.view !== "tree" || nextWidth === width) return;
      width = nextWidth;
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => { if (!disposed && !gesture) renderBoard(); });
    });
    observer.observe(target);
  }
  target._treeController = {
    capture() {
      view.left = scroll?.scrollLeft || 0;
      view.top = scroll?.scrollTop || 0;
      view.isolatedOpen = !!isolated?.open;
      view.pinned = pinned ? activeEdge?.dataset.edgeKey : null;
      view.focus = root.contains(document.activeElement) ? treeFocusKey(document.activeElement) : null;
    },
    dispose() {
      disposed = true;
      clearTimeout(hideTimer);
      cancelAnimationFrame(resizeFrame);
      observer?.disconnect();
      listeners.forEach(remove => remove());
    },
  };
  if (savedPin && edgeByKey(savedPin)) showTooltip(edgeByKey(savedPin), true);
  if (saved?.focus) {
    let focus = treeFocusTarget(root, saved.focus);
    const details = focus?.closest("details");
    if (details && !details.open) focus = details.querySelector("summary");
    focus ||= scroll || root.querySelector("[data-tree-isolated-toggle]");
    if (focus) focus.focus({preventScroll: true});
    else { root.tabIndex = -1; root.focus({preventScroll: true}); }
  }
}
