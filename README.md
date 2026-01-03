# Attendance + Salary Tracking

Small FastAPI app for attendance checks and monthly salary adjustments.

## Local run

1. Create a virtual environment (optional):
   - Windows: `python -m venv .venv` and `.venv\\Scripts\\activate`
   - macOS/Linux: `python -m venv .venv` and `source .venv/bin/activate`
2. Install dependencies: `pip install -r requirements.txt`
3. Run the app: `uvicorn app.main:app --reload`

Open `http://127.0.0.1:8000`.

The SQLite database is stored at `./data/app.db` and created automatically.

## Docker

Build and run:

- `docker build -t attendance-app .`
- `docker run --rm -p 8000:8000 -v ${PWD}/data:/app/data attendance-app`

## Docker Compose (Nginx + app)

1. Place TLS certs at:
   - `deploy/nginx/certs/fullchain.pem`
   - `deploy/nginx/certs/privkey.pem`
   - Aliyun: download the certificate and rename to the paths above.
   - Certbot: use webroot `deploy/nginx/www` and copy/symlink the certs here.
2. Run: `docker compose up -d --build`
3. Visit `https://YOUR_DOMAIN` (ports 80/443 are exposed by Nginx).

The Nginx config lives at `deploy/nginx/default.conf` and proxies to the app container.

## Configuration

- `APP_TIMEZONE`: defaults to `Asia/Shanghai` (Beijing time).

## Nginx notes

Security groups should allow 80/443. Port 8000 stays internal to Docker by default.
If you want to pin a specific domain, edit `deploy/nginx/default.conf` and replace `server_name _;`.

## Example workflow

1. Add people on the Home page.
2. Create a check on the Checks page (timestamp optional).
3. Open the check and mark everyone present/absent.
4. Close a month under Monthly Ops to apply salary changes.
5. View a person's salary history and export CSV.
6. If you edit past checks or marks, use Recalculate All.
