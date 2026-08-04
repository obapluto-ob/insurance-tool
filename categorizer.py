from database import get_connection

# Keyword rules — checked in order, first match wins
# Each entry: (keywords_any_of, category)
_RULES = [
    # ACTIVE — has a live upcoming appointment, do not SMS
    (["upcoming appointment"],                          "ACTIVE"),

    # POS — existing policy owner
    (["pos", "policy owner", "existing policy",
      "current policy", "policy holder"],               "POS"),

    # SGLW — union / labour group members
    (["union", "sglw", "labour", "labor",
      "guild", "association member", "local "],          "SGLW"),

    # NO_POLICY — no insurance, needs outreach
    (["will kit", "mcgruff", "childsafe", "child safe",
      "plus lead", "no policy", "no appointment",
      "past appointment", "new address",
      "no insurance", "uninsured"],                     "NO_POLICY"),
]

# Who can be SMS'd and with what template
SMSABLE_TEMPLATES = {
    "NO_POLICY": ["Will Kit", "McGruff Child Safe Kit", "Plus Leads"],
    "POS":       ["Will Kit", "Plus Leads"],
    "SGLW":      ["Will Kit", "McGruff Child Safe Kit", "Plus Leads"],
    # ACTIVE = has upcoming appointment — never SMS
}

CATEGORY_LABELS = {
    "NO_POLICY": "No Policy",
    "POS":       "Policy Owner",
    "SGLW":      "Union Member",
    "ACTIVE":    "Active Appt",
}


def categorize_lead(policy_status: str) -> str:
    status = (policy_status or "").lower().strip()
    for keywords, category in _RULES:
        if any(kw in status for kw in keywords):
            return category
    return "NO_POLICY"  # default — needs outreach


def get_allowed_templates(category: str) -> list:
    return SMSABLE_TEMPLATES.get(category, [])


def categorize_all_leads(status_callback=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, policy_status FROM leads")
    rows = c.fetchall()

    counts = {"NO_POLICY": 0, "POS": 0, "SGLW": 0, "ACTIVE": 0}

    for lead_id, policy_status in rows:
        category = categorize_lead(policy_status or "")
        counts[category] = counts.get(category, 0) + 1
        c.execute("UPDATE leads SET category=? WHERE id=?", (category, lead_id))

    conn.commit()
    conn.close()

    total = sum(counts.values())
    if status_callback:
        status_callback(f"Categorized {total} leads — NO_POLICY: {counts['NO_POLICY']} | POS: {counts['POS']} | SGLW: {counts['SGLW']} | ACTIVE: {counts['ACTIVE']}")

    return total


def get_leads_by_category(category=None):
    conn = get_connection()
    c = conn.cursor()
    if category:
        c.execute("SELECT * FROM leads WHERE category=?", (category,))
    else:
        c.execute("SELECT * FROM leads")
    columns = [desc[0] for desc in c.description]
    rows = c.fetchall()
    conn.close()
    return [dict(zip(columns, row)) for row in rows]


def get_emailable_leads():
    """Returns leads that should be emailed:
    - Has email address
    - Not ACTIVE (has appointment)
    - No response yet
    - Either never emailed OR last emailed 3+ days ago (follow-up)
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM leads
        WHERE category IN ('NO_POLICY', 'POS', 'SGLW')
        AND response_received = 0
        AND email != ''
        AND email IS NOT NULL
        AND (
            email_sent = 0
            OR (
                last_emailed_date IS NOT NULL
                AND julianday('now') - julianday(last_emailed_date) >= 3
            )
        )
    """)
    columns = [desc[0] for desc in c.description]
    rows = c.fetchall()
    conn.close()
    return [dict(zip(columns, row)) for row in rows]


if __name__ == "__main__":
    categorize_all_leads(print)
