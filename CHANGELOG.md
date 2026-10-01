# Changelog

What changed in each version, in plain words. The section of a version becomes its GitHub release notes and
the "What's new" list in the app's update banner, so write it for the person who uses Pulse. Every version needs
one before its tag is pushed (the release is not published without it).

## 0.4.6

- The assistant answers in the language of your last message. Before, a Russian question asked after an English
  one (for example after one of the suggestion buttons) could get an English answer.

## 0.4.5

- The assistant explains the auto-tune in plain words, with the numbers: what the best step would gain and whether
  that is worth it, and why the dips in the slowest 1% stay. It no longer suggests lowering settings that gained nothing, and offers
  the Apply button only for a step that clearly helped.
- From the next update on, the update banner has a "What's new" button with a list like this one.

## 0.4.4

- Auto-tune for CS2 (Games → Counter-Strike 2): lowers each graphics setting one step from yours, measures what
  each step gains and ranks them. Your settings are restored after every run, and "Stop after this run" ends it cleanly.
- A step that clearly helped gets an Apply button (with Undo), and the assistant can recommend and propose it.
  The app can now change FSR, dynamic shadows, ambient occlusion, shaders and textures besides MSAA and shadows.
- A benchmark batch says where its worst 1% of frames come from: the same heavy places of the route every run,
  or random moments that point at something else on the PC.
- The worst-1% FPS is now judged against its own run-to-run spread, so a lucky run is no longer called "Better".
- The processor-heavy bots scene works in the game.
- Setup refuses to move the installed app's background jobs onto a source checkout (that led to "No collected data").

## 0.4.3

- Game sessions are recorded automatically while a listed game runs, and the Overview has a live "right now" row.
- CPU temperature, and AMD and Intel graphics cards, through LibreHardwareMonitor when it runs.
- A weekly digest compares this week with the last one.
- Benchmark batches show readable variant names and can be shared as Markdown or a picture.
- The assistant can propose a CS2 video setting change, applied only by your button click.
- Fewer false alarms: programs Windows re-creates or updates, per-user services reborn at logon and the app's own
  jobs are no longer reported as new autostart entries; programs launched through a script host by a document
  or a browser are flagged.
