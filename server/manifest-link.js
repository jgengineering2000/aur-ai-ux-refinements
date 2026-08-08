// Normalises the .bar of every doc page into one header:
//   LEFT   — "← Manifest" link + muted "Alt+D" label
//   THEN   — the FILENAME: the document's identity, selectable for copy, and
//            free to wrap onto a second line when the window is narrow
//   RIGHT  — generation timestamp (parsed from the filename) + ↻ Refresh
//
// It also REMOVES what the generated document put there itself: a second
// manifest link (the document's own is relative, the injected one absolute, so
// they never matched and both rendered), the human title, and the "|" framing
// spans. The title is dropped because the filename already identifies the
// document and carries its date; two names for one thing is what pushed this
// header into overflow.
//
// Repairing at the injector rather than in the generator is deliberate: it
// fixes every document already written, not only the next one. md2doc.py stops
// emitting them too, so a fresh page needs no repair.
(function () {
  var IS_MANIFEST = window.location.pathname.endsWith('/manifest.html');

  // Reload restore, take two. VSCode's Simple Browser reloads the URL it was
  // OPENED with — always the manifest — so refreshing while reading a document
  // lands on the index.
  //
  // The first attempt recorded the document in sessionStorage on load and had
  // the manifest check performance navigation type. It never fired: the reload
  // builds a fresh webview context, so sessionStorage is empty by the time the
  // manifest reads it, and the navigation type is not reliably 'reload' there.
  //
  // So: localStorage (survives the context), written at PAGEHIDE rather than at
  // load. A fresh unload record means we were reading a document a moment ago
  // and are now somehow at the manifest — which is a reload. Clicking
  // "← Manifest" clears the record first, so a deliberate visit to the index
  // stays there.
  //
  // This is a heuristic and worth naming as one: Alt+D pressed seconds after
  // reading is indistinguishable from a reload, because both re-request the
  // same URL with no other signal. The window is kept short to bound that.
  try {
    if (!IS_MANIFEST) {
      window.addEventListener('pagehide', function () {
        try {
          localStorage.setItem('aur:lastDoc', JSON.stringify(
            { p: window.location.pathname, t: Date.now() }));
        } catch (e) {}
      });
    }
  } catch (e) { /* storage disabled: the refresh button still works */ }

  if (IS_MANIFEST) return;

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
    if (bar) {
      // The bar is a flex row the DOCUMENT authored, sized for the two or three
      // children it ships with. This script then adds up to six more — manifest
      // link, Alt+D, url, name, date, refresh — so without wrapping the row
      // overflows and the items render on top of one another. The document
      // cannot know that, and editing every generated file is not a fix, so the
      // injector enforces it on whatever bar it is handed.
      bar.style.flexWrap = 'wrap';
      if (!bar.style.rowGap) bar.style.rowGap = '6px';
      return bar;
    }
    // Self-contained host: pages that include manifest-link.js but don't
    // pre-render a .bar div still get the header.
    //
    // Robust full-bleed sticky bar that makes NO assumption about the body's
    // padding. The previous `margin:-32px` bled the bar off-screen on any doc
    // whose body padding wasn't exactly 32px (manifest.html uses 0 via
    // .container, plan docs 28px) — that brittleness is why the header
    // "vanished". Instead: neutralise the body's top padding so the sticky bar
    // pins to the true viewport top, and pull the bar out over the body's
    // ACTUAL left/right padding (whatever it is) to stay edge-to-edge.
    var cs = getComputedStyle(document.body);
    var padT = parseInt(cs.paddingTop, 10) || 0;
    var padL = parseInt(cs.paddingLeft, 10) || 0;
    var padR = parseInt(cs.paddingRight, 10) || 0;
    document.body.style.paddingTop = '0';
    bar = document.createElement('div');
    bar.className = 'bar';
    bar.style.cssText =
      'position:sticky;top:0;z-index:100;background:var(--cb,#181825);' +
      'border-bottom:1px solid var(--bd,#3a3a55);padding:8px 16px;' +
      'display:flex;align-items:center;gap:12px;flex-wrap:wrap;row-gap:6px;' +
      'margin:0 -' + padR + 'px ' + (padT || 24) + 'px -' + padL + 'px;' +
      'font-size:12px;color:var(--mu,#7f849c)';
    document.body.insertBefore(bar, document.body.firstChild);
    return bar;
  }

  function inject() {
    var bar = ensureBar();
    if (!bar) return;

    // ── Strip what the document authored ─────────────────────────────────
    // A generated doc ships its own bar: a RELATIVE manifest link, its human
    // title, and "|" separators. The relative href never matched the absolute
    // one looked for below, so both links rendered and pointed at the same
    // place. Remove the document's versions and let one injected set stand.
    Array.prototype.forEach.call(bar.querySelectorAll('a'), function (a) {
      if (/(^|\/)manifest\.html$/.test(a.getAttribute('href') || '')) a.remove();
    });
    Array.prototype.forEach.call(bar.querySelectorAll('strong'), function (s) {
      s.remove();                       // the human title: the filename says it
    });
    Array.prototype.forEach.call(bar.querySelectorAll('span'), function (s) {
      if (s.textContent.trim() === '|') s.remove();
    });

    // ── Left: ← Manifest + Alt+D ─────────────────────────────────────────
    var existingLink = bar.querySelector('a.manifest-link');
    if (!existingLink) {
      var a = document.createElement('a');
      a.className = 'manifest-link';
      a.href = '/manifest.html';
      a.textContent = '← Manifest';
      linkStyle(a);
      bar.insertBefore(a, bar.firstChild);
      existingLink = a;
    }
    // Going to the index deliberately must not bounce straight back, so drop
    // the restore record before navigating.
    existingLink.addEventListener('click', function () {
      try { localStorage.removeItem('aur:lastDoc'); } catch (e) {}
    });
    if (!bar.querySelector('.altd-label')) {
      var altd = makeAltD();
      altd.className = 'altd-label';
      existingLink.insertAdjacentElement('afterend', altd);
    }

    // ── The filename IS the identity ─────────────────────────────────────
    // In flow, where the title used to sit — not absolutely centred, because a
    // centred element overlaps its neighbours the moment the row wraps, and
    // wrapping is exactly what a long filename must be allowed to do.
    //
    // flex:1 1 auto with min-width:0 lets it take the slack and then wrap
    // rather than force the row wider; overflow-wrap:anywhere breaks inside the
    // name, since a generated filename is one unbroken token with no space to
    // wrap at. user-select:all makes one click select the whole thing to copy,
    // which is why the separate URL chip is gone — it was the widest element in
    // the bar and said the same thing twice.
    if (!bar.querySelector('.doc-name')) {
      var name = document.createElement('span');
      name.className = 'doc-name';
      var base = decodeURIComponent(window.location.pathname.split('/').pop() || '');
      name.textContent = base || document.title || '(document)';
      name.title = window.location.origin + window.location.pathname;
      name.style.cssText =
        'flex:1 1 auto;min-width:0;font-weight:600;color:var(--tx,#cdd6f4);' +
        'font-size:12px;white-space:normal;overflow-wrap:anywhere;' +
        'word-break:break-word;user-select:all;cursor:text';
      var anchor = bar.querySelector('.altd-label') || existingLink;
      anchor.insertAdjacentElement('afterend', name);
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

    // Escape hatch to a real browser. Simple Browser is a single webview: it
    // has no tab model at all, so Ctrl+T cannot be made to work inside it —
    // VSCode owns that chord, and there is nothing for it to open. A real
    // browser has tabs, a refresh that stays put, and its own find. This link
    // is the honest fix for both, rather than reimplementing a browser badly.
    var ext = document.createElement('a');
    ext.textContent = '↗ Browser';
    ext.href = window.location.href;
    ext.target = '_blank';
    ext.rel = 'noopener';
    ext.title = 'Open this document in the system browser (real tabs, real refresh)';
    chipStyle(ext);
    ext.style.textDecoration = 'none';
    right.appendChild(ext);

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
