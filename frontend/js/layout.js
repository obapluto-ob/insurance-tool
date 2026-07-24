function renderNav(active) {
  const links = [
    { href: "dashboard.html", icon: "📊", label: "Dashboard" },
    { href: "leads.html", icon: "📋", label: "All Leads" },
    { href: "leads.html?category=NO_POLICY", icon: "🔴", label: "No Policy" },
    { href: "leads.html?category=POS", icon: "🟡", label: "POS" },
    { href: "leads.html?category=SGLW", icon: "🔵", label: "SGLW" },
    { href: "responses.html", icon: "📥", label: "Responses" },
    { href: "settings.html", icon: "⚙️", label: "Settings" },
  ];

  const nav = links.map(l => `
    <a href="${l.href}" class="nav-link ${l.label === active ? 'active' : ''}">
      ${l.icon} ${l.label}
    </a>
  `).join("");

  document.body.insertAdjacentHTML("afterbegin", `
    <nav class="sidebar">
      <div class="logo">🛡️ Dona's Tool</div>
      ${nav}
      <div class="sidebar-bottom">
        <a href="#" class="nav-link logout" onclick="logout()">🚪 Logout</a>
      </div>
    </nav>
    <main class="with-sidebar">
      <div class="topbar" id="status-bar">⏳ Ready.</div>
      <div id="page-content"></div>
    </main>
  `);

  setInterval(() => {
    apiFetch("/api/status").then(r => r.json()).then(d => {
      document.getElementById("status-bar").textContent = d.message;
    }).catch(() => {});
  }, 2000);
}

function logout() {
  localStorage.removeItem("token");
  window.location.href = "../index.html";
}
