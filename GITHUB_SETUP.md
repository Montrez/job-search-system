# Git + GitHub Setup

Run these commands once from inside the `job_search_system/` folder.

## 1. Initialize and make first commit

```bash
cd job_search_system/

git init
git branch -m main
git add job_search_mcp.py gmail_automation.py requirements.txt \
        mcp_config.json setup.sh README.md LINKEDIN_SETUP.md \
        .gitignore .env.example
git commit -m "Initial commit — Job Search Automation System"
```

## 2. Push to GitHub

```bash
# Create the repo on GitHub first (go to github.com/new)
# Name it: job-search-system   (or whatever you want)
# Set it to PRIVATE — your .env.example is safe but be cautious

git remote add origin https://github.com/YOUR_USERNAME/job-search-system.git
git push -u origin main
```

## 3. What's tracked vs ignored

| File | Tracked? | Why |
|------|----------|-----|
| `job_search_mcp.py` | ✅ Yes | Core server code |
| `gmail_automation.py` | ✅ Yes | Email automation |
| `requirements.txt` | ✅ Yes | Dependencies |
| `mcp_config.json` | ✅ Yes | Claude config template |
| `setup.sh` | ✅ Yes | Install script |
| `README.md` | ✅ Yes | Docs |
| `.env.example` | ✅ Yes | Safe template (no real keys) |
| `.env` | ❌ No | Has your real keys — never commit |
| `data/contacts.json` | ❌ No | Your personal job search data |
| `data/criteria.json` | ❌ No | Your personal criteria |
| `data/gmail_token.json` | ❌ No | Gmail OAuth token |

## 4. Ongoing workflow

```bash
# After making changes to the MCP server or scripts:
git add -p                          # review each change
git commit -m "describe what changed"
git push
```

## 5. Sync dashboard data with MCP server

The dashboard (browser) and MCP server use separate data stores.
To keep them in sync:

**Dashboard → MCP server:**
1. Click **⬇ Export** in the dashboard sidebar → saves `contacts.json`
2. Move that file to `job_search_system/data/contacts.json`

**MCP server → Dashboard:**
1. Click **⬆ Import** in the dashboard sidebar
2. Pick `job_search_system/data/contacts.json`
3. New contacts merge in (duplicates skipped automatically)
