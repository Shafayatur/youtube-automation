# YouTube Reel Agent — Setup Guide

## What This Agent Does
Every day it automatically:
1. Scans your Google Drive folder for videos added **that day**
2. Downloads up to 3 of them
3. Uses **Claude AI** to write a catchy title & description
4. Uploads them to YouTube as Shorts/Reels

---

## Step 1 — Install Python Dependencies

```bash
pip install -r requirements.txt
```

---

## Step 2 — Google Cloud Console Setup

### Enable APIs
1. Go to https://console.cloud.google.com
2. Create a new project (or select existing)
3. Go to **APIs & Services → Library**
4. Enable **Google Drive API**
5. Enable **YouTube Data API v3**

### Create OAuth Credentials
1. Go to **APIs & Services → Credentials**
2. Click **Create Credentials → OAuth 2.0 Client IDs**
3. Choose **Desktop App**, name it "Reel Agent"
4. Download the JSON file
5. **Rename it to `credentials.json`** and place it in this folder

### Set OAuth Consent Screen
1. Go to **APIs & Services → OAuth consent screen**
2. Choose **External**, fill in app name
3. Add your Google account as a **Test User**
4. Add scopes: `drive.readonly` and `youtube.upload`

---

## Step 3 — Get Your FREE Gemini API Key

1. Go to https://aistudio.google.com/app/apikey
2. Sign in with your Google account (same one as Drive/YouTube)
3. Click **Create API Key**
4. Copy the key

Then set it as an environment variable:

```bash
# Mac / Linux
export GEMINI_API_KEY="AIza..."
```

On Windows:
```cmd
set GEMINI_API_KEY=AIza...
```

✅ Gemini free tier = **1,500 requests/day**. Uploading 3 reels uses only 3. Free forever.

---

## Step 4 — Configure agent.py

Open `agent.py` and edit these lines at the top:

```python
DRIVE_FOLDER_ID = "YOUR_GOOGLE_DRIVE_FOLDER_ID"  # ← paste your folder ID here
MAX_UPLOADS_PER_DAY = 3                            # max reels per day
YOUTUBE_PRIVACY = "public"                         # public | unlisted | private
YOUTUBE_TAGS = ["reels", "shorts", "viral"]        # your default tags
```

**How to find your Google Drive Folder ID:**
Open the folder in Google Drive → look at the URL:
`https://drive.google.com/drive/folders/THIS_PART_IS_YOUR_FOLDER_ID`

---

## Step 5 — First Run (Authorize)

Run once manually to authorize your Google account:

```bash
python agent.py
```

A browser window will open — log in and allow access.
This saves a `token.json` file for future runs.

---

## Step 6 — Schedule Daily Runs

### Option A: Linux/Mac (cron)

```bash
crontab -e
```

Add this line to run every day at 9:00 AM:
```
0 9 * * * cd /path/to/agent && GEMINI_API_KEY=AIza... python agent.py >> agent.log 2>&1
```

### Option B: Windows (Task Scheduler)
1. Open **Task Scheduler**
2. Create Basic Task → Daily → 9:00 AM
3. Action: Start a program → `python`
4. Arguments: `agent.py`
5. Start in: `C:\path\to\agent\folder`

### Option C: Google Cloud Run Jobs (fully automated cloud)
```bash
# Build and deploy as a Cloud Run Job
gcloud run jobs create reel-agent \
  --image=python:3.11 \
  --command="python" \
  --args="agent.py" \
  --schedule="0 9 * * *" \
  --region=us-central1
```

### Option D: GitHub Actions (free, cloud-based)

Create `.github/workflows/daily_upload.yml`:

```yaml
name: Daily Reel Upload
on:
  schedule:
    - cron: '0 9 * * *'   # 9 AM UTC daily
  workflow_dispatch:        # allows manual trigger

jobs:
  upload:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python agent.py
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
```

Store your `credentials.json` and `token.json` as GitHub Secrets.

---

## File Structure

```
youtube_reel_agent/
├── agent.py            ← main script
├── requirements.txt    ← dependencies
├── credentials.json    ← Google OAuth (you create this)
├── token.json          ← auto-created after first login
├── uploaded.json       ← tracks already-uploaded files
└── agent.log           ← run logs
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `credentials.json not found` | Download from Google Cloud Console, rename correctly |
| `quota exceeded` | YouTube allows ~6 uploads/day for new apps. Apply for quota increase |
| `token expired` | Delete `token.json` and re-run agent.py to re-authorize |
| `No new videos` | Make sure videos were added to Drive **today** (UTC time) |
| Gemini API error | Check `GEMINI_API_KEY` is set correctly |

---

## YouTube Upload Quota Note

New Google Cloud projects get **10,000 units/day** of YouTube API quota.
Each video upload costs ~1,600 units → you can upload ~6 videos/day.
3 reels/day is well within limits. ✅
