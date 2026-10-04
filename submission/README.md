# Submission package

Everything the Final Submission Guide asks for, item by item. Submit via the
official form AND projects.hack-nation.ai.

| # | Item | Where it is | Status |
|---|---|---|---|
| 1 | Short description, 150 to 300 words | `01-project-summary.md` (278 words, verified) | Paste into form |
| 2 | Demo video, max 60 s | `demo-video.mp4` (57.3 s: intro card, captioned screen recording, closing card, per the guide structure) + optional voiceover lines in `02-demo-video.md` | Upload as is, paste link |
| 3 | Tech video, max 60 s | `tech-video.mp4` (58.0 s: diagram, real code, the actual bug diff, limitations, reflection; captions burned in) + transcript in `03-tech-video.md` | Upload as is, paste link |
| 4 | 1-page report, PDF | `RentalLawNavigator_OnePager.pdf` (1 page, built by `make_onepager.py`) | Rename to `<TeamName>_OnePager.pdf` if your registered team name differs, then upload |
| 5 | GitHub repository | <https://github.com/Sheru01/rental-law-navigator> (public; README has description, setup, dependencies) | Paste link |
| 6 | Zipped code | `rental-law-navigator-code.zip`, produced by `make_zip.sh` from the pushed tree (no .git, no node_modules, no raw geocoder passthrough) | Upload |
| 7 | Dataset | Form answer in `07-dataset.md` | Paste into form |

Live product: <https://navigator.mdsrana.com>

Both videos are captioned and uploadable silent. To add narration, see
"Voiceover" below.

## Voiceover (optional)

Both videos work silent, because the captions carry the message. Three ways
to add a voice, best first:

1. **Your own voice.** Open the MP4, record over it with QuickTime Player
   (New Screen Recording, microphone on) or Loom, reading the timed lines in
   `02-demo-video.md` and `03-tech-video.md`. Best result for a judged demo.
2. **Synthesized, on your Mac.** `make-voiceover.sh` speaks each line with
   the macOS `say` command and places it at the exact second its section
   starts, so narration stays locked to the captions:

   ```sh
   cd submission
   ./make-voiceover.sh tech-video.mp4 vo-tech.txt tech-video-vo.mp4
   ./make-voiceover.sh demo-video.mp4 vo-demo.txt demo-video-vo.mp4
   ```

   Needs macOS and ffmpeg (`brew install ffmpeg`). Pick a nicer voice with
   `say -v '?' | grep en_US`, then pass it: `... tech-video-vo.mp4 "Ava (Premium)"`.
   The script refuses to write anything if a line would overrun its section,
   and tells you which line and what rate to try.
3. **Leave them silent.** The guide asks for captions *or* voiceover, and the
   captions are burned in.

What only you can do: upload the videos and fill the form.
