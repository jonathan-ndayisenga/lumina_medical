# Ternah Books: server setup

Everything here is switched on with settings on the server. Until a setting is added, that feature stays off and the rest of Books works as normal.

Add settings in DigitalOcean: **App → Settings → the web component → Environment Variables**. Mark passwords and keys as **Encrypted**. Saving redeploys the app.

## 1. File storage (DigitalOcean Spaces)

Do this first. Without it, files uploaded to Books (expense receipts, withholding tax certificates) are stored on the app's own disk, which is wiped on every deploy.

1. In DigitalOcean, go to **Spaces Object Storage → Create Bucket**. Choose the region closest to your app (for example `fra1`), name it `ternah-books`, and set file listing to **Restricted**.
2. Go to **Spaces Keys → Generate New Key**, limited to that bucket. Copy the secret straight away, because it is shown only once.
3. Add these settings:

   | Setting | Value |
   |---|---|
   | `SPACES_BUCKET` | `ternah-books` |
   | `SPACES_REGION` | `fra1` |
   | `SPACES_ACCESS_KEY` | your access key |
   | `SPACES_SECRET_KEY` | your secret key |

4. After the deploy, upload a test receipt on an expense and open it from the Expenses list.
5. If the server still has older local files, copy them over from the app's **Console**:

   ```bash
   python manage.py copy_media_to_spaces
   ```

Files stay private. Each link is signed and expires after 10 minutes.

Each app should get its own bucket and its own key, so a problem in one app can't reach another's files. Hospital logos in Ternah Health move to the same storage when these settings are on, so check them after the first deploy.

## 2. Email

Needed for the **Email to client** buttons and automatic reminders. Use an SMTP account from your email provider.

| Setting | Example |
|---|---|
| `EMAIL_HOST` | `smtp.gmail.com` |
| `EMAIL_PORT` | `587` |
| `EMAIL_HOST_USER` | `ternah22@gmail.com` |
| `EMAIL_HOST_PASSWORD` | an app password, not your normal password |
| `DEFAULT_FROM_EMAIL` | `Ternah <ternah22@gmail.com>` |

The buttons appear once `EMAIL_HOST` is set and the client has an email address. Send one to yourself first.

## 3. Automatic reminders

Reminders go out 3 days before the due date, on the due date, then 3, 14 and 30 days late. Each one is sent once, with the invoice attached.

1. Add a daily scheduled job to your app. The example is at the top of `.do/app.yaml.example`.
2. Check what it would send, from the Console:

   ```bash
   python manage.py send_invoice_reminders --dry-run
   ```

3. Turn on **Reminders enabled** under Books → Settings. They stay off until you do this.

Dry run reports "turned off" until step 3, so tick it, run the dry run straight away, and untick it if the list looks wrong.

## 4. Lock the database to the app

The database connection already requires SSL. To stop anyone else from even trying to connect:

1. Go to **Databases → your database → Settings → Trusted Sources**.
2. Add your App Platform app. Remove anything you don't recognise.

Also check that debug mode is off on the live site. Run this in the Console; it must print `False`:

```bash
python manage.py shell -c "from django.conf import settings; print(settings.DEBUG)"
```

If it prints `True`, set `DJANGO_DEBUG` to `0`. Debug pages show internal details to anyone who triggers an error.

## 5. Moving Books to its own droplet

Practise on a test server first. The live site keeps running until the final step.

**Prepare the droplet**

1. Create an Ubuntu droplet and a new database, and install the app from the same code with HTTPS (Nginx and Certbot).
2. Set a new `DJANGO_SECRET_KEY`, plus `DATABASE_URL`, `DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`. Point the Spaces settings at the same bucket.
3. Run `python manage.py migrate`. Do not create invoices or any other data on it.

**Practice run**

1. On the current server, run:

   ```bash
   python manage.py export_books /tmp/books.json
   ```

   This holds Books records and staff logins, including password hashes. It holds no hospital or patient data. Treat it as secret and delete it after the move.
2. Copy the file to the droplet with `scp` or similar, then run:

   ```bash
   python manage.py import_books /tmp/books.json
   ```

   It refuses to run on a server that already has invoices or payments, and it fails loudly if the trial balance doesn't balance.
3. Compare the two servers: trial balance, money owed, number of invoices and receipts, and the next invoice number. To repeat the practice, drop and recreate the droplet's database first.

**Switch over**

1. Ask everyone to stop entering Books data.
2. Recreate the droplet's database, run `migrate`, then repeat the export and import.
3. Compare the totals again. Don't continue unless they match.
4. Point your Books domain at the droplet and sign in.
5. Keep the old data untouched until you've confirmed a few days of normal work.

**After the move, you look after**

- Security updates (`unattended-upgrades`) and the firewall (allow only SSH, HTTP and HTTPS).
- Daily database backups stored off the droplet. Restore one to test it.
- The daily reminder and recurring invoice jobs, using `cron`.

## Not included

**EFRIS** (URA e-invoicing) isn't built. It needs your URA registration and credentials, plus testing against URA's own system. If you're VAT-registered or about to be, ask your accountant whether you need it.
