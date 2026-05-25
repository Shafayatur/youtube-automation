"""
YouTube Reel Agent
------------------
Watches a Google Drive folder for new video files added today,
auto-generates title & description using Google Gemini AI (FREE),
then uploads them as YouTube Shorts / Reels.

100% Free APIs:
  - Google Drive API (free)
  - YouTube Data API v3 (free)
  - Google Gemini API (free tier: 1,500 requests/day)
  - GitHub Actions scheduler (free)

Schedule this script daily using a cron job or cloud scheduler.
"""

import os
import io
import json
import datetime
import logging
import urllib.request
from pathlib import Path
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# Load environment variables from .env (if present)
load_dotenv()

# ─────────────────────────────────────────────
#  CONFIGURATION — edit these values
# ─────────────────────────────────────────────
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")
if not DRIVE_FOLDER_ID:
    raise EnvironmentError(
        "DRIVE_FOLDER_ID environment variable not set. Please set DRIVE_FOLDER_ID in your .env or export it before running."
    )

MAX_UPLOADS_PER_DAY = 3                             # Max reels to upload per run
YOUTUBE_CATEGORY_ID = "22"                         # 22 = People & Blogs
YOUTUBE_PRIVACY = "public"                         # public | unlisted | private
YOUTUBE_TAGS = ["reels", "shorts", "viral"]        # Default tags
LOG_FILE = "agent.log"
UPLOAD_LOG_FILE = "uploaded.json"                  # Tracks already-uploaded files

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
]

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)




# ─────────────────────────────────────────────
#  AUTH HELPERS
# ─────────────────────────────────────────────
def get_google_credentials():
    """Load or refresh Google OAuth2 credentials."""
    creds = None
    token_path = Path("token.json")
    creds_path = Path("credentials.json")

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                raise FileNotFoundError(
                    "credentials.json not found. Download it from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return creds


# ─────────────────────────────────────────────
#  UPLOAD TRACKING
# ─────────────────────────────────────────────
def load_uploaded_ids():
    if Path(UPLOAD_LOG_FILE).exists():
        with open(UPLOAD_LOG_FILE) as f:
            return set(json.load(f))
    return set()


def save_uploaded_id(file_id: str):
    ids = load_uploaded_ids()
    ids.add(file_id)
    with open(UPLOAD_LOG_FILE, "w") as f:
        json.dump(list(ids), f, indent=2)


# ─────────────────────────────────────────────
#  GOOGLE DRIVE — find today's new videos
# ─────────────────────────────────────────────
def get_todays_new_videos(drive_service):
    """Return up to MAX_UPLOADS_PER_DAY video files added to Drive folder today."""
    today = datetime.datetime.utcnow().date().isoformat()
    tomorrow = (datetime.datetime.utcnow().date() + datetime.timedelta(days=1)).isoformat()

    query = (
        f"'{DRIVE_FOLDER_ID}' in parents "
        f"and createdTime >= '{today}T00:00:00Z' "
        f"and createdTime < '{tomorrow}T00:00:00Z' "
        f"and trashed = false"
    )

    result = drive_service.files().list(
        q=query,
        fields="files(id, name, mimeType, createdTime)",
        orderBy="createdTime asc",
    ).execute()

    files = result.get("files", [])
    uploaded_ids = load_uploaded_ids()

    # Filter to video files not yet uploaded
    videos = [
        f for f in files
        if Path(f["name"]).suffix.lower() in VIDEO_EXTENSIONS
        and f["id"] not in uploaded_ids
    ]

    log.info(f"Found {len(videos)} new video(s) today (not yet uploaded).")
    return videos[:MAX_UPLOADS_PER_DAY]


# ─────────────────────────────────────────────
#  DOWNLOAD from Drive to temp filee
# ─────────────────────────────────────────────
def download_video(drive_service, file_id: str, filename: str) -> Path:
    """Download a Drive file to /tmp and return its local path."""
    tmp_path = Path("/tmp") / filename
    request = drive_service.files().get_media(fileId=file_id)
    with open(tmp_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            log.info(f"  Downloading {filename}: {int(status.progress() * 100)}%")
    return tmp_path


# ─────────────────────────────────────────────
#  AI — generate title & description (Gemini, FREE)
# ─────────────────────────────────────────────
def generate_metadata(filename: str) -> dict:
    """Use Google Gemini (free) to generate an engaging YouTube title and description."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable not set.")

    prompt = (
        f'You are a YouTube Shorts / Reels content strategist.\n'
        f'Given the video filename: "{filename}"\n\n'
        f'Generate:\n'
        f'1. A catchy, SEO-optimized YouTube Shorts title (max 100 chars, no hashtags in title)\n'
        f'2. An engaging description (2-3 sentences, include 5 relevant hashtags at the end)\n\n'
        f'Respond ONLY as valid JSON with keys: "title" and "description".\n'
        f'No markdown, no backticks, no extra text.'
    )

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 400, "temperature": 0.7},
    }).encode("utf-8")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-1.5-flash:generateContent?key={api_key}"
    )

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        raw = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Strip accidental markdown fences
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
    except Exception as e:
        log.warning(f"Gemini API error ({e}), falling back to filename-based title.")
        data = {
            "title": Path(filename).stem.replace("_", " ").replace("-", " ").title(),
            "description": "Watch this amazing reel! #shorts #reels #viral #trending #fyp",
        }

    log.info(f"  Generated title: {data['title']}")
    return data


# ─────────────────────────────────────────────
#  YOUTUBE — upload video
# ─────────────────────────────────────────────
def upload_to_youtube(youtube_service, video_path: Path, title: str, description: str):
    """Upload a video file to YouTube and return the video ID."""
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": YOUTUBE_TAGS,
            "categoryId": YOUTUBE_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": YOUTUBE_PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/*",
        resumable=True,
        chunksize=5 * 1024 * 1024,  # 5 MB chunks
    )

    request = youtube_service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info(f"  Upload progress: {int(status.progress() * 100)}%")

    video_id = response.get("id")
    log.info(f"  ✅ Uploaded! https://youtube.com/shorts/{video_id}")
    return video_id


# ─────────────────────────────────────────────
#  MAIN AGENT LOOP
# ─────────────────────────────────────────────
def run_agent():
    log.info("=" * 60)
    log.info("🎬 YouTube Reel Agent — starting daily run")
    log.info("=" * 60)

    creds = get_google_credentials()
    drive_service = build("drive", "v3", credentials=creds)
    youtube_service = build("youtube", "v3", credentials=creds)

    videos = get_todays_new_videos(drive_service)

    if not videos:
        log.info("No new videos to upload today. Exiting.")
        return

    results = []
    for video in videos:
        log.info(f"\n📁 Processing: {video['name']} (Drive ID: {video['id']})")
        try:
            # 1. Download from Drive
            local_path = download_video(drive_service, video["id"], video["name"])

            # 2. Generate AI metadata
            metadata = generate_metadata(video["name"])

            # 3. Upload to YouTube
            yt_id = upload_to_youtube(
                youtube_service,
                local_path,
                metadata["title"],
                metadata["description"],
            )

            # 4. Track uploaded file
            save_uploaded_id(video["id"])

            # 5. Cleanup temp file
            local_path.unlink(missing_ok=True)

            results.append({
                "drive_id": video["id"],
                "filename": video["name"],
                "youtube_id": yt_id,
                "title": metadata["title"],
                "url": f"https://youtube.com/shorts/{yt_id}",
                "status": "success",
            })

        except Exception as e:
            log.error(f"  ❌ Failed to process {video['name']}: {e}")
            results.append({
                "drive_id": video["id"],
                "filename": video["name"],
                "status": "failed",
                "error": str(e),
            })

    log.info("\n📊 Run Summary:")
    for r in results:
        if r["status"] == "success":
            log.info(f"  ✅ {r['filename']} → {r['url']}")
        else:
            log.info(f"  ❌ {r['filename']} → {r['error']}")

    log.info("=" * 60)
    log.info("Agent run complete.")


if __name__ == "__main__":
    run_agent()
