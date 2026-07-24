# Dona's Insurance Lead Tool - Setup Guide

## Requirements
- Windows PC
- Python 3.10+ installed (https://python.org)
- Google Chrome installed
- Gmail App Password (see below)

---

## Step 1 - Install Python dependencies

Open Command Prompt and run:

```
cd life-income-insurance
pip install -r requirements.txt
```

---

## Step 2 - Set up Gmail App Password

1. Go to https://myaccount.google.com
2. Click **Security**
3. Under "How you sign in to Google" → enable **2-Step Verification**
4. Then go back to Security → search **App Passwords**
5. Create a new App Password for "Mail"
6. Copy the 16-character password

---

## Step 3 - Fill in your .env file

Open the `.env` file and fill in:

```
PORTAL_URL=https://planetaltig.com
PORTAL_USERNAME=donamaina
PORTAL_PASSWORD=your_portal_password_here
GMAIL_ADDRESS=donamaina@gmail.com
GMAIL_APP_PASSWORD=your_16_char_app_password_here
```

---

## Step 4 - Run the tool

```
python main.py
```

---

## How to use

1. Click **Sync Portal** → tool logs into planetaltig.com and pulls all leads
2. Use the **Filter** on the left to view leads by category:
   - **NO_POLICY** → people without insurance (best to email)
   - **POS** → existing policy owners
   - **SGLW** → union members
3. Click **Select All Emailable** to auto-select leads with no policy
4. Choose email template: Will Kit / McGruff Child Safe Kit / Plus Leads
5. Click **Send to Selected** → emails go out automatically
6. Click **Check Replies** to fetch responses from Gmail
7. Click **View Responses** to read all replies

---

## Notes
- The tool opens a Chrome window when syncing — this is normal
- All leads are saved locally in `data/leads.db`
- Never share your .env file with anyone
