# LinkedIn MCP Setup Guide

## Option A — Free: stickerdaniel/linkedin-mcp-server (Recommended to start)

This uses browser automation — it controls a real Chrome browser logged into your LinkedIn account.
No official API key needed. Risk: violates LinkedIn ToS, but at personal-use volumes, rarely flagged.

### Install

```bash
# Requires Node.js (check: node --version)
npm install -g linkedin-mcp-server

# Or clone and run directly:
git clone https://github.com/stickerdaniel/linkedin-mcp-server
cd linkedin-mcp-server
npm install
npm run build
```

### Add to Claude Code / Cowork

Add this to your MCP config file (`~/.claude/mcp_config.json` or via Claude settings):

```json
{
  "mcpServers": {
    "linkedin": {
      "command": "node",
      "args": ["/path/to/linkedin-mcp-server/dist/index.js"],
      "env": {
        "LINKEDIN_EMAIL": "montrez.cox@gmail.com",
        "LINKEDIN_PASSWORD": "your_linkedin_password"
      }
    }
  }
}
```

### First Run — Browser Login
The first time you run it, a Chrome browser window will open.
Log in normally (it handles 2FA/CAPTCHA). After that, session is saved.

### Available Tools (14 total)
- `search_jobs` — search by title, location, remote filter
- `search_people` — find recruiters by name, title, company
- `get_job_details` — full description for a job posting
- `get_profile` — extract a person's full profile
- `send_connection_request` — connect with a message
- `send_message` — DM an existing connection
- `get_conversations` — read your message threads
- `accept_connection` — accept pending invites
- `search_companies` — find companies by industry/size

### Safe Usage Rules (avoid getting flagged)
- Don't run more than 50–100 searches per day
- Space out connection requests (max 20–30/day)
- Don't bulk message people who aren't connections
- Use it like a human would, just faster

---

## Option B — Paid: Unipile (~$5–7/month)

Uses proper OAuth2 — LinkedIn officially knows you authorized it.
22 tools including Sales Navigator filters (seniority, tenure, company headcount).

### Setup
1. Sign up at https://www.unipile.com (7-day free trial, no credit card)
2. Connect your LinkedIn account via OAuth in Unipile dashboard
3. Get your API key from Unipile
4. Install the MCP: https://github.com/bhaktatejas922/unipile-linkedin-mcp

```bash
git clone https://github.com/bhaktatejas922/unipile-linkedin-mcp
cd unipile-linkedin-mcp
npm install
```

Add to MCP config:
```json
{
  "mcpServers": {
    "unipile": {
      "command": "node",
      "args": ["/path/to/unipile-linkedin-mcp/dist/index.js"],
      "env": {
        "UNIPILE_API_KEY": "your_unipile_api_key",
        "UNIPILE_ACCOUNT_ID": "your_linkedin_account_id"
      }
    }
  }
}
```

---

## If Charles's Key Is for One of These

Ask Charles which server/service the key is for and what format it expects.
Then update `.env`:

```bash
# For stickerdaniel (no key needed, just credentials)
LINKEDIN_EMAIL=montrez.cox@gmail.com
LINKEDIN_PASSWORD=your_password

# For Unipile
UNIPILE_API_KEY=charles_provided_key
UNIPILE_ACCOUNT_ID=your_account_id

# For any other service
LINKEDIN_MCP_KEY=charles_provided_key
```

---

## Immediate Manual Workaround (While Waiting)

Jack said start with 10 manually. Here's the search query to use on LinkedIn:

```
"recruiter" ("sales engineer" OR "solutions architect" OR "pre-sales") remote
```

Filter: People → 2nd connections → United States

Export the 10 contacts into the tracker dashboard. That's your seed list.
