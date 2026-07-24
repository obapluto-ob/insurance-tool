from database import get_connection

# Exact values from planetaltig.com portal lead_tags column
POS_KEYWORDS = ["pos", "policy owner", "policy owner services"]
SGLW_KEYWORDS = ["sglw", "union", "union member"]

# Emailable categories and which templates apply
EMAILABLE_TEMPLATES = {
    "NO_POLICY": ["Will Kit", "McGruff Child Safe Kit", "Plus Leads"],
    "POS":       ["Will Kit", "Plus Leads"],
    "SGLW":      ["Will Kit", "McGruff Child Safe Kit", "Plus Leads"],
}


def categorize_lead(policy_status: str) -> str:
    status = (policy_status or "").lower().strip()

    for kw in SGLW_KEYWORDS:
        if kw in status:
            return "SGLW"

    for kw in POS_KEYWORDS:
        if kw in status:
            return "POS"

    # Everything else — no policy / unknown
    return "NO_POLICY"


def get_allowed_templates(category: str) -> list:
    return EMAILABLE_TEMPLATES.get(category, [])


def categorize_all_leads(status_callback=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, policy_status FROM leads")
    rows = c.fetchall()

    counts = {"NO_POLICY": 0, "POS": 0, "SGLW": 0, "UNKNOWN": 0}
    missing_fields = 0

    for lead_id, policy_status in rows:
        if not policy_status or policy_status.strip() == "" or policy_status.strip() == "Unknown":
            category = "NO_POLICY"
            missing_fields += 1
        else:
            category = categorize_lead(policy_status)

        counts[category] = counts.get(category, 0) + 1
        c.execute("UPDATE leads SET category=? WHERE id=?", (category, lead_id))

    conn.commit()
    conn.close()

    total = sum(counts.values())
    if status_callback:
        status_callback(f"Categorized {total} leads — NO_POLICY: {counts['NO_POLICY']} | POS: {counts['POS']} | SGLW: {counts['SGLW']}")
        if missing_fields:
            status_callback(f"Note: {missing_fields} leads had missing/unknown policy field — saved as NO_POLICY")

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
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM leads
        WHERE category IN ('NO_POLICY', 'POS', 'SGLW')
        AND email_sent = 0
        AND email != ''
        AND email IS NOT NULL
    """)
    columns = [desc[0] for desc in c.description]
    rows = c.fetchall()
    conn.close()
    return [dict(zip(columns, row)) for row in rows]


if __name__ == "__main__":
    categorize_all_leads(print)
