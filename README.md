# LinkedIn Auto-Poster — SQL Server Content

Posts one original, AI-generated SQL Server tip/insight to your LinkedIn
profile every day, automatically, via Windows Task Scheduler.

## 1. Install dependencies

```powershell
cd C:\Users\ETERNAL\LinkedInAutoPoster
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

(If you don't want a virtual environment, just `pip install -r requirements.txt`
into your system/user Python — then skip the `venv\Scripts\activate` step and
point Task Scheduler directly at your regular `python.exe`.)

## 2. Configure secrets

Copy `.env.example` to `.env` and fill in the values:

```powershell
Copy-Item .env.example .env
notepad .env
```

- `OPENAI_API_KEY` — from https://platform.openai.com/api-keys
- `LINKEDIN_ACCESS_TOKEN` / `LINKEDIN_PERSON_URN` — see step 3 below
- `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` — from your LinkedIn
  Developer App (kept here for reference / future token regeneration)
- `MIN_DAYS_BEFORE_REPEAT` — optional, defaults to 14 days between repeats
  of the same topic

**`.env` contains secrets — never share it or commit it anywhere.**

## 3. Obtain your LinkedIn access token & person URN

Your Developer App already has the `w_member_social` scope. You need a
one-time (well, every ~60 days) 3-legged OAuth flow to get a member access
token for your own account:

1. In the LinkedIn Developer Portal, on your app's **Auth** tab, confirm
   `w_member_social` and `openid`/`profile` are authorized products/scopes,
   and that a redirect URL is registered (e.g. `https://localhost:8000/callback`
   — it doesn't need to be a live server, LinkedIn just redirects your
   browser there with a `code` parameter).
2. Build this URL (replace `YOUR_CLIENT_ID` and `YOUR_REDIRECT_URI`, URL-encoded),
   then open it in a browser and log in/approve:
   ```
   https://www.linkedin.com/oauth/v2/authorization?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=YOUR_REDIRECT_URI&scope=openid%20profile%20w_member_social
   ```
3. After approving, you're redirected to `YOUR_REDIRECT_URI?code=XXXX...`.
   Copy the `code` value from the address bar.
4. Exchange the code for an access token:
   ```powershell
   curl -X POST https://www.linkedin.com/oauth/v2/accessToken `
     -H "Content-Type: application/x-www-form-urlencoded" `
     -d "grant_type=authorization_code&code=THE_CODE&redirect_uri=YOUR_REDIRECT_URI&client_id=YOUR_CLIENT_ID&client_secret=YOUR_CLIENT_SECRET"
   ```
   The response JSON's `access_token` is your `LINKEDIN_ACCESS_TOKEN`
   (valid ~60 days).
5. Get your person URN (needed as the `author` field for posts):
   ```powershell
   curl https://api.linkedin.com/v2/userinfo -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
   ```
   The `sub` field in the response is your member ID — set
   `LINKEDIN_PERSON_URN=urn:li:person:THAT_SUB_VALUE`.

**Token expiry:** LinkedIn member access tokens generally expire after
~60 days and standard apps don't get refresh tokens without additional
LinkedIn partner approval. When `poster.py` logs a `401 Unauthorized`
error, repeat steps 2-4 above to get a fresh token and update `.env`.
Put a recurring personal reminder (e.g. every 55 days) so posting doesn't
silently stop.

## 4. Test with a dry run

```powershell
python poster.py --dry-run
```

Check `logs\poster.log` and the console output — confirm the generated
post reads well and is under 3000 characters. Re-run a few times to see
topic rotation working (it reads/writes `history.json`).

## 5. Test a real post

```powershell
python poster.py
```

Check your LinkedIn profile feed to confirm it was published, and check
`logs\poster.log` for the result.

## 6. Schedule it with Windows Task Scheduler

1. Open **Task Scheduler** → **Create Task...** (not "Basic Task", so you
   get full options).
2. **General tab:** Name it `LinkedIn SQL Server Poster`. Select
   "Run whether user is logged on or not" if you want it to work even
   when locked/logged out (requires saving your Windows password when
   prompted).
3. **Triggers tab:** New trigger → Daily → set your preferred time
   (e.g. 9:00 AM) → Enabled.
4. **Actions tab:** New action → Action: "Start a program" →
   Program/script: `C:\Users\ETERNAL\LinkedInAutoPoster\run_poster.bat` →
   Start in: `C:\Users\ETERNAL\LinkedInAutoPoster`.
5. **Conditions/Settings tabs:** Uncheck "Start the task only if the
   computer is on AC power" if this is a laptop you want posting from on
   battery; leave other defaults.
6. Save, then right-click the task → **Run** once to confirm it works
   immediately, and check `logs\poster.log` / `logs\task_scheduler_stdout.log`.

## Files

- `poster.py` — main script (topic selection, OpenAI generation, LinkedIn posting)
- `config/topics.json` — editable list of SQL Server sub-topics to rotate through
- `history.json` — auto-generated record of what's been posted and when
- `run_poster.bat` — wrapper invoked by Task Scheduler
- `logs/poster.log` — rotating log of every run's outcome
- `.env` — your secrets (create from `.env.example`, never share/commit)

## Customizing

- Add/remove topics any time by editing `config/topics.json` — no code
  changes needed.
- Adjust tone/format by editing the `system_prompt` in `poster.py`'s
  `generate_post()` function.
- Change `MIN_DAYS_BEFORE_REPEAT` in `.env` to control how long before a
  topic can be reused.
