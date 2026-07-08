
import os
import io
import json
import time
import base64
import hashlib
import datetime as dt
from typing import Any, Dict

from flask import Flask, request, jsonify, send_from_directory
import requests
import openai

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from apscheduler.schedulers.background import BackgroundScheduler

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5").strip()

GOOGLE_SERVICE_JSON = os.getenv("GOOGLE_SERVICE_JSON", "").strip()
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "").strip()
UPLOAD_TTL_HOURS = int(os.getenv("UPLOAD_TTL_HOURS", "24"))

LOG_TO_CSV = os.getenv("LOG_TO_CSV", "1") == "1"
CSV_LOG_PATH = os.getenv("CSV_LOG_PATH", "/tmp/epic_webhook_logs.csv")

TEMP_DIR = "/tmp/uploads"
DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")
os.makedirs(TEMP_DIR, exist_ok=True)

openai.api_key = OPENAI_API_KEY
app = Flask(__name__)

SYSTEM_PROMPTS = {
    "hr": "Ты — Эпичный HR студии лазерной эпиляции 'Эпичные'. Отвечай кандидатам из Avito уверенно, доброжелательно, с лёгким зумерским вайбом. Задавай уточняющие вопросы, предлагай 2 слота времени для собеседования. Пиши от первого лица ('я').",
    "analytic": "Ты — Эпичный Аналитик студии 'Эпичные'. Анализируешь переписки и отчёты, находишь ошибки, предлагаешь улучшения. Если видишь клиентское сообщение — сформулируй премиум-ответ от студии, уверенный и доброжелательный. Отвечай с тоном бренда: уверенность, забота, премиум.",
    "finance": "Ты — Эпичный Финансист. Рассчитываешь зарплаты, P&L и KPI. Отвечай кратко, сухо и логично. Стиль — уверенный, профессиональный.",
    "content": "Ты — Эпичный Контент-продюсер бренда 'Эпичные'. Создаёшь тексты для Telegram, Авито, Instagram, лендингов. Стиль — уверенный, премиум, с лёгким креативом. Добавляй короткие CTA.",
    "mentor": "Ты — Эпичный Наставник управляющего. Помогаешь сохранять внутренний баланс, рассуждаешь рационально, задаёшь направляющие вопросы. Тон — взрослый, мудрый, с заботой, но без сюсюканья.",
    "product": "Ты — Эпичный Продакт-коуч. Помогаешь переходить от project к product управлению. Дай чёткие шаги, материалы и план недели. Тон — структурный, мотивирующий, системный."
}

def log_csv(record: Dict[str, Any]):
    if not LOG_TO_CSV:
        return
    header = ["ts","role","message_len","attachments_count","reply_len","error"]
    exists = os.path.exists(CSV_LOG_PATH)
    with open(CSV_LOG_PATH, "a", encoding="utf-8") as f:
        if not exists:
            f.write(";".join(header) + "\n")
        row = [
            dt.datetime.utcnow().isoformat(),
            record.get("role",""),
            str(record.get("message_len",0)),
            str(record.get("attachments_count",0)),
            str(record.get("reply_len",0)),
            (record.get("error","") or "").replace("\n"," ").replace(";"," ")
        ]
        f.write(";".join(row) + "\n")

def get_drive_service():
    if not GOOGLE_SERVICE_JSON or not DRIVE_FOLDER_ID:
        return None
    try:
        info = json.loads(GOOGLE_SERVICE_JSON)
        creds = Credentials.from_service_account_info(info, scopes=[
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/drive.file",
        ])
        return build("drive","v3",credentials=creds)
    except Exception as e:
        print("Drive auth error:", e)
        return None

def save_temp_from_url(url: str) -> str:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    ext = ""
    ct = r.headers.get("content-type","")
    if "png" in ct: ext = ".png"
    elif "jpeg" in ct or "jpg" in ct: ext = ".jpg"
    elif "pdf" in ct: ext = ".pdf"
    elif "webp" in ct: ext = ".webp"
    name = hashlib.sha1((url+str(time.time())).encode()).hexdigest()[:12] + ext
    path = os.path.join(TEMP_DIR, name)
    with open(path,"wb") as f: f.write(r.content)
    return path

def save_temp_from_base64(b64: str, ext: str = "") -> str:
    data = base64.b64decode(b64.split(",")[-1])
    name = hashlib.sha1((str(len(data))+str(time.time())).encode()).hexdigest()[:12] + ext
    path = os.path.join(TEMP_DIR, name)
    with open(path,"wb") as f: f.write(data)
    return path

def upload_to_drive(local_path: str, drive, folder_id: str) -> str:
    file_name = os.path.basename(local_path)
    media = MediaIoBaseUpload(io.FileIO(local_path,"rb"), mimetype="application/octet-stream", chunksize=1024*1024, resumable=True)
    meta = {"name": file_name, "parents":[folder_id]}
    created = drive.files().create(body=meta, media_body=media, fields="id, webViewLink").execute()
    return created.get("webViewLink")

def cleanup_old():
    cutoff = time.time() - UPLOAD_TTL_HOURS*3600
    removed = 0
    for fn in os.listdir(TEMP_DIR):
        fp = os.path.join(TEMP_DIR, fn)
        try:
            if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                os.remove(fp)
                removed += 1
        except Exception:
            pass
    print(f"🧹 cleanup: removed {removed} files")

scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(cleanup_old, "interval", hours=12, id="cleanup_job", replace_existing=True)
scheduler.start()

@app.route("/", methods=["GET"])
def status():
    return jsonify({
        "ok": True,
        "service": "Epic GPT Webhook Pro",
        "default_role": "analytic",
        "drive_enabled": bool(GOOGLE_SERVICE_JSON and DRIVE_FOLDER_ID),
        "model": OPENAI_MODEL
    }), 200

@app.route("/certificates/", methods=["GET"])
def certificates_page():
    return send_from_directory(DOCS_DIR, "index.html")

@app.route("/certificates/<path:filename>", methods=["GET"])
def certificates_files(filename: str):
    return send_from_directory(DOCS_DIR, filename, as_attachment=filename.lower().endswith(".pdf"))

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    role = data.get("role","analytic")
    message = (data.get("message") or "").strip()
    attachments = data.get("attachments", []) or []

    drive = get_drive_service()
    uploaded_links = []
    desc = []

    for item in attachments:
        try:
            path = None
            if isinstance(item, str) and (item.startswith("http://") or item.startswith("https://")):
                path = save_temp_from_url(item)
            elif isinstance(item, dict) and "base64" in item:
                path = save_temp_from_base64(item["base64"], item.get("ext",""))
            elif isinstance(item, str) and item.startswith("data:"):
                path = save_temp_from_base64(item, "")
            if path:
                link = None
                if drive:
                    try:
                        link = upload_to_drive(path, drive, DRIVE_FOLDER_ID)
                        uploaded_links.append(link)
                    except Exception as e:
                        print("Drive upload error:", e)
                desc.append(os.path.basename(path) + (f" → {link}" if link else ""))
        except Exception as e:
            print("Attachment error:", e)

    user_message = message
    if desc:
        user_message += "\n\nВложения:\n" + "\n".join(f"- {x}" for x in desc)

    system_prompt = SYSTEM_PROMPTS.get(role, SYSTEM_PROMPTS["analytic"])

    reply_text = ""
    error = None
    try:
        completion = openai.ChatCompletion.create(
            model=OPENAI_MODEL,
            messages=[
                {"role":"system","content": system_prompt},
                {"role":"user","content": user_message}
            ]
        )
        reply_text = completion.choices[0].message["content"]
    except Exception as e:
        error = str(e)

    log_csv({
        "role": role,
        "message_len": len(message),
        "attachments_count": len(attachments),
        "reply_len": len(reply_text),
        "error": error
    })

    if error:
        return jsonify({"error": error}), 500

    return jsonify({"reply": reply_text, "role": role, "attachments_uploaded": uploaded_links})
