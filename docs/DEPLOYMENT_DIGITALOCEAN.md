# Lumina Medical Services Deployment Guide

Prepared: April 7, 2026

This guide explains how to take the current Lumina laboratory system from local development to production on GitHub and DigitalOcean.

It covers:

- repository preparation,
- production Django settings,
- GitHub push steps,
- DigitalOcean App Platform deployment,
- DigitalOcean Droplet deployment,
- required environment variables,
- database, static files, domain, and SSL setup,
- and post-deployment checks.

---

## 1. Recommended Deployment Path

For this project, the recommended production path is:

1. push the code to GitHub,
2. deploy the Django app to **DigitalOcean App Platform**,
3. attach a **managed PostgreSQL database**,
4. point your domain to the app,
5. enable HTTPS and production security settings.

This is the fastest and lowest-maintenance option.

---

## 2. Alternative Deployment Path

If you want more server control, use:

- a **DigitalOcean Ubuntu Droplet**,
- **Gunicorn** as the app server,
- **Nginx** as the reverse proxy and static-file server,
- and either:
  - PostgreSQL, or
  - SQLite for small internal use only.

For production with real staff usage, **PostgreSQL is strongly recommended**.

---

## 3. What You Need Before Deployment

### Accounts and services

- a GitHub account with access to the repository,
- a DigitalOcean account,
- optionally a custom domain,
- optionally a DigitalOcean Managed PostgreSQL database,
- optionally an SSH key for Droplet deployment.

### Project access

This project is already linked to:

- GitHub remote: `https://github.com/jonathan-ndayisenga/lumina_medical.git`
- branch: `main`

### Minimum production stack

- Python 3.12
- Django 6
- Gunicorn
- WhiteNoise
- PostgreSQL driver (`psycopg`)

---

## 4. Deployment Preparation Added to the Project

The project has now been prepared for deployment with the following changes:

- `labsystem/labsystem/settings.py`
  - environment-driven secret key,
  - environment-driven debug mode,
  - environment-driven allowed hosts,
  - environment-driven CSRF trusted origins,
  - production-ready static and media paths,
  - optional PostgreSQL `DATABASE_URL` support,
  - secure proxy / HTTPS settings,
  - conditional WhiteNoise static-file support.

- `requirements.txt`
  - includes:
    - `gunicorn`
    - `psycopg[binary]`
    - `whitenoise`

- `.env.example`
  - provides the production environment variables you need.

- `.gitignore`
  - ignores runtime and environment artifacts.

- `labsystem/requirements.txt`
  - contains the Python dependencies directly inside the App Platform source directory so the Python buildpack can install them reliably.

- `labsystem/runtime.txt`
  - pins App Platform to Python 3.12 instead of using a newer default runtime.

- `labsystem/.python-version`
  - explicitly pins App Platform to Python 3.12 for builders that check `.python-version`.

- `.do/app.yaml.example`
  - sample DigitalOcean App Platform spec for this project.

---

## 5. Production Environment Variables

Use these environment variables in production:

### Required

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG=0`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`

### Strongly recommended

- `DATABASE_URL`
- `DJANGO_SECURE_SSL_REDIRECT=1`
- `DJANGO_SECURE_HSTS_SECONDS=3600`
- `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=1`
- `DJANGO_SECURE_HSTS_PRELOAD=0`
- `DB_CONN_MAX_AGE=60`

### Example

```env
DJANGO_SECRET_KEY=replace-with-a-long-random-secret
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=your-app.ondigitalocean.app,your-domain.com,www.your-domain.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://your-app.ondigitalocean.app,https://your-domain.com,https://www.your-domain.com
DJANGO_SECURE_SSL_REDIRECT=1
DJANGO_SECURE_HSTS_SECONDS=3600
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=1
DJANGO_SECURE_HSTS_PRELOAD=0
DB_CONN_MAX_AGE=60
DATABASE_URL=postgresql://username:password@host:25060/database_name?sslmode=require
```

---

## 6. Local Pre-Deployment Checklist

Before pushing:

```powershell
cd C:\Users\USER\Desktop\Projects\Lumina_medical_services\labsystem
..\ven\Scripts\python.exe manage.py check
..\ven\Scripts\python.exe manage.py migrate
```

If you want an extra production-style check:

```powershell
$env:DJANGO_DEBUG="0"
$env:DJANGO_SECRET_KEY="replace-this-before-real-deploy"
$env:DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost"
$env:DJANGO_CSRF_TRUSTED_ORIGINS="http://127.0.0.1,http://localhost"
..\ven\Scripts\python.exe manage.py check --deploy
```

---

## 7. Pushing the Project to GitHub

From the repository root:

```powershell
cd C:\Users\USER\Desktop\Projects\Lumina_medical_services
git status
git add .
git commit -m "Prepare Lumina lab system for DigitalOcean deployment"
git push origin main
```

### Important note

Before running `git add .`, make sure you do **not** want to push:

- local database changes,
- temporary files,
- compiled cache files,
- personal environment files.

The new `.gitignore` helps with this, but already-tracked files may still need manual care.

### One-time SQLite safety cleanup

If `labsystem/db.sqlite3` was committed earlier, `.gitignore` alone does **not** stop Git from continuing to track it.

Run this once from the repository root:

```powershell
git rm --cached labsystem/db.sqlite3
git commit -m "Stop tracking local SQLite database"
git push origin main
```

This removes the SQLite file from Git **without deleting your local copy**.

### Why App Platform data appears to change after redeploys

If `DATABASE_URL` is missing, the project falls back to SQLite in `labsystem/labsystem/settings.py`.

On DigitalOcean App Platform, the application filesystem is not persistent across redeployments. This means:

- live SQLite changes can disappear when a new container is deployed,
- if `db.sqlite3` is still tracked in Git, the app can appear to "revert" to the older database snapshot stored in the repository,
- data can therefore look altered, reset, or out of date after a GitHub push triggers a new deployment.

For App Platform, PostgreSQL should be treated as the real production database.

---

## 8. Recommended DigitalOcean App Platform Deployment

This is the easiest deployment path.

### Step 1 - Push code to GitHub

Push the repository to:

- `jonathan-ndayisenga/lumina_medical`

### Step 2 - Create an App Platform app

In DigitalOcean:

1. open **App Platform**,
2. choose **Create App**,
3. connect GitHub,
4. select repository:
   - `jonathan-ndayisenga/lumina_medical`
5. choose branch:
   - `main`

### Step 3 - Configure the app component

Use:

- **Type:** Web Service
- **Environment:** Python
- **Source Directory:** `labsystem`
- **HTTP Port:** `8080`
- **Python version files present inside source directory:** `.python-version` and `runtime.txt`

### Step 4 - Build and run commands

Build command:

```bash
python manage.py collectstatic --noinput
```

Run command:

```bash
gunicorn --worker-tmp-dir /dev/shm labsystem.wsgi:application --workers 2 --bind 0.0.0.0:$PORT
```

### Step 5 - HTTP port

Set:

- `8080`

App Platform will provide `PORT=8080` automatically when `http_port` is set.

### Step 6 - Instance size

Recommended starting point:

- `apps-s-1vcpu-1gb`

If more staff will use the system at once, increase later.

### Step 7 - Environment variables

Add all variables from `.env.example`, replacing placeholders with real values.

When you open the Environment Variable Editor, paste values in `KEY=VALUE` format, one per line.

If the interface gives you a scope option, make sure the Django variables below are available to the web service at runtime. If there is a build-time visibility option, it is also safe to enable it for `DJANGO_SECRET_KEY` and `DJANGO_DEBUG`.

Recommended first set:

```env
DJANGO_SECRET_KEY=replace-with-a-long-random-secret
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=${APP_DOMAIN}
DJANGO_CSRF_TRUSTED_ORIGINS=${APP_URL}
DJANGO_SECURE_SSL_REDIRECT=1
DJANGO_SECURE_HSTS_SECONDS=3600
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=1
DJANGO_SECURE_HSTS_PRELOAD=0
DB_CONN_MAX_AGE=60
```

### Step 8 - Attach a database

Recommended:

- create a **Managed PostgreSQL** database in DigitalOcean,
- copy the connection URL,
- set it as:
  - `DATABASE_URL`

Important distinction:

- an **App Platform Dev Database** is useful for quick testing inside the app platform,
- a **Managed PostgreSQL** database is the better production choice,
- if you need to import data from your own laptop or another external machine, use **Managed PostgreSQL**,
- a dev database may only be reachable from the attached app components, so local import tooling can fail even when the app itself is live.

If you name the managed database `lumina-db`, the bindable variable normally looks like:

```env
DATABASE_URL=${lumina-db.DATABASE_URL}
```

Important:

- Django migrations create tables inside a database.
- They do **not** create the DigitalOcean managed database service itself.
- So on App Platform, you should create or attach the database first, then deploy, then run `python manage.py migrate`.

### Step 9 - First deployment tasks

After the app is live, open the console and run:

```bash
python manage.py migrate
python manage.py createsuperuser
```

### App Platform step-by-step summary

1. Push code to GitHub.
2. Create App in App Platform.
3. Select repository `jonathan-ndayisenga/lumina_medical`.
4. Set source directory to `labsystem`.
5. Confirm build command and run command.
6. Add environment variables in the editor.
7. Create and attach a managed PostgreSQL database.
8. Deploy the app.
9. Open the console and run migrations.
10. Create the superuser.
11. Test login, report creation, CBC, urinalysis, and printing.

### Step 9.1 - Migrate existing SQLite data into PostgreSQL

If your current data lives in SQLite and you want to preserve it, use this workflow **before** relying on the new PostgreSQL database.

#### If the live App Platform data is the source of truth

Open the App Platform console and export the data first:

```bash
python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.permission --indent 2 > lumina-data.json
```

Download or copy that JSON safely before the next redeploy.

#### If your local SQLite file is the source of truth

From the local project:

```powershell
cd C:\Users\USER\Desktop\Projects\Lumina_medical_services\labsystem
..\ven\Scripts\python.exe manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.permission --indent 2 > lumina-data.json
```

#### After the PostgreSQL `DATABASE_URL` is configured

Run:

```bash
python manage.py migrate
python manage.py loaddata lumina-data.json
```

Then verify:

```bash
python manage.py shell
```

And check a few records, for example:

```python
from lab.models import LabReport
print(LabReport.objects.count())
```

#### Important migration note

- Do not run `loaddata` repeatedly on the same PostgreSQL database unless you know the target is empty or you are intentionally restoring a snapshot.
- Keep a backup copy of both:
  - `labsystem/db.sqlite3`
  - `lumina-data.json`
- If you are using an **App Platform Dev Database** and your laptop cannot connect to it directly, perform the import from the **App Platform console** instead of your local terminal.

### If App Platform shows a build error

Check these first:

1. source directory is exactly `labsystem`,
2. `labsystem/requirements.txt` exists and contains the full dependency list,
3. `labsystem/.python-version` exists so App Platform does not default to Python 3.14,
4. run command is exactly:

```bash
gunicorn --worker-tmp-dir /dev/shm labsystem.wsgi:application --workers 2 --bind 0.0.0.0:$PORT
```

5. if you attached PostgreSQL, confirm `DATABASE_URL` is present,
6. if you are not attaching PostgreSQL yet, leave `DATABASE_URL` unset so the app falls back to SQLite.

### Step 10 - Domain and HTTPS

Then:

- add your domain in App Platform,
- point DNS records to DigitalOcean,
- verify HTTPS is active,
- update:
  - `DJANGO_ALLOWED_HOSTS`
  - `DJANGO_CSRF_TRUSTED_ORIGINS`

---

## 9. Using the Included App Spec

This repo includes:

- `.do/app.yaml.example`

You can use it as a starting point for App Platform or `doctl`.

Before using it:

1. copy it to your real app spec file,
2. replace the placeholder secret key,
3. replace the placeholder domain names,
4. replace the example `DATABASE_URL`,
5. confirm the region and instance size you want.

---

## 10. DigitalOcean Droplet Deployment

Use this path if you want full server control.

### Recommended Droplet

- Ubuntu 24.04 LTS
- 2 GB RAM minimum
- 1 vCPU minimum
- region near your users

### Packages to install

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx postgresql postgresql-contrib git
```

### Clone the repo

```bash
git clone https://github.com/jonathan-ndayisenga/lumina_medical.git
cd lumina_medical/labsystem
```

### Create virtual environment and install packages

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Create environment file

Create `.env` with the same values shown in `.env.example`.

### Collect static and migrate

```bash
python manage.py collectstatic --noinput
python manage.py migrate
python manage.py createsuperuser
```

### Gunicorn test run

```bash
gunicorn labsystem.wsgi:application --bind 0.0.0.0:8000
```

If that works, stop it and create a systemd service.

### Example Gunicorn systemd service

Create:

- `/etc/systemd/system/lumina-lab.service`

Example:

```ini
[Unit]
Description=Lumina Lab Django Application
After=network.target

[Service]
User=root
Group=www-data
WorkingDirectory=/root/lumina_medical/labsystem
EnvironmentFile=/root/lumina_medical/labsystem/.env
ExecStart=/root/lumina_medical/labsystem/.venv/bin/gunicorn labsystem.wsgi:application --workers 3 --bind 127.0.0.1:8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable lumina-lab
sudo systemctl start lumina-lab
sudo systemctl status lumina-lab
```

### Example Nginx site config

Create:

- `/etc/nginx/sites-available/lumina-lab`

Example:

```nginx
server {
    listen 80;
    server_name your-domain.com www.your-domain.com;

    location /static/ {
        alias /root/lumina_medical/labsystem/staticfiles/;
    }

    location /media/ {
        alias /root/lumina_medical/labsystem/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/lumina-lab /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### Add SSL

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
```

After SSL is active, keep:

- `DJANGO_SECURE_SSL_REDIRECT=1`

---

## 11. Static Files in Production

This project now supports:

- `STATIC_ROOT = labsystem/staticfiles`
- WhiteNoise when installed
- Nginx static serving on Droplets

### App Platform

Static files are prepared with:

```bash
python manage.py collectstatic --noinput
```

WhiteNoise is included in `requirements.txt` to simplify App Platform deployment.

### Droplet

Nginx serves the collected static files from `staticfiles/`.

---

## 12. Database Recommendation

### Recommended

- DigitalOcean Managed PostgreSQL

### Acceptable only for very small internal testing

- SQLite

SQLite is fine for development, but for multi-user production use, PostgreSQL is safer and more durable.

---

## 13. Post-Deployment Checklist

After deployment:

- open the app URL,
- confirm login works,
- confirm static assets load,
- confirm the logo loads,
- create a lab report,
- load CBC template,
- load Urinalysis template,
- print a report,
- confirm logout returns to login,
- confirm admin login works,
- confirm database writes persist after restart.

---

## 14. Backup and Operations

### Recommended backups

- enable DigitalOcean database backups,
- keep periodic repository backups,
- export important reports if needed for offline archival.

### Monitoring

Watch:

- deployment status,
- memory usage,
- restart count,
- application logs,
- database connection errors,
- 500 errors from Django.

---

## 15. Troubleshooting

### Static files missing

Check:

- `collectstatic` ran successfully,
- `STATIC_ROOT` exists,
- WhiteNoise is installed for App Platform,
- Nginx alias paths are correct on Droplet.

### Invalid host / CSRF errors

Check:

- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`

### PostgreSQL connection errors

Check:

- `DATABASE_URL`
- firewall/network access,
- SSL mode in the connection string,
- database user permissions.

### App Platform health-check failure

Check:

- Gunicorn is binding to `$PORT`,
- `http_port` is `8080`,
- the app starts without migration/import errors.

---

## 16. Files Added or Updated for Deployment

- `labsystem/labsystem/settings.py`
- `requirements.txt`
- `labsystem/requirements.txt`
- `.env.example`
- `.gitignore`
- `.do/app.yaml.example`

---

## 17. Final Recommendation

For this Lumina system, use:

- **GitHub** for source control,
- **DigitalOcean App Platform** for hosting,
- **DigitalOcean Managed PostgreSQL** for the database,
- **a custom domain + HTTPS** for production access.

That path gives the best balance of:

- speed,
- lower server maintenance,
- simpler scaling,
- and cleaner future expansion when you add doctors, reception, and overall admin roles.
