/* Urmărirea activității: coadă offline în localStorage + trimitere batch la Supabase.
   Dacă js/config.js nu are chei, totul devine no-op și aplicația merge ca înainte. */

const Tracker = (() => {
  const QUEUE_KEY = "ci_pending_events";
  const enabled =
    typeof SUPABASE_URL === "string" &&
    SUPABASE_URL.startsWith("https://") &&
    typeof SUPABASE_ANON_KEY === "string" &&
    SUPABASE_ANON_KEY.length > 0;
  let flushPromise = null;
  let scoreRefreshPromise = null;

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

  function readQueue() {
    try {
      return JSON.parse(localStorage.getItem(QUEUE_KEY)) || [];
    } catch {
      return [];
    }
  }

  function writeQueue(q) {
    localStorage.setItem(QUEUE_KEY, JSON.stringify(q));
  }

  function log(evt) {
    if (!enabled) return;
    const q = readQueue();
    // păstrează momentul real al răspunsului, chiar dacă trimiterea se face mai târziu
    q.push({ ...evt, created_at: new Date().toISOString() });
    writeQueue(q);
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
      while (true) {
        const batch = readQueue();
        if (batch.length === 0) return sentAny;
        const userId = typeof Auth !== "undefined" && Auth.getUserId ? Auth.getUserId() : null;
        if (!userId) return sentAny;
        const payload = batch.map((evt) => ({ ...evt, user_id: userId }));
        try {
          const res = await fetch(`${SUPABASE_URL}/rest/v1/events`, {
            method: "POST",
            headers: await authHeaders({
              "Content-Type": "application/json",
              Prefer: "return=minimal",
            }),
            body: JSON.stringify(payload),
          });
          if (!res.ok) return sentAny;
          // Evenimente adăugate în timpul trimiterii sunt procesate în următoarea
          // iterație înainte ca apelantul să poată citi scorul serverului.
          writeQueue(readQueue().slice(batch.length));
          sentAny = true;
          await refreshScore();
        } catch {
          // Offline sau eroare de rețea — coada rămâne pentru următoarea încercare.
          return sentAny;
        }
      }
    })().finally(() => {
      flushPromise = null;
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
  async function fetchScores() {
    if (!enabled) return [];
    try {
      const res = await fetch(
        `${SUPABASE_URL}/rest/v1/rpc/get_public_leaderboard`,
        {
          method: "POST",
          headers: await authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ p_limit: 1000 }),
        }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch {
      // tabel lipsă/offline — apelantul recurge la calculul din evenimente
      return [];
    }
  }

  async function fetchOwnScore() {
    const userName = typeof Auth !== "undefined" && Auth.currentUser ? Auth.currentUser() : null;
    if (!userName) return null;
    const userId = typeof Auth !== "undefined" && Auth.getUserId ? Auth.getUserId() : null;
    const scores = await fetchScores();
    // Potrivire după user_id (distinge conturi cu același nume afișat);
    // recade pe nume doar dacă RPC-ul e neactualizat și nu trimite user_id.
    const target = userName.toLocaleLowerCase("ro-RO");
    const row = userId
      ? scores.find((entry) => entry.user_id === userId)
      : scores.find((entry) => String(entry.user_name || "").toLocaleLowerCase("ro-RO") === target);
    return row ? Number(row.points) || 0 : null;
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
