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

## Docker Compose

- `docker compose up -d --build`

The app listens on `127.0.0.1:8000`, intended for a host Nginx reverse proxy.

## Configuration

- `APP_TIMEZONE`: defaults to `Asia/Shanghai` (Beijing time).
- `APP_SECRET_KEY`: session secret for admin login (set this in production).

## Admin accounts

The app seeds three admin accounts on first run:

- `professor1` / `professor123`
- `professor2` / `professor123`
- `professor3` / `professor123`

Only admins can create/edit checks and activate/deactivate people. Change passwords by editing the `users` table in the database.

## Nginx (host reverse proxy)

If your host Nginx already listens on 8080 and 443, point it at the app container:

```nginx
server {
    listen 8080;
    server_name your-domain.example;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name your-domain.example;

    ssl_certificate /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Security groups should allow 443 (and 8080 if you expose it). Port 8000 stays internal.

## Example workflow

1. Add people on the People page (admin only).
2. Create a check on the Checks page (timestamp optional).
3. Open the check and mark everyone present/absent.
4. Close a month under Monthly Ops to apply salary changes.
5. View a person's salary history and export CSV (salary history + attendance).
6. If you edit past checks or marks, use Recalculate All.
