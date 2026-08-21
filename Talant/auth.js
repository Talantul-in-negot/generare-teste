/* Autentificare cu nume de utilizator și parolă prin Supabase Auth. */
const Auth = (() => {
  const DOMAIN = '@talant.app';
  let clientInstance = null;
  let session = null;
  let onChange = null;

  function client() {
    if (!clientInstance && typeof supabase !== 'undefined') {
      clientInstance = supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
        auth: { persistSession: true, storageKey: 'talant_auth' },
      });
    }
    return clientInstance;
  }

  function normalizeUsername(value) {
    return String(value || '').trim().replace(/\s+/g, ' ').slice(0, 30);
  }

  function toEmail(username) {
    const value = normalizeUsername(username).toLocaleLowerCase('ro-RO').replace(/\s/g, '_');
    // Formularul este bazat pe nume de utilizator, însă acceptăm și emailul
    // intern complet pentru conturile create manual din Supabase Dashboard.
    return value.includes('@') ? value : value + DOMAIN;
  }

  function displayName(user) {
    const name = normalizeUsername(user?.user_metadata?.username || (user?.email || '').replace(DOMAIN, ''));
    // Afișăm consecvent numele cu inițială mare, indiferent cum a fost creat
    // contul în formular sau direct în Supabase.
    return name ? name.slice(0, 1).toLocaleUpperCase('ro-RO') + name.slice(1) : null;
  }

  function userFriendlyError(message) {
    if (message?.includes('Invalid login') || message?.includes('invalid_credentials')) return 'Nume sau parolă incorectă.';
    if (message?.includes('already registered') || message?.includes('already been registered') || message?.includes('duplicate key') || message?.includes('users_email_partial_key')) return 'Acest nume de utilizator este deja folosit. Încearcă să intri în cont sau alege alt nume.';
    if (message?.includes('email not confirmed') || message?.includes('Email not confirmed')) return 'Acest cont nu este confirmat în Supabase. Verifică să folosești exact același nume sau email cu care a fost creat contul.';
    if (message?.includes('Password should')) return 'Parola trebuie să aibă cel puțin 6 caractere.';
    if (message?.includes('rate limit')) return 'Prea multe încercări. Încearcă mai târziu.';
    return message || 'Eroare necunoscută.';
  }

  async function init(callback) {
    onChange = callback;
    const c = client();
    if (!c) return null;
    const { data } = await c.auth.getSession();
    session = data.session;
    c.auth.onAuthStateChange((_, nextSession) => {
      session = nextSession;
      onChange?.(currentUser());
    });
    return currentUser();
  }

  async function signIn(username, password) {
    const c = client();
    if (!c) throw new Error('Serviciul de autentificare nu este disponibil.');
    const { data, error } = await c.auth.signInWithPassword({ email: toEmail(username), password });
    if (error) throw new Error(userFriendlyError(error.message));
    session = data.session;
    return currentUser();
  }

  async function signUp(username, password) {
    const name = normalizeUsername(username);
    if (name.length < 2) throw new Error('Numele trebuie să aibă cel puțin 2 caractere.');
    if (password.length < 6) throw new Error('Parola trebuie să aibă cel puțin 6 caractere.');
    const c = client();
    if (!c) throw new Error('Serviciul de autentificare nu este disponibil.');
    const { data, error } = await c.auth.signUp({
      email: toEmail(name), password, options: { data: { username: name } },
    });
    if (error) throw new Error(userFriendlyError(error.message));
    if (data.user && !data.session) throw new Error('Confirmarea prin email este activă în configurația Supabase.');
    session = data.session;
    return currentUser();
  }

  async function signOut() {
    await client()?.auth.signOut();
    session = null;
  }

  async function accessToken() {
    const { data } = await client()?.auth.getSession() || {};
    return data?.session?.access_token || null;
  }

  function currentUser() { return displayName(session?.user); }
  function userId() { return session?.user?.id || null; }
  return { init, signIn, signUp, signOut, accessToken, currentUser, userId };
})();
