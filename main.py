import os
import tempfile
import shutil
import logging
from datetime import datetime
from functools import wraps

from flask import Flask, request, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from dotenv import load_dotenv
import yt_dlp

# ——— Load your secret API key from Replit’s Secrets panel ———
load_dotenv()
API_KEY = os.getenv('API_KEY')

app = Flask(__name__)
CORS(app)

# ——— Database setup ———
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///downloads.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

logging.basicConfig(level=logging.INFO)
MAX_DOWNLOAD_RETRIES = 2

class DownloadHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), nullable=False)
    file_format = db.Column(db.String(10), nullable=False)
    quality = db.Column(db.String(20))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def as_dict(self):
        return {
            'id': self.id,
            'url': self.url,
            'file_format': self.file_format,
            'quality': self.quality,
            'timestamp': self.timestamp.isoformat() + 'Z'
        }

with app.app_context():
    db.create_all()

# ——— API key decorator ———
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if auth != f'Bearer {API_KEY}':
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

# ——— yt-dlp options ———
def get_ydl_opts(fmt, quality, tpl):
    if fmt == 'mp4':
        return {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': tpl,
            'merge_output_format': 'mp4',
            'retries': MAX_DOWNLOAD_RETRIES,
        }
    elif fmt == 'mp3':
        return {
            'format': 'bestaudio/best',
            'outtmpl': tpl,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'retries': MAX_DOWNLOAD_RETRIES,
        }
    else:
        raise ValueError(f'Unsupported format: {fmt}')

def download_single(url, fmt, quality, tpl):
    opts = get_ydl_opts(fmt, quality, tpl)
    for attempt in range(MAX_DOWNLOAD_RETRIES):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            break
        except Exception as e:
            logging.warning(f"Attempt {attempt+1} failed: {e}")
            if attempt == MAX_DOWNLOAD_RETRIES - 1:
                raise

    # locate the downloaded file
    d = os.path.dirname(tpl)
    prefix = os.path.basename(tpl).split('.')[0]
    for fname in os.listdir(d):
        if fname.startswith(prefix) and fname.lower().endswith(fmt):
            return os.path.join(d, fname)
    raise FileNotFoundError(f"No .{fmt} file found for {prefix}")

# ——— Routes ———
@app.route('/')
def index():
    return "JustPaste Backend Running!"

@app.route('/healthz')
def healthz():
    return 'OK', 200

@app.route('/download_get', methods=['GET'])
@require_api_key
def download_get():
    url = request.args.get('url')
    fmt = request.args.get('format')
    quality = request.args.get('quality', 'best')
    if not url or not fmt:
        return jsonify({'error': 'Missing url or format'}), 400

    tmp = tempfile.mkdtemp()
    try:
        tpl = os.path.join(tmp, 'dl.%(ext)s')
        path = download_single(url, fmt, quality, tpl)

        db.session.add(DownloadHistory(
            url=url, file_format=fmt, quality=quality))
        db.session.commit()

        return send_file(path,
                         as_attachment=True,
                         download_name=f"JustPaste.{fmt}")
    except Exception as e:
        logging.exception("Download error")
        return jsonify({'error': str(e)}), 500
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

@app.route('/history', methods=['GET'])
@require_api_key
def history():
    recs = DownloadHistory.query.order_by(
        DownloadHistory.timestamp.desc()).all()
    return jsonify([r.as_dict() for r in recs])

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port, debug=True)
