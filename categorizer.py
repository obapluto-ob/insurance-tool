from database import get_connection

NO_POLICY_KEYWORDS = ["no policy", "none", "uninsured", "not insured", "no coverage", "prospect", "new"]
POS_KEYWORDS = ["pos", "policy owner", "policy owner services", "existing", "active policy", "insured"]
SGLW_KEYWORDS = ["sglw", "union", "union member", "labor union"]


def categorize_lead(policy_status: str) -> str:
    status = policy_status.lower().strip() if policy_status else ""

    for kw in SGLW_KEYWORDS:
        if kw in status:
            return "SGLW"

    for kw in POS_KEYWORDS:
        if kw in status:
            return "POS"

    for kw in NO_POLICY_KEYWORDS:
        if kw in status:
            return "NO_POLICY"

    return "NO_POLICY"


def categorize_all_leads(status_callback=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, policy_status FROM leads")
    rows = c.fetchall()

    updated = 0
    for lead_id, policy_status in rows:
        category = categorize_lead(policy_status)
        c.execute("UPDATE leads SET category = ? WHERE id = ?", (category, lead_id))
        updated += 1

    conn.commit()
    conn.close()

    if status_callback:
        status_callback(f"Categorized {updated} leads.")
    return updated


def get_leads_by_category(category=None):
    conn = get_connection()
    c = conn.cursor()

    if category:
        c.execute("SELECT * FROM leads WHERE category = ?", (category,))
    else:
        c.execute("SELECT * FROM leads")

    columns = [desc[0] for desc in c.description]
    rows = c.fetchall()
    conn.close()

    return [dict(zip(columns, row)) for row in rows]


def get_emailable_leads():
    """Returns leads with no active policy and not yet emailed"""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM leads
        WHERE category = 'NO_POLICY'
        AND email_sent = 0
        AND email != ''
    """)
    columns = [desc[0] for desc in c.description]
    rows = c.fetchall()
    conn.close()
    return [dict(zip(columns, row)) for row in rows]


if __name__ == "__main__":
    categorize_all_leads(print)
