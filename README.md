# SCS Trade Analyst Opinion → Discord

Monitors:

https://www.scstrade.com/AnalystOpinionMain.aspx

and sends newly detected analyst-opinion posts to a Discord webhook.

## How duplicate protection works

The bot stores SHA-256 IDs in:

`sent_scs_analyst_posts.json`

The ID is based on the post URL/title and identifying fields. If the same post is found again, it is not sent twice.

## Important first-run behavior

By default:

`SEND_EXISTING_ON_FIRST_RUN=false`

The first run only learns the posts currently visible on the page. It does NOT send all existing posts to Discord.

After that, only newly detected posts are sent.

If you intentionally want the first run to send the currently visible posts, set:

`SEND_EXISTING_ON_FIRST_RUN=true`

## 1. Discord webhook

In Discord:

Server → Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL

Do NOT put the webhook URL directly in the Python file.

## 2. Local test

Python 3.11+:

```bash
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
$env:DISCORD_WEBHOOK_URL="YOUR_DISCORD_WEBHOOK_URL"
python scs_analyst_bot.py
```

## 3. GitHub

Create a repository, for example:

`psx-scs-analyst-discord-alerts`

Upload:

```text
scs_analyst_bot.py
requirements.txt
sent_scs_analyst_posts.json
.gitignore
README.md
.github/workflows/manual-test.yml
```

## 4. GitHub secret

Repository:

Settings → Secrets and variables → Actions → New repository secret

Name:

`DISCORD_WEBHOOK_URL`

Value:

your Discord webhook URL.

Then:

Actions → SCS Analyst Bot - Manual Test → Run workflow

## 5. Cron-job.org

Use cron-job.org to trigger the bot because cron-job.org is better suited to frequent polling than GitHub scheduled workflows.

Create a GitHub Personal Access Token with the minimum permissions needed to trigger your workflow.

Then create a cron-job.org job:

Method:

`POST`

GitHub workflow dispatch endpoint:

```text
https://api.github.com/repos/YOUR_GITHUB_USERNAME/YOUR_REPO/actions/workflows/manual-test.yml/dispatches
```

Headers:

```text
Accept: application/vnd.github+json
Authorization: Bearer YOUR_GITHUB_TOKEN
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

Body:

```json
{
  "ref": "main"
}
```

Recommended schedule:

```text
*/10 * * * *
```

This checks every 10 minutes.

### Important

The GitHub workflow is the worker, while cron-job.org is the scheduler.

The Discord webhook stays inside GitHub Secrets.

The GitHub token stays inside cron-job.org's secure request/header configuration.

## 6. Recommended schedule

For SCS analyst posts, start with:

Every 10 minutes

If you want fewer requests:

Every 15 minutes

I would not use a 1-minute schedule unless you have a specific reason.

## 7. What happens when a new post appears?

Example:

```text
SCS page
   ↓
Python downloads HTML
   ↓
BeautifulSoup finds analyst-post candidates
   ↓
Create unique SHA-256 ID
   ↓
Check sent_scs_analyst_posts.json
   ↓
NEW?
 ┌───────┴────────┐
 NO                YES
 ↓                  ↓
Ignore        Send Discord
                   ↓
             Save post ID
```

## Troubleshooting

If Discord receives nothing:

1. Run the workflow manually.
2. Open the workflow logs.
3. Confirm `DISCORD_WEBHOOK_URL` exists as a repository secret.
4. Check whether the SCS page is reachable from GitHub Actions.
5. If the log says `No analyst posts were detected`, SCS likely changed its HTML structure. Update the extraction rules in `extract_posts()`.

Do not commit the Discord webhook URL to GitHub.
