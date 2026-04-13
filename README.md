# Job Search Automation System — Montrez Cox
> Goal: 100 contacts → Weekly check-in → Automated emails → Remote role secured

---

## What's In This System

| File | What It Does |
|------|-------------|
| `job_search_mcp.py` | MCP server — AI tools, contact management, weekly check-in automation |
| `gmail_automation.py` | Command-line Gmail integration — draft & send outreach, check for replies |
| `requirements.txt` | Python dependencies |
| `.env` | Your API keys (already has Gemini key) |
| `data/contacts.json` | Local contact database (auto-created on first run) |
| `data/criteria.json` | Your job search criteria (auto-created on first run) |

The **dashboard** (`../job-search-dashboard.html`) is a standalone browser app with AI tools built in.

---

## Quick Start

### 1. Install dependencies
```bash
cd job_search_system
pip install -r requirements.txt
```

### 2. Verify your .env file
```bash
cat .env
# GEMINI_API_KEY should already be set
# Add LINKEDIN_MCP_KEY when you get it from Charles
```

### 3. Run the MCP server
```bash
python job_search_mcp.py
```

### 4. Test it with the MCP Inspector (optional)
```bash
npx @modelcontextprotocol/inspector python job_search_mcp.py
```

---

## MCP Server Tools Reference

### Contact Management
| Tool | What It Does |
|------|-------------|
| `job_search_add_contact` | Add a recruiter/company to the tracker |
| `job_search_update_contact` | Update status, notes, follow-up date |
| `job_search_list_contacts` | List all contacts with filters |
| `job_search_get_followups` | Get overdue/due-today/this-week follow-ups |

### AI Tools (Powered by Gemini)
| Tool | What It Does |
|------|-------------|
| `job_search_tailor_resume` | Paste job description → get tailored bullets + keywords |
| `job_search_draft_email` | Generate personalized outreach for any contact/template |
| `job_search_qualify_job` | Score a job posting against your criteria (PASS/FAIL/MAYBE) |

### Weekly Workflow
| Tool | What It Does |
|------|-------------|
| `job_search_weekly_checkin` | Full weekly summary: stats, overdue, action list |
| `job_search_get_criteria` | View your current search criteria |
| `job_search_update_criteria` | Update non-negotiables and preferences |

### LinkedIn (Ready when key arrives)
| Tool | What It Does |
|------|-------------|
| `linkedin_search_jobs` | Search LinkedIn jobs — activate with LINKEDIN_MCP_KEY |
| `linkedin_find_recruiters` | Find recruiters by role type — activate with LINKEDIN_MCP_KEY |

---

## Gmail Automation Commands

```bash
# First-time setup — authorizes your Gmail account
python gmail_automation.py auth

# Check if any tracked contacts have replied to your emails
python gmail_automation.py check_replies

# Draft + optionally send outreach for one contact
python gmail_automation.py send_outreach <contact_id>

# Create drafts for ALL 'not_contacted' contacts at once
python gmail_automation.py batch_outreach

# Draft follow-ups for all overdue email_sent contacts
python gmail_automation.py send_followups

# Quick view of all tracked contacts
python gmail_automation.py list_contacts
```

### Gmail Setup (one-time)
1. Go to https://console.cloud.google.com
2. Create a project → Enable **Gmail API**
3. Create OAuth 2.0 credentials (Desktop app)
4. Download → save as `credentials.json` in this folder
5. Run `python gmail_automation.py auth` — browser window opens, sign in
6. You're set — token saved to `data/gmail_token.json`

---

## Adding LinkedIn MCP Key

When Charles gives you the key:

```bash
# Add to .env file
echo "LINKEDIN_MCP_KEY=your_key_here" >> .env

# Restart the MCP server
python job_search_mcp.py
```

Then `linkedin_search_jobs` and `linkedin_find_recruiters` activate automatically.

---

## Weekly Check-In Workflow (60 min/week)

Every Monday morning, run this sequence:

```bash
# 1. Start MCP server (if not running)
python job_search_mcp.py

# 2. Check for Gmail replies
python gmail_automation.py check_replies

# 3. In Claude/your MCP client, run:
#    job_search_weekly_checkin  →  see your full action list
#    job_search_get_followups urgency=overdue  →  see what's overdue
#    job_search_draft_email + send_outreach for follow-ups

# 4. Send batch outreach for new contacts
python gmail_automation.py batch_outreach
```

---

## Data Sync Between Dashboard and MCP Server

The dashboard (`job-search-dashboard.html`) stores data in browser localStorage.
The MCP server uses `data/contacts.json`.

**To sync from dashboard → MCP server:**
1. In the dashboard, open DevTools (F12) → Console
2. Run: `copy(localStorage.getItem('jsc_montrez_2026'))`
3. Paste into `data/contacts.json`

**To sync from MCP server → dashboard:**
1. Copy contents of `data/contacts.json`
2. In DevTools Console: `localStorage.setItem('jsc_montrez_2026', '<paste here>')`
3. Refresh the dashboard

> TODO: Add an import/export button to the dashboard for one-click sync (next iteration).

---

## Architecture Overview

```
                        ┌─────────────────┐
                        │  Job Search     │
                        │  Dashboard.html │  ← Use daily in browser
                        │  (localStorage) │
                        └────────┬────────┘
                                 │ manual sync (JSON paste)
                        ┌────────▼────────┐
                        │  contacts.json  │  ← Shared data store
                        └────────┬────────┘
                   ┌─────────────┼─────────────┐
          ┌────────▼──────┐  ┌──▼──────────┐  ┌▼──────────────────┐
          │  MCP Server   │  │   Gmail     │  │  LinkedIn MCP     │
          │  (Gemini AI)  │  │  Automation │  │  (key from Charles)│
          │ resume tailor │  │ drafts/send │  │  job search       │
          │ email draft   │  │ reply check │  │  recruiter find   │
          │ qualify job   │  └─────────────┘  └───────────────────┘
          │ weekly checkin│
          └───────────────┘
                 ↑
         Used by Claude / any MCP client
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | ✅ Yes | Already set in .env |
| `LINKEDIN_MCP_KEY` | ⏳ Pending | Get from Charles |
| `GMAIL_CREDENTIALS_FILE` | Gmail only | Path to OAuth credentials.json |
