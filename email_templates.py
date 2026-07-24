TEMPLATES = {
    "Will Kit": {
        "subject": "Your Free Will Kit – Protect Your Family Today",
        "body": """Dear {name},

My name is Dona Maina, and I'm reaching out because I want to make sure your family is protected.

I'd love to send you a FREE Will Kit — a simple but powerful tool that helps you organize your final wishes, protect your loved ones, and give your family peace of mind.

This is completely free with no obligation. Many families don't realize how important it is to have these documents in place until it's too late.

To receive your free Will Kit, simply reply to this email or give me a call and I'll get it out to you right away.

Warm regards,
Dona Maina
Life & Income Insurance Specialist
{gmail}
"""
    },

    "McGruff Child Safe Kit": {
        "subject": "Free McGruff Child Safe Kit for Your Family",
        "body": """Dear {name},

As a parent or guardian, keeping your children safe is your top priority — and I want to help.

I'm offering a FREE McGruff Child Safe Kit, which includes important tools to help identify and protect your child in case of an emergency. This kit includes fingerprinting materials, ID cards, and safety tips.

There is absolutely no cost and no obligation. This is our way of giving back to families in our community.

Reply to this email or call me directly to claim your free kit today.

Warm regards,
Dona Maina
Life & Income Insurance Specialist
{gmail}
"""
    },

    "Plus Leads": {
        "subject": "Exclusive Coverage Options Available for You",
        "body": """Dear {name},

I hope this message finds you well. My name is Dona Maina and I specialize in life and income protection insurance.

I'm reaching out because I have some exclusive coverage options that may be a perfect fit for you and your family. Whether you're looking for life insurance, income protection, or supplemental benefits — I can help find the right plan at the right price.

I'd love to schedule a quick 10-minute call to walk you through your options. There's no pressure and no obligation — just honest information to help you make the best decision for your family.

Reply to this email or call me and let's talk!

Warm regards,
Dona Maina
Life & Income Insurance Specialist
{gmail}
"""
    }
}


def get_template(template_name: str, name: str, gmail: str) -> dict:
    template = TEMPLATES.get(template_name)
    if not template:
        return None
    return {
        "subject": template["subject"],
        "body": template["body"].format(name=name, gmail=gmail)
    }
