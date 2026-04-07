"""Add all 6 sequence steps to the PlusVibe campaign.

Step 1  — Day 0:  Initial outreach (brand love + opportunity)
Step 2  — Day 4:  Creator Economy Proof (followup_2)
Step 3  — Day 9:  Category Insight (followup_3)
Step 4  — Day 16: Direct Question (followup_4)
Step 5  — Day 25: Right Person? (followup_5)
Step 6  — Day 35: Breakup email (followup_6)

PlusVibe variables used:
  {{first_name}}          — contact first name
  {{company_name}}   — cleaned company name
  {{custom_niche}}        — LLM-detected niche
  {{custom_custom_body}}  — brand love + opportunity paragraph (HTML)
  {{custom_custom_subject}} — subject line
"""
import json, os, sys
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

API_KEY      = os.getenv("PLUSVIBE_API_KEY")
BASE_URL     = os.getenv("PLUSVIBE_API_URL", "https://api.plusvibe.ai/api/v1")
WORKSPACE_ID = os.getenv("PLUSVIBE_WORKSPACE_ID", "69adcb166f1add4e083d64ee")
CAMPAIGN_ID  = os.getenv("PLUSVIBE_CAMPAIGN_ID", "69d0537061e40aacb9647685")

HEADERS = {"x-api-key": API_KEY, "Content-Type": "application/json"}

# ── Sequence steps ────────────────────────────────────────────────────────────

SEQUENCES = [
    # ── Step 1: Initial outreach ─────────────────────────────────────────────
    {
        "step": 1,
        "wait_time": 1,
        "variations": [{
            "variation": "A",
            "name": "Initial outreach",
            "subject": "{{custom_custom_subject}}",
            "body": """<p>Hey {{first_name}}, hope your week is going good</p>
{{custom_email_body}}
<p>We handle the entire platform for you, end-to-end, and are confident we can take {{company_name}} to the top of its category within months.</p>
<p>Would love to explore this opportunity further with you! Even if this isn't on your radar, we can use this time as a discovery conversation about TikTok Shop to point you in the right direction.</p>
<p>Would you be interested in hearing more details?</p>
<p>Warmly,<br>Sayim Khan</p>
<p>P.S. ~ also, we don't identify as a traditional agency, so no tossing the work out to crappy account managers. We're a 2-man team who get the job done ourselves completely :)</p>"""
        }]
    },
]

# ── All follow-up steps (not added yet) ───────────────────────────────────────

ALL_SEQUENCES = [
    SEQUENCES[0],

    # ── Step 2: Day 4 — Creator Economy Proof ────────────────────────────────
    {
        "step": 2,
        "wait_time": 4,
        "variations": [{
            "variation": "A",
            "name": "Follow-up 1 — Creator proof",
            "subject": "quick follow up - {{company_name}}",
            "body": """<p>Hey {{first_name}},</p>
<p>Wanted to share something relevant — one of the ecommerce brands we work with just crossed 500 daily orders purely from creator-led TikTok Shop content. No paid ads, no influencer fees. Just product samples going to creators who genuinely wanted to try them.</p>
<p>The reason it works so well for {{custom_niche}} brands is that customers genuinely love watching how products fit into real routines, and that organic trust is what drives the purchase.</p>
<p>Thought of {{company_name}} immediately because your products are exactly the kind creators love showing off.</p>
<p>Worth a quick 15-min chat?</p>
<p>Sayim</p>"""
        }]
    },

    # ── Step 3: Day 9 — Category Insight ─────────────────────────────────────
    {
        "step": 3,
        "wait_time": 5,
        "variations": [{
            "variation": "A",
            "name": "Follow-up 2 — Category insight",
            "subject": "{{custom_niche}} on tiktok shop",
            "body": """<p>Hey {{first_name}},</p>
<p>Thought you'd find this interesting — {{custom_niche}} is currently one of the fastest-growing categories on TikTok Shop. The brands getting in now are basically building their creator network before it gets crowded.</p>
<p>What's wild is that most of these brands aren't spending on ads. The creator content itself is the growth engine — and once you have 500+ creators posting about your products, the compound effect is massive.</p>
<p>Happy to share a quick breakdown of what's working in your category right now. No pitch — just useful context if TikTok Shop is anywhere on your roadmap.</p>
<p>Sayim</p>"""
        }]
    },

    # ── Step 4: Day 16 — Direct Question ─────────────────────────────────────
    {
        "step": 4,
        "wait_time": 7,
        "variations": [{
            "variation": "A",
            "name": "Follow-up 3 — Direct question",
            "subject": "{{company_name}} + tiktok",
            "body": """<p>Hey {{first_name}},</p>
<p>Honest question — is TikTok Shop something you're actively thinking about, or is the timing just not right?</p>
<p>No pressure either way. Just want to make sure I'm not filling your inbox with something that's not relevant to where you're at right now.</p>
<p>Sayim</p>"""
        }]
    },

    # ── Step 5: Day 25 — Right Person? ───────────────────────────────────────
    {
        "step": 5,
        "wait_time": 9,
        "variations": [{
            "variation": "A",
            "name": "Follow-up 4 — Right person",
            "subject": "right person?",
            "body": """<p>Hey {{first_name}},</p>
<p>I might be reaching out to the wrong person here — if someone else at {{company_name}} handles growth, e-commerce, or new sales channels, I'd love an intro.</p>
<p>And if it is you — totally get it if the timing isn't right. No hard feelings.</p>
<p>Sayim</p>"""
        }]
    },

    # ── Step 6: Day 35 — Breakup email ───────────────────────────────────────
    {
        "step": 6,
        "wait_time": 10,
        "variations": [{
            "variation": "A",
            "name": "Follow-up 5 — Breakup",
            "subject": "last one from me",
            "body": """<p>Hey {{first_name}},</p>
<p>I'll stop filling your inbox after this one.</p>
<p>If TikTok Shop ever becomes a priority for {{company_name}}, just reply here and I'll pick it right back up. The opportunity isn't going anywhere — but the early-mover advantage in your category won't last forever.</p>
<p>Either way, love what you're building with {{company_name}}. Rooting for you.</p>
<p>Sayim</p>
<p>P.S. ~ if there's someone else at {{company_name}} I should be talking to, a name would be super helpful.</p>"""
        }]
    },
]


def add_sequences():
    payload = {
        "workspace_id": WORKSPACE_ID,
        "campaign_id": CAMPAIGN_ID,
        "sequences": SEQUENCES,
        "first_wait_time": 1,
        "first_wait_time_unit": "days",
    }

    print(f"Adding {len(SEQUENCES)} sequence steps to campaign {CAMPAIGN_ID}...")
    print()

    resp = requests.patch(
        f"{BASE_URL}/campaign/update/campaign",
        json=payload,
        headers=HEADERS,
        timeout=30,
    )

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    if resp.status_code in (200, 201, 202) and (data.get("status") == "success" or data.get("id")):
        print("Sequences added successfully!")
        print()
        for s in SEQUENCES:
            step = s["step"]
            name = s["variations"][0]["name"]
            subj = s["variations"][0]["subject"]
            wait = s["wait_time"]
            day  = sum(x["wait_time"] for x in SEQUENCES[:step])
            print(f"  Step {step} (Day {day:2d}, +{wait}d) — {name}")
            print(f"           Subject: {subj}")
    else:
        print(f"Failed: {resp.status_code}")
        print(json.dumps(data, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    add_sequences()
