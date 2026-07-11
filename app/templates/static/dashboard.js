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
    // Forward both user_id and role so server-side require_role() succeeds.
    // Role is taken from the URL first, falling back to sessionStorage.
    let role = new URLSearchParams(window.location.search).get('role');
    if (!role) {
      try { role = JSON.parse(sessionStorage.getItem('login_profile') || '{}').role; } catch (_) {}
    }
    const params = new URLSearchParams();
    if (userId) params.set('user_id', userId);
    if (role) params.set('role', role);
    const sep = path.includes('?') ? '&' : '?';
    const url = params.toString() ? `${path}${sep}${params}` : path;
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

  /**
   * LiveCash — a tiny client-side ledger for the agent's physical cash drawer.
   *
   * Usage:
   *   const cash = DASH.LiveCash.create({ starting_cash: 500000 });
   *   // ... later, every time new transactions arrive ...
   *   cash.applyBatch(txList);  // each tx: { tx_id, type:'CASH_OUT'|'CASH_IN', amount:number }
   *   cash.snapshot();          // { starting, deductions, additions, balance, tx_count }
   *
   * Idempotency: tx_id is used as the dedup key — replaying the same list
   * won't double-count.
   */
  LiveCash: {
    create({ starting_cash = 0 } = {}) {
      const state = {
        starting: Number(starting_cash) || 0,
        deductions: 0,           // total CASH_OUT (reduces balance)
        additions: 0,            // total CASH_IN  (increases balance)
        tx_count: 0,             // number of unique txns applied
        seen: new Set(),         // dedup set of tx_id
      };
      return {
        /** Apply new transactions; only unseen tx_ids affect the balance. */
        applyBatch(txns) {
          let newDeductions = 0;
          let newAdditions = 0;
          let newCount = 0;
          for (const t of (txns || [])) {
            if (!t || !t.tx_id) continue;
            if (state.seen.has(t.tx_id)) continue;
            state.seen.add(t.tx_id);
            const amt = Number(t.amount) || 0;
            if (t.type === 'CASH_OUT') newDeductions += amt;
            else if (t.type === 'CASH_IN') newAdditions += amt;
            newCount += 1;
          }
          state.deductions += newDeductions;
          state.additions   += newAdditions;
          state.tx_count    += newCount;
          return { newDeductions, newAdditions, newCount };
        },
        /** Read-only snapshot of the current ledger. */
        snapshot() {
          const balance = state.starting + state.additions - state.deductions;
          return {
            starting: state.starting,
            deductions: state.deductions,
            additions: state.additions,
            balance,
            tx_count: state.tx_count,
          };
        },
        /** Reset ledger back to the starting cash (for "Reset sim" button). */
        reset() {
          state.deductions = 0;
          state.additions = 0;
          state.tx_count = 0;
          state.seen.clear();
        },
      };
    },
  },

  /**
   * LiveWallet — multi-ledger version of LiveCash, one per provider wallet.
   *
   * Usage:
   *   const w = DASH.LiveWallet.create([
   *     { provider_id: 'bkash',  e_money_balance: 45000 },
   *     { provider_id: 'nagad',  e_money_balance: 32000 },
   *     { provider_id: 'rocket', e_money_balance: 28000 },
   *   ]);
   *   w.applyBatch(txList);   // txList items: { tx_id, provider_id, type, amount }
   *   w.snapshot();           // returns a map: { bkash: {starting, deductions, additions, balance, ...}, ... }
   *   w.snapshotAll();        // same shape, plus `total` and `total_delta` aggregates
   *
   * Same idempotency rules as LiveCash — tx_id is the dedup key.
   * Provider matching uses the txn's `provider_id` field (numeric '1'/'2'/'3'
   * from the dashboard API, or string 'bkash'/'nagad'/'rocket' from login).
   * Both forms are normalised to the lowercase string for matching.
   */
  LiveWallet: {
    PROVIDER_NAME: { '1': 'bKash', '2': 'Nagad', '3': 'Rocket',
                     bkash: 'bKash', nagad: 'Nagad', rocket: 'Rocket' },

    create(wallets = []) {
      // Capture PROVIDER_NAME in a local so the returned object's methods can
      // reach it without depending on `this` binding (broken when callers do
      // `this.snapshot()` from a sibling method on the same returned object).
      const PROVIDER_NAME = this.PROVIDER_NAME;
      // Normalize each wallet to { provider_id: lower-string key, starting: float }
      const ledgers = new Map();
      for (const w of wallets) {
        if (!w || !w.provider_id) continue;
        const key = String(w.provider_id).toLowerCase();
        const starting = Number(w.e_money_balance) || 0;
        ledgers.set(key, {
          starting,
          deductions: 0,
          additions: 0,
          tx_count: 0,
          seen: new Set(),
        });
      }

      // API returns provider_id as '1'/'2'/'3' (numeric), login returns 'bkash'/'nagad'/'rocket'.
      // Normalise both forms to the same lower-string key for matching.
      const NUM_TO_STR = { '1': 'bkash', '2': 'nagad', '3': 'rocket' };
      const _normProvider = (p) => {
        const s = String(p || '').toLowerCase();
        return NUM_TO_STR[s] || s;
      };

      return {
        /** Apply new transactions; only unseen tx_ids, routed by provider_id. */
        applyBatch(txns) {
          const newByProvider = {};
          for (const t of (txns || [])) {
            if (!t || !t.tx_id) continue;
            const key = _normProvider(t.provider_id);
            const ledger = ledgers.get(key);
            if (!ledger) continue;                            // unknown provider -> skip
            if (ledger.seen.has(t.tx_id)) continue;           // already applied
            ledger.seen.add(t.tx_id);
            const amt = Number(t.amount) || 0;
            if (t.type === 'CASH_OUT') ledger.deductions += amt;
            else if (t.type === 'CASH_IN') ledger.additions += amt;
            ledger.tx_count += 1;
            newByProvider[key] = (newByProvider[key] || 0) + 1;
          }
          return { newByProvider };
        },
        /** Per-provider snapshots keyed by lowercase provider_id. */
        snapshot() {
          const out = {};
          for (const [key, st] of ledgers.entries()) {
            out[key] = {
              starting: st.starting,
              deductions: st.deductions,
              additions: st.additions,
              balance: st.starting + st.additions - st.deductions,
              tx_count: st.tx_count,
              provider_name: PROVIDER_NAME[key] || key,
            };
          }
          return out;
        },
        /** Convenience: per-provider + total aggregate. */
        snapshotAll() {
          const per = this.snapshot();
          let total_starting = 0, total_deductions = 0, total_additions = 0, total_tx = 0;
          for (const v of Object.values(per)) {
            total_starting   += v.starting;
            total_deductions += v.deductions;
            total_additions  += v.additions;
            total_tx         += v.tx_count;
          }
          return {
            per,
            total: {
              starting: total_starting,
              deductions: total_deductions,
              additions: total_additions,
              balance: total_starting + total_additions - total_deductions,
              tx_count: total_tx,
            },
          };
        },
        /** Reset all ledgers back to their starting balances. */
        reset() {
          for (const st of ledgers.values()) {
            st.deductions = 0;
            st.additions = 0;
            st.tx_count = 0;
            st.seen.clear();
          }
        },
      };
    },
  },
};