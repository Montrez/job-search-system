#!/usr/bin/env python3
"""
Job Search MCP Server — Montrez Cox
====================================
FastMCP server that powers automated job search workflows.
Integrates with Gemini for AI resume tailoring and email drafting,
and exposes LinkedIn tools (ready to wire in when key arrives).

Run:  python job_search_mcp.py
Test: npx @modelcontextprotocol/inspector python job_search_mcp.py
"""

import json
import os
import re
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncio
import urllib.request
import urllib.error
from mcp.server.fastmcp import FastMCP, Context
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─────────────────────────────────────────────────────────────────────────────
# Config — load from environment variables (never hardcode keys)
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
LINKEDIN_EMAIL    = os.getenv("LINKEDIN_EMAIL", "")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "")
GEMINI_API_URL    = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# Local JSON store (same format as the dashboard's localStorage export)
DATA_FILE = Path(__file__).parent / "data" / "contacts.json"
CRITERIA_FILE = Path(__file__).parent / "data" / "criteria.json"

DEFAULT_CRITERIA = {
    "worktype": "Remote Only",
    "minsalary": "$80,000",
    "roles": "Sales Engineer, Solutions Architect, Pre-Sales, Consulting",
    "geo": "US-based remote preferred",
    "size": "Small (1–100)",
    "target": "100",
    "musthaves": "Remote flexibility, base salary above $80k, US resident allowed",
    "nicetohaves": "Healthcare, 401k match, equity, flexible hours",
}

# ─────────────────────────────────────────────────────────────────────────────
# Server
# ─────────────────────────────────────────────────────────────────────────────
mcp = FastMCP("job_search_mcp")


# ─────────────────────────────────────────────────────────────────────────────
# Shared Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _load_contacts() -> List[Dict[str, Any]]:
    """Load contacts from the local JSON store."""
    if not DATA_FILE.exists():
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text("[]")
        return []
    try:
        return json.loads(DATA_FILE.read_text())
    except json.JSONDecodeError:
        return []


def _save_contacts(contacts: List[Dict[str, Any]]) -> None:
    """Persist contacts to the local JSON store."""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(contacts, indent=2, default=str))


def _load_criteria() -> Dict[str, str]:
    """Load job search criteria."""
    if not CRITERIA_FILE.exists():
        return DEFAULT_CRITERIA
    try:
        return json.loads(CRITERIA_FILE.read_text())
    except json.JSONDecodeError:
        return DEFAULT_CRITERIA


def _save_criteria(criteria: Dict[str, str]) -> None:
    CRITERIA_FILE.parent.mkdir(parents=True, exist_ok=True)
    CRITERIA_FILE.write_text(json.dumps(criteria, indent=2))


def _days_until_followup(followup_date_str: str) -> Optional[int]:
    """Return days until follow-up (negative = overdue)."""
    if not followup_date_str:
        return None
    try:
        fu = datetime.fromisoformat(followup_date_str).date()
        return (fu - date.today()).days
    except ValueError:
        return None


def _handle_api_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 401:
            return "Error: Invalid API key. Check GEMINI_API_KEY in your environment."
        if code == 429:
            return "Error: Rate limit exceeded. Wait a moment and try again."
        if code == 404:
            return "Error: Endpoint not found. Check the API URL."
        return f"Error: API request failed with status {code}: {e.response.text[:200]}"
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. Check your connection and retry."
    return f"Error: Unexpected error — {type(e).__name__}: {str(e)}"


async def _gemini_generate(prompt: str, system: str = "") -> str:
    """Call Gemini API and return the text response."""
    if not GEMINI_API_KEY:
        return "Error: GEMINI_API_KEY not set. Add it to your environment: export GEMINI_API_KEY=your_key"

    contents = []
    if system:
        contents.append({"role": "user", "parts": [{"text": f"[System context]: {system}"}]})
        contents.append({"role": "model", "parts": [{"text": "Understood. Ready to help."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload = {"contents": contents, "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048}}

    def _call() -> str:
        body = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["candidates"][0]["content"]["parts"][0]["text"]

    try:
        return await asyncio.to_thread(_call)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API error {e.code}: {detail}") from e


# ─────────────────────────────────────────────────────────────────────────────
# Enums and Input Models
# ─────────────────────────────────────────────────────────────────────────────

class ContactStatus(str, Enum):
    NOT_CONTACTED      = "not_contacted"
    EMAIL_SENT         = "email_sent"
    RESPONDED          = "responded"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    SUBMITTED          = "submitted"
    OFFER              = "offer"
    REJECTED           = "rejected"
    WITHDRAWN          = "withdrawn"


class LocationType(str, Enum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON     = "json"


class FollowupUrgency(str, Enum):
    OVERDUE = "overdue"
    TODAY   = "today"
    WEEK    = "week"
    ALL     = "all"


# ── Contact Models ────────────────────────────────────────────────────────────

class AddContactInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    company:    str           = Field(...,               description="Company name (required, e.g. 'Acme Corp')", min_length=1, max_length=200)
    contact:    Optional[str] = Field(default=None,      description="Recruiter or contact name")
    email:      Optional[str] = Field(default=None,      description="Contact email address")
    linkedin:   Optional[str] = Field(default=None,      description="LinkedIn profile URL")
    jobtitle:   Optional[str] = Field(default=None,      description="Job title being applied for")
    joburl:     Optional[str] = Field(default=None,      description="URL of the job posting")
    location:   LocationType  = Field(default=LocationType.REMOTE, description="Work location type: remote, hybrid, onsite")
    salary:     Optional[str] = Field(default=None,      description="Salary range (e.g. '$80k-$120k')")
    status:     ContactStatus = Field(default=ContactStatus.NOT_CONTACTED, description="Current application status")
    followup:   Optional[str] = Field(default=None,      description="Follow-up date in YYYY-MM-DD format")
    confirmed:  str           = Field(default="false",   description="Whether recruiter confirmed company name before submitting: 'true' or 'false'")
    submittedby: Optional[str] = Field(default=None,     description="Name of recruiter who submitted your resume")
    notes:      Optional[str] = Field(default=None,      description="Any notes or context about this contact")


class UpdateContactInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    contact_id:  str                    = Field(...,          description="Contact ID to update (from list_contacts)")
    status:      Optional[ContactStatus] = Field(default=None, description="New status")
    followup:    Optional[str]           = Field(default=None, description="New follow-up date (YYYY-MM-DD)")
    notes:       Optional[str]           = Field(default=None, description="Notes to append (will be added to existing notes)")
    confirmed:   Optional[str]           = Field(default=None, description="Set to 'true' if recruiter confirmed company")
    submittedby: Optional[str]           = Field(default=None, description="Recruiter who submitted your resume")


class ListContactsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status:   Optional[str] = Field(default=None, description="Filter by status (e.g. 'email_sent', 'responded')")
    location: Optional[str] = Field(default=None, description="Filter by location type: remote, hybrid, onsite")
    search:   Optional[str] = Field(default=None, description="Text search across company, contact, job title")
    limit:    int            = Field(default=20,   description="Max results to return", ge=1, le=200)
    offset:   int            = Field(default=0,    description="Pagination offset", ge=0)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format: markdown or json")


class GetFollowupsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urgency:         FollowupUrgency = Field(default=FollowupUrgency.OVERDUE, description="Which follow-ups to surface: overdue, today, week, all")
    response_format: ResponseFormat  = Field(default=ResponseFormat.MARKDOWN,  description="Output format: markdown or json")


# ── AI Tool Models ────────────────────────────────────────────────────────────

class TailorResumeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    job_description: str = Field(..., description="Full job description text — paste the entire posting", min_length=50)
    your_background: str = Field(..., description="Brief summary of your background, skills, and experience (3-5 sentences)", min_length=20)
    num_bullets:     int = Field(default=5, description="Number of tailored bullet points to generate", ge=3, le=10)


class DraftEmailInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    contact_id:    Optional[str] = Field(default=None, description="Contact ID from tracker — if provided, personalizes to that contact's details")
    template_type: str           = Field(default="initial_outreach",
                                         description="Email type: initial_outreach, follow_up, criteria_statement, anti_double_submit, interview_thank_you")
    company:       Optional[str] = Field(default=None, description="Company name (if no contact_id)")
    recruiter:     Optional[str] = Field(default=None, description="Recruiter name (if no contact_id)")
    job_title:     Optional[str] = Field(default=None, description="Job title (if no contact_id)")
    extra_context: Optional[str] = Field(default=None, description="Any extra context to personalize the email further")


class QualifyJobInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    job_description: str = Field(..., description="Full job description or summary to evaluate", min_length=20)
    company:         Optional[str] = Field(default=None, description="Company name")


class WeeklyCheckinInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


# ── LinkedIn Models (ready for when key arrives) ──────────────────────────────

class LinkedInSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query:    str           = Field(..., description="Job title or keyword to search (e.g. 'Sales Engineer remote')", min_length=2)
    location: Optional[str] = Field(default="United States", description="Location filter")
    limit:    int           = Field(default=10, description="Max results", ge=1, le=50)


class UpdateCriteriaInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    worktype:     Optional[str] = Field(default=None, description="Work type preference (e.g. 'Remote Only')")
    minsalary:    Optional[str] = Field(default=None, description="Minimum salary (e.g. '$80,000')")
    roles:        Optional[str] = Field(default=None, description="Preferred roles (comma-separated)")
    geo:          Optional[str] = Field(default=None, description="Geographic preference")
    musthaves:    Optional[str] = Field(default=None, description="Non-negotiable requirements")
    nicetohaves:  Optional[str] = Field(default=None, description="Nice-to-have preferences")


# ─────────────────────────────────────────────────────────────────────────────
# TOOLS — Contact Management
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="job_search_add_contact",
    annotations={"title": "Add Recruiter/Company Contact", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
)
async def job_search_add_contact(params: AddContactInput) -> str:
    """Add a new recruiter or company to the job search tracker.

    Creates a new contact record in the local JSON store. Automatically checks
    for potential double-submission risk by comparing against existing contacts
    at the same company with 'submitted' status.

    Args:
        params (AddContactInput): Contact details including company, recruiter info,
            job title, status, follow-up date, and notes.

    Returns:
        str: Confirmation with the new contact's ID and a double-submit warning
             if another contact at the same company is already submitted.
    """
    contacts = _load_contacts()

    # Check for double-submit risk
    same_company = [
        c for c in contacts
        if c.get("company", "").lower() == params.company.lower()
        and c.get("status") in ("submitted", "email_sent", "responded", "interview_scheduled")
    ]

    record = {
        "id":          str(int(datetime.now().timestamp() * 1000)),
        "company":     params.company,
        "contact":     params.contact,
        "email":       params.email,
        "linkedin":    params.linkedin,
        "jobtitle":    params.jobtitle,
        "joburl":      params.joburl,
        "location":    params.location.value,
        "salary":      params.salary,
        "status":      params.status.value,
        "followup":    params.followup or (date.today() + timedelta(days=7)).isoformat(),
        "confirmed":   params.confirmed,
        "submittedby": params.submittedby,
        "notes":       params.notes,
        "dateAdded":   datetime.now().isoformat(),
        "lastUpdated": datetime.now().isoformat(),
    }
    contacts.append(record)
    _save_contacts(contacts)

    result = f"✅ Contact added — ID: {record['id']}\n"
    result += f"Company: {params.company}"
    if params.contact:
        result += f" | Contact: {params.contact}"
    result += f"\nStatus: {params.status.value} | Follow-up: {record['followup']}"
    if same_company:
        result += f"\n\n⚠️  DOUBLE-SUBMIT WARNING: {len(same_company)} existing contact(s) at {params.company} are already in progress. Confirm with recruiter before submitting."
    return result


@mcp.tool(
    name="job_search_update_contact",
    annotations={"title": "Update Contact Status", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
async def job_search_update_contact(params: UpdateContactInput) -> str:
    """Update a contact's status, follow-up date, or notes in the tracker.

    Args:
        params (UpdateContactInput): The contact ID and fields to update.
            Only provided fields are updated; others remain unchanged.

    Returns:
        str: Confirmation of what was updated, or an error if contact not found.
    """
    contacts = _load_contacts()
    idx = next((i for i, c in enumerate(contacts) if c["id"] == params.contact_id), None)
    if idx is None:
        return f"Error: Contact with ID '{params.contact_id}' not found. Use job_search_list_contacts to find valid IDs."

    c = contacts[idx]
    changes = []

    if params.status is not None:
        c["status"] = params.status.value
        changes.append(f"status → {params.status.value}")
    if params.followup is not None:
        c["followup"] = params.followup
        changes.append(f"follow-up → {params.followup}")
    if params.notes is not None:
        existing = c.get("notes") or ""
        timestamp = datetime.now().strftime("%m/%d %I:%M%p")
        c["notes"] = f"{existing}\n[{timestamp}] {params.notes}".strip()
        changes.append("notes appended")
    if params.confirmed is not None:
        c["confirmed"] = params.confirmed
        changes.append(f"confirmed → {params.confirmed}")
    if params.submittedby is not None:
        c["submittedby"] = params.submittedby
        changes.append(f"submitted by → {params.submittedby}")

    c["lastUpdated"] = datetime.now().isoformat()
    contacts[idx] = c
    _save_contacts(contacts)

    return f"✅ Updated {c['company']} ({params.contact_id}): {', '.join(changes) if changes else 'no changes'}"


@mcp.tool(
    name="job_search_list_contacts",
    annotations={"title": "List Job Search Contacts", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
async def job_search_list_contacts(params: ListContactsInput) -> str:
    """List all tracked contacts with optional filtering and pagination.

    Args:
        params (ListContactsInput): Filter options (status, location, search text),
            pagination (limit/offset), and output format.

    Returns:
        str: Formatted list of contacts matching the filters, with total count
             and pagination info. In JSON mode, returns structured data.
    """
    contacts = _load_contacts()

    # Apply filters
    filtered = contacts
    if params.status:
        filtered = [c for c in filtered if c.get("status") == params.status]
    if params.location:
        filtered = [c for c in filtered if c.get("location") == params.location]
    if params.search:
        q = params.search.lower()
        filtered = [c for c in filtered if any(
            q in str(c.get(f, "")).lower()
            for f in ("company", "contact", "jobtitle", "notes")
        )]

    total = len(filtered)
    page  = filtered[params.offset: params.offset + params.limit]

    if params.response_format == ResponseFormat.JSON:
        return json.dumps({
            "total": total, "count": len(page),
            "offset": params.offset, "has_more": total > params.offset + len(page),
            "contacts": page,
        }, indent=2, default=str)

    if not page:
        return "No contacts found matching your filters. Try adjusting the search or status filter."

    lines = [f"# Job Search Contacts ({total} total, showing {len(page)})\n"]
    for c in page:
        diff = _days_until_followup(c.get("followup", ""))
        fu_label = ""
        if diff is not None:
            if diff < 0:
                fu_label = f" ⚠️ OVERDUE {abs(diff)}d"
            elif diff == 0:
                fu_label = " 🔴 TODAY"
            elif diff <= 3:
                fu_label = f" 🟡 in {diff}d"

        lines.append(f"**{c['company']}** (ID: {c['id']})")
        lines.append(f"  Contact: {c.get('contact') or '—'} | {c.get('email') or '—'}")
        lines.append(f"  Role: {c.get('jobtitle') or '—'} | {c.get('location', 'remote').title()}")
        lines.append(f"  Status: {c.get('status', '—')} | Follow-up: {c.get('followup') or '—'}{fu_label}")
        if c.get("notes"):
            lines.append(f"  Notes: {c['notes'][:100]}{'...' if len(c.get('notes',''))>100 else ''}")
        lines.append("")

    if total > params.offset + len(page):
        next_off = params.offset + params.limit
        lines.append(f"_(Page {params.offset // params.limit + 1} of {-(-total // params.limit)}) — Use offset={next_off} for next page_")

    return "\n".join(lines)


@mcp.tool(
    name="job_search_get_followups",
    annotations={"title": "Get Follow-Up Reminders", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
async def job_search_get_followups(params: GetFollowupsInput) -> str:
    """Get contacts that need follow-up action, sorted by urgency.

    Use this at the start of every weekly check-in to see what needs attention.

    Args:
        params (GetFollowupsInput): urgency filter (overdue/today/week/all) and format.

    Returns:
        str: List of contacts needing follow-up with urgency labels and suggested actions.
    """
    contacts = _load_contacts()
    active = [c for c in contacts if c.get("status") not in ("rejected", "withdrawn", "offer")]

    def include(c: Dict) -> bool:
        diff = _days_until_followup(c.get("followup", ""))
        if diff is None:
            return False
        if params.urgency == FollowupUrgency.OVERDUE:
            return diff < 0
        if params.urgency == FollowupUrgency.TODAY:
            return diff == 0
        if params.urgency == FollowupUrgency.WEEK:
            return 0 <= diff <= 7
        return True  # ALL

    due = sorted([c for c in active if include(c)],
                 key=lambda c: c.get("followup", ""))

    if params.response_format == ResponseFormat.JSON:
        return json.dumps({"count": len(due), "urgency": params.urgency.value, "contacts": due}, indent=2, default=str)

    if not due:
        label = {"overdue": "overdue", "today": "due today", "week": "due this week", "all": "pending"}[params.urgency.value]
        return f"✅ No follow-ups {label}. You're on top of it!"

    label_map = {"overdue": "🔴 OVERDUE", "today": "🟠 DUE TODAY", "week": "🟡 THIS WEEK", "all": "📋 ALL"}
    lines = [f"# Follow-Ups — {label_map.get(params.urgency.value, '')} ({len(due)} contacts)\n"]

    for c in due:
        diff = _days_until_followup(c.get("followup", ""))
        if diff is not None and diff < 0:
            urgency_str = f"🔴 {abs(diff)} day(s) overdue"
        elif diff == 0:
            urgency_str = "🟠 Due today"
        else:
            urgency_str = f"🟡 Due in {diff} day(s)"

        status = c.get("status", "not_contacted")
        if status == "email_sent":
            action = "→ Send follow-up email (no response yet)"
        elif status == "not_contacted":
            action = "→ Send initial outreach email"
        elif status == "responded":
            action = "→ Continue conversation / schedule call"
        else:
            action = f"→ Check in on status"

        lines.append(f"**{c['company']}** (ID: {c['id']}) — {urgency_str}")
        lines.append(f"  {c.get('contact') or 'No contact'} | {c.get('jobtitle') or 'No title'} | Status: {status}")
        lines.append(f"  {action}")
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# TOOLS — AI-Powered (Gemini)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="job_search_tailor_resume",
    annotations={"title": "AI Resume Tailor (Gemini)", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
)
async def job_search_tailor_resume(params: TailorResumeInput, ctx: Context) -> str:
    """Generate tailored resume bullet points for a specific job posting using Gemini AI.

    Analyzes the job description and your background to produce ATS-optimized,
    keyword-matched resume bullets you can drop directly into your resume.

    Args:
        params (TailorResumeInput): job_description (full posting text),
            your_background (your skills summary), num_bullets (3-10).

    Returns:
        str: Tailored resume bullet points, key matching keywords, and a
             brief gap analysis showing what the job wants vs. your background.

    Requires: GEMINI_API_KEY environment variable.
    """
    await ctx.report_progress(0.1, "Analyzing job description...")

    criteria = _load_criteria()
    system = f"""You are an expert resume coach specializing in tech sales,
solutions engineering, and consulting roles. The candidate's criteria: {json.dumps(criteria)}.
Write concise, impactful bullet points in the format: [Action verb] + [what you did] + [quantified result].
Always start with strong action verbs. Never use 'responsible for' or 'helped with'."""

    prompt = f"""Job Description:
---
{params.job_description}
---

Candidate Background:
---
{params.your_background}
---

Please provide:

1. **{params.num_bullets} Tailored Resume Bullet Points** — optimized for this specific job and ATS systems.
   Format each as: • [Action Verb] [specific achievement with metrics/scope where possible]

2. **Top 8 Keywords to Include** — exact phrases from the job posting I should weave into my resume.

3. **Match Score** — estimate how well my background matches this role (0-100%) with a 2-sentence explanation.

4. **1 Gap to Address** — the biggest mismatch and how to frame or bridge it.

Keep bullets punchy and results-focused. Use language from the job description naturally."""

    await ctx.report_progress(0.5, "Generating tailored content with Gemini...")
    try:
        result = await _gemini_generate(prompt, system)
        await ctx.report_progress(1.0, "Done!")
        return result
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="job_search_draft_email",
    annotations={"title": "AI Email Drafter (Gemini)", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
)
async def job_search_draft_email(params: DraftEmailInput) -> str:
    """Draft a personalized outreach or follow-up email using Gemini AI.

    If a contact_id is provided, pulls the contact's details from the tracker
    to personalize the email. Otherwise, uses the provided company/recruiter info.

    Args:
        params (DraftEmailInput): template_type, contact_id (optional), company,
            recruiter, job_title, and any extra context.

    Template types:
        - initial_outreach: First email to a recruiter/company
        - follow_up: No response after 7+ days
        - criteria_statement: Sharing your job requirements upfront
        - anti_double_submit: Requesting confirmation before submission
        - interview_thank_you: Post-interview thank you

    Returns:
        str: Complete email with subject line, ready to send or customize.
    Requires: GEMINI_API_KEY environment variable.
    """
    criteria = _load_criteria()
    company, recruiter, jobtitle = params.company, params.recruiter, params.job_title

    # Load from tracker if contact_id provided
    if params.contact_id:
        contacts = _load_contacts()
        c = next((x for x in contacts if x["id"] == params.contact_id), None)
        if c:
            company   = company   or c.get("company")
            recruiter = recruiter or c.get("contact")
            jobtitle  = jobtitle  or c.get("jobtitle")

    template_instructions = {
        "initial_outreach": "Write a concise, professional first outreach email to a recruiter. Make it personal, brief (under 150 words body), and end with a clear call to action. Include my work type preference (remote) and salary range naturally.",
        "follow_up": "Write a brief, non-pushy follow-up email for a recruiter who hasn't responded after 7-10 days. Keep it under 100 words. Reference the original email. Stay warm but direct.",
        "criteria_statement": "Write an email that clearly states my job criteria upfront to save both parties time. Include: remote only, salary floor, preferred roles, and the anti-double-submit requirement. Professional but conversational tone.",
        "anti_double_submit": "Write a short email requesting the recruiter confirm the company name, state, and that no other recruiter has submitted me before they proceed. Frame it as standard process to protect both parties.",
        "interview_thank_you": "Write a warm, professional post-interview thank you email. Reference the conversation and why I'm excited about the role. Keep it sincere and under 200 words.",
    }.get(params.template_type, "Write a professional job search email.")

    system = f"""You are an expert career coach writing emails for Montrez Cox, a tech professional
seeking remote roles. His criteria: work type = {criteria.get('worktype')},
min salary = {criteria.get('minsalary')}, preferred roles = {criteria.get('roles')}.
Write in a confident, personable, professional tone. Always include a subject line."""

    prompt = f"""{template_instructions}

Details:
- Candidate: Montrez Cox
- Company: {company or '[Company]'}
- Recruiter: {recruiter or '[Recruiter Name]'}
- Job Title: {jobtitle or '[Job Title]'}
{f'- Extra context: {params.extra_context}' if params.extra_context else ''}

Write the complete email including subject line. Use placeholders like [Company] or [Recruiter]
where specific info is missing. Make it ready to send with minimal editing."""

    try:
        return await _gemini_generate(prompt, system)
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="job_search_qualify_job",
    annotations={"title": "Qualify Job Against Criteria", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
)
async def job_search_qualify_job(params: QualifyJobInput) -> str:
    """Evaluate whether a job posting matches your saved search criteria using Gemini.

    Scores the job against your non-negotiables (remote, salary, role type) and
    returns a clear PASS/FAIL/MAYBE with reasoning and negotiation tips.

    Args:
        params (QualifyJobInput): job_description and optional company name.

    Returns:
        str: PASS/FAIL/MAYBE verdict with scoring breakdown and recommended action.
    Requires: GEMINI_API_KEY environment variable.
    """
    criteria = _load_criteria()

    prompt = f"""Evaluate this job posting against my search criteria and give me a clear verdict.

MY CRITERIA:
- Work type: {criteria.get('worktype', 'Remote Only')}
- Min salary: {criteria.get('minsalary', '$80,000')}
- Preferred roles: {criteria.get('roles', 'Sales Engineer, Solutions Architect')}
- Geography: {criteria.get('geo', 'US-based remote')}
- Must-haves: {criteria.get('musthaves', '')}
- Nice-to-haves: {criteria.get('nicetohaves', '')}

JOB POSTING{f' at {params.company}' if params.company else ''}:
---
{params.job_description}
---

Provide:
1. **Verdict**: PASS ✅ / FAIL ❌ / MAYBE 🤔 (with confidence %)
2. **Criteria Scorecard** (table): each criterion → Met / Not Met / Unclear
3. **Biggest Red Flags** (if any)
4. **Negotiation Angles** — if MAYBE, what to negotiate or clarify upfront
5. **Recommended Action**: Apply now / Pass / Ask recruiter about X first

Be direct. Don't hedge excessively."""

    try:
        return await _gemini_generate(prompt)
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="job_search_weekly_checkin",
    annotations={"title": "Run Weekly Check-In", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
async def job_search_weekly_checkin(params: WeeklyCheckinInput) -> str:
    """Run the full weekly job search check-in and generate a prioritized action list.

    Aggregates stats, surfaces overdue follow-ups, tracks progress toward the
    100-contact goal, and generates a ranked action list for the week.

    Returns:
        str: Complete weekly report: stats, pipeline status, overdue actions,
             priority list, and motivational progress tracker.
    """
    contacts = _load_contacts()
    criteria = _load_criteria()
    today = date.today().strftime("%A, %B %d, %Y")
    goal = int(criteria.get("target", "100"))

    # Stats
    total = len(contacts)
    by_status: Dict[str, int] = {}
    for c in contacts:
        s = c.get("status", "not_contacted")
        by_status[s] = by_status.get(s, 0) + 1

    overdue = [c for c in contacts if c.get("followup") and
               _days_until_followup(c["followup"]) is not None and
               _days_until_followup(c["followup"]) < 0 and
               c.get("status") not in ("rejected", "withdrawn", "offer")]

    due_today = [c for c in contacts if c.get("followup") and
                 _days_until_followup(c["followup"]) == 0 and
                 c.get("status") not in ("rejected", "withdrawn", "offer")]

    no_followup = [c for c in contacts if not c.get("followup") and
                   c.get("status") not in ("rejected", "withdrawn", "offer")]

    not_confirmed = [c for c in contacts if c.get("status") == "submitted" and
                     c.get("confirmed") != "true"]

    progress_pct = min(100, int((total / goal) * 100))
    bar_filled = int(progress_pct / 5)
    progress_bar = "█" * bar_filled + "░" * (20 - bar_filled)

    if params.response_format == ResponseFormat.JSON:
        return json.dumps({
            "date": today, "total": total, "goal": goal, "progress_pct": progress_pct,
            "by_status": by_status, "overdue_count": len(overdue),
            "due_today_count": len(due_today), "no_followup_count": len(no_followup),
            "unconfirmed_submissions": len(not_confirmed),
        }, indent=2)

    lines = [
        f"# 📊 Weekly Job Search Check-In",
        f"**{today}**\n",
        f"## Progress to Goal",
        f"`[{progress_bar}]` {total}/{goal} contacts ({progress_pct}%)\n",
        f"## Pipeline Snapshot",
    ]

    status_labels = {
        "not_contacted": "Not Contacted", "email_sent": "Email Sent",
        "responded": "Responded", "interview_scheduled": "Interview Scheduled",
        "submitted": "Submitted", "offer": "Offer Received",
        "rejected": "Rejected", "withdrawn": "Withdrawn",
    }
    for status, label in status_labels.items():
        count = by_status.get(status, 0)
        if count > 0:
            lines.append(f"  • {label}: **{count}**")

    lines.append(f"\n## 🔴 Priority Actions This Week")

    priority = 1
    if overdue:
        lines.append(f"\n**{priority}. OVERDUE FOLLOW-UPS ({len(overdue)}) — Do these first**")
        for c in overdue[:5]:
            diff = abs(_days_until_followup(c.get("followup", "")) or 0)
            lines.append(f"   • {c['company']} ({c.get('contact') or 'no contact'}) — {diff}d overdue | use `job_search_draft_email` with contact_id={c['id']}")
        if len(overdue) > 5:
            lines.append(f"   ... and {len(overdue)-5} more")
        priority += 1

    if due_today:
        lines.append(f"\n**{priority}. DUE TODAY ({len(due_today)})**")
        for c in due_today:
            lines.append(f"   • {c['company']} — Status: {c.get('status')}")
        priority += 1

    if not_confirmed:
        lines.append(f"\n**{priority}. ⚠️  UNCONFIRMED SUBMISSIONS — Double-Submit Risk ({len(not_confirmed)})**")
        for c in not_confirmed:
            lines.append(f"   • {c['company']} (submitted by: {c.get('submittedby') or '?'}) — confirm recruiter sent only to this company!")
        priority += 1

    if no_followup:
        lines.append(f"\n**{priority}. NO FOLLOW-UP DATE SET ({len(no_followup)})**")
        lines.append(f"   Use `job_search_update_contact` to set a follow-up date for these {len(no_followup)} contacts.")
        priority += 1

    to_add = max(0, goal - total)
    if to_add > 0:
        weekly_add = max(5, min(20, to_add // 4))
        lines.append(f"\n**{priority}. ADD NEW CONTACTS — Target: +{weekly_add} this week**")
        lines.append(f"   You need {to_add} more contacts to hit your goal of {goal}.")
        lines.append(f"   Use `linkedin_search_jobs` or manually add with `job_search_add_contact`.")

    lines.append(f"\n---\n_Use `job_search_get_followups urgency=overdue` for full overdue list._")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# TOOLS — LinkedIn Bridge (stickerdaniel/linkedin-mcp-server integration)
#
# How this works:
#   1. The linkedin MCP server runs separately via: uvx linkedin-mcp-server
#   2. You call ITS tools (search_jobs, search_people) to get data from LinkedIn
#   3. You pipe those results into these tools to import into your tracker
#
# Setup: see mcp_config.json — both servers run side by side in Claude/Cowork
# ─────────────────────────────────────────────────────────────────────────────

class ImportLinkedInJobsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    jobs_json: str = Field(..., description="Raw JSON output from the linkedin MCP server's search_jobs tool. Paste it here to import into your tracker.")
    auto_qualify: bool = Field(default=True, description="Run each job through Gemini criteria check before adding. Set false to import everything.")


class ImportLinkedInRecruitersInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    people_json: str = Field(..., description="Raw JSON output from the linkedin MCP server's search_people tool.")
    job_title_context: Optional[str] = Field(default=None, description="Role you were searching for — pre-fills job title in tracker (e.g. 'Sales Engineer').")


class LinkedInQueryBuilderInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    search_type: str = Field(default="jobs", description="What to search: 'jobs', 'recruiters', or 'companies'")
    keywords: Optional[str] = Field(default=None, description="Extra keywords beyond your default criteria")


@mcp.tool(
    name="linkedin_build_search_query",
    annotations={"title": "Build LinkedIn Search Query", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
async def linkedin_build_search_query(params: LinkedInQueryBuilderInput) -> str:
    """Generate the exact parameters to use with the linkedin MCP server's search tools.

    Translates your saved criteria into ready-to-use arguments for search_jobs or
    search_people. Run this first, then copy the output into the linkedin server's tool.

    Args:
        params (LinkedInQueryBuilderInput): search_type (jobs/recruiters/companies) and optional extra keywords.

    Returns:
        str: Exact tool name + JSON parameters for the linkedin MCP server,
             plus instructions for piping results into job_search_import_jobs or job_search_import_recruiters.
    """
    criteria = _load_criteria()
    roles    = criteria.get("roles", "Sales Engineer, Solutions Architect")
    extra    = f" {params.keywords}" if params.keywords else ""

    if params.search_type == "jobs":
        query = f"{roles.split(',')[0].strip()} remote{extra}"
        return (
            f"## Step 1 — Call the `linkedin` MCP server:\n\n"
            f"**Tool**: `search_jobs`\n"
            f"```json\n{{\n"
            f'  "query": "{query}",\n'
            f'  "location": "United States",\n'
            f'  "remote": true,\n  "limit": 20\n}}\n```\n\n'
            f"## Step 2 — Import results here:\n"
            f"**Tool**: `job_search_import_jobs`  ← paste the JSON output into `jobs_json`\n\n"
            f"**Also try these searches**:\n"
            + "\n".join(f'- `"{r.strip()} remote"`' for r in roles.split(",")[:4])
        )
    elif params.search_type == "recruiters":
        query = f"recruiter {roles.split(',')[0].strip()}{extra}"
        return (
            f"## Step 1 — Call the `linkedin` MCP server:\n\n"
            f"**Tool**: `search_people`\n"
            f"```json\n{{\n"
            f'  "query": "{query}",\n'
            f'  "location": "United States",\n  "limit": 25\n}}\n```\n\n'
            f"## Step 2 — Import results here:\n"
            f"**Tool**: `job_search_import_recruiters`\n"
            f'  → `people_json`: paste the JSON output\n'
            f'  → `job_title_context`: "{roles.split(",")[0].strip()}"\n\n'
            f"**Recommended variations**:\n"
            f'- `"technical recruiter solutions architect"`\n'
            f'- `"recruiter pre-sales remote technology"`\n'
            f'- `"staffing sales engineer SaaS"`\n'
        )
    else:
        return (
            f"## Call the `linkedin` MCP server:\n\n"
            f"**Tool**: `search_companies`\n"
            f"```json\n{{\n"
            f'  "query": "SaaS technology startup remote{extra}",\n  "limit": 20\n}}\n```\n\n'
            f"Then add interesting companies with `job_search_add_contact`."
        )


@mcp.tool(
    name="job_search_import_jobs",
    annotations={"title": "Import LinkedIn Jobs to Tracker", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
)
async def job_search_import_jobs(params: ImportLinkedInJobsInput, ctx: Context) -> str:
    """Import job search results from the linkedin MCP server into your tracker.

    Takes JSON from linkedin's search_jobs tool, optionally qualifies each against
    your Gemini criteria, and adds passing jobs as 'not_contacted' contacts.

    Workflow: linkedin_build_search_query → search_jobs (linkedin server) → paste JSON here.

    Args:
        params (ImportLinkedInJobsInput): jobs_json and auto_qualify flag.

    Returns:
        str: Import summary — how many added, skipped, and their tracker IDs.
    """
    try:
        data = json.loads(params.jobs_json)
    except json.JSONDecodeError:
        return "Error: Invalid JSON. Paste the raw output from the linkedin MCP server's search_jobs tool."

    jobs = data if isinstance(data, list) else data.get("jobs", data.get("results", []))
    if not jobs:
        return "No jobs in the provided JSON."

    criteria      = _load_criteria()
    contacts      = _load_contacts()
    added, skipped = [], []
    followup_date = (date.today() + timedelta(days=7)).isoformat()

    for i, job in enumerate(jobs):
        await ctx.report_progress(0.1 + 0.8 * i / len(jobs), f"Processing {i+1}/{len(jobs)}...")
        company = job.get("company", job.get("companyName", "Unknown"))
        title   = job.get("title", job.get("jobTitle", "Unknown"))
        job_url = job.get("url", job.get("jobUrl", job.get("link", "")))
        desc    = job.get("description", job.get("jobDescription", ""))
        loc_raw = (job.get("location", "") + title).lower()
        loc_type = "remote" if "remote" in loc_raw else "hybrid" if "hybrid" in loc_raw else "onsite"

        already = any(c.get("company","").lower()==company.lower() and c.get("jobtitle","").lower()==title.lower() for c in contacts)
        if already:
            skipped.append(f"{company} — {title} (already tracked)"); continue
        if criteria.get("worktype") == "Remote Only" and loc_type == "onsite":
            skipped.append(f"{company} — {title} (onsite)"); continue

        qualify_note = ""
        if params.auto_qualify and desc and GEMINI_API_KEY:
            try:
                verdict = await _gemini_generate(
                    f"One-word verdict (PASS/FAIL/MAYBE) + one sentence. "
                    f"Criteria: {criteria.get('worktype')}, min {criteria.get('minsalary')}, roles: {criteria.get('roles')}. "
                    f"Job: {title} at {company}. Snippet: {desc[:600]}"
                )
                if verdict.strip().upper().startswith("FAIL"):
                    skipped.append(f"{company} — {title} (AI: {verdict.strip()[:50]})"); continue
                qualify_note = verdict.strip()[:80]
            except Exception:
                pass

        record = {
            "id": str(int(datetime.now().timestamp() * 1000) + len(added)),
            "company": company, "contact": None, "email": None,
            "linkedin": job_url, "jobtitle": title, "joburl": job_url,
            "location": loc_type, "salary": job.get("salary"),
            "status": "not_contacted", "followup": followup_date,
            "confirmed": "false", "submittedby": None,
            "notes": f"LinkedIn import.{f' AI: {qualify_note}' if qualify_note else ''}",
            "dateAdded": datetime.now().isoformat(), "lastUpdated": datetime.now().isoformat(),
        }
        contacts.append(record)
        added.append(f"✅ {company} — {title} (ID: {record['id']})")

    if added:
        _save_contacts(contacts)
    await ctx.report_progress(1.0, "Done!")

    lines = [f"## LinkedIn Jobs Import — {len(added)} added, {len(skipped)} skipped\n"]
    lines.extend(added[:20])
    if len(added) > 20: lines.append(f"...and {len(added)-20} more")
    if skipped: lines.append(f"\n**Skipped**: " + " | ".join(skipped[:8]))
    lines.append(f"\n→ Next: `job_search_draft_email` or `gmail_automation.py batch_outreach`")
    return "\n".join(lines)


@mcp.tool(
    name="job_search_import_recruiters",
    annotations={"title": "Import LinkedIn Recruiters to Tracker", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
)
async def job_search_import_recruiters(params: ImportLinkedInRecruitersInput) -> str:
    """Import recruiter profiles from the linkedin MCP server into your tracker.

    Takes JSON from linkedin's search_people tool and adds each person as a
    new 'not_contacted' contact ready for outreach.

    Workflow: linkedin_build_search_query (recruiters) → search_people (linkedin server) → paste JSON here.

    Args:
        params (ImportLinkedInRecruitersInput): people_json and job_title_context.

    Returns:
        str: Recruiters added with IDs and LinkedIn URLs, ready for outreach.
    """
    try:
        data = json.loads(params.people_json)
    except json.JSONDecodeError:
        return "Error: Invalid JSON. Paste output from the linkedin MCP server's search_people tool."

    people = data if isinstance(data, list) else data.get("people", data.get("results", data.get("profiles", [])))
    if not people:
        return "No people in the JSON."

    contacts      = _load_contacts()
    added, skipped = [], []
    followup_date = (date.today() + timedelta(days=3)).isoformat()

    for person in people:
        name     = person.get("name", person.get("fullName", "Unknown"))
        title    = person.get("title", person.get("headline", "Recruiter"))
        company  = person.get("company", person.get("currentCompany", person.get("organization", "Unknown")))
        linkedin = person.get("profileUrl", person.get("url", person.get("linkedinUrl", "")))
        location = person.get("location", "")

        already = any(c.get("contact","").lower()==name.lower() and c.get("company","").lower()==company.lower() for c in contacts if c.get("contact"))
        if already:
            skipped.append(f"{name} @ {company}"); continue

        record = {
            "id": str(int(datetime.now().timestamp() * 1000) + len(added)),
            "company": company, "contact": name,
            "email": person.get("email"), "linkedin": linkedin,
            "jobtitle": params.job_title_context or "Open Role",
            "joburl": linkedin, "location": "remote", "salary": None,
            "status": "not_contacted", "followup": followup_date,
            "confirmed": "false", "submittedby": None,
            "notes": f"Recruiter: {title}. {location}. LinkedIn import.".strip(),
            "dateAdded": datetime.now().isoformat(), "lastUpdated": datetime.now().isoformat(),
        }
        contacts.append(record)
        added.append({"name": name, "company": company, "id": record["id"], "linkedin": linkedin})

    if added:
        _save_contacts(contacts)

    lines = [f"## Recruiters Imported — {len(added)} added, {len(skipped)} skipped\n"]
    for r in added:
        li = f" · [LinkedIn]({r['linkedin']})" if r.get("linkedin") else ""
        lines.append(f"  • **{r['name']}** @ {r['company']} (ID: `{r['id']}`){li}")
    if skipped:
        lines.append(f"\n**Already tracked**: {', '.join(skipped[:5])}")
    if added:
        lines.append(f"\n→ Generate outreach: `job_search_draft_email` with any ID above")
        lines.append(f"→ Batch email all: `python gmail_automation.py batch_outreach`")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# TOOLS — Criteria Management
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="job_search_get_criteria",
    annotations={"title": "Get Job Search Criteria", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
async def job_search_get_criteria() -> str:
    """Get the current saved job search criteria (non-negotiables and preferences).

    Returns:
        str: Formatted criteria including work type, salary floor, preferred roles,
             geography, must-haves, and nice-to-haves.
    """
    c = _load_criteria()
    return (
        f"# Job Search Criteria — Montrez Cox\n\n"
        f"- **Work Type**: {c.get('worktype', '—')}\n"
        f"- **Min Salary**: {c.get('minsalary', '—')}\n"
        f"- **Preferred Roles**: {c.get('roles', '—')}\n"
        f"- **Geography**: {c.get('geo', '—')}\n"
        f"- **Company Size**: {c.get('size', '—')}\n"
        f"- **Target Contacts**: {c.get('target', '100')}\n\n"
        f"**Must-Haves**: {c.get('musthaves', '—')}\n\n"
        f"**Nice-to-Haves**: {c.get('nicetohaves', '—')}"
    )


@mcp.tool(
    name="job_search_update_criteria",
    annotations={"title": "Update Job Search Criteria", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)
async def job_search_update_criteria(params: UpdateCriteriaInput) -> str:
    """Update your job search criteria. Only provided fields are updated.

    Args:
        params (UpdateCriteriaInput): Any criteria fields to update.

    Returns:
        str: Confirmation showing what was changed.
    """
    criteria = _load_criteria()
    changes = []
    for field in ("worktype", "minsalary", "roles", "geo", "musthaves", "nicetohaves"):
        val = getattr(params, field, None)
        if val is not None:
            old = criteria.get(field)
            criteria[field] = val
            changes.append(f"{field}: '{old}' → '{val}'")
    _save_criteria(criteria)
    return f"✅ Criteria updated:\n" + "\n".join(f"  • {c}" for c in changes) if changes else "No changes provided."


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run()
