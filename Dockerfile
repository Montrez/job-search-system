FROM python:3.13-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY job_search_mcp.py .
COPY gmail_automation.py .
COPY server.py .
COPY seed_jobs.py .
COPY job-search-dashboard.html .

# Data directory — mount a volume here to persist contacts.json + Excel
RUN mkdir -p /app/data

# Excel tracker — copy in at build time; data volume will overlay /app/data only
COPY Job_Search_Tracker_Montrez.xlsx .

EXPOSE 8765

# Default: run the dashboard server
# Override with: docker run ... python3 seed_jobs.py  (to seed data)
#                docker run ... python3 job_search_mcp.py  (to run MCP server)
CMD ["python3", "server.py"]
