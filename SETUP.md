# One-time setup — The Luxury Lane autopilot

About 45 minutes, once. You need your laptop only for this. After step 6 it can stay off for good.

What you need: the Google account that owns **The Luxury Lane** YouTube channel, and a free GitHub account.
No Instagram account, no AI key, no music downloads and no payment — all of that is built in.

You will collect 3 values ("secrets"), plus 2 optional ones for phone alerts. Keep a notepad open.

---

## Step 1 — Connect YouTube (20 min)

Use the Google account that **owns or manages The Luxury Lane** for every part of this step.

**1.1 Create a project.** Open <https://console.cloud.google.com/>, accept the terms if asked, click the project
picker (top left) → **New project** → name `luxury-lane` → **Create**. Make sure it is selected.

**1.2 Turn on the YouTube API.** Open <https://console.cloud.google.com/apis/library/youtube.googleapis.com> →
**Enable**.

**1.3 Consent screen.** Open <https://console.cloud.google.com/auth/overview> → **Get started** →
App name `Luxury Lane Uploader`, your email → **Next** → Audience **External** → **Next** → your email →
**Next** → tick the agreement → **Create**.

**1.4 Publish it — important.** (Do step 2 first if you haven't: you need your GitHub repository's address.)
1. Open <https://console.cloud.google.com/auth/branding> and fill in:
   - **Application home page:** `https://github.com/YOUR_GITHUB_NAME/luxury-lane-autopilot`
   - **Application privacy policy link:** `https://github.com/YOUR_GITHUB_NAME/luxury-lane-autopilot/blob/main/PRIVACY.md`
   - **Authorized domains → Add domain:** `github.com`
   - Click **Save**.
2. Open <https://console.cloud.google.com/auth/audience> → **Publish app** → **Confirm**. It must say
   **In production** (in *Testing* Google logs the uploader out every 7 days). If Google mentions verification,
   that is fine: you can stay unverified because only you use the app.

**1.5 Key file.** Open <https://console.cloud.google.com/auth/clients> → **Create client** → type
**Desktop app** → **Create** → **Download JSON** (lands in your Downloads folder).

**1.6 Authorise your channel.** Open **PowerShell** on your laptop and run:
```powershell
cd D:\Jashan\Desktop\The_luxury_lane_tool
.venv\Scripts\python tools\youtube_auth.py (Get-ChildItem $env:USERPROFILE\Downloads\client_secret_*.json | Select-Object -First 1).FullName
```
In the browser: choose your account, then **The Luxury Lane**. On *"Google hasn't verified this app"* click
**Advanced → Go to Luxury Lane Uploader** (it is your own app) → allow. Copy the 3 printed lines:
`YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.

**1.7 Verify the channel (2 min)** at <https://www.youtube.com/verify> with your phone number. This unlocks custom
thumbnails, which the system makes for every episode.

**1.8 Apply for the YouTube API audit (today — it takes days to weeks).**
YouTube makes every video uploaded by a new API app **private** until Google approves the app (this rule applies
to everyone). Until approval arrives, your videos are still made and uploaded every day — you will find them in
YouTube Studio → Content as private videos, and you can publish any of them yourself.
1. Your **Project number** is on <https://console.cloud.google.com/home/dashboard> (*Project info* card).
2. Fill the form at <https://support.google.com/youtube/contact/yt_api_form> (audit / quota extension). Describe
   it honestly, for example:
   > Internal tool used only by me to upload videos to my own channel, The Luxury Lane
   > (https://www.youtube.com/@theluxurylane). It uploads my edited videos with scheduled publish times, sets their
   > thumbnails and reads my own channel's upload list to avoid duplicates. No other users, no other data.
3. Answer Google's email questions if they send any.

## Step 2 — Put the project on GitHub (10 min)

1. Create a free account at <https://github.com/signup> and verify your email.
2. Install **GitHub Desktop** from <https://desktop.github.com/> and sign in.
3. GitHub Desktop → **File → Add local repository…** → choose `D:\Jashan\Desktop\The_luxury_lane_tool` →
   **Add repository**. (Pick exactly this folder — **not** `D:\Jashan`, which is a different repository holding
   your whole user folder.)
4. Everything is already committed. If GitHub Desktop still lists changed files at the bottom-left, type the
   summary `Setup` and click **Commit to main**.
5. **Publish repository** → name `luxury-lane-autopilot` → **untick "Keep this code private"** (public repos get
   unlimited free running time; your keys are stored separately as encrypted secrets) → **Publish repository**.
6. On github.com open the repository → **Settings → Actions → General** → *Workflow permissions* →
   **Read and write permissions** → **Save**.

## Step 3 — Add the secrets (3 min)

Open `https://github.com/YOUR_GITHUB_NAME/luxury-lane-autopilot/settings/secrets/actions` and for each value
click **New repository secret**, type the name exactly, paste the value, **Add secret**:

`YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`

## Step 4 — Phone alerts on Telegram (5 min, recommended)

This is how the system tells you if it ever needs you. On normal days it sends nothing.
1. In Telegram open **@BotFather** → `/newbot` → any name → it replies with a **token** = `TELEGRAM_BOT_TOKEN`.
2. Open your new bot and send it `hi`.
3. Open `https://api.telegram.org/botPASTE_TOKEN_HERE/getUpdates` and copy the number after `"chat":{"id":` =
   `TELEGRAM_CHAT_ID`.
4. Add both as GitHub secrets like in step 3.

## Step 5 — YouTube Studio, once (3 min, recommended)

So every episode ends with clickable "next video" and "subscribe" boxes:
YouTube Studio → **Settings → Upload defaults → Advanced settings**, and in **Customisation → Layout** set a
**default end screen**: an *element: Best for viewer* video + *Subscribe*, covering the last 20 seconds. The
episodes leave room for it on their end card.

## Step 6 — Test, then go live (about an hour, mostly waiting)

1. On github.com open your repository → **Actions** tab → if asked, **"I understand my workflows, go ahead and
   enable them"**.
2. **Luxury Lane autopilot** (left) → **Run workflow** → tick **Test mode**, **max videos** `2` → **Run workflow**.
   The first run downloads the free AI model (about 4 GB, cached afterwards), so allow 45–60 minutes.
3. When it shows a green ✓, open the run → **Artifacts** → download **test-videos** and watch them.
   (Red ✗? Open it — the report at the top says what is wrong. Send me that text.)
4. Go live: **Run workflow** again **without** Test mode — it prepares today and tomorrow (2 episodes + 20 Shorts,
   which can take a few hours; the next scheduled run finishes anything left).

**Done. Shut the laptop.** From now on it runs by itself three times a day, every day.

---

## What happens on its own

- New official videos, photos and articles on hamdan.ae are picked up automatically.
- Every day: **1 documentary episode (8–12 min)** in the evening and **10 Shorts** spread through the day, each
  Short in a different hour, at random times.
- Nothing is ever repeated: each event appears in at most one episode, and no Short reuses footage another Short
  already used.
- GitHub's 60-day inactivity pause is prevented automatically; failed steps are retried or skipped without
  stopping the day; today and tomorrow are always prepared ahead.

## The only things that can still reach you (Telegram alert)

| Alert | What to do |
|---|---|
| *video download(s) failed* | YouTube sometimes blocks downloads from GitHub's servers; the system then uses the official photos instead, so posting continues. If it keeps happening, add the optional secret below. |
| *YouTube refresh token rejected* | Only if you change the Google password or remove the app's access: repeat step 1.6 and update `YT_REFRESH_TOKEN`. |
| *No usable material left* | After many months; lower `shorts_per_day` in `config.yaml`. |

**Optional `YT_DOWNLOAD_COOKIES` secret** (only if download alerts keep coming): sign in to YouTube in Chrome with
a *spare* Google account, install the extension "Get cookies.txt LOCALLY", export cookies for youtube.com, then in
PowerShell run `[Convert]::ToBase64String([IO.File]::ReadAllBytes("$env:USERPROFILE\Downloads\youtube.com_cookies.txt"))`
and save the output as the secret `YT_DOWNLOAD_COOKIES`.
