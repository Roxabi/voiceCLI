# Changelog

All notable changes to this project will be documented in this file.
Entries are generated automatically by `/promote` and committed to staging before the promotion PR.

## [v0.2.0] - 2026-03-11

### Added
- feat(dictate): waveform overlay, cancel, clipboard fix, smart language detection, save recordings
- feat(dictate): modes system + transcription history (#8)
- feat(dictate): Tab mode cycling, overlay position fix, next-mode command
- feat(dictate): --setup wizard + smarter clipboard error
- feat(dictate): overlay keyboard grab + 7 STT modes + default_mode from toml
- feat(overlay): waveform bars + auto-paste (AHK trigger) + real audio levels
- feat(overlay): add start/stop UI sounds (mic tap + slow cut)
- feat(voice-design): autonomous voice personality design skill + tg.py helper
- feat: make voiceCLI a self-contained Claude Code marketplace plugin

### Fixed
- fix(overlay): move to top of screen, add test-overlay command
- fix(overlay): remove duplicate chimes from stt_daemon
- fix(dictate): add cancel command + fix overlay shortcuts + lower sound volume
- fix(ci): install portaudio19-dev for pyaudio build
- fix(test): update stt_daemon mocks for _play_ui_sound/_spawn_overlay

### Performance
- perf(overlay): play start sound from daemon for zero-latency feedback

### Changed
- refactor(stt): deduplicate LEVEL_FILE, WSL detection, and history append
- docs: full doc audit — dictate section, LICENSE, config/stt ref
- docs: sync dictation-setup + CLAUDE.md with overlay/AHK/auto-paste changes
- chore: make voicecli SKILL.md source of truth in this repo

## [v0.1.0] - 2026-03-08

### Added
- feat(api): expose voiceCLI as importable Python library (#14)
- feat(stt): add stt-serve daemon with pyaudio recording and clipboard (#9)

### Fixed
- fix(config): frontmatter voice field overrides voicecli.toml default (#12)
- fix(tests): add pytest pythonpath for src layout (#4)

### Changed
- chore: commit doctor fixes — README, CONTRIBUTING, pip-licenses
- docs: add implementation plan for #6 stt-serve daemon
- docs: add approved spec for #6 stt-serve daemon
- chore: doctor fixes + frame for #6 stt-serve daemon
