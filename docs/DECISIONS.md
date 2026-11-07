# Decisions and scope

What we decided, and why, so nobody has to re-derive it. Newest first within each section.
Status: agreed by Rachel (B); Lili (A) and Emma (C) to confirm before recording (T12).

## What Side Eye can and can't detect
- **Smart glasses broadcast Bluetooth whenever they're switched on, recording or not.**
  The signal carries no "camera on" flag, so no Bluetooth detector (ours or existing apps
  like Nearby Glasses, NoPeek, ZuckOff) can tell whether glasses are recording.
- **Switched-off glasses don't broadcast,** so they can't be detected.
- **So Side Eye detects: camera glasses that are switched on and have stayed close to you
  for a while.** It does not detect recording.
- **It can't tell where the glasses are pointed or how they're held** (on a face, on a table,
  in a bag). It only sees signal strength over time.
- **Pitch lines to keep honest:** "We detect smart glasses that are switched on near you,
  not whether they're recording." and "We have not validated this on real smart glasses."

## Main case we design for
- **Glasses worn on someone's face.** That's what they're made for and what people worry
  about. Glasses on a table or tucked in a corner are an edge case we mention as a
  limitation, not something we try to solve.

## Company IDs
- Sources disagree on which ID Ray-Ban Meta glasses broadcast: `0x058E` (Meta Platforms
  Technologies) or `0x0D53` (Luxottica). `0x01AB` (Meta Platforms) also exists.
- `META_IDS` in `pi/canary_core.py` already includes all three, so `scan --real` covers them.
  The demo only accepts our exact test packet (`058E` / `CA01`).
- Matching on company ID can also catch other Meta devices (e.g. VR headsets). It's a known
  false-positive source; mention it as a limitation.

## Walk protocol (calibration and all 24 walks)
- **Phone held at the right ear,** where glasses would sit, for calibration and every walk.
  This replaces the guide's "right hand at chest height". Hold it the same way every time.
  Reason: glasses sit at head height next to the head, and body position changes the signal
  a lot (about 12 dB from body rotation alone in the VSC data). The VSC "held to left ear"
  phone is the closest public stand-in for glasses.
- **Blocked walk:** B stands **0.5 m in front of the Pi**, on the direct line to the phone.
  Needs a floor mark (T10). NIST measured about -10 dB for this case at 2 m.
- **Bag walk: kept, as an honest hard case.** It stands for "glasses switched on but packed
  away", which should not buzz (all rows labelled 0). A bag barely weakens the signal
  (about 0 to 3 dB), so it will probably buzz anyway. Report that result rather than hide it.
  The bag sits on a chair at 2 m, not worn.

## Simulation settings that follow from this
- Blocked walks: loss from NIST, person 0.5 m from a device, 1-3 m (median 12.5 dB),
  drawn per walk. See `nist/README.md`.
- Bag walks: bag on a chair, so small: VSC purse vs ear at mirror rotations (median 3.8 dB),
  drawn per walk. See `nist/README.md`.
- Base signal and noise: MITRE Android recordings, matched to our Pi using our own 2 m and
  6 m calibration (which will be taken with the phone at the ear).

## Sources
- Nearby Glasses Android app: https://www.helpnetsecurity.com/2026/02/27/nearby-glasses-android-app-detect-smart-glasses/
- The Register on Nearby Glasses: https://www.theregister.com/2026/02/25/meta_smart_glasses_android_app/
- ZuckOff: https://pinggy.io/blog/zuckoff_meta_glasses_bluetooth_detector/
- NoPeek: https://radar.offseq.com/threat/nopeek-detecting-smart-glasses-and-vr-headsets-via-828327fb
- NoPeek build write-up: https://dev.to/getnopeek/how-i-built-an-app-to-detect-meta-ray-ban-glasses-using-immutable-ble-company-ids-3cfe
- Data behind the walk numbers: `nist/README.md` (NIST BLE RSSI, MIT-LL VSC).
