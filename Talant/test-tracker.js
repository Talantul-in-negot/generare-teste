/* Jurnal offline + RPC pentru teste tip "Talantul în negoț" (secțiuni I-IV, puncte
   variabile). Separat de tracker.js (quiz-ul Ioan) — foloseşte acelaşi cont, dar
   tabele/RPC-uri proprii, aşa că nu atinge scorul sau clasamentul existente. */
const TestTracker = (() => {
  const QUEUE_KEY = 'talant_test_pending_attempts';
  const enabled = typeof SUPABASE_URL === 'string' && SUPABASE_URL.startsWith('https://') && !!SUPABASE_ANON_KEY;
  let flushing = null;
  let lastError = null;

  function readQueue() {
    try { return JSON.parse(localStorage.getItem(QUEUE_KEY)) || []; } catch { return []; }
  }
  function writeQueue(queue) { localStorage.setItem(QUEUE_KEY, JSON.stringify(queue)); }
  function attemptId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = Math.floor(Math.random() * 16);
      return (c === 'x' ? r : (r & 3) | 8).toString(16);
    });
  }
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
          await rpc('talant_record_test_attempt', queue[0]);
          writeQueue(queue.slice(1));
        } catch (error) { lastError = error; return false; }
      }
    })().finally(() => { flushing = null; });
    return flushing;
  }

  async function record(attempt) {
    log(attempt);
    const saved = await flush();
    if (!saved) throw lastError || new Error('Rezultatul nu a putut fi salvat.');
    return ownStats(attempt.quiz_version);
  }
  async function ownStats(quizVersion) {
    if (!enabled || !Auth.userId()) return null;
    const rows = await rpc('talant_test_my_stats', { p_quiz_version: quizVersion });
    return Array.isArray(rows) ? rows[0] || null : rows;
  }
  async function leaderboard(quizVersion, limit = 20) {
    return enabled ? rpc('talant_test_leaderboard', { p_quiz_version: quizVersion, p_limit: limit }) : [];
  }
  async function myAttempts(quizVersion, limit = 20) {
    return enabled && Auth.userId() ? rpc('talant_test_my_attempts', { p_quiz_version: quizVersion, p_limit: limit }) : [];
  }

  window.addEventListener('online', () => { flush(); });
  return { enabled, record, flush, ownStats, leaderboard, myAttempts };
})();
