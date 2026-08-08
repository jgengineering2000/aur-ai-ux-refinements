/* Annotation system — auto-derives DOC from URL pathname */
const DOC = location.pathname.split('/').pop().replace(/\.[^.]+$/, '');
const API = `/annotations/${DOC}`;
let notes = {}, tmr = null, annMap = new Map();

// ── Utilities ──────────────────────────────────────────────────────────────
const STOP = new Set(['a','an','the','and','or','but','in','on','at','to','for','of','with']);
function headingPrefix(text) {
  return text.replace(/[^a-zA-Z0-9\s]/g,' ').split(/\s+/)
    .filter(w => w && !STOP.has(w.toLowerCase())).slice(0,3)
    .map(w => w[0].toUpperCase()).join('') || 'S';
}
function mkWrap(ph) {
  const w = document.createElement('div'); w.className = 'ann-wrap';
  w.innerHTML = `<textarea placeholder="${ph}"></textarea><div class="ann-status"></div>`;
  return w;
}
function wireInput(id, btn, ta, st) {
  ta.oninput = () => {
    notes[id] = ta.value;
    btn.classList.toggle('has-note', ta.value.length > 0);
    if (!btn.dataset.icon) btn.textContent = ta.value ? '✎ Note' : '✎ Annotate';
    st.textContent = 'Saving…';
    try { localStorage.setItem('ann:' + DOC, JSON.stringify(notes)); } catch(e) {}
    clearTimeout(tmr); tmr = setTimeout(save, 800);
  };
  annMap.set(id, {btn, ta});
}

// ── Phase 0: snapshot heading prefixes and hierarchical keys ──────────────
const allH = [...document.querySelectorAll('h2,h3')];
allH.forEach((h, i) => {
  h.dataset.annPrefix = headingPrefix(h.textContent.trim());
  h.dataset.annHi = i;
  let k = h.textContent.trim();
  if (h.tagName === 'H3') {
    for (let j = i - 1; j >= 0; j--) { if (allH[j].tagName === 'H2') { k = allH[j].textContent.trim() + ' > ' + k; break; } }
  }
  h.dataset.annKey = k;
});

// ── Phase 1: .finding / .card divs ────────────────────────────────────────
document.querySelectorAll('.finding,.card').forEach((c, i) => {
  const titleEl = c.querySelector('.finding-header,.title,h3') || c.firstElementChild || c;
  const id = 'card:' + titleEl.textContent.trim().slice(0, 100), btn = document.createElement('button');
  btn.className = 'ann-btn'; btn.textContent = '✎ Annotate';
  titleEl.appendChild(btn);
  const wrap = mkWrap('Add a note…'); c.appendChild(wrap); c.dataset.annId = id;
  const ta = wrap.querySelector('textarea'), st = wrap.querySelector('.ann-status');
  btn.onclick = e => { e.stopPropagation(); wrap.classList.toggle('show'); if (wrap.classList.contains('show')) ta.focus(); };
  wireInput(id, btn, ta, st);
});

// ── Phase 2: h2 / h3 headings ─────────────────────────────────────────────
allH.forEach((h, i) => {
  if (h.closest('.card')) return;
  const id = h.dataset.annKey, btn = document.createElement('button');
  btn.className = 'ann-btn'; btn.textContent = '✎ Annotate'; h.appendChild(btn);
  const wrap = mkWrap('Section note…'); h.insertAdjacentElement('afterend', wrap); h.dataset.annId = id;
  const ta = wrap.querySelector('textarea'), st = wrap.querySelector('.ann-status');
  btn.onclick = e => { e.stopPropagation(); wrap.classList.toggle('show'); if (wrap.classList.contains('show')) ta.focus(); };
  wireInput(id, btn, ta, st);
});

// ── Phase 3: table section header rows (single <td colspan>) ──────────────
document.querySelectorAll('tr').forEach((row, i) => {
  if (row.cells.length !== 1 || row.cells[0].tagName !== 'TD' || row.cells[0].colSpan < 2) return;
  const id = 'trsec:' + row.cells[0].textContent.trim().slice(0, 100), btn = document.createElement('button');
  btn.className = 'ann-btn'; btn.textContent = '✎ Annotate'; row.cells[0].appendChild(btn);
  const annRow = document.createElement('tr'); annRow.style.display = 'none';
  const annTd = document.createElement('td'); annTd.colSpan = 99; annTd.style.padding = '0';
  const wrap = mkWrap('Table section note…');
  Object.assign(wrap.style, {margin:'0', padding:'6px 10px 8px', borderTop:'none'});
  annTd.appendChild(wrap); annRow.appendChild(annTd); row.insertAdjacentElement('afterend', annRow);
  row.dataset.annId = id;
  const ta = wrap.querySelector('textarea'), st = wrap.querySelector('.ann-status');
  btn.onclick = e => { e.stopPropagation(); const open = annRow.style.display !== 'none'; annRow.style.display = open ? 'none' : ''; if (!open) ta.focus(); };
  wireInput(id, btn, ta, st);
});

// ── Phase 4: item-level — li and data tr rows ──────────────────────────────
const allItems = [...document.querySelectorAll('li,tr')].filter(el =>
  el.tagName === 'TR' ? el.cells.length > 1 && el.cells[0].tagName === 'TD' : true);
const hCounts = new Map();

allItems.forEach(el => {
  let nearestH = null;
  for (let j = allH.length - 1; j >= 0; j--) {
    if (allH[j].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) { nearestH = allH[j]; break; }
  }
  if (!nearestH) return;

  const hk = nearestH.dataset.annKey;
  const n = (hCounts.get(hk) || 0) + 1; hCounts.set(hk, n);
  const displayId = `${nearestH.dataset.annPrefix}${n}`;
  const storeId   = hk + ':item:' + n;

  const idSpan = document.createElement('span');
  idSpan.className = 'ann-id'; idSpan.textContent = displayId; idSpan.title = displayId;
  const btn = document.createElement('button');
  btn.className = 'ann-item-btn'; btn.textContent = '✎';
  btn.title = `Annotate ${displayId}`; btn.dataset.icon = '1';

  if (el.tagName === 'LI') {
    el.prepend(idSpan); el.appendChild(btn);
    const wrap = mkWrap(`Note for ${displayId}…`); el.appendChild(wrap);
    const ta = wrap.querySelector('textarea'), st = wrap.querySelector('.ann-status');
    btn.onclick = e => { e.stopPropagation(); wrap.classList.toggle('show'); if (wrap.classList.contains('show')) ta.focus(); };
    wireInput(storeId, btn, ta, st);
  } else {
    el.cells[0].prepend(idSpan); el.cells[el.cells.length - 1].appendChild(btn);
    const annRow = document.createElement('tr'); annRow.style.display = 'none';
    const annTd = document.createElement('td'); annTd.colSpan = 99; annTd.style.padding = '0';
    const wrap = mkWrap(`Note for ${displayId}…`);
    Object.assign(wrap.style, {margin:'0', padding:'4px 10px 6px', borderTop:'none'});
    annTd.appendChild(wrap); annRow.appendChild(annTd); el.insertAdjacentElement('afterend', annRow);
    const ta = wrap.querySelector('textarea'), st = wrap.querySelector('.ann-status');
    btn.onclick = e => { e.stopPropagation(); const open = annRow.style.display !== 'none'; annRow.style.display = open ? 'none' : ''; if (!open) ta.focus(); };
    wireInput(storeId, btn, ta, st);
  }
});

// ── Save / Load ────────────────────────────────────────────────────────────
async function save() {
  try {
    await fetch(API, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(notes)});
    document.querySelectorAll('.ann-status').forEach(s => { s.textContent = 'Saved ' + new Date().toLocaleTimeString(); s.style.color = ''; });
  } catch(e) {
    document.querySelectorAll('.ann-status').forEach(s => { s.textContent = '⚠ Not saved'; s.style.color = 'var(--rd)'; });
  }
}
async function load() {
  try { notes = await (await fetch(API)).json(); }
  catch(e) { try { const l = localStorage.getItem('ann:' + DOC); if (l) notes = JSON.parse(l); } catch(e2) {} }
  annMap.forEach(({btn, ta}, id) => {
    if (notes[id]) { ta.value = notes[id]; btn.classList.add('has-note'); if (!btn.dataset.icon) btn.textContent = '✎ Note'; }
  });
}
load();
