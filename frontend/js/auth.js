function getToken() {
  return localStorage.getItem("token");
}

function setToken(token) {
  localStorage.setItem("token", token);
}

function clearToken() {
  localStorage.removeItem("token");
}

function authHeaders() {
  return {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${getToken()}`
  };
}

function requireAuth() {
  if (!getToken()) {
    const base = window.location.pathname.includes("/pages/") ? "../index.html" : "index.html";
    window.location.href = base;
  }
}

async function apiFetch(path, options = {}) {
  try {
    const res = await fetch(`${API}${path}`, {
      ...options,
      headers: { ...authHeaders(), ...(options.headers || {}) }
    });
    if (res.status === 401) {
      clearToken();
      const base = window.location.pathname.includes("/pages/") ? "../index.html" : "index.html";
      window.location.href = base;
    }
    return res;
  } catch (err) {
    console.error(`[apiFetch] ${path} failed:`, err);
    throw err;
  }
}
