/* Urmărirea activității: coadă offline în localStorage + trimitere batch la Supabase.
   Dacă js/config.js nu are chei, totul devine no-op și aplicația merge ca înainte. */

const Tracker = (() => {
  const QUEUE_KEY = "ci_pending_events";
  const QUEUE_LOCK_KEY = "ci_pending_events_lock";
  const EVENT_ID_FIELD = "_ci_event_id";
  const EVENT_OWNER_FIELD = "_ci_owner_id";
  const MAX_QUEUE_EVENTS = 500;
  const POST_BATCH_SIZE = 100;
  const LOCK_TTL_MS = 60 * 1000;
  const tabId = typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const enabled =
    typeof SUPABASE_URL === "string" &&
    SUPABASE_URL.startsWith("https://") &&
    typeof SUPABASE_ANON_KEY === "string" &&
    SUPABASE_ANON_KEY.length > 0;
  let flushPromise = null;
  let scoreRefreshPromise = null;
  let bufferedEvents = [];
  let bufferedDrainTimer = null;

  // Antetele pentru Supabase. `apikey` rămâne mereu cheia anon (identifică
  // proiectul), dar `Authorization` poartă JWT-ul utilizatorului logat — așa
  // cererea ajunge la Postgres ca rol `authenticated`, cu identitatea dovedită
  // criptografic, nu ca `anon` (cheie publică, oricine o poate copia din
  // js/config.js). Politicile RLS pot astfel impune „doar rândul tău".
  // Fără sesiune se cade înapoi pe cheia anon: scrierile vor fi respinse de
  // RLS și rămân în coadă până la relogare (vezi flush()).
  async function authHeaders(extra) {
    let token = null;
    if (typeof Auth !== "undefined" && Auth.getAccessToken) {
      token = await Auth.getAccessToken();
    }
    return {
      apikey: SUPABASE_ANON_KEY,
      Authorization: `Bearer ${token || SUPABASE_ANON_KEY}`,
      ...(extra || {}),
    };
  }

  function makeEventId() {
    return typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  function trimQueue(queue) {
    return queue.length > MAX_QUEUE_EVENTS
      ? queue.slice(queue.length - MAX_QUEUE_EVENTS)
      : queue;
  }

  // Pending events belong to the account that was active *when they were
  // created*, never to the account that happens to be active during a later
  // retry. Legacy/anonymous entries deliberately remain unclaimed: without a
  // user choice, assigning them to the next person on a shared device is unsafe.
  function readQueue() {
    try {
      const raw = JSON.parse(localStorage.getItem(QUEUE_KEY)) || [];
      if (!Array.isArray(raw)) return [];
      let migrated = false;
      const queue = raw
        .filter((evt) => evt && typeof evt === "object")
        .map((evt) => {
          if (typeof evt[EVENT_ID_FIELD] === "string" && EVENT_OWNER_FIELD in evt) return evt;
          migrated = true;
          return {
            ...evt,
            [EVENT_ID_FIELD]: typeof evt[EVENT_ID_FIELD] === "string" ? evt[EVENT_ID_FIELD] : makeEventId(),
            [EVENT_OWNER_FIELD]: typeof evt[EVENT_OWNER_FIELD] === "string" ? evt[EVENT_OWNER_FIELD] : null,
          };
        });
      if (migrated) writeQueue(queue);
      return queue;
    } catch {
      return [];
    }
  }

  function writeQueue(q) {
    localStorage.setItem(QUEUE_KEY, JSON.stringify(trimQueue(q)));
  }

  function lockRecord() {
    try {
      const lock = JSON.parse(localStorage.getItem(QUEUE_LOCK_KEY) || "null");
      return lock && typeof lock === "object" ? lock : null;
    } catch {
      return null;
    }
  }

  function tryAcquireQueueLock() {
    const now = Date.now();
    const existing = lockRecord();
    if (existing && existing.owner !== tabId && existing.expiresAt > now) return false;
    const mine = { owner: tabId, expiresAt: now + LOCK_TTL_MS };
    localStorage.setItem(QUEUE_LOCK_KEY, JSON.stringify(mine));
    return lockRecord()?.owner === tabId;
  }

  function releaseQueueLock() {
    if (lockRecord()?.owner === tabId) localStorage.removeItem(QUEUE_LOCK_KEY);
  }

  function scheduleBufferedDrain() {
    if (bufferedDrainTimer) return;
    bufferedDrainTimer = setTimeout(() => {
      bufferedDrainTimer = null;
      drainBufferedEvents();
    }, 100);
  }

  function drainBufferedEvents() {
    if (bufferedEvents.length === 0) return;
    if (!tryAcquireQueueLock()) {
      scheduleBufferedDrain();
      return;
    }
    try {
      writeQueue(readQueue().concat(bufferedEvents));
      bufferedEvents = [];
    } finally {
      releaseQueueLock();
    }
    flush();
  }

  function log(evt) {
    if (!enabled) return;
    const ownerId = typeof Auth !== "undefined" && Auth.getUserId ? Auth.getUserId() : null;
    const entry = {
      ...evt,
      created_at: new Date().toISOString(),
      [EVENT_ID_FIELD]: makeEventId(),
      [EVENT_OWNER_FIELD]: ownerId || null,
    };
    if (!tryAcquireQueueLock()) {
      bufferedEvents.push(entry);
      scheduleBufferedDrain();
      return;
    }
    // păstrează momentul real al răspunsului, chiar dacă trimiterea se face mai târziu
    try {
      writeQueue(readQueue().concat(entry));
    } finally {
      releaseQueueLock();
    }
    // nu declanșează flush() aici — checkAnswers() apelează log() de mai multe ori
    // la rând (o dată per verset); un singur flush() după buclă evită atât rafala
    // de cereri, cât și cursa în care evenimente adăugate în timpul unui flush
    // în desfășurare rămâneau blocate în coadă (flush-ul următor era ignorat
    // din cauza gărzii "flushing").
  }

  async function flush() {
    if (!enabled) return false;
    if (flushPromise) return flushPromise;

    flushPromise = (async () => {
      let sentAny = false;
      if (!tryAcquireQueueLock()) return sentAny;
      while (true) {
        const userId = typeof Auth !== "undefined" && Auth.getUserId ? Auth.getUserId() : null;
        if (!userId) return sentAny;
        // Send only events explicitly recorded for this account. Unclaimed
        // anonymous events stay local until a future explicit claim flow.
        const batch = readQueue()
          .filter((evt) => evt[EVENT_OWNER_FIELD] === userId)
          .slice(0, POST_BATCH_SIZE);
        if (batch.length === 0) return sentAny;
        const payload = batch.map(({ [EVENT_ID_FIELD]: eventId, [EVENT_OWNER_FIELD]: _ownerId, ...evt }) => ({
          ...evt,
          client_event_id: eventId,
          user_id: userId,
        }));
        try {
          const res = await fetch(`${SUPABASE_URL}/rest/v1/events`, {
            method: "POST",
            headers: await authHeaders({
              "Content-Type": "application/json",
              Prefer: "resolution=ignore-duplicates,return=minimal",
            }),
            body: JSON.stringify(payload),
          });
          if (!res.ok) return sentAny;
          // Evenimente adăugate în timpul trimiterii sunt procesate în următoarea
          // iterație înainte ca apelantul să poată citi scorul serverului.
          const sentIds = new Set(batch.map((evt) => evt[EVENT_ID_FIELD]));
          writeQueue(readQueue().filter((evt) => !sentIds.has(evt[EVENT_ID_FIELD])));
          sentAny = true;
          await refreshScore();
        } catch {
          // Offline sau eroare de rețea — coada rămâne pentru următoarea încercare.
          return sentAny;
        }
      }
    })().finally(() => {
      releaseQueueLock();
      flushPromise = null;
      if (bufferedEvents.length > 0) scheduleBufferedDrain();
    });
    return flushPromise;
  }

  async function fetchAll() {
    if (!enabled) return [];
    // Supabase plafonează serverul la 1000 de rânduri per cerere, indiferent de
    // `limit`. Paginăm cu offset până se golește, ca să prindem TOȚI utilizatorii
    // (altfel cei cu evenimente mai vechi dispar din clasament).
    const PAGE = 1000;
    let all = [];
    for (let offset = 0; offset < 100000; offset += PAGE) {
      const url =
        `${SUPABASE_URL}/rest/v1/events` +
        `?select=user_name,verse_ref,answer,chosen,correct,created_at,cycle` +
        `&order=created_at.desc&limit=${PAGE}&offset=${offset}`;
      const res = await fetch(url, { headers: await authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const rows = await res.json();
      all = all.concat(rows);
      if (rows.length < PAGE) break;
    }
    return all;
  }

  async function fetchUserEvents(userName) {
    if (!enabled || !userName) return [];
    const userId = typeof Auth !== "undefined" && Auth.getUserId ? Auth.getUserId() : null;
    if (!userId) return [];
    // ilike fără wildcards = egalitate case-insensitive (prinde și numele
    // vechi salvate cu literă mică, ex. "sergiu" vs "Sergiu").
    // Paginat (cap Supabase = 1000/cerere) ca scorul să fie corect chiar și
    // pentru cine depășește 1000 de evenimente.
    const PAGE = 1000;
    let all = [];
    for (let offset = 0; offset < 100000; offset += PAGE) {
      const url =
        `${SUPABASE_URL}/rest/v1/events` +
        `?select=verse_ref,correct,created_at,cycle,answer,chosen` +
        `&user_id=eq.${encodeURIComponent(userId)}` +
        `&order=created_at.desc&limit=${PAGE}&offset=${offset}`;
      const res = await fetch(url, { headers: await authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const rows = await res.json();
      all = all.concat(rows);
      if (rows.length < PAGE) break;
    }
    return all;
  }

  // Clasamentul agregat: un singur rând per utilizator (user_name, points),
  // derivat de Supabase din evenimente. Evită descărcarea întregului istoric
  // la fiecare deschidere de statistici.
  const LEADERBOARD_PAGE_SIZE = 1000;

  async function fetchScores() {
    if (!enabled) return [];
    try {
      const all = [];
      for (let offset = 0; offset < 100000; offset += LEADERBOARD_PAGE_SIZE) {
        const res = await fetch(`${SUPABASE_URL}/rest/v1/rpc/get_public_leaderboard`, {
          method: "POST",
          headers: await authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ p_limit: LEADERBOARD_PAGE_SIZE, p_offset: offset }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const rows = await res.json();
        all.push(...rows);
        if (rows.length < LEADERBOARD_PAGE_SIZE) break;
      }
      return all;
    } catch {
      // tabel lipsă/offline — apelantul recurge la calculul din evenimente
      return [];
    }
  }

  async function fetchOwnScore() {
    const userId = typeof Auth !== "undefined" && Auth.getUserId ? Auth.getUserId() : null;
    if (!userId) return null;
    try {
      // The public leaderboard is intentionally limited. This authenticated
      // RPC reads only the caller's score, so reconciliation also works for
      // users who are outside the displayed ranking.
      const res = await fetch(`${SUPABASE_URL}/rest/v1/rpc/get_own_score`, {
        method: "POST",
        headers: await authHeaders({ "Content-Type": "application/json" }),
        body: "{}",
      });
      if (!res.ok) return null;
      const value = await res.json();
      return Number(value) || 0;
    } catch {
      return null;
    }
  }

  // Supabase derivează scorul exclusiv din evenimentele utilizatorului curent.
  // Browserul nu mai transmite niciun total de puncte care ar putea fi modificat.
  async function refreshScore() {
    if (!enabled) return false;
    if (scoreRefreshPromise) return scoreRefreshPromise;
    const userId = typeof Auth !== "undefined" && Auth.getUserId ? Auth.getUserId() : null;
    if (!userId) return false;
    scoreRefreshPromise = (async () => {
      try {
        const res = await fetch(`${SUPABASE_URL}/rest/v1/rpc/upsert_own_score`, {
          method: "POST",
          headers: await authHeaders({
            "Content-Type": "application/json",
          }),
          body: JSON.stringify({}),
        });
        return res.ok;
      } catch {
        // Offline sau eroare de rețea — următorul flush de evenimente reîncearcă.
        return false;
      }
    })().finally(() => {
      scoreRefreshPromise = null;
    });
    return scoreRefreshPromise;
  }

  const DEFAULT_LEADERBOARD_SIZE = 5;

  async function fetchConfig() {
    if (!enabled) return { leaderboard_size: DEFAULT_LEADERBOARD_SIZE };
    try {
      const res = await fetch(
        `${SUPABASE_URL}/rest/v1/app_config?select=leaderboard_size&limit=1`,
        { headers: await authHeaders() }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const rows = await res.json();
      return rows[0] || { leaderboard_size: DEFAULT_LEADERBOARD_SIZE };
    } catch {
      // tabel lipsă sau offline — clasamentul tot funcționează, cu valoarea implicită
      return { leaderboard_size: DEFAULT_LEADERBOARD_SIZE };
    }
  }

  window.addEventListener("online", () => {
    flush();
    refreshScore();
  });

  return { enabled, log, flush, refreshScore, fetchAll, fetchUserEvents, fetchScores, fetchOwnScore, fetchConfig };
})();
