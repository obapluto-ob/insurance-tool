from database import get_connection

# Exact lead_type values from planetaltig.com portal
POS_KEYWORDS = ["pos"]
SGLW_KEYWORDS = ["union"]
# These are prospects to email — no policy yet
PROSPECT_KEYWORDS = ["will kit", "mcgruff", "childsafe", "plus lead", "new address", "appointment"]

EMAILABLE_TEMPLATES = {
    "NO_POLICY": ["Will Kit", "McGruff Child Safe Kit", "Plus Leads"],
    "POS":       ["Will Kit", "Plus Leads"],
    "SGLW":      ["Will Kit", "McGruff Child Safe Kit", "Plus Leads"],
}


def categorize_lead(policy_status: str) -> str:
    status = (policy_status or "").lower().strip()

    for kw in POS_KEYWORDS:
        if kw in status:
            return "POS"

    for kw in SGLW_KEYWORDS:
        if kw in status:
            return "SGLW"

    # Anything with appointment/will kit/plus lead/mcgruff = prospect = NO_POLICY
    for kw in PROSPECT_KEYWORDS:
        if kw in status:
            return "NO_POLICY"

    return "NO_POLICY"


def get_allowed_templates(category: str) -> list:
    return EMAILABLE_TEMPLATES.get(category, [])


def categorize_all_leads(status_callback=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, policy_status FROM leads")
    rows = c.fetchall()

    counts = {"NO_POLICY": 0, "POS": 0, "SGLW": 0}

    for lead_id, policy_status in rows:
        category = categorize_lead(policy_status or "")
        counts[category] = counts.get(category, 0) + 1
        c.execute("UPDATE leads SET category=? WHERE id=?", (category, lead_id))

    conn.commit()
    conn.close()

    total = sum(counts.values())
    if status_callback:
        status_callback(f"Categorized {total} leads — NO_POLICY: {counts['NO_POLICY']} | POS: {counts['POS']} | SGLW: {counts['SGLW']}")

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
