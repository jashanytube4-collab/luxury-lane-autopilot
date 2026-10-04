# The Luxury Lane — autopilot

Every day, with your laptop off, this repo publishes to **The Luxury Lane** on YouTube:

- **1 long-form documentary episode (8–12 min, 1920x1080)** in the evening: about six official events of
  H.H. Sheikh Hamdan bin Mohammed ("Fazza") woven into one story. It opens on a cold-open hook and a title
  sequence, then each chapter has an animated chapter card, footage intercut with photos, a lower third and
  narration written from the official article. It closes with an outro and an end card, and comes with
  subtitles, music, sound design and a custom thumbnail.
- **10 Shorts (15–20 s, 1080x1920)** spread through the day, each in a different hour, at random times.
  Every Short has:
  - a hook title and word-by-word gold captions;
  - smooth camera moves, punch, whip and flash transitions, and a whoosh on every cut;
  - a cinematic grade and music that dips under the narrator.

**Source:** the official website **hamdan.ae**, with its media gallery (videos and full-resolution photos)
and the official article behind each event. The original sound is removed; the narration is new.

**Everything is free:**

| Part | What it uses |
|---|---|
| Computers | GitHub Actions |
| AI writer and editor | open-source Qwen3.5 running on the GitHub machine; no key, never billed |
| Voice | Microsoft neural voices, with Kokoro as backup |
| Music | Kevin MacLeod CC BY tracks, included and credited automatically |

## Setup

Follow **[SETUP.md](SETUP.md)** once (about 45 minutes). After that it runs by itself.

## How it never repeats

- Each official event is used in at most one episode.
- Shorts never reuse footage that another Short already used.
- Every upload carries a hidden tag, and each run reads the channel back before posting.
- The state is saved and committed after every single upload, so a crash can't cause a double post.

## Day to day

- **Actions** tab: every run has a report at the top listing what was scheduled, what is left in the library,
  and any problems.
- **`state/days/<date>.json`**: the day's posting plan, with the YouTube ID for each slot.
- **`state/catalog.json`**: what has been used from each event.
- **`config.yaml`**: change counts, posting windows, voice, episode length or music level. The next run picks
  the changes up.
- Make test videos locally without uploading: `.venv\Scripts\python -m lane.pipeline --dry-run -n 2`

## Good to know

- **Private until approved.** YouTube keeps uploads from new API apps private until Google approves the app
  (SETUP.md, step 1.8).
- **Copyright matching.** Most official footage is also on YouTube, so YouTube's automatic copyright matching
  may recognise it. Heavy editing, new narration and photos lower the risk but can't remove it. Watch
  YouTube Studio for claims in the first weeks.
- **Footage library.** YouTube blocks downloads from GitHub's servers, so the official videos are copied once into
  a private GitHub library (`luxury-lane-library`, built with `tools/build_library.py`) and the daily runs read from
  there. Videos published later that aren't in the library yet are covered by the official photos. To add them, run
  `tools/build_library.py` again from any home connection (optional).
- **The 5 daily pictures** you asked for at the start aren't included, because YouTube has no way for software
  to create picture posts.
