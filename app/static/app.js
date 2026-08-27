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
