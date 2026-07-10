# SFX pack

Placeholder sound clips, synthesized from scratch by `scripts/make_sfx.py`
(stdlib only, seeded — CC0-by-construction, safe to ship).

Where each clip is used is pure config:

- **Per-move sounds** — the `sfx` key on each entry in `config/moves.yaml`.
- **Event stingers** — the `ui.audio.events_sfx` block in `config/settings.yaml`
  (crit → crowd_roar, fumble → sad_trombone, ko → ko_bell, combo → air_horn,
  sudden_death → drumroll, replay → replay).

## Swapping in real sounds

Grab CC0 clips from Kenney.nl, Mixkit, or Freesound and either:

1. overwrite a file here with the same name (`.wav`; keep clips short), or
2. drop new files in and point the YAML keys at the new names.

Zero code changes either way. The host's Web Audio manager
(`web/host/audio.js`) lazy-loads whatever the config names.
