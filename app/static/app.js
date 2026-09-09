// Model Deployment app — small vanilla-JS helpers (HTMX handles most POSTs).

function formatBytes(n) {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(n) / Math.log(1024));
  return (n / Math.pow(1024, i)).toFixed(i ? 1 : 0) + ' ' + u[i];
}

// Serialize a <form> to FormData for fetch calls.
function formToFormData(form) {
  const fd = new FormData(form);
  return fd;
}

// ---- Client-side SPA navigation --------------------------------------------
// The top nav tabs (/ , /targets, /downloads, /jobs, /settings) are separate
// server-rendered routes. Instead of a full page reload on each switch, we
// fetch each route once and keep its DOM mounted in a hidden <section>, then
// toggle visibility. Because the section stays attached and its scripts run
// exactly once, returning to a tab restores its exact state (scroll, open
// drawer, search text, filters, loaded grid) with no reload.
(function () {
  if (typeof window === 'undefined') return;
  var MAIN = document.querySelector('main');
  if (!MAIN) return;

  var sections = {};        // path -> <section> element
  var scrollPos = {};       // path -> window.scrollY captured on leave
  var activePath = null;
  var loading = {};         // path -> true while a fetch is in flight
  var routeHrefs = ['/', '/targets', '/downloads', '/packages', '/jobs', '/settings'];

  function pathOf(href) {
    try {
      var u = new URL(href, location.origin);
      if (u.origin !== location.origin) return null;
      if (u.pathname.indexOf('/api') === 0) return null;
      return u.pathname + u.search;
    } catch (e) { return null; }
  }

  function routeMatches(href) {
    return routeHrefs.indexOf(pathOf(href)) !== -1;
  }

  function ensureSection(path) {
    if (sections[path]) return sections[path];
    var sec = document.createElement('section');
    sec.setAttribute('data-route', path);
    sec.setAttribute('hidden', '');
    MAIN.appendChild(sec);
    sections[path] = sec;
    return sec;
  }

  function setActiveNav(path) {
    var links = document.querySelectorAll('nav .tab-row a');
    for (var i = 0; i < links.length; i++) {
      links[i].classList.toggle('tab-on', pathOf(links[i].getAttribute('href')) === path);
    }
  }

  function show(path, restore) {
    if (activePath && activePath !== path) scrollPos[activePath] = window.scrollY;
    var sec = ensureSection(path);
    for (var p in sections) {
      if (sections.hasOwnProperty(p) && p !== path) sections[p].setAttribute('hidden', '');
    }
    sec.removeAttribute('hidden');
    activePath = path;
    setActiveNav(path);
    if (restore !== false && scrollPos[path] != null) {
      requestAnimationFrame(function () { window.scrollTo(0, scrollPos[path]); });
    }
  }

  function goHash(hash) {
    if (!hash) return;
    var el = document.getElementById(hash);
    if (el) el.scrollIntoView({ block: 'start' });
  }

  // Compile and run a page's own inline <script> blocks. External scripts
  // (tailwind, htmx, app.js) have src and are skipped; head config is skipped too.
  function runInlineScripts(doc) {
    var scripts = doc.querySelectorAll('script');
    var ran = 0;
    for (var i = 0; i < scripts.length; i++) {
      var s = scripts[i];
      if (s.getAttribute('src')) continue;
      if (s.closest && s.closest('head')) continue;
      var code = s.textContent;
      if (!code || !code.trim()) continue;
      var ns = document.createElement('script');
      ns.textContent = code;
      (document.body || MAIN).appendChild(ns);
      ns.remove();
      ran++;
    }
    return ran;
  }

  function load(path, hash) {
    var sec = ensureSection(path);
    if (sec.getAttribute('data-loaded')) {
      show(path);
      goHash(hash);
      return Promise.resolve();
    }
    if (loading[path]) return Promise.resolve();
    loading[path] = true;
    return fetch(path, { headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var nm = doc.querySelector('main');
        var content = nm ? nm.cloneNode(true) : doc.body.cloneNode(true);
        content.querySelectorAll('script').forEach(function (n) { n.remove(); });
        sec.innerHTML = content.innerHTML;
        sec.setAttribute('data-loaded', '1');
        runInlineScripts(doc);
        if (window.htmx) window.htmx.process(sec);
      })
      .catch(function (err) {
        sec.innerHTML = '<p class="card text-err">Couldn’t load ' + path +
          ' — <a href="' + path + '">try again</a></p>';
      })
      .then(function () {
        delete loading[path];
        show(path);
        goHash(hash);
      });
  }

  function navigate(url) {
    var path = pathOf(url);
    if (path === null) return false;
    var hash = url.indexOf('#') !== -1 ? url.slice(url.indexOf('#') + 1) : '';
    if (path === activePath && !hash) return true;
    if (path !== activePath) {
      history.pushState({ route: path }, '', url);
    }
    load(path, hash);
    return true;
  }

  // Intercept internal GET navigation (nav tabs + in-page links like /jobs#id).
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;
    if (a.getAttribute('target') === '_blank') return;
    if (!routeMatches(a.getAttribute('href'))) return;
    e.preventDefault();
    navigate(a.getAttribute('href'));
  });

  window.addEventListener('popstate', function (e) {
    var path = (e.state && e.state.route) || pathOf(location.href) || '/';
    var sec = sections[path];
    if (sec && sec.getAttribute('data-loaded')) {
      show(path);
      goHash(location.hash.replace('#', ''));
    } else {
      load(path);
    }
  });

  // ~Boot~: wrap the already-rendered current page into its section.
  // ensureSection appends the section as a child of MAIN, so only move the
  // pre-existing children (leave the section itself in place).
  var boot = pathOf(location.href) || '/';
  var bootSec = ensureSection(boot);
  while (MAIN.firstChild && MAIN.firstChild !== bootSec) {
    bootSec.appendChild(MAIN.firstChild);
  }
  bootSec.removeAttribute('hidden');
  bootSec.setAttribute('data-loaded', '1');
  activePath = boot;
  setActiveNav(boot);
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

  // Public helper so page code can redirect through the router (downloads form).
  window.MDSPA = { navigate: navigate };
})();

// Open an EventSource stream and append log lines / update progress into el.
function openSSE(jobId, el) {
  if (el.dataset.started) return;
  el.dataset.started = '1';
  const es = new EventSource('/api/stream/' + jobId);
  es.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.line) {
      el.textContent += msg.line + '\n';
      if (el.tagName === 'PRE') el.scrollTop = el.scrollHeight;
    }
    if (typeof msg.progress === 'number' && el.querySelector('.progress-bar')) {
      const bar = el.querySelector('.progress-bar');
      if (bar) bar.style.width = msg.progress + '%';
    }
    if (msg.final) {
      es.close();
    }
  };
}

// ---- PWA: register service worker + expose install affordance ---------------
(function () {
  if (typeof window === 'undefined') return;
  // SW only works over a secure context (HTTPS / localhost); guard so the app
  // still loads over plain HTTP on a LAN.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }

  // Chrome/Edge fire beforeinstallprompt only when installable; capture it to
  // offer a button. iOS has no API — users use Share -> "Add to Home Screen".
  let deferred = null;
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    if (!deferred) deferred = e;
  });

  window.MDInstall = {
    canPrompt: () => deferred !== null,
    prompt: async () => {
      if (!deferred) return false;
      deferred.prompt();
      try { await deferred.userChoice; } catch (_) {}
      deferred = null;
      return true;
    },
  };
})();
