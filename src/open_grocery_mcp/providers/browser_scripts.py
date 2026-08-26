"""JavaScript snippets used by the Playwright browser driver."""

from __future__ import annotations

DOM_CART_SCRIPT = r"""
() => {
  const moneyRe = /(\d{1,6}(?:[.,]\d{1,2})?)\s*€/g;
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const number = (value, fallback = 0) => {
    const match = String(value ?? '').replace(',', '.').match(/-?\d+(?:\.\d+)?/);
    return match ? Number(match[0]) : fallback;
  };
  const closestRow = (node) => {
    const explicit = node.closest(
      '.shopping-cart-item,[class*="cart-item" i],[class*="basket-item" i],tr'
    ) || node.closest('li,article');
    if (explicit && explicit !== document.body) return explicit;
    let current = node;
    for (let i = 0; current && i < 8; i += 1, current = current.parentElement) {
      if (!current || current === document.body) break;
      const text = clean(current.innerText);
      const productLinks = current.querySelectorAll('a[href*="product"],a[href*="producto"],a[href*="gadisline.com/"]').length;
      const controls = current.querySelectorAll('input[type="number"],input[class*="quantity" i],button[aria-label*="cantidad" i],button[aria-label*="eliminar" i],button[aria-label*="quitar" i]').length;
      if (text.length > 3 && text.length < 1500 && (productLinks || controls)) return current;
    }
    return node.parentElement || node;
  };
  const seed = Array.from(document.querySelectorAll(
    'input[type="number"], input[name*="quantity" i], input[name*="cantidad" i], input[class*="quantity" i], [data-testid*="quantity" i], button[aria-label*="eliminar" i], button[aria-label*="quitar" i], button[aria-label*="aumentar" i], button[aria-label*="incrementar" i], button[aria-label*="disminuir" i], button[aria-label*="reducir" i]'
  ));
  const rows = [];
  const seen = new Set();
  for (const node of seed) {
    const row = closestRow(node);
    if (!row || seen.has(row)) continue;
    seen.add(row);
    rows.push(row);
  }
  const lines = rows.flatMap((row) => {
    const text = clean(row.innerText);
    const link = row.querySelector('a[href*="product"],a[href*="producto"],a[href]');
    const href = link ? link.href : '';
    const dataset = {...row.dataset, ...(link ? link.dataset : {})};
    const marker = row.innerHTML.match(/basket-product-(\d+)/i);
    const productId = marker
      ? marker[1]
      : String(dataset.productId || dataset.productid || dataset.sku || dataset.id || '').trim();
    const input = row.querySelector('input[type="number"],input[name*="quantity" i],input[name*="cantidad" i],input[class*="quantity" i]');
    let quantity = input ? number(input.value, 1) : 1;
    if (!input) {
      const quantityNode = row.querySelector('[data-testid*="quantity" i],[class*="quantity" i],[class*="cantidad" i]');
      if (quantityNode) quantity = number(quantityNode.textContent, 1);
    }
    const prices = Array.from(text.matchAll(moneyRe)).map((match) => number(match[1]));
    const unitPrice = prices.length ? prices[0] : 0;
    const image = row.querySelector('img[alt]');
    let name = clean((link && link.textContent) || (image && image.alt) || '');
    if (!name) {
      const heading = row.querySelector('h1,h2,h3,h4,[class*="name" i],[class*="title" i]');
      name = clean(heading && heading.textContent);
    }
    if (!name) name = text.replace(moneyRe, '').slice(0, 240).trim();
    if (!name && !productId && !href) return [];
    return [{product_id: productId, name, quantity, unit_price: unitPrice, url: href}];
  });
  const totalNodes = Array.from(document.querySelectorAll(
    '[data-testid*="total" i],[id*="total" i],[class*="total" i],[aria-label*="total" i]'
  ));
  let total = 0;
  for (const node of totalNodes) {
    const values = Array.from(clean(node.textContent).matchAll(moneyRe)).map((match) => number(match[1]));
    if (values.length) total = values[values.length - 1];
  }
  const bodyText = clean(document.body && document.body.innerText);
  return {lines, total, text: bodyText.slice(0, 12000), url: location.href};
}
"""

DOM_OPTIONS_SCRIPT = r"""
(kind) => {
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const nodes = Array.from(document.querySelectorAll('input[type="radio"], option, button, [role="option"]'));
  return nodes.flatMap((node, index) => {
    const labelNode = node.id ? document.querySelector(`label[for="${CSS.escape(node.id)}"]`) : null;
    const row = node.closest('label,li,article,tr,[class*="option" i],[class*="slot" i],[class*="address" i]') || node;
    const text = clean((labelNode && labelNode.textContent) || row.textContent || node.textContent);
    if (!text) return [];
    const timeLike = /\b\d{1,2}:\d{2}\b|\b(?:lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)\b/i.test(text);
    const addressLike = /\b\d{5}\b|\b(?:calle|rúa|rua|avenida|plaza|praza|paseo|camino|estrada)\b/i.test(text);
    if (kind === 'slot' && !timeLike) return [];
    if (kind === 'address' && !addressLike) return [];
    return [{
      id: String(node.value || node.dataset?.id || row.dataset?.id || index),
      label: text.slice(0, 500),
      value: String(node.value || ''),
      checked: Boolean(node.checked || node.selected || node.getAttribute('aria-selected') === 'true'),
      disabled: Boolean(node.disabled || node.getAttribute('aria-disabled') === 'true'),
    }];
  });
}
"""
