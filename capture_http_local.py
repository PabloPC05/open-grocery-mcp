#!/usr/bin/env python3
"""Repository-aware launcher for the local HTTP contract capture.

Besides making the ``src`` layout importable, this launcher improves the
browser overlay so that the active capture phase survives page navigations and
is shown persistently. The actual capture implementation remains in
``tools/capture_http_local.py``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPOSITORY_ROOT = Path(__file__).resolve().parent
for _path in (_REPOSITORY_ROOT / "src", _REPOSITORY_ROOT):
    _value = str(_path)
    if _value not in sys.path:
        sys.path.insert(0, _value)

import tools.capture_http_local as capture_mod


# Playwright bindings survive document navigation, but the HTML overlay is
# recreated for every document. Expose a read endpoint so each new overlay can
# restore the real Python-side phase instead of misleadingly showing "login".
def _capture_state(capture: Any) -> dict[str, Any]:
    with capture._lock:
        labels = dict(capture_mod.PHASES)
        return {
            "phase": capture.phase,
            "label": labels.get(capture.phase, capture.phase),
            "events": len(capture.events),
            "blocked": len(capture.blocked),
        }


_original_set_phase = capture_mod.LocalCapture.set_phase


def _set_phase_and_return_state(capture: Any, phase: str) -> dict[str, Any]:
    _original_set_phase(capture, phase)
    return _capture_state(capture)


_original_setup_page = capture_mod.LocalCapture._setup_page


def _setup_page_with_state(capture: Any, page: Any) -> None:
    _original_setup_page(capture, page)
    try:
        page.expose_function(
            "__openGroceryGetCaptureState",
            lambda: _capture_state(capture),
        )
    except Exception:
        # The binding may already exist if Playwright reuses the same page.
        pass


capture_mod.LocalCapture.set_phase = _set_phase_and_return_state
capture_mod.LocalCapture._setup_page = _setup_page_with_state


capture_mod.OVERLAY = r"""
(() => {
  const phases = __OPEN_GROCERY_PHASES__;
  const phaseLabels = new Map(phases);
  const hostId = '__open_grocery_capture';
  const positionKey = '__open_grocery_capture_position__';
  const phaseKey = '__open_grocery_capture_phase__';

  const important = (element, property, value) => {
    element.style.setProperty(property, value, 'important');
  };

  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

  const install = () => {
    if (document.getElementById(hostId)) return;

    const host = document.createElement('div');
    host.id = hostId;
    important(host, 'all', 'initial');
    important(host, 'position', 'fixed');
    important(host, 'top', '12px');
    important(host, 'right', '12px');
    important(host, 'left', 'auto');
    important(host, 'bottom', 'auto');
    important(host, 'z-index', '2147483647');
    important(host, 'width', 'min(410px, calc(100vw - 24px))');
    important(host, 'max-height', 'calc(100vh - 24px)');
    important(host, 'overflow', 'auto');
    important(host, 'box-sizing', 'border-box');
    important(host, 'isolation', 'isolate');
    important(host, 'pointer-events', 'auto');
    important(host, 'transform', 'none');

    try {
      const saved = JSON.parse(localStorage.getItem(positionKey) || 'null');
      if (saved && Number.isFinite(saved.left) && Number.isFinite(saved.top)) {
        important(host, 'left', `${saved.left}px`);
        important(host, 'top', `${saved.top}px`);
        important(host, 'right', 'auto');
      }
    } catch {}

    const shadow = host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = `
      *, *::before, *::after { box-sizing: border-box; }
      .panel {
        width: 100%; padding: 14px; background: #111; color: #fff;
        border-radius: 12px; box-shadow: 0 8px 32px #0008;
        font: 13px/1.35 system-ui, sans-serif;
      }
      .header {
        display: flex; align-items: center; gap: 8px; margin-bottom: 8px;
        cursor: move; user-select: none;
      }
      .title { flex: 1; font-weight: 700; }
      .hint { font-size: 11px; color: #bbb; }
      .collapse {
        border: 0; border-radius: 7px; padding: 4px 8px; cursor: pointer;
        background: #333; color: #fff; font: inherit;
      }
      .status {
        margin: 0 0 10px; padding: 9px 10px; border-radius: 8px;
        background: #183c2d; border: 1px solid #3a8f69; color: #d9ffed;
        font-weight: 650;
      }
      .status.syncing {
        background: #343434; border-color: #666; color: #eee;
      }
      .note { margin-bottom: 10px; color: #ddd; }
      select {
        display: block; width: 100%; margin: 0 0 8px; padding: 9px;
        border: 1px solid #777; border-radius: 8px; background: #fff;
        color: #111; font: inherit;
      }
      .actions { display: flex; gap: 8px; }
      .actions button {
        flex: 1; padding: 9px; border: 0; border-radius: 8px;
        cursor: pointer; font: inherit;
      }
      .actions button:disabled { cursor: default; opacity: .65; }
      .mark { background: #f2f2f2; color: #111; }
      .mark.active { background: #37b37e; color: #071f15; font-weight: 700; }
      .finish { background: #2f80ed; color: #fff; }
      .warning { margin-top: 9px; color: #ffcc80; font-size: 12px; }
      .hidden { display: none; }
    `;

    const panel = document.createElement('section');
    panel.className = 'panel';

    const header = document.createElement('div');
    header.className = 'header';
    const titleWrap = document.createElement('div');
    titleWrap.style.flex = '1';
    const title = document.createElement('div');
    title.className = 'title';
    title.textContent = 'Open Grocery · captura HTTP local';
    const hint = document.createElement('div');
    hint.className = 'hint';
    hint.textContent = 'Arrastra esta barra para mover el panel';
    titleWrap.append(title, hint);
    const collapse = document.createElement('button');
    collapse.className = 'collapse';
    collapse.type = 'button';
    collapse.textContent = '−';
    collapse.title = 'Contraer o desplegar';
    header.append(titleWrap, collapse);

    const body = document.createElement('div');
    const status = document.createElement('div');
    status.className = 'status syncing';
    status.textContent = 'Sincronizando la fase activa…';

    const note = document.createElement('div');
    note.className = 'note';
    note.textContent =
      'Selecciona una fase y actívala ANTES de realizar esa acción. ' +
      'La fase permanece activa hasta que marques otra.';

    const select = document.createElement('select');
    for (const [value, label] of phases) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    }

    let activePhase = null;

    const actions = document.createElement('div');
    actions.className = 'actions';
    const mark = document.createElement('button');
    mark.className = 'mark';
    mark.type = 'button';
    mark.textContent = 'Activar fase';

    const renderState = (state) => {
      if (!state || !state.phase) return;
      activePhase = state.phase;
      select.value = state.phase;
      const label = state.label || phaseLabels.get(state.phase) || state.phase;
      const eventCount = Number.isFinite(state.events) ? state.events : 0;
      status.className = 'status';
      status.textContent = `GRABANDO: ${label} · ${eventCount} eventos capturados`;
      mark.className = 'mark active';
      mark.textContent = 'Fase activa ✓';
      panel.style.outline =
        state.phase === 'order_submit_probe' ? '3px solid #ff4d4f' : 'none';
      try { localStorage.setItem(phaseKey, state.phase); } catch {}
    };

    select.addEventListener('change', () => {
      const selectedIsActive = select.value === activePhase;
      mark.className = selectedIsActive ? 'mark active' : 'mark';
      mark.textContent = selectedIsActive ? 'Fase activa ✓' : 'Activar fase';
    });

    mark.addEventListener('click', async () => {
      mark.disabled = true;
      mark.className = 'mark';
      mark.textContent = 'Activando…';
      try {
        const state = await window.__openGrocerySetCapturePhase(select.value);
        renderState(state || {
          phase: select.value,
          label: phaseLabels.get(select.value),
          events: 0,
        });
      } catch (error) {
        status.className = 'status syncing';
        status.textContent = `No se pudo activar la fase: ${String(error)}`;
        mark.textContent = 'Reintentar';
      } finally {
        mark.disabled = false;
      }
    });

    const finish = document.createElement('button');
    finish.className = 'finish';
    finish.type = 'button';
    finish.textContent = 'Finalizar';
    finish.addEventListener('click', async () => {
      finish.disabled = true;
      finish.textContent = 'Guardando…';
      await window.__openGroceryFinishCapture();
      finish.textContent = 'Captura finalizada';
    });
    actions.append(mark, finish);

    const warning = document.createElement('div');
    warning.className = 'warning';
    warning.textContent =
      'No pegues credenciales en un chat: escríbelas solo en esta ventana.';

    body.append(status, note, select, actions, warning);
    collapse.addEventListener('click', (event) => {
      event.stopPropagation();
      const collapsed = body.classList.toggle('hidden');
      collapse.textContent = collapsed ? '+' : '−';
    });

    let dragging = false;
    let offsetX = 0;
    let offsetY = 0;

    const persistPosition = () => {
      try {
        localStorage.setItem(
          positionKey,
          JSON.stringify({ left: host.offsetLeft, top: host.offsetTop }),
        );
      } catch {}
    };

    const startDrag = (event) => {
      if (event.target.closest('button, select, option, input, textarea, a')) return;
      dragging = true;
      const rect = host.getBoundingClientRect();
      offsetX = event.clientX - rect.left;
      offsetY = event.clientY - rect.top;
      important(host, 'left', `${rect.left}px`);
      important(host, 'top', `${rect.top}px`);
      important(host, 'right', 'auto');
      important(host, 'bottom', 'auto');
      event.preventDefault();
    };

    const onMove = (event) => {
      if (!dragging) return;
      const width = host.offsetWidth;
      const height = host.offsetHeight;
      const left = clamp(
        event.clientX - offsetX,
        8,
        Math.max(8, window.innerWidth - width - 8),
      );
      const top = clamp(
        event.clientY - offsetY,
        8,
        Math.max(8, window.innerHeight - height - 8),
      );
      important(host, 'left', `${left}px`);
      important(host, 'top', `${top}px`);
    };

    const stopDrag = () => {
      if (!dragging) return;
      dragging = false;
      persistPosition();
    };

    header.addEventListener('pointerdown', startDrag);
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', stopDrag);

    panel.append(header, body);
    shadow.append(style, panel);
    (document.body || document.documentElement).appendChild(host);

    const syncState = async (attempt = 0) => {
      try {
        if (typeof window.__openGroceryGetCaptureState === 'function') {
          renderState(await window.__openGroceryGetCaptureState());
          return;
        }
      } catch {}

      if (attempt < 20) {
        setTimeout(() => syncState(attempt + 1), 250);
        return;
      }

      try {
        const savedPhase = localStorage.getItem(phaseKey);
        if (savedPhase && phaseLabels.has(savedPhase)) {
          select.value = savedPhase;
          status.className = 'status syncing';
          status.textContent =
            `Fase visual restaurada: ${phaseLabels.get(savedPhase)}. Pulsa Activar fase para confirmarla.`;
          mark.textContent = 'Confirmar fase';
          return;
        }
      } catch {}

      status.className = 'status syncing';
      status.textContent = 'No se pudo consultar la fase activa. Pulsa Activar fase.';
    };

    syncState();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install, { once: true });
  } else {
    install();
  }
  new MutationObserver(install).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
})();
"""

main = capture_mod.main


if __name__ == "__main__":
    raise SystemExit(main())
