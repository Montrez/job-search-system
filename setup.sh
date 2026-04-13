#!/bin/bash
# ============================================================
# Job Search System — One-Time Setup Script
# Run this once from the job_search_system/ folder
# ============================================================

set -e

echo ""
echo "⚡ Job Search System Setup"
echo "=========================="
echo ""

# ── 1. Check Python ──────────────────────────────────────────
echo "[ 1/5 ] Checking Python..."
PYTHON=$(which python3 || which python)
PY_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
echo "       Found: $PY_VERSION"

# ── 2. Check/Install uv ──────────────────────────────────────
echo "[ 2/5 ] Checking uv (Python package runner)..."
if ! which uvx &>/dev/null; then
  echo "       Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  echo "       ✅ uv installed"
else
  echo "       ✅ uv found: $(uvx --version 2>&1 | head -1)"
fi

# ── 3. Install MCP server deps ───────────────────────────────
echo "[ 3/5 ] Installing job search MCP server dependencies..."
$PYTHON -m pip install "mcp[cli]" httpx pydantic --quiet
echo "       ✅ Dependencies installed"

# ── 4. Test LinkedIn MCP package ─────────────────────────────
echo "[ 4/5 ] Verifying LinkedIn MCP package (stickerdaniel/linkedin-mcp-server)..."
uvx linkedin-mcp-server --help &>/dev/null && echo "       ✅ linkedin-mcp-server ready" || echo "       ⚠️  Will install on first run"

# ── 5. Create data directory ─────────────────────────────────
echo "[ 5/5 ] Creating data directory..."
mkdir -p data
echo "       ✅ data/ ready"

# ── Done ─────────────────────────────────────────────────────
echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Add your LinkedIn credentials to .env"
echo "     LINKEDIN_EMAIL=montrez.cox@gmail.com"
echo "     LINKEDIN_PASSWORD=your_linkedin_password"
echo ""
echo "  2. Add the MCP servers to Claude/Cowork:"
echo "     See mcp_config.json in this folder"
echo ""
echo "  3. Start the job search MCP server:"
echo "     python job_search_mcp.py"
echo ""
echo "  4. Start LinkedIn MCP (runs separately):"
echo "     uvx linkedin-mcp-server"
echo "     (opens a browser to log in on first run)"
echo ""
