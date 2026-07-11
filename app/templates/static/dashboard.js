// Shared dashboard helpers
window.DASH = {
  escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  },

  toast(msg, type='info') {
    const el = document.createElement('div');
    el.className = 'toast';
    el.style.borderColor = type === 'error' ? 'var(--red)' : type === 'success' ? 'var(--green)' : 'var(--accent)';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3000);
  },

  getUserIdFromURL() {
    return new URLSearchParams(window.location.search).get('user_id') || '';
  },

  requireUserId() {
    const id = this.getUserIdId ? this.getUserIdId() : this.getUserIdFromURL();
    if (!id) {
      const main = document.getElementById('content');
      main.innerHTML = `<div class="error-banner">Missing <code>user_id</code> query parameter. Please <a href="/">log in</a> again.</div>`;
      return null;
    }
    return id;
  },

  async api(method, path, body=null) {
    const userId = this.getUserIdFromURL();
    const sep = path.includes('?') ? '&' : '?';
    const url = `${path}${sep}user_id=${encodeURIComponent(userId)}`;
    const opts = { method, headers: {} };
    if (body) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({detail: res.statusText}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async openEvidence(ticketId, role) {
    try {
      const url = `/api/tickets/${ticketId}/evidence?user_id=${encodeURIComponent(this.getUserIdFromURL())}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error('Forbidden or not found');
      const data = await res.json();
      const modal = document.createElement('div');
      modal.className = 'modal-backdrop';
      modal.innerHTML = `
        <div class="modal">
          <h3>Evidence — ${this.escapeHtml(ticketId)}</h3>
          <div style="margin-bottom:0.75rem;">
            <span class="badge sev-${data.severity}">${data.severity}</span>
            <span class="pill">C=${data.confidence_score}</span>
            <span class="pill">Type: ${data.alert_type}</span>
          </div>
          <h4 style="margin:1rem 0 0.5rem;">Bangla Summary</h4>
          <p class="bangla" style="background:var(--bg-card-2);padding:0.75rem;border-radius:8px;">${this.escapeHtml(data.message_bn)}</p>
          <h4 style="margin:1rem 0 0.5rem;">Evidence JSON</h4>
          <div class="evidence-block">${this.escapeHtml(JSON.stringify(data.evidence, null, 2))}</div>
          <div style="margin-top:1rem;text-align:right;">
            <button id="close-modal" style="background:var(--bg-card-2);color:var(--text);border:1px solid var(--border);padding:0.4rem 1rem;border-radius:6px;cursor:pointer;">Close</button>
          </div>
        </div>
      `;
      document.body.appendChild(modal);
      document.getElementById('close-modal').addEventListener('click', () => modal.remove());
      modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
    } catch (e) {
      this.toast(e.message, 'error');
    }
  },

  openNoteModal(ticketId, onSave) {
    const modal = document.createElement('div');
    modal.className = 'modal-backdrop';
    modal.innerHTML = `
      <div class="modal">
        <h3>Add Field Note — ${this.escapeHtml(ticketId)}</h3>
        <textarea id="note-text" placeholder="e.g., Spoke with agent, normal Eid-eve rush, no fraud indicators…"></textarea>
        <div style="margin-top:1rem;text-align:right;display:flex;gap:0.5rem;justify-content:flex-end;">
          <button id="cancel-note" style="background:var(--bg-card-2);color:var(--text);border:1px solid var(--border);padding:0.4rem 1rem;border-radius:6px;cursor:pointer;">Cancel</button>
          <button id="save-note" class="primary" style="background:var(--accent);color:#0c1d3a;border:none;padding:0.4rem 1rem;border-radius:6px;cursor:pointer;font-weight:600;">Save Note</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    document.getElementById('cancel-note').addEventListener('click', () => modal.remove());
    document.getElementById('save-note').addEventListener('click', async () => {
      const text = document.getElementById('note-text').value.trim();
      if (!text) return this.toast('Note text required', 'error');
      try {
        await this.api('POST', `/api/tickets/${ticketId}/add-note`, { notes_text: text });
        this.toast('Note saved', 'success');
        modal.remove();
        if (onSave) onSave();
      } catch (e) { this.toast(e.message, 'error'); }
    });
  },
};