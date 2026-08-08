// Injects into the .bar of every Claude doc page:
//   LEFT   — "← Manifest" link + muted "Alt+D" label
//   MIDDLE — current document URL (selectable code chip)
//   RIGHT  — document generation timestamp (from filename) + ↻ Refresh button
// Skips entirely on manifest.html itself.
(function () {
  if (window.location.pathname.endsWith('/manifest.html')) return;

  function parseDateFromPath() {
    var filename = window.location.pathname.split('/').pop();
    var m = filename.match(/-(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})(?:\.html)?$/);
    if (!m) return '';
    var h = parseInt(m[4], 10);
    return m[1] + '-' + m[2] + '-' + m[3] + ' ' +
           (h % 12 || 12) + ':' + m[5] + (h >= 12 ? 'pm' : 'am');
  }

  function linkStyle(el) {
    el.style.cssText =
      'font-size:12px;color:var(--ac,#89b4fa);text-decoration:none;flex-shrink:0;white-space:nowrap';
    el.onmouseenter = function () { this.style.textDecoration = 'underline'; };
    el.onmouseleave = function () { this.style.textDecoration = 'none'; };
  }

  function chipStyle(el) {
    el.style.cssText =
      'background:none;border:1px solid var(--bd,#3a3a55);color:var(--mu,#7f849c);' +
      'font-size:11px;padding:2px 8px;border-radius:4px;cursor:pointer;font-family:inherit;flex-shrink:0';
    el.onmouseenter = function () {
      this.style.borderColor = 'var(--ac,#89b4fa)';
      this.style.color = 'var(--ac,#89b4fa)';
    };
    el.onmouseleave = function () {
      this.style.borderColor = 'var(--bd,#3a3a55)';
      this.style.color = 'var(--mu,#7f849c)';
    };
  }

  function makeAltD() {
    var s = document.createElement('span');
    s.style.cssText = 'font-size:11px;color:var(--mu,#7f849c);opacity:.5;flex-shrink:0';
    s.textContent = 'Alt+D';
    return s;
  }

  function ensureBar() {
    var bar = document.querySelector('.bar');
    if (bar) return bar;
    // Self-contained host: pages that include manifest-link.js but don't
    // pre-render a .bar div still get the header.
    bar = document.createElement('div');
    bar.className = 'bar';
    bar.style.cssText =
      'position:sticky;top:0;z-index:100;background:var(--cb,#181825);' +
      'border-bottom:1px solid var(--bd,#3a3a55);padding:8px 16px;' +
      'display:flex;align-items:center;gap:12px;' +
      'margin:-32px -32px 28px;font-size:12px;color:var(--mu,#7f849c)';
    document.body.insertBefore(bar, document.body.firstChild);
    return bar;
  }

  function inject() {
    var bar = ensureBar();
    if (!bar) return;

    // ── Left: ← Manifest + Alt+D ─────────────────────────────────────────
    var existingLink = bar.querySelector('a[href="/manifest.html"]');
    if (!existingLink) {
      // Inject new link as first child
      var a = document.createElement('a');
      a.className = 'manifest-link';
      a.href = '/manifest.html';
      a.textContent = '← Manifest';
      linkStyle(a);
      bar.insertBefore(a, bar.firstChild);
      existingLink = a;
    }
    // Insert Alt+D immediately after the manifest link if not already there
    if (!bar.querySelector('.altd-label')) {
      var altd = makeAltD();
      altd.className = 'altd-label';
      existingLink.insertAdjacentElement('afterend', altd);
    }

    // ── Middle: current document URL ─────────────────────────────────────
    // Only inject if no <code> already exists in the bar (older docs
    // hardcoded one). Strip any sibling "Project:" label — wasted space.
    if (!bar.querySelector('code')) {
      var url = document.createElement('code');
      url.className = 'doc-url';
      url.style.cssText =
        'font-family:"SF Mono","Fira Code",Consolas,monospace;font-size:11px;' +
        'color:var(--ac,#89b4fa);background:var(--sf,#27273a);' +
        'padding:2px 7px;border-radius:4px;border:1px solid var(--bd,#3a3a55);' +
        'user-select:all;flex-shrink:0';
      url.textContent = window.location.origin + window.location.pathname;
      var anchor = bar.querySelector('.altd-label') || existingLink;
      anchor.insertAdjacentElement('afterend', url);
    }

    // ── Right: timestamp + ↻ Refresh — skip if already injected ──────────
    if (bar.querySelector('.doc-right')) return;

    var dtStr = parseDateFromPath();
    var right = document.createElement('span');
    right.className = 'doc-right';
    right.style.cssText =
      'display:flex;align-items:center;gap:8px;flex-shrink:0;margin-left:auto';

    if (dtStr) {
      var ts = document.createElement('span');
      ts.style.cssText = 'font-size:11px;color:var(--mu,#7f849c);opacity:.75';
      ts.textContent = dtStr;
      right.appendChild(ts);
    }

    var btn = document.createElement('button');
    btn.textContent = '↻ Refresh';
    chipStyle(btn);
    btn.onclick = function() { location.href = location.pathname + '?t=' + Date.now(); };
    right.appendChild(btn);

    var spacer = bar.querySelector('[style*="margin-left:auto"]');
    if (spacer) {
      bar.replaceChild(right, spacer);
    } else {
      bar.appendChild(right);
    }
  }

  // ── Find in page ─────────────────────────────────────────────────────────
  // VSCode's Simple Browser captures the browser's native Ctrl+F, leaving
  // the doc unsearchable. This overlay restores find-in-page:
  //   Ctrl+F / Cmd+F → open      Enter → next      Shift+Enter → prev
  //   F3 / Ctrl+G    → next      Esc   → close
  var findState = { box: null, matches: [], idx: -1, query: '' };
  var FIND_SKIP = '.bar, #ann-pop, .ann-badge, .ann-btn, #find-box, script, style';

  function buildFindWidget() {
    var st = document.createElement('style');
    st.textContent =
      'mark.findhl{background:rgba(249,226,175,.35);color:inherit;padding:0;border-radius:2px}' +
      'mark.findhl-current{background:rgba(249,226,175,.9);color:var(--bg,#1e1e2e);' +
      'outline:1px solid var(--am,#f9e2af)}' +
      '#find-box button:hover{border-color:var(--ac,#89b4fa)!important;color:var(--ac,#89b4fa)!important}';
    document.head.appendChild(st);

    var box = document.createElement('div');
    box.id = 'find-box';
    box.style.cssText =
      'position:fixed;top:48px;right:16px;z-index:200;display:none;' +
      'background:var(--cb,#181825);border:1px solid var(--bd,#3a3a55);' +
      'border-radius:6px;padding:6px 8px;gap:6px;align-items:center;' +
      'box-shadow:0 4px 14px rgba(0,0,0,.45);font-size:12px';
    box.innerHTML =
      '<input id="find-q" type="text" placeholder="Find in page" ' +
      'style="background:var(--sf,#27273a);border:1px solid var(--bd,#3a3a55);' +
      'color:var(--tx,#cdd6f4);font-size:12px;padding:3px 7px;border-radius:3px;' +
      'width:170px;outline:none;font-family:inherit">' +
      '<span id="find-count" style="color:var(--mu,#7f849c);font-size:11px;' +
      'min-width:64px;text-align:right;font-family:monospace"></span>' +
      '<button id="find-prev" title="Previous (Shift+Enter)">↑</button>' +
      '<button id="find-next" title="Next (Enter)">↓</button>' +
      '<button id="find-close" title="Close (Esc)">×</button>';
    document.body.appendChild(box);
    box.querySelectorAll('button').forEach(function (b) {
      b.style.cssText =
        'background:none;border:1px solid var(--bd,#3a3a55);color:var(--mu,#7f849c);' +
        'font-size:12px;padding:1px 7px;border-radius:3px;cursor:pointer;' +
        'font-family:inherit;line-height:1.2';
    });
    return box;
  }

  function clearFindHighlights() {
    var marks = document.querySelectorAll('mark.findhl');
    var parents = new Set();
    marks.forEach(function (m) {
      var p = m.parentNode;
      if (p) {
        p.replaceChild(document.createTextNode(m.textContent), m);
        parents.add(p);
      }
    });
    parents.forEach(function (p) { p.normalize(); });
  }

  function nodeInSkipZone(node) {
    for (var p = node.parentNode; p && p !== document.body; p = p.parentNode) {
      if (p.matches && p.matches(FIND_SKIP)) return true;
    }
    return false;
  }

  function applyFindHighlights(q) {
    clearFindHighlights();
    findState.matches = [];
    findState.idx = -1;
    findState.query = q;
    if (!q) return;
    var ql = q.toLowerCase();
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        if (!n.nodeValue) return NodeFilter.FILTER_REJECT;
        if (nodeInSkipZone(n)) return NodeFilter.FILTER_REJECT;
        return n.nodeValue.toLowerCase().indexOf(ql) >= 0
          ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    var hits = [];
    var n;
    while ((n = walker.nextNode())) hits.push(n);
    hits.forEach(function (textNode) {
      var text = textNode.nodeValue;
      var lower = text.toLowerCase();
      var parent = textNode.parentNode;
      var frag = document.createDocumentFragment();
      var pos = 0, idx;
      while ((idx = lower.indexOf(ql, pos)) !== -1) {
        if (idx > pos) frag.appendChild(document.createTextNode(text.slice(pos, idx)));
        var mk = document.createElement('mark');
        mk.className = 'findhl';
        mk.textContent = text.slice(idx, idx + q.length);
        frag.appendChild(mk);
        findState.matches.push(mk);
        pos = idx + q.length;
      }
      if (pos < text.length) frag.appendChild(document.createTextNode(text.slice(pos)));
      parent.replaceChild(frag, textNode);
    });
  }

  function setCurrentMatch(i) {
    var cnt = document.getElementById('find-count');
    if (!findState.matches.length) {
      cnt.textContent = findState.query ? 'no matches' : '';
      return;
    }
    findState.matches.forEach(function (m) { m.classList.remove('findhl-current'); });
    if (i < 0) i = findState.matches.length - 1;
    if (i >= findState.matches.length) i = 0;
    findState.idx = i;
    var cur = findState.matches[i];
    cur.classList.add('findhl-current');
    cur.scrollIntoView({ block: 'center', behavior: 'smooth' });
    cnt.textContent = (i + 1) + ' / ' + findState.matches.length;
  }

  function openFind() {
    if (!findState.box) {
      findState.box = buildFindWidget();
      var inp = document.getElementById('find-q');
      var debounce;
      inp.addEventListener('input', function () {
        clearTimeout(debounce);
        debounce = setTimeout(function () {
          applyFindHighlights(inp.value);
          if (findState.matches.length) setCurrentMatch(0);
          else setCurrentMatch(-1);
        }, 120);
      });
      inp.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          if (findState.matches.length) setCurrentMatch(findState.idx + (e.shiftKey ? -1 : 1));
        } else if (e.key === 'Escape') {
          e.preventDefault();
          closeFind();
        }
      });
      document.getElementById('find-next').onclick =
        function () { if (findState.matches.length) setCurrentMatch(findState.idx + 1); inp.focus(); };
      document.getElementById('find-prev').onclick =
        function () { if (findState.matches.length) setCurrentMatch(findState.idx - 1); inp.focus(); };
      document.getElementById('find-close').onclick = closeFind;
    }
    findState.box.style.display = 'flex';
    var input = document.getElementById('find-q');
    input.focus();
    input.select();
    // If a selection exists in the page, seed it
    var sel = window.getSelection && window.getSelection().toString();
    if (sel && sel.trim() && sel.length < 80) {
      input.value = sel.trim();
      applyFindHighlights(input.value);
      if (findState.matches.length) setCurrentMatch(0);
    }
  }

  function closeFind() {
    if (findState.box) findState.box.style.display = 'none';
    clearFindHighlights();
    findState.matches = [];
    findState.idx = -1;
  }

  function wireFindShortcuts() {
    document.addEventListener('keydown', function (e) {
      var k = e.key;
      var mod = e.ctrlKey || e.metaKey;
      if (mod && (k === 'f' || k === 'F')) {
        e.preventDefault();
        openFind();
      } else if (k === 'F3' || (mod && (k === 'g' || k === 'G'))) {
        e.preventDefault();
        if (findState.matches.length) {
          setCurrentMatch(findState.idx + (e.shiftKey ? -1 : 1));
        } else {
          openFind();
        }
      } else if (k === 'Escape' && findState.box && findState.box.style.display !== 'none') {
        e.preventDefault();
        closeFind();
      }
    });
  }

  function init() {
    inject();
    wireFindShortcuts();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}());
