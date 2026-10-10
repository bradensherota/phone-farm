# Phone Farm

Local Mac app that plans a week of posts and stories for multiple iPhones and then
posts them by driving the real Instagram, TikTok, and X apps on each phone over USB,
one action at a time, the way a person holding the phone would.

## Setup on a new Mac

You need a Mac with **Xcode** (App Store, open it once), **Homebrew**, and an **Apple ID** signed
into Xcode (Settings → Accounts; a free one is fine, the app re-signs the phone runner itself every
7 days). Then paste this into Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/bradensherota/phone-farm/main/bootstrap.sh | zsh
```

It downloads the app into `~/phone-farm`, installs it, and sets the server to run whenever you're
logged in, at http://localhost:8765. For a Dock icon, open that address in Chrome and use
menu ⋮ → Cast, save and share → Install page as app. In Settings, put your name in "Your name".

**Trial and activation.** A new Mac works for 7 days straight away. After that Braden activates it
(the sidebar shows the trial days left, and the app says when it's waiting). Phones per Mac: 3 unless
Braden sets more.

**Updating:** Settings → Updates → **Update now**. It pulls the latest from GitHub, reinstalls and
restarts; your data stays where it is. The installed version shows at the bottom-left of the sidebar.
(`./update.sh` in the folder does the same from Terminal.)

Per phone: Developer Mode on (Settings → Privacy & Security), Auto-Lock Never, plug in over
USB, tap Trust, press **Find phones** in the app. The first connect builds and installs the runner
for that phone (2-5 min, phone unlocked). On a Mac that has never signed an app, Xcode first needs
to create a certificate: open `~/PhoneFarm/WebDriverAgent/WebDriverAgent.xcodeproj`, select the
WebDriverAgentRunner target → Signing & Capabilities → tick "Automatically manage signing" → pick
your team, then press Rebuild runner on the phone's page. If the phone shows "Untrusted Developer",
trust it under Settings → General → VPN & Device Management and press Connect. Then on the phone's
page add your accounts, and in Photos make a **Farm Queue** album for the clips you plan to post.
Plan the week in the planner, press **Run day** in the morning.

`./uninstall.sh` stops the server. `tools/package.sh` still makes a zip for someone without GitHub.

For development: `./run.sh --server` runs the API only; `cd frontend && npm run dev` serves
the UI with hot reload, proxying `/api` to it.

## What it does

- **Phones**: one card per iPhone with a live screen, its accounts, camera roll count,
  and today's progress. Pause any phone or the whole runner.
- **Phone page**: manage accounts (add, pause, ban, story link), browse the camera roll
  as it is on the phone, see the phone's schedule.
- **Week planner**: rows are accounts grouped by phone, columns are days. Click a cell
  to plan that account's day: Reel + Story for Instagram, Post + Story for TikTok,
  Post for X. Pick media from that phone's roll, write the caption or link text, leave
  the time blank to run in the phone's morning routine or set an exact time.
- **Runner**: every phone is its own serial queue. At the phone's routine start it runs
  that day's actions in account order with a random gap between them. Failures retry
  once after a few minutes, then land in Activity with the log and a screenshot.
- **Activity**: everything the phones did, with per-step logs and the screen at finish.

## How the phone finds the media

Content gets to the phone by AirDrop. The app reads the phone's Photos library index
(`/PhotoData/Photos.sqlite` over USB, read-only) and mirrors it: capture date, duration,
kind, and the **date-added order**, which is exactly the order Instagram's Recents grid
uses. The picker shows that order with each item's position. When posting, the flow
opens Instagram's gallery, flicks to the planned position, and verifies the cell's own
label (photo/video, capture date, duration) before tapping; if it doesn't match it
probes the neighbouring cells. Thumbnails are the only thing pulled file by file.

**Queue album (automatic).** Each phone has a queue album (default `Farm Queue`) that the
app itself fills and empties by driving the Photos app through the runner:

- *Prepare*: every item referenced by scheduled jobs in the next 7 days that isn't in the
  album yet gets added. Photos search by capture date, select the cell whose label matches
  (photo/video + capture time to the minute), Add to Album, creating the album on first use.
  Runs `prepare_lead_min` minutes before the phone's routine (default 60), on demand from
  "Prepare album now" on the phone page, and as a safety net right before a job.
- *Release*: after a successful post the item is removed from the album (album trash,
  "Remove from Album"); when nothing scheduled still needs anything in the album, it's
  emptied in one go with Select All. Media is never deleted from the library.
- Posts pick from the queue album first, then the story album, then Recents.

**Story album (manual, optional).** A Photos album (default `Stories`) you curate yourself.
The picker marks its items and filters to them. Same picking mechanism.

Both album names and the lead time are per phone on the phone page. The Photos labels and
identifiers used are documented in `devices/ios_photos.py`.

## Layout

```
backend/farm/
  app.py            native window + server entry
  main.py           FastAPI app, static frontend, startup
  models.py         Device, Account, MediaItem, Job, JobLog, Setting
  scheduler.py      the runner (per-device serial queues, due logic, retries)
  devices/base.py   Driver interface every phone implements
  devices/simulated.py   fake phone: roll = files in sim_media/<device_id>/
  devices/ios.py    real iPhone driver (stub; pymobiledevice3 + WebDriverAgent)
  devices/registry.py    one driver per device, camera-roll mirror + thumbnails
  api/              REST endpoints used by the UI
frontend/src/       React + Vite UI (pages/, components/, lib/api.ts)
data/               SQLite db, thumbnails, screenshots (ignored by git)
sim_media/          fake camera rolls for simulated phones (ignored by git)
```

## Real iPhones

Done once per Mac:

1. Xcode installed, opened once, signed into the developer Apple ID (Settings, Accounts),
   and selected for the command line: `sudo xcode-select -s /Applications/Xcode.app`.
2. Build WebDriverAgent **outside iCloud-synced folders** (Desktop/Documents add file
   attributes that make code signing fail with "resource fork, Finder information, or
   similar detritus"). The app expects it at `~/PhoneFarm`, and `tools/wda-build.sh <UDID>` runs exactly this:

   ```bash
   mkdir -p ~/PhoneFarm && cd ~/PhoneFarm
   git clone --depth 1 https://github.com/appium/WebDriverAgent.git
   cd WebDriverAgent
   xcodebuild -project WebDriverAgent.xcodeproj -scheme WebDriverAgentRunner \
     -destination "id=<UDID of any plugged-in phone>" \
     -allowProvisioningUpdates -allowProvisioningDeviceRegistration \
     -derivedDataPath ~/PhoneFarm/wda-build \
     DEVELOPMENT_TEAM=<your 10-char Team ID> \
     PRODUCT_BUNDLE_IDENTIFIER=com.<you>.WebDriverAgentRunner \
     build-for-testing
   ```

   Team ID is on developer.apple.com/account under Membership details.

Per phone:

1. Developer Mode on (Settings, Privacy & Security), Auto-Lock Never, automatic iOS and
   App Store updates off. Plug in over USB, tap Trust.
2. The app builds and installs the runner for a new phone the first time it connects, and
   rebuilds it when its signature has expired (`tools/wda-build.sh <UDID>` does the same by
   hand; "Rebuild runner" on the phone page too). A free Apple ID's signature lasts 7 days.
3. Launch the app. It finds the phone over USB, starts its runner (one local port per
   phone, 8101 and up), reads the camera roll, and shows the live screen. "Find phones"
   on the Phones page rescans without restarting.

Under the hood: `devices/ios.py` keeps one `xcodebuild test-without-building` process and
one `pymobiledevice3 usbmux forward` per phone, talks to WebDriverAgent over HTTP, and
reads `/DCIM` over AFC. Posting flows live in `devices/ios_flows.py` and are written
against the live apps; labels change with app updates, so expect touch-ups.

**TikTok is driven blind** (`devices/ios_blind.py`, `devices/ios_blind_story.py`). TikTok
freezes or quits when the UI-testing harness attaches to it, so the runner never launches or
queries it. It taps the app icon on the home screen, reads screenshots with Apple's Vision
text recognition (`tools/ocr.swift`, compiled once to `~/PhoneFarm/ocr`), taps by position,
types with the runner's keyboard input, and finds the item in the queue album by counting
back from the newest (the story gallery has a camera tile first). Post and story with link
sticker are both mapped.

**Wake and lock.** Farm phones without a passcode wake with home + swipe. A phone with a
passcode (set it on the phone page; stored only locally) is woken through a standby runner
session kept open from before the lock, since WebDriverAgent can't open a session on a
locked phone; the keypad is read off a screenshot and the digits tapped. Sleep = lock the
screen; on a Face ID phone (iOS 26) WebDriverAgent's lock command does nothing, so the
runner presses the side button through a HID event instead. The Sleep and Wake buttons on
the phone page run exactly the steps the runner takes after the last job and before the first.
The **Wake test** card on the phone page locks the phone, then wakes and re-locks it after each
interval you give (default 15, 30, 60, 180 minutes), logging every step, to prove the runner
survives long locked stretches before trusting an overnight schedule. The phone's runner
leaks a connection per request when the phone is locked, so the app never asks it for
screenshots while locked and polls the screen only while the page is visible. Leave the phone
page closed overnight anyway; less traffic to the phone is always safer.

## Checkpoints

Every flow is four stages: **open** (app + account), **pick** (album and the exact item, through
to the editor), **compose** (caption, link sticker), **post**. A job remembers the last stage
that passed and the stage that broke, and any later run continues from the next stage instead
of starting over ("Run now" in the job details starts from the beginning). The **Day checklist**
tab on a phone page lists the day as checkpoints: wake, fill the queue album, each app/account
switch, each job with its stages, sleep. Every line has **Run** (only that line; the phone must
already be where the previous line left it) and **Continue from here** (that line and everything
after it). While the phone is **paused** only the line you press runs, which is how a day is
stepped through by hand; resume the phone and the queue keeps going on its own.

**Trial reel** (`ig_trial`): a reel with Instagram's "Trial" switch on (shown to non-followers
only). Same flow as a reel; in the caption stage it scrolls the share screen to the Trial row,
turns the switch on and checks it. Order within an account: Reel, Trial reel, Story. If the
Trial row isn't there or the switch won't turn on, the job fails at once (no retry) and the
queue moves on, so it is never posted as a normal reel by mistake.
