import customtkinter as ctk
import threading
from tkinter import ttk, messagebox
import tkinter as tk
from scraper import run_scraper
from categorizer import categorize_all_leads, get_leads_by_category, get_emailable_leads
from emailer import send_bulk_emails, check_replies
from database import init_db

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Dona's Insurance Lead Tool")
        self.geometry("1200x750")
        self.resizable(True, True)
        init_db()
        self.selected_leads = []
        self.build_ui()

    def build_ui(self):
        # Sidebar
        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar.pack(side="left", fill="y")

        ctk.CTkLabel(self.sidebar, text="🛡️ Dona's Tool", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=30)

        self.btn_leads = ctk.CTkButton(self.sidebar, text="📋 Leads", command=self.show_leads)
        self.btn_leads.pack(pady=8, padx=20, fill="x")

        self.btn_scrape = ctk.CTkButton(self.sidebar, text="🔄 Sync Portal", command=self.run_scrape)
        self.btn_scrape.pack(pady=8, padx=20, fill="x")

        self.btn_replies = ctk.CTkButton(self.sidebar, text="📬 Check Replies", command=self.run_check_replies)
        self.btn_replies.pack(pady=8, padx=20, fill="x")

        self.btn_replies_view = ctk.CTkButton(self.sidebar, text="📥 View Responses", command=self.show_responses)
        self.btn_replies_view.pack(pady=8, padx=20, fill="x")

        ctk.CTkLabel(self.sidebar, text="Filter by Category", font=ctk.CTkFont(size=12)).pack(pady=(30, 5))
        self.category_var = ctk.StringVar(value="ALL")
        for cat in ["ALL", "NO_POLICY", "POS", "SGLW"]:
            ctk.CTkRadioButton(self.sidebar, text=cat, variable=self.category_var, value=cat, command=self.show_leads).pack(anchor="w", padx=25, pady=2)

        # Main content
        self.main = ctk.CTkFrame(self)
        self.main.pack(side="right", fill="both", expand=True, padx=10, pady=10)

        # Status bar
        self.status_var = tk.StringVar(value="Ready.")
        self.status_bar = ctk.CTkLabel(self, textvariable=self.status_var, anchor="w", font=ctk.CTkFont(size=12))
        self.status_bar.pack(side="bottom", fill="x", padx=10, pady=4)

        self.show_leads()

    def clear_main(self):
        for widget in self.main.winfo_children():
            widget.destroy()

    def set_status(self, msg):
        self.status_var.set(f"  {msg}")
        self.update_idletasks()

    def show_leads(self):
        self.clear_main()
        cat = self.category_var.get()
        leads = get_leads_by_category(None if cat == "ALL" else cat)

        # Top bar
        top = ctk.CTkFrame(self.main)
        top.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(top, text=f"Leads ({len(leads)})", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=10)

        ctk.CTkLabel(top, text="Email Template:").pack(side="left", padx=(20, 5))
        self.template_var = ctk.StringVar(value="Will Kit")
        template_menu = ctk.CTkOptionMenu(top, variable=self.template_var, values=["Will Kit", "McGruff Child Safe Kit", "Plus Leads"])
        template_menu.pack(side="left")

        ctk.CTkButton(top, text="📧 Send to Selected", command=self.send_selected).pack(side="left", padx=10)
        ctk.CTkButton(top, text="✅ Select All Emailable", command=self.select_emailable).pack(side="left", padx=5)

        # Table
        frame = ctk.CTkFrame(self.main)
        frame.pack(fill="both", expand=True)

        cols = ("Select", "Name", "Email", "Phone", "Category", "Policy Status", "Emailed", "Response")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=25)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#2b2b2b", foreground="white", fieldbackground="#2b2b2b", rowheight=28)
        style.configure("Treeview.Heading", background="#1f538d", foreground="white", font=("Arial", 10, "bold"))
        style.map("Treeview", background=[("selected", "#1f538d")])

        col_widths = {"Select": 60, "Name": 160, "Email": 200, "Phone": 120, "Category": 100, "Policy Status": 130, "Emailed": 80, "Response": 90}
        for col in cols:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=col_widths.get(col, 100), anchor="center")

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.lead_map = {}
        self.checked = {}

        for lead in leads:
            emailed = "✅" if lead["email_sent"] else "❌"
            response = "✅" if lead["response_received"] else "—"
            iid = self.tree.insert("", "end", values=(
                "☐", lead.get("full_name", ""), lead.get("email", ""),
                lead.get("phone", ""), lead.get("category", ""),
                lead.get("policy_status", ""), emailed, response
            ))
            self.lead_map[iid] = lead
            self.checked[iid] = False

        self.tree.bind("<ButtonRelease-1>", self.toggle_check)

    def toggle_check(self, event):
        region = self.tree.identify("column", event.x, event.y)
        if region == "#1":
            iid = self.tree.identify_row(event.y)
            if iid:
                self.checked[iid] = not self.checked[iid]
                mark = "☑" if self.checked[iid] else "☐"
                vals = list(self.tree.item(iid, "values"))
                vals[0] = mark
                self.tree.item(iid, values=vals)

    def select_emailable(self):
        emailable = get_emailable_leads()
        emailable_emails = {l["email"] for l in emailable}
        for iid, lead in self.lead_map.items():
            if lead.get("email") in emailable_emails:
                self.checked[iid] = True
                vals = list(self.tree.item(iid, "values"))
                vals[0] = "☑"
                self.tree.item(iid, values=vals)
        self.set_status(f"{len(emailable_emails)} emailable leads selected.")

    def send_selected(self):
        selected = [self.lead_map[iid] for iid, checked in self.checked.items() if checked]
        if not selected:
            messagebox.showwarning("No Selection", "Please select at least one lead.")
            return
        template = self.template_var.get()
        confirm = messagebox.askyesno("Confirm", f"Send '{template}' to {len(selected)} leads?")
        if not confirm:
            return
        self.set_status(f"Sending emails...")
        threading.Thread(target=self._send_thread, args=(selected, template), daemon=True).start()

    def _send_thread(self, leads, template):
        sent, failed = send_bulk_emails(leads, template, self.set_status)
        self.set_status(f"Done! ✅ Sent: {sent} | ❌ Failed: {failed}")
        self.show_leads()

    def run_scrape(self):
        self.set_status("Syncing portal... please wait.")
        threading.Thread(target=self._scrape_thread, daemon=True).start()

    def _scrape_thread(self):
        count = run_scraper(self.set_status)
        categorize_all_leads(self.set_status)
        self.set_status(f"Sync complete! {count} new leads added.")
        self.show_leads()

    def run_check_replies(self):
        self.set_status("Checking Gmail for replies...")
        threading.Thread(target=self._replies_thread, daemon=True).start()

    def _replies_thread(self):
        replies = check_replies(self.set_status)
        self.set_status(f"Found {len(replies)} new replies.")
        self.show_responses()

    def show_responses(self):
        self.clear_main()
        ctk.CTkLabel(self.main, text="📥 Responses", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)

        from database import get_connection
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT full_name, email, response_text, date_responded FROM leads WHERE response_received = 1")
        rows = c.fetchall()
        conn.close()

        if not rows:
            ctk.CTkLabel(self.main, text="No responses yet.", font=ctk.CTkFont(size=14)).pack(pady=40)
            return

        frame = ctk.CTkScrollableFrame(self.main)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        for name, em, text, date in rows:
            card = ctk.CTkFrame(frame)
            card.pack(fill="x", pady=6, padx=5)
            ctk.CTkLabel(card, text=f"👤 {name}  |  ✉️ {em}  |  📅 {date}", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=10, pady=5)
            ctk.CTkLabel(card, text=text or "No content", wraplength=800, justify="left").pack(anchor="w", padx=15, pady=(0, 8))


if __name__ == "__main__":
    app = App()
    app.mainloop()
