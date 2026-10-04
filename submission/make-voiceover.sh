#!/bin/bash
# Add a synthesized voiceover to a submission video, on macOS.
#
#   ./make-voiceover.sh tech-video.mp4 vo-tech.txt tech-video-vo.mp4 [voice] [rate]
#   ./make-voiceover.sh demo-video.mp4 vo-demo.txt demo-video-vo.mp4 [voice] [rate]
#
# The lines file is "start_seconds|text", one spoken line per video section.
# Each line is synthesized with macOS `say` and placed at its exact start time,
# so the narration stays locked to the captions already burned into the video.
#
# Requirements: macOS (for `say`) and ffmpeg (brew install ffmpeg).
# Pick a better voice first:  say -v '?' | grep en_US
# Good ones if installed: "Ava (Premium)", "Zoe (Premium)", "Tom", "Allison".
#
# The script REFUSES to write a file whose narration overruns the next section,
# and tells you which line to shorten or what rate to use instead.
set -euo pipefail

VIDEO="${1:?usage: make-voiceover.sh VIDEO.mp4 LINES.txt OUT.mp4 [voice] [rate]}"
LINES="${2:?missing lines file}"
OUT="${3:?missing output path}"
VOICE="${4:-Samantha}"
RATE="${5:-180}"

command -v say >/dev/null || { echo "ERROR: 'say' not found. Run this on macOS, not in a container."; exit 2; }
command -v ffmpeg >/dev/null || { echo "ERROR: ffmpeg not found. brew install ffmpeg"; exit 2; }
[ -f "$VIDEO" ] || { echo "ERROR: no such video: $VIDEO"; exit 2; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

starts=(); n=0
while IFS='|' read -r start text <&3; do
  case "$start" in ''|\#*) continue ;; esac
  n=$((n + 1))
  starts+=("$start")
  say -v "$VOICE" -r "$RATE" -o "$WORK/seg$n.aiff" "$text" < /dev/null
done 3< "$LINES"
[ "$n" -gt 0 ] || { echo "ERROR: no lines parsed from $LINES"; exit 2; }

# Verify nothing overruns its slot before building anything.
VIDDUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$VIDEO")
overrun=0
for i in $(seq 1 "$n"); do
  d=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$WORK/seg$i.aiff")
  s=${starts[$((i - 1))]}
  if [ "$i" -lt "$n" ]; then nxt=${starts[$i]}; else nxt=$VIDDUR; fi
  slot=$(echo "$nxt - $s" | bc -l)
  if [ "$(echo "$d > $slot" | bc -l)" -eq 1 ]; then
    printf 'OVERRUN  line %d (starts %ss): speech %.1fs > slot %.1fs\n' "$i" "$s" "$d" "$slot"
    overrun=1
  else
    printf 'ok       line %d (starts %ss): speech %.1fs fits %.1fs\n' "$i" "$s" "$d" "$slot"
  fi
done
if [ "$overrun" -eq 1 ]; then
  echo
  echo "Nothing written. Shorten the flagged line, or re-run faster, e.g. rate $((RATE + 20)):"
  echo "  $0 $VIDEO $LINES $OUT \"$VOICE\" $((RATE + 20))"
  exit 1
fi

# Place each clip at its start time and mix, without re-encoding the video.
inputs=(-i "$VIDEO"); filter=""; mixin=""
for i in $(seq 1 "$n"); do
  inputs+=(-i "$WORK/seg$i.aiff")
  ms=$(printf '%.0f' "$(echo "${starts[$((i - 1))]} * 1000" | bc -l)")
  filter+="[$i:a]adelay=${ms}|${ms}[a$i];"
  mixin+="[a$i]"
done
filter+="${mixin}amix=inputs=${n}:normalize=0:dropout_transition=0,apad[aout]"

ffmpeg -y -loglevel error "${inputs[@]}" -filter_complex "$filter" \
  -map 0:v -map "[aout]" -c:v copy -c:a aac -b:a 160k -shortest -movflags +faststart "$OUT"

echo
echo "wrote $OUT"
ffprobe -v error -show_entries format=duration -show_entries stream=codec_type -of default=noprint_wrappers=1 "$OUT"
