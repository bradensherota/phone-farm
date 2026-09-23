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

Content gets to the phone by AirDrop. The app reads the phone's camera roll listing
(read-only) and shows it in the picker. When posting, the flow opens the app's own
gallery picker and taps the item at the same position in Recents (newest first). The
media list is re-synced right before a phone's queue runs so positions are current.

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

## Wiring in real iPhones (next phase, needs Xcode)

1. Apple Developer account (paid, so the runner signs for a year).
2. Install Xcode, enable Developer Mode on each phone, build and install
   WebDriverAgent on each one, one port per phone.
3. Fill in `devices/ios.py`: discovery via `pymobiledevice3`, roll listing from
   `/DCIM` over AFC, screenshots, and `run_action` dispatching to flow scripts.
4. Write the flows against the live apps: switch account, reel, story with link
   sticker, TikTok post/story, X post. Verify labels on-device; they change with app
   updates.
5. Replace the seeded simulated devices with the real UDIDs (or keep both).

The planner, scheduler, activity log, and UI don't change for this step.
