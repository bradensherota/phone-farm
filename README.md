# Phone Farm

Local Mac app that plans a week of posts and stories for multiple iPhones and then
posts them by driving the real Instagram, TikTok, and X apps on each phone over USB,
one action at a time, the way a person holding the phone would.

Status: **control plane complete, running against simulated phones.** The real iPhone
driver is stubbed and gets filled in on the Mac that has Xcode.

## Run it

```bash
./run.sh
```

Opens a native window. `./run.sh --server` runs the API only (for UI development with
`cd frontend && npm run dev`, which proxies `/api` to the server).

### First-time setup on a new Mac

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # Python manager, no admin needed
cd backend && uv sync && cd ..
cd frontend && npm install && npm run build && cd ..
./run.sh
```

Requires `ffmpeg` on the PATH for video thumbnails (`brew install ffmpeg`).

For real phones, on the Mac with Xcode: `cd backend && uv sync --extra ios`.

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

**Story album (recommended).** Make a Photos album on each phone (default name
`Stories`, editable on the phone page) and add the content you plan to post to it when
you sit down to schedule. The app reads album membership from the same index; the picker
marks those items and filters to them. For any item in the album, the flow switches
Instagram's gallery to that album (Recents dropdown, All albums, the album) and picks by
position in a list of a dozen instead of scrolling a roll of hundreds. Items not in the
album still work via Recents.

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
   similar detritus"). The app expects it at `~/PhoneFarm`:

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
2. First time only, install the runner:
   `xcrun devicectl device install app --device <UDID> ~/PhoneFarm/wda-build/Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app`
   (a new phone also has to be added to the provisioning profile: rerun the build
   above once with that phone as the destination).
3. Launch the app. It finds the phone over USB, starts its runner (one local port per
   phone, 8101 and up), reads the camera roll, and shows the live screen. "Find phones"
   on the Phones page rescans without restarting.

Under the hood: `devices/ios.py` keeps one `xcodebuild test-without-building` process and
one `pymobiledevice3 usbmux forward` per phone, talks to WebDriverAgent over HTTP, and
reads `/DCIM` over AFC. Posting flows live in `devices/ios_flows.py` and are written
against the live apps; labels change with app updates, so expect touch-ups.
