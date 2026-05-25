"""
YouTube Reel Agent
------------------
Posts 1 video per run, 3 times/day (10 AM, 4 PM, 8 PM BD time).
Videos are posted serially from the Drive folder (1.mp4, 2.mp4, 3.mp4...).
Captions are read from captions.txt in the same Drive folder.
Gemini AI is used as fallback if no caption found.

100% Free APIs:
  - Google Drive API (free)
  - YouTube Data API v3 (free)
  - Google Gemini API (free tier: 1,500 requests/day)
  - GitHub Actions scheduler (free)
"""

import os
import re
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

load_dotenv()

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")
if not DRIVE_FOLDER_ID:
    raise EnvironmentError("DRIVE_FOLDER_ID environment variable not set.")

YOUTUBE_CATEGORY_ID = "22"          # 22 = People & Blogs
YOUTUBE_PRIVACY     = "public"      # public | unlisted | private
YOUTUBE_TAGS        = ["shorts", "reels", "viral", "trending", "fyp"]
CAPTIONS_FILENAME   = "captions.txt"
UPLOAD_LOG_FILE     = "uploaded.json"
LOG_FILE            = "agent.log"

# Keywords in description that indicate kids content
KIDS_KEYWORDS = [
    "kids", "children", "child", "toddler", "baby", "nursery",
    "cartoon", "animated", "for kids", "educational for children"
]

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
#  AUTH
# ─────────────────────────────────────────────
def get_google_credentials():
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
                raise FileNotFoundError("credentials.json not found.")
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return creds


# ─────────────────────────────────────────────
#  UPLOAD TRACKING
# ─────────────────────────────────────────────
def load_uploaded_ids() -> set:
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
#  GOOGLE DRIVE — list ALL videos sorted by name
# ─────────────────────────────────────────────
def get_all_videos(drive_service) -> list:
    """
    Return all video files in the Drive folder sorted by filename.
    Client should name files: 1.mp4, 2.mp4, 3.mp4 ... for correct order.
    """
    query = f"'{DRIVE_FOLDER_ID}' in parents and trashed = false"

    result = drive_service.files().list(
        q=query,
        fields="files(id, name, mimeType)",
        orderBy="name",
        pageSize=100,
    ).execute()

    files = result.get("files", [])
    videos = [
        f for f in files
        if Path(f["name"]).suffix.lower() in VIDEO_EXTENSIONS
    ]

    log.info(f"Total videos in folder: {len(videos)}")
    return videos


def get_next_video(drive_service) -> dict | None:
    """Return the next unposted video (lowest filename that hasn't been uploaded)."""
    all_videos = get_all_videos(drive_service)
    uploaded_ids = load_uploaded_ids()

    for video in all_videos:
        if video["id"] not in uploaded_ids:
            log.info(f"Next video to upload: {video['name']}")
            return video

    log.info("All videos in folder have been uploaded.")
    return None


# ─────────────────────────────────────────────
#  CAPTIONS.TXT — parse from Drive
# ─────────────────────────────────────────────
def fetch_captions_file(drive_service) -> str | None:
    """Download captions.txt from Drive folder and return its content."""
    query = (
        f"'{DRIVE_FOLDER_ID}' in parents "
        f"and name = '{CAPTIONS_FILENAME}' "
        f"and trashed = false"
    )
    result = drive_service.files().list(q=query, fields="files(id, name)").execute()
    files = result.get("files", [])

    if not files:
        log.warning("captions.txt not found in Drive folder.")
        return None

    file_id = files[0]["id"]
    request = drive_service.files().get_media(fileId=file_id)
    content = request.execute()
    return content.decode("utf-8")


def parse_captions(captions_text: str) -> dict:
    """
    Parse captions.txt into a dict keyed by filename.

    Expected format:
        1.mp4
        Title: My Amazing Reel
        Description: Watch this! #shorts #viral

        2.mp4
        Title: Another Video
        Description: Cool content. #reels #fyp
    """
    captions = {}
    blocks = re.split(r'\n\s*\n', captions_text.strip())

    for block in blocks:
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue

        filename = lines[0].strip()
        title = ""
        description = ""

        for line in lines[1:]:
            if line.lower().startswith("title:"):
                title = line[6:].strip()
            elif line.lower().startswith("description:"):
                description = line[12:].strip()

        if filename and title:
            captions[filename] = {"title": title, "description": description}

    log.info(f"Parsed captions for {len(captions)} video(s) from captions.txt")
    return captions


def get_metadata_from_captions(drive_service, filename: str) -> dict | None:
    """Try to get title+description from captions.txt for a given filename."""
    text = fetch_captions_file(drive_service)
    if not text:
        return None

    captions = parse_captions(text)
    if filename in captions:
        log.info(f"  Found caption in captions.txt for: {filename}")
        return captions[filename]

    log.warning(f"  No caption entry found for '{filename}' in captions.txt")
    return None


# ─────────────────────────────────────────────
#  GEMINI — fallback metadata generation
# ─────────────────────────────────────────────
def generate_metadata_gemini(filename: str) -> dict:
    """Fallback: use Gemini to generate title+description from filename."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY not set.")

    prompt = (
        f'You are a YouTube Shorts content strategist.\n'
        f'Given the video filename: "{filename}"\n\n'
        f'Generate:\n'
        f'1. A catchy, SEO-optimized YouTube Shorts title (max 100 chars, no hashtags)\n'
        f'2. An engaging description (2-3 sentences + 5 hashtags at the end)\n\n'
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
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        raw = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        log.info(f"  Gemini generated title: {data['title']}")
        return data
    except Exception as e:
        log.warning(f"Gemini failed ({e}), using filename as title.")
        return {
            "title": Path(filename).stem.replace("_", " ").replace("-", " ").title(),
            "description": "Watch this amazing reel! #shorts #reels #viral #trending #fyp",
        }


def get_metadata(drive_service, filename: str) -> dict:
    """Get metadata: captions.txt first, Gemini as fallback."""
    data = get_metadata_from_captions(drive_service, filename)
    if data:
        return data
    log.info(f"  Falling back to Gemini for: {filename}")
    return generate_metadata_gemini(filename)


# ─────────────────────────────────────────────
#  KIDS DETECTION
# ─────────────────────────────────────────────
def is_for_kids(description: str) -> bool:
    desc_lower = description.lower()
    detected = any(kw in desc_lower for kw in KIDS_KEYWORDS)
    if detected:
        log.info("  Kids content detected from description keywords.")
    return detected


# ─────────────────────────────────────────────
#  DOWNLOAD
# ─────────────────────────────────────────────
def download_video(drive_service, file_id: str, filename: str) -> Path:
    tmp_path = Path("/tmp") / filename
    request = drive_service.files().get_media(fileId=file_id)
    with open(tmp_path, "wb") as fh:
        from googleapiclient.http import MediaIoBaseDownload
        import io
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            log.info(f"  Downloading {filename}: {int(status.progress() * 100)}%")
    return tmp_path


# ─────────────────────────────────────────────
#  YOUTUBE UPLOAD
# ─────────────────────────────────────────────
def upload_to_youtube(youtube_service, video_path: Path, title: str, description: str, for_kids: bool):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": YOUTUBE_TAGS,
            "categoryId": YOUTUBE_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": YOUTUBE_PRIVACY,
            "selfDeclaredMadeForKids": for_kids,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/*",
        resumable=True,
        chunksize=5 * 1024 * 1024,
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

    # Set auto-generated thumbnail (YouTube picks best frame)
    try:
        youtube_service.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(video_path), mimetype="video/*")
        )
        log.info("  Auto thumbnail set.")
    except Exception as e:
        log.warning(f"  Thumbnail set skipped: {e}")

    log.info(f"  ✅ Uploaded! https://youtube.com/shorts/{video_id}")
    return video_id


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def run_agent():
    log.info("=" * 60)
    log.info("🎬 YouTube Reel Agent — starting run")
    log.info("=" * 60)

    creds = get_google_credentials()
    drive_service  = build("drive",   "v3", credentials=creds)
    youtube_service = build("youtube", "v3", credentials=creds)

    video = get_next_video(drive_service)
    if not video:
        log.info("No new videos to upload. Exiting.")
        return

    filename = video["name"]
    log.info(f"\n📁 Processing: {filename} (Drive ID: {video['id']})")

    try:
        local_path = download_video(drive_service, video["id"], filename)
        metadata   = get_metadata(drive_service, filename)
        for_kids   = is_for_kids(metadata["description"])

        yt_id = upload_to_youtube(
            youtube_service,
            local_path,
            metadata["title"],
            metadata["description"],
            for_kids,
        )

        save_uploaded_id(video["id"])
        local_path.unlink(missing_ok=True)

        log.info(f"\n✅ Done: {filename} → https://youtube.com/shorts/{yt_id}")

    except Exception as e:
        log.error(f"❌ Failed: {filename} — {e}")

    log.info("=" * 60)
    log.info("Agent run complete.")


if __name__ == "__main__":
    run_agent()