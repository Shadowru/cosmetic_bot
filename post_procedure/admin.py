import json
import os
from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)
PRODUCTS_FILE = os.path.join(os.path.dirname(__file__), "products.json")

HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Post-Procedure — Редактор ссылок</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0d0f14;
    --surface: #161920;
    --surface2: #1e2130;
    --border: #2a2d3e;
    --text: #e2e4f0;
    --muted: #6b7094;
    --accent: #7c6df0;
    --accent-hover: #9080f5;
    --success: #2ecc71;
    --warning: #f39c12;
    --empty: #e74c3c;
    --wb: #c4336a;
    --ozon: #005bff;
  }

  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    display: flex;
    height: 100vh;
    overflow: hidden;
  }

  /* Sidebar */
  aside {
    width: 240px;
    min-width: 240px;
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow-y: auto;
  }

  .sidebar-header {
    padding: 20px 16px 12px;
    border-bottom: 1px solid var(--border);
  }

  .sidebar-header h1 {
    font-size: 13px;
    font-weight: 600;
    letter-spacing: .06em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .progress-bar {
    margin-top: 10px;
    height: 4px;
    background: var(--border);
    border-radius: 2px;
    overflow: hidden;
  }

  .progress-fill {
    height: 100%;
    background: var(--accent);
    border-radius: 2px;
    transition: width .4s;
  }

  .progress-label {
    margin-top: 6px;
    font-size: 11px;
    color: var(--muted);
  }

  .nav-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 11px 16px;
    cursor: pointer;
    border-left: 3px solid transparent;
    transition: background .15s, border-color .15s;
    font-size: 13.5px;
  }

  .nav-item:hover { background: var(--surface2); }

  .nav-item.active {
    background: var(--surface2);
    border-left-color: var(--accent);
    color: #fff;
  }

  .nav-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .nav-count {
    margin-left: auto;
    font-size: 11px;
    color: var(--muted);
  }

  /* Main */
  main {
    flex: 1;
    overflow-y: auto;
    padding: 28px 32px;
  }

  .section { display: none; }
  .section.active { display: block; }

  .section-title {
    font-size: 22px;
    font-weight: 700;
    margin-bottom: 6px;
  }

  .section-subtitle {
    font-size: 13px;
    color: var(--muted);
    margin-bottom: 28px;
  }

  .phase-block {
    margin-bottom: 32px;
  }

  .phase-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .phase-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
  }

  .phase-dot.acute { background: var(--empty); }
  .phase-dot.recovery { background: var(--warning); }

  .step-group {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    margin-bottom: 10px;
  }

  .step-header {
    padding: 12px 16px;
    background: var(--surface2);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: baseline;
    gap: 10px;
  }

  .step-name {
    font-size: 13px;
    font-weight: 600;
  }

  .step-note {
    font-size: 12px;
    color: var(--muted);
  }

  .product-row {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr 36px;
    gap: 0;
    align-items: center;
    border-bottom: 1px solid var(--border);
    transition: background .1s;
  }

  .product-row:last-child { border-bottom: none; }
  .product-row:hover { background: rgba(124,109,240,.04); }

  .product-name {
    padding: 10px 16px;
    font-size: 13px;
    color: var(--text);
    border-right: 1px solid var(--border);
  }

  .url-cell {
    padding: 6px 10px;
    border-right: 1px solid var(--border);
    position: relative;
  }

  .url-input {
    width: 100%;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 6px 8px;
    font-size: 12px;
    color: var(--text);
    outline: none;
    transition: border-color .15s, background .15s;
    font-family: 'SF Mono', 'Fira Code', monospace;
  }

  .url-input::placeholder { color: var(--muted); }

  .url-input:focus {
    border-color: var(--accent);
    background: rgba(124,109,240,.07);
  }

  .url-input.filled { color: var(--success); }

  .url-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .05em;
    margin-bottom: 2px;
  }

  .url-label.wb { color: var(--wb); }
  .url-label.ozon { color: var(--ozon); }

  .status-cell {
    padding: 0 10px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--border);
    transition: background .2s;
  }

  .status-dot.partial { background: var(--warning); }
  .status-dot.full { background: var(--success); }

  /* Save button */
  .save-bar {
    position: fixed;
    bottom: 0;
    left: 240px;
    right: 0;
    padding: 14px 32px;
    background: rgba(13,15,20,.9);
    backdrop-filter: blur(10px);
    border-top: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 16px;
    z-index: 100;
  }

  .save-btn {
    padding: 9px 28px;
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: background .15s, transform .1s;
  }

  .save-btn:hover { background: var(--accent-hover); }
  .save-btn:active { transform: scale(.97); }
  .save-btn:disabled { opacity: .5; cursor: default; }

  .save-status {
    font-size: 13px;
    color: var(--muted);
    transition: color .3s;
  }

  .save-status.ok { color: var(--success); }
  .save-status.err { color: var(--empty); }

  .unsaved-badge {
    font-size: 11px;
    background: var(--accent);
    color: #fff;
    padding: 2px 8px;
    border-radius: 20px;
    display: none;
  }

  .unsaved-badge.visible { display: inline-block; }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
</style>
</head>
<body>

<aside>
  <div class="sidebar-header">
    <h1>Ссылки на товары</h1>
    <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
    <div class="progress-label" id="progressLabel">Загрузка...</div>
  </div>
  <nav id="nav"></nav>
</aside>

<main id="main"></main>

<div class="save-bar">
  <button class="save-btn" id="saveBtn" onclick="save()">Сохранить</button>
  <span class="unsaved-badge" id="unsavedBadge">Есть изменения</span>
  <span class="save-status" id="saveStatus"></span>
</div>

<script>
let products = {};
let dirty = false;

async function load() {
  const res = await fetch('/api/products');
  products = await res.json();
  render();
  updateProgress();
}

function render() {
  const nav = document.getElementById('nav');
  const main = document.getElementById('main');
  nav.innerHTML = '';
  main.innerHTML = '';

  const entries = Object.entries(products);

  entries.forEach(([code, proc], idx) => {
    // Nav item
    const item = document.createElement('div');
    item.className = 'nav-item' + (idx === 0 ? ' active' : '');
    item.dataset.code = code;
    item.onclick = () => switchTo(code);

    const dot = document.createElement('div');
    dot.className = 'nav-dot';
    dot.id = `dot-${code}`;

    const label = document.createElement('span');
    label.textContent = proc.name;

    const count = document.createElement('span');
    count.className = 'nav-count';
    count.id = `count-${code}`;

    item.append(dot, label, count);
    nav.appendChild(item);

    // Section
    const section = document.createElement('div');
    section.className = 'section' + (idx === 0 ? ' active' : '');
    section.id = `section-${code}`;

    section.innerHTML = `
      <div class="section-title">${proc.name}</div>
      <div class="section-subtitle">Заполни ссылки WB и Ozon для каждого продукта</div>
    `;

    ['acute', 'recovery'].forEach(phase => {
      if (!proc[phase]) return;
      const phaseData = proc[phase];

      const block = document.createElement('div');
      block.className = 'phase-block';
      block.innerHTML = `
        <div class="phase-label">
          <div class="phase-dot ${phase}"></div>
          ${phaseData.label}
        </div>
      `;

      phaseData.steps.forEach((step, si) => {
        const group = document.createElement('div');
        group.className = 'step-group';
        group.innerHTML = `
          <div class="step-header">
            <span class="step-name">${step.step}</span>
            ${step.note ? `<span class="step-note">— ${step.note}</span>` : ''}
          </div>
        `;

        step.products.forEach((product, pi) => {
          const row = document.createElement('div');
          row.className = 'product-row';

          const wbVal = product.wb_url || '';
          const ozonVal = product.ozon_url || '';

          row.innerHTML = `
            <div class="product-name">${product.name}</div>
            <div class="url-cell">
              <div class="url-label wb">WILDBERRIES</div>
              <input class="url-input ${wbVal ? 'filled' : ''}"
                     type="url"
                     placeholder="https://www.wildberries.ru/catalog/..."
                     value="${escHtml(wbVal)}"
                     data-code="${code}" data-phase="${phase}" data-step="${si}" data-prod="${pi}" data-field="wb_url"
                     oninput="onInput(this)" onpaste="onPaste(this)">
            </div>
            <div class="url-cell">
              <div class="url-label ozon">OZON</div>
              <input class="url-input ${ozonVal ? 'filled' : ''}"
                     type="url"
                     placeholder="https://www.ozon.ru/product/..."
                     value="${escHtml(ozonVal)}"
                     data-code="${code}" data-phase="${phase}" data-step="${si}" data-prod="${pi}" data-field="ozon_url"
                     oninput="onInput(this)" onpaste="onPaste(this)">
            </div>
            <div class="status-cell">
              <div class="status-dot" id="status-${code}-${phase}-${si}-${pi}"></div>
            </div>
          `;
          group.appendChild(row);
          updateRowStatus(code, phase, si, pi, wbVal, ozonVal);
        });

        block.appendChild(group);
      });

      section.appendChild(block);
    });

    main.appendChild(section);
  });

  updateAllCounts();
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function switchTo(code) {
  document.querySelectorAll('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.code === code));
  document.querySelectorAll('.section').forEach(el => el.classList.toggle('active', el.id === `section-${code}`));
}

function onInput(input) {
  const { code, phase, step, prod, field } = input.dataset;
  const val = input.value.trim();
  products[code][phase].steps[step].products[prod][field] = val;
  input.classList.toggle('filled', !!val);

  const wbVal = products[code][phase].steps[step].products[prod].wb_url;
  const ozonVal = products[code][phase].steps[step].products[prod].ozon_url;
  updateRowStatus(code, phase, step, prod, wbVal, ozonVal);
  updateAllCounts();
  markDirty();
}

function onPaste(input) {
  setTimeout(() => onInput(input), 0);
}

function updateRowStatus(code, phase, si, pi, wb, ozon) {
  const dot = document.getElementById(`status-${code}-${phase}-${si}-${pi}`);
  if (!dot) return;
  const filled = (wb ? 1 : 0) + (ozon ? 1 : 0);
  dot.className = 'status-dot' + (filled === 2 ? ' full' : filled === 1 ? ' partial' : '');
}

function countLinks(proc) {
  let total = 0, filled = 0;
  ['acute', 'recovery'].forEach(phase => {
    if (!proc[phase]) return;
    proc[phase].steps.forEach(step => {
      step.products.forEach(p => {
        total += 2;
        if (p.wb_url) filled++;
        if (p.ozon_url) filled++;
      });
    });
  });
  return { total, filled };
}

function updateAllCounts() {
  let grandTotal = 0, grandFilled = 0;
  Object.entries(products).forEach(([code, proc]) => {
    const { total, filled } = countLinks(proc);
    grandTotal += total;
    grandFilled += filled;

    const dot = document.getElementById(`dot-${code}`);
    const count = document.getElementById(`count-${code}`);
    if (dot) {
      const pct = total > 0 ? filled / total : 0;
      dot.style.background = pct === 1 ? 'var(--success)' : pct > 0 ? 'var(--warning)' : 'var(--empty)';
    }
    if (count) count.textContent = `${filled}/${total}`;
  });

  const pct = grandTotal > 0 ? grandFilled / grandTotal : 0;
  document.getElementById('progressFill').style.width = (pct * 100) + '%';
  document.getElementById('progressLabel').textContent = `${grandFilled} из ${grandTotal} ссылок заполнено`;
}

function updateProgress() { updateAllCounts(); }

function markDirty() {
  dirty = true;
  document.getElementById('unsavedBadge').classList.add('visible');
  document.getElementById('saveStatus').textContent = '';
  document.getElementById('saveStatus').className = 'save-status';
}

async function save() {
  const btn = document.getElementById('saveBtn');
  const status = document.getElementById('saveStatus');
  btn.disabled = true;
  btn.textContent = 'Сохраняю...';

  try {
    const res = await fetch('/admin/api/products', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(products),
    });
    if (!res.ok) throw new Error('Server error');
    dirty = false;
    document.getElementById('unsavedBadge').classList.remove('visible');
    status.textContent = '✓ Сохранено';
    status.className = 'save-status ok';
    setTimeout(() => { status.textContent = ''; status.className = 'save-status'; }, 3000);
  } catch (e) {
    status.textContent = '✗ Ошибка сохранения';
    status.className = 'save-status err';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Сохранить';
  }
}

window.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); save(); }
});

window.addEventListener('beforeunload', e => {
  if (dirty) { e.preventDefault(); e.returnValue = ''; }
});

load();
</script>
</body>
</html>
"""


@app.get("/admin/")
@app.get("/admin")
def admin_ui():
    return render_template_string(HTML)


@app.get("/api/products")
def get_products():
    with open(PRODUCTS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    resp = jsonify(data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.post("/admin/api/products")
def save_products():
    data = request.get_json()
    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Открой в браузере: http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
