function renderNav(active) {
  const links = [
    { href: "dashboard.html", label: "Dashboard" },
    { href: "leads.html", label: "All Leads" },
    { href: "leads.html?category=NO_POLICY", label: "No Policy" },
    { href: "leads.html?category=POS", label: "POS" },
    { href: "leads.html?category=SGLW", label: "SGLW" },
    { href: "leads.html?category=ACTIVE", label: "Active Policy" },
    { href: "responses.html", label: "Responses" },
    { href: "settings.html", label: "Settings" },
  ];

  const nav = links.map(l => `
    <a href="${l.href}" class="nav-link ${l.label === active ? 'active' : ''}">
      ${l.label}
    </a>
  `).join("");

  document.body.insertAdjacentHTML("afterbegin", `
    <nav class="sidebar">
      <div class="logo">Dona's Tool</div>
      ${nav}
      <div class="sidebar-bottom">
        <a href="#" class="nav-link logout" onclick="logout()">Logout</a>
      </div>
    </nav>
    <main class="with-sidebar">
      <div class="topbar" id="status-bar">Ready.</div>
      <div id="page-content"></div>
    </main>
  `);
}

function logout() {
  localStorage.removeItem("token");
  window.location.href = "../index.html";
}
