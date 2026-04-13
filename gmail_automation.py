#!/usr/bin/env python3
"""
Gmail Automation — Job Search System
======================================
Sends job search emails using Gmail App Password (SMTP).
No Google Cloud project, no OAuth flow, no credentials.json needed.

Setup (2 minutes):
  1. Go to https://myaccount.google.com/apppasswords
  2. App name: "Job Search Bot" → Generate
  3. Copy the 16-char password into .env as GMAIL_APP_PASSWORD
  4. Done. Run commands below.

Commands:
  python gmail_automation.py check_replies          # Scan inbox for recruiter replies
  python gmail_automation.py send_outreach <id>     # Draft + send for one contact
  python gmail_automation.py batch_outreach         # Draft all 'not_contacted' contacts
  python gmail_automation.py send_followups         # Send follow-ups for overdue contacts
  python gmail_automation.py list_contacts          # Summary of all tracked contacts
"""

import imaplib
import json
import os
import smtplib
import sys
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import message_from_bytes
from pathlib import Path
from typing import Any, Dict, List, Optional

import urllib.request
import urllib.error

# ── Load .env ─────────────────────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

SENDER_EMAIL     = os.getenv("SENDER_EMAIL",      "montrez.cox@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")   # 16-char App Password
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY",    "")
GEMINI_API_URL   = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
IMAP_HOST = "imap.gmail.com"

DATA_FILE     = Path(__file__).parent / "data" / "contacts.json"
CRITERIA_FILE = Path(__file__).parent / "data" / "criteria.json"

DEFAULT_CRITERIA = {
    "worktype": "Remote Only",
    "minsalary": "$80,000",
    "roles": "Sales Engineer, Solutions Architect, Pre-Sales",
    "geo": "US-based remote preferred",
}


# ── Data Helpers ──────────────────────────────────────────────────────────────

def load_contacts() -> List[Dict[str, Any]]:
    if not DATA_FILE.exists():
        return []
    try:
        return json.loads(DATA_FILE.read_text())
    except Exception:
        return []


def save_contacts(contacts: List[Dict[str, Any]]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(contacts, indent=2, default=str))


def load_criteria() -> Dict[str, str]:
    if not CRITERIA_FILE.exists():
        return DEFAULT_CRITERIA
    try:
        return json.loads(CRITERIA_FILE.read_text())
    except Exception:
        return DEFAULT_CRITERIA


def update_contact(contact_id: str, status: str = None, note: str = "") -> bool:
    contacts = load_contacts()
    for c in contacts:
        if c["id"] == contact_id:
            if status:
                c["status"] = status
            if note:
                existing = c.get("notes") or ""
                ts = datetime.now().strftime("%m/%d %I:%M%p")
                c["notes"] = f"{existing}\n[{ts}] {note}".strip()
            c["followup"]    = (date.today() + timedelta(days=7)).isoformat()
            c["lastUpdated"] = datetime.now().isoformat()
            save_contacts(contacts)
            return True
    return False


# ── Gemini ────────────────────────────────────────────────────────────────────

def gemini_generate(prompt: str, system: str = "") -> str:
    if not GEMINI_API_KEY:
        print("⚠️  GEMINI_API_KEY not set in .env — using template email instead.")
        return ""

    contents = []
    if system:
        contents += [
            {"role": "user",  "parts": [{"text": f"[System]: {system}"}]},
            {"role": "model", "parts": [{"text": "Understood."}]},
        ]
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload = json.dumps({"contents": contents, "generationConfig": {"temperature": 0.7, "maxOutputTokens": 800}}).encode("utf-8")
    req = urllib.request.Request(
        f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gemini API error {e.code}: {e.read().decode()}") from e


def draft_email(contact: Dict[str, Any], template: str = "initial_outreach") -> Dict[str, str]:
    """Generate a personalized email for a contact using Gemini (or template fallback)."""
    crit      = load_criteria()
    company   = contact.get("company", "[Company]")
    recruiter = contact.get("contact") or "[Recruiter Name]"
    jobtitle  = contact.get("jobtitle") or "[Job Title]"

    system = (
        f"Write emails for Montrez Cox ({SENDER_EMAIL}), a tech professional targeting remote "
        f"{crit.get('roles', 'tech sales/solutions')} roles. Min salary: {crit.get('minsalary', '$80k')}. "
        f"Confident, professional, personable tone. Always put 'Subject:' on line 1."
    )

    prompts = {
        "initial_outreach": (
            f"Write a first outreach email to recruiter '{recruiter}' at {company} about a {jobtitle} role. "
            f"Body under 150 words. Mention remote preference and {crit.get('minsalary', '$80k')}+ salary. "
            f"End with a clear call to action. Include Subject line."
        ),
        "follow_up": (
            f"Write a brief follow-up to '{recruiter}' at {company} about {jobtitle}. "
            f"No response in 7+ days. Under 100 words. Warm but direct. Include Subject line."
        ),
        "criteria_statement": (
            f"Email to '{recruiter}' at {company} sharing my job criteria: remote only, "
            f"min {crit.get('minsalary', '$80k')}, roles: {crit.get('roles', '')}. "
            f"Include request to confirm company name before submitting resume. Include Subject line."
        ),
    }

    raw = gemini_generate(prompts.get(template, prompts["initial_outreach"]), system)

    # Parse subject from response, or use default
    if raw and raw.startswith("Subject:"):
        lines   = raw.split("\n", 1)
        subject = lines[0].replace("Subject:", "").strip()
        body    = lines[1].strip() if len(lines) > 1 else raw
    elif raw:
        subject = f"Exploring {jobtitle} Opportunities — Remote, US-Based"
        body    = raw
    else:
        # Fallback template (no Gemini)
        subject = f"Exploring {jobtitle} Opportunities — Remote, US-Based"
        body = (
            f"Hi {recruiter},\n\n"
            f"I came across your profile and wanted to reach out regarding {jobtitle} opportunities at {company}.\n\n"
            f"Quick overview of what I'm looking for:\n"
            f"• Work type: Remote (US-based)\n"
            f"• Salary: {crit.get('minsalary', '$80k')}+ base\n"
            f"• Roles: {crit.get('roles', 'Sales / Solutions Engineering')}\n\n"
            f"Before submitting, please confirm the company name and that no other recruiter "
            f"has already submitted me — just standard practice to avoid double-submission issues.\n\n"
            f"Happy to connect if there's a potential fit.\n\n"
            f"Best,\nMontrez Cox\n{SENDER_EMAIL}"
        )

    return {"subject": subject, "body": body, "to": contact.get("email", "")}


# ── Gmail SMTP / IMAP ─────────────────────────────────────────────────────────

def check_app_password():
    if not GMAIL_APP_PASSWORD:
        print("❌  GMAIL_APP_PASSWORD not set.")
        print("\nTo fix (2 minutes):")
        print("  1. Go to https://myaccount.google.com/apppasswords")
        print("  2. App name: 'Job Search Bot' → Generate")
        print("  3. Add to .env:  GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx")
        print("  4. Re-run this command")
        sys.exit(1)


def send_smtp(to: str, subject: str, body: str) -> None:
    """Send an email via Gmail SMTP using App Password."""
    check_app_password()
    msg = MIMEMultipart("alternative")
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
        server.sendmail(SENDER_EMAIL, to, msg.as_string())


def check_replies_imap(contacts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check Gmail inbox for replies from tracked contacts using IMAP."""
    check_app_password()
    email_sent = [c for c in contacts if c.get("status") == "email_sent" and c.get("email")]
    if not email_sent:
        print("No contacts in 'email_sent' status with email addresses.")
        return []

    print(f"Connecting to Gmail IMAP... checking {len(email_sent)} contacts")
    replies = []

    with imaplib.IMAP4_SSL(IMAP_HOST) as mail:
        mail.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
        mail.select("INBOX")

        for c in email_sent:
            addr = c.get("email", "")
            # Search for emails FROM this recruiter
            _, data = mail.search(None, f'FROM "{addr}"')
            if data[0]:
                msg_ids = data[0].split()
                latest  = msg_ids[-1]
                _, msg_data = mail.fetch(latest, "(RFC822)")
                msg = message_from_bytes(msg_data[0][1])
                replies.append({
                    "contact": c,
                    "subject": msg.get("Subject", "(no subject)"),
                    "from":    msg.get("From", ""),
                    "date":    msg.get("Date", ""),
                })

    return replies


# ── CLI Commands ──────────────────────────────────────────────────────────────

def cmd_check_replies():
    print("📬 Checking for recruiter replies...\n")
    contacts = load_contacts()
    replies  = check_replies_imap(contacts)

    if not replies:
        print("No replies found from tracked contacts.")
        return

    print(f"✅ Found {len(replies)} reply/replies:\n")
    for r in replies:
        c = r["contact"]
        print(f"  📧 {c['company']} — {r['subject']} (from {r['from']})")
        print(f"     Contact ID: {c['id']} | Date: {r['date']}")
        if input("  Mark as 'responded'? (y/n): ").lower() == "y":
            update_contact(c["id"], "responded", f"Reply received: {r['subject']}")
            print(f"  ✅ Updated to 'responded'\n")


def cmd_send_outreach(contact_id: str):
    contacts = load_contacts()
    contact  = next((c for c in contacts if c["id"] == contact_id), None)
    if not contact:
        print(f"❌ Contact '{contact_id}' not found. Run 'list_contacts' to see IDs.")
        return

    print(f"\n✍️  Drafting email for {contact['company']} ({contact.get('contact') or 'no contact'})...")
    email = draft_email(contact)

    print(f"\n{'='*60}")
    print(f"TO:      {email['to'] or '(no email on file)'}")
    print(f"SUBJECT: {email['subject']}")
    print(f"{'-'*60}")
    print(email["body"])
    print(f"{'='*60}\n")

    if not email["to"]:
        print("⚠️  No email address. Update contact in dashboard first.")
        return

    action = input("(s)end / (q)uit: ").lower().strip()
    if action == "s":
        send_smtp(email["to"], email["subject"], email["body"])
        update_contact(contact_id, "email_sent", f"Outreach sent: {email['subject']}")
        print("✅ Sent and tracker updated.")


def cmd_batch_outreach():
    contacts = load_contacts()
    targets  = [c for c in contacts if c.get("status") == "not_contacted" and c.get("email")]

    if not targets:
        print("No 'not_contacted' contacts with email addresses.")
        return

    print(f"\n📤 Ready to send outreach to {len(targets)} contacts:\n")
    for c in targets:
        print(f"  • {c['company']} — {c.get('contact')} ({c.get('email')})")

    if input(f"\nSend to all {len(targets)}? (y/n): ").lower() != "y":
        return

    sent = 0
    for c in targets:
        try:
            email = draft_email(c)
            send_smtp(email["to"], email["subject"], email["body"])
            update_contact(c["id"], "email_sent", f"Batch outreach sent: {email['subject']}")
            print(f"  ✅ Sent to {c['company']}")
            sent += 1
        except Exception as e:
            print(f"  ❌ Failed for {c['company']}: {e}")

    print(f"\n✅ Sent {sent}/{len(targets)} emails.")


def cmd_send_followups():
    contacts = load_contacts()
    overdue  = []
    for c in contacts:
        if c.get("status") != "email_sent" or not c.get("email") or not c.get("followup"):
            continue
        try:
            fu   = datetime.fromisoformat(c["followup"]).date()
            diff = (fu - date.today()).days
            if diff < 0:
                overdue.append(c)
        except Exception:
            continue

    if not overdue:
        print("No overdue 'email_sent' contacts to follow up with.")
        return

    print(f"\n🔁 {len(overdue)} overdue follow-ups:\n")
    for c in overdue:
        print(f"  • {c['company']} — {c.get('contact')} ({c.get('email')})")

    if input(f"\nSend follow-ups to all {len(overdue)}? (y/n): ").lower() != "y":
        return

    sent = 0
    for c in overdue:
        try:
            email = draft_email(c, template="follow_up")
            send_smtp(email["to"], email["subject"], email["body"])
            update_contact(c["id"], note=f"Follow-up sent: {email['subject']}")
            print(f"  ✅ Follow-up sent to {c['company']}")
            sent += 1
        except Exception as e:
            print(f"  ❌ Failed for {c['company']}: {e}")

    print(f"\n✅ Sent {sent}/{len(overdue)} follow-ups.")


def cmd_list_contacts():
    contacts = load_contacts()
    if not contacts:
        print("No contacts yet.")
        return
    from collections import Counter
    counts = Counter(c.get("status", "not_contacted") for c in contacts)
    print(f"\n📋 {len(contacts)} total contacts")
    for s, n in counts.most_common():
        print(f"   {s}: {n}")
    print()
    for c in contacts[:25]:
        print(f"  [{c['id']}] {c['company']} — {c.get('contact') or '—'} | {c.get('status')} | FU: {c.get('followup') or '—'}")
    if len(contacts) > 25:
        print(f"  ... and {len(contacts) - 25} more")


# ── Main ──────────────────────────────────────────────────────────────────────

COMMANDS = {
    "check_replies":  cmd_check_replies,
    "batch_outreach": cmd_batch_outreach,
    "send_followups": cmd_send_followups,
    "list_contacts":  cmd_list_contacts,
}

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        print("Commands:", ", ".join(list(COMMANDS.keys()) + ["send_outreach <id>"]))
        sys.exit(0)

    cmd = args[0]
    if cmd == "send_outreach":
        if len(args) < 2:
            print("Usage: python gmail_automation.py send_outreach <contact_id>")
            sys.exit(1)
        cmd_send_outreach(args[1])
    elif cmd in COMMANDS:
        COMMANDS[cmd]()
    else:
        print(f"Unknown command: {cmd}")
        print("Commands:", ", ".join(list(COMMANDS.keys()) + ["send_outreach <id>"]))
        sys.exit(1)
