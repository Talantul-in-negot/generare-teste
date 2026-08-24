/* Jurnal local offline + apeluri RPC. Serverul stochează fiecare încercare și
   derivează totalul; browserul nu trimite niciodată un scor agregat. */
const Tracker = (() => {
  const QUEUE_KEY = 'talant_pending_attempts';
  const enabled = typeof SUPABASE_URL === 'string' && SUPABASE_URL.startsWith('https://') && !!SUPABASE_ANON_KEY;
  let flushing = null;
  let lastError = null;

  function readQueue() {
    try { return JSON.parse(localStorage.getItem(QUEUE_KEY)) || []; } catch { return []; }
  }
  function attemptId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = Math.floor(Math.random() * 16);
      return (c === 'x' ? r : (r & 3) | 8).toString(16);
    });
  }
  function writeQueue(queue) { localStorage.setItem(QUEUE_KEY, JSON.stringify(queue)); }
  async function headers(extra = {}) {
    const token = await Auth.accessToken();
    return { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${token || SUPABASE_ANON_KEY}`, ...extra };
  }
  async function rpc(name, body) {
    const response = await fetch(`${SUPABASE_URL}/rest/v1/rpc/${name}`, {
      method: 'POST', headers: await headers({ 'Content-Type': 'application/json' }), body: JSON.stringify(body || {}),
    });
    if (!response.ok) {
      const bodyText = await response.text();
      let message = `HTTP ${response.status}`;
      try { message = JSON.parse(bodyText).message || JSON.parse(bodyText).hint || message; } catch { message = bodyText || message; }
      throw new Error(message);
    }
    if (response.status === 204) return null;
    const text = await response.text();
    return text ? JSON.parse(text) : null;
  }

  function log(attempt) {
    if (!enabled || !Auth.userId()) return;
    const queue = readQueue();
    queue.push({ ...attempt, client_attempt_id: attemptId(), attempted_at: new Date().toISOString() });
    writeQueue(queue);
  }

  async function flush() {
    if (!enabled || !Auth.userId()) return false;
    if (flushing) return flushing;
    flushing = (async () => {
      while (true) {
        const queue = readQueue();
        if (!queue.length) { lastError = null; return true; }
        try {
          await rpc('talant_record_attempt', queue[0]);
          writeQueue(queue.slice(1));
        } catch (error) { lastError = error; return false; }
      }
    })().finally(() => { flushing = null; });
    return flushing;
  }

  async function record(attempt) {
    log(attempt);
    const saved = await flush();
    if (!saved) throw lastError || new Error('Răspunsul nu a putut fi salvat.');
    return ownStats();
  }
  async function ownStats() {
    if (!enabled || !Auth.userId()) return null;
    const rows = await rpc('talant_my_stats');
    return Array.isArray(rows) ? rows[0] || null : rows;
  }
  async function leaderboard() { return enabled ? rpc('talant_leaderboard', { p_limit: 20 }) : []; }
  async function myAttempts(limit = 20) { return enabled && Auth.userId() ? rpc('talant_my_attempts', { p_limit: limit }) : []; }

  window.addEventListener('online', () => { flush(); });
  return { enabled, record, flush, ownStats, leaderboard, myAttempts };
})();
