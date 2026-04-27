# Changelog

All notable changes to this project will be documented in this file.
Entries are generated automatically by `/promote` and committed to staging before the promotion PR.

## [1.0.0](https://github.com/Roxabi/voiceCLI/compare/voicecli/v0.2.1...voicecli/v1.0.0) (2026-04-27)


### ⚠ BREAKING CHANGES

* **deps:** editable installs now require an extra. Use 'uv sync --extra all' for full feature set, or '--extra tts' / '--extra stt' for single-engine setups.

### Features

* **deploy:** add container form factor for Quadlet deployment ([#101](https://github.com/Roxabi/voiceCLI/issues/101)) ([ef32895](https://github.com/Roxabi/voiceCLI/commit/ef32895eaa669836c99802125515145f1d05dc86))
* **docker:** split production image into TTS and STT variants ([a55bb4a](https://github.com/Roxabi/voiceCLI/commit/a55bb4ae8a75f2c6b1832d4a659596c5c9dd4e2f))
* **docker:** split production image into TTS and STT variants ([0b3d216](https://github.com/Roxabi/voiceCLI/commit/0b3d21652f883e9417acc613b9d2a2d25cca2171)), closes [#121](https://github.com/Roxabi/voiceCLI/issues/121)
* **e2e:** slim Dockerfile.mock + dual publish for fast E2E SUT ([147892a](https://github.com/Roxabi/voiceCLI/commit/147892a8dace5edf3dd491d39244ddff99fc66d7)), closes [#116](https://github.com/Roxabi/voiceCLI/issues/116)
* **infra:** migrate to shared roxabi-ml-base image + torch as extra ([8b6a9f2](https://github.com/Roxabi/voiceCLI/commit/8b6a9f2176f7a8db1794f7654dac34658dca98e2))
* **model-registry:** add LRU eviction for VRAM management ([#100](https://github.com/Roxabi/voiceCLI/issues/100)) ([055db2f](https://github.com/Roxabi/voiceCLI/commit/055db2feeb096b7b5e6c3344ce051145cd5b35bd))
* **ops:** adopt cross-project container publishing pattern ([#112](https://github.com/Roxabi/voiceCLI/issues/112)) ([e9d5015](https://github.com/Roxabi/voiceCLI/commit/e9d50153e933e96757623b4a53fa229dfc6667b9))
* **quadlet:** repoint voiceCLI workers to lyra-nats on roxabi.network (big-bang NATS) ([c8713d6](https://github.com/Roxabi/voiceCLI/commit/c8713d606be3f1998b7cbd0872ff545b7d03ccc9))
* **quadlet:** repoint voiceCLI workers to lyra-nats on roxabi.network (big-bang NATS) ([82897b6](https://github.com/Roxabi/voiceCLI/commit/82897b63b2ab5de738188ee90757e78022e6963e))
* **quadlet:** voiceCLI Phase 2 Quadlet infrastructure (ADR-055) ([#105](https://github.com/Roxabi/voiceCLI/issues/105)) ([64b9756](https://github.com/Roxabi/voiceCLI/commit/64b975697f873b9a68c673aed0b63cf8485279ae))
* **tts:** pre-warm TTS engine on adapter startup + auto-update labels ([a352de8](https://github.com/Roxabi/voiceCLI/commit/a352de89459ff69e7d4d01bfe130017da2ff801a))


### Bug Fixes

* **daemon:** re-export _sanitize_request for backwards compat with tests ([5661622](https://github.com/Roxabi/voiceCLI/commit/5661622cd1b6bba6a639d18f217a5542922f96a3))
* **docker:** correct nvidia/cuda base tag — 12.4 ubuntu24 never existed on Docker Hub ([aad8512](https://github.com/Roxabi/voiceCLI/commit/aad8512424c8ed1faf48e1a5554ee760344d6610))
* **docker:** correct nvidia/cuda base tag — 12.4 ubuntu24 never existed on Docker Hub ([938b8b6](https://github.com/Roxabi/voiceCLI/commit/938b8b65cf7bf1cb4ea1a93c8e06992d5c8e4b92))
* **docker:** drop --extra voxtral — heavy optional engine not used by default ([95e0949](https://github.com/Roxabi/voiceCLI/commit/95e0949d6af3bfd1b262c491971a315fbf8fd5a7))
* **docker:** drop `--extra voxtral` — heavy optional engine not used by default ([f68cc55](https://github.com/Roxabi/voiceCLI/commit/f68cc558364c70c75d5f4bf5b00e96584e49356d))
* **docker:** drop `uv run` from entrypoint — uv not in runtime stage ([d654f00](https://github.com/Roxabi/voiceCLI/commit/d654f006f61af02024eac542b761e7c9cf7020c2))
* **docker:** drop uv run from entrypoint — uv missing in runtime stage ([8edf94d](https://github.com/Roxabi/voiceCLI/commit/8edf94d63316073532682b5b187a6b5eace595e9))
* **docker:** exclude nvidia-*/triton transitive deps from venv ([e016b33](https://github.com/Roxabi/voiceCLI/commit/e016b33044fedf305ea73111047d6c94955cfc1c))
* **docker:** fix uv tarball extraction path (strip-components + explicit member) ([63c869f](https://github.com/Roxabi/voiceCLI/commit/63c869fbd22754ab428b2add37c2c79ce5870d15))
* **docker:** fix uv tarball extraction path (strip-components + explicit member) ([a3e6315](https://github.com/Roxabi/voiceCLI/commit/a3e631553cbc67965592d3e0e17f807cc0e29bab))
* **docker:** fully qualify base images to docker.io/* (unblock M₁ build) ([1d42f05](https://github.com/Roxabi/voiceCLI/commit/1d42f0592d43085e716854ef5041d88e247a0c6c))
* **docker:** fully qualify base images to docker.io/* (unblock M₁ build) ([014866c](https://github.com/Roxabi/voiceCLI/commit/014866c510db0444923fe864b143095ae7d19b86))
* **docker:** inherit torch from ml-base via system-site-packages ([a29c280](https://github.com/Roxabi/voiceCLI/commit/a29c2800c8f94a1e57dcd2b5dc9a263e60880fa0)), closes [#116](https://github.com/Roxabi/voiceCLI/issues/116)
* **docs,quadlet,supervisor:** address PR [#107](https://github.com/Roxabi/voiceCLI/issues/107) review — post-cutover topology ([e894508](https://github.com/Roxabi/voiceCLI/commit/e8945082f2c9498c137bcd1b19a0ed67825d01e1))
* **importlinter:** register daemon_protocol exemptions in layer contract ([fd2d61d](https://github.com/Roxabi/voiceCLI/commit/fd2d61def08f895a67637245f5123599a1fd1a98))
* **nats:** Fix 1 — lowercase inbox_prefix for voice-tts and voice-stt ([48c81fd](https://github.com/Roxabi/voiceCLI/commit/48c81fd61fc20767ee5fadc485a80a0b268566f8))
* **nats:** tighten /tmp/voicecli-nats file perms to 0o600 ([#103](https://github.com/Roxabi/voiceCLI/issues/103)) ([8e4df4e](https://github.com/Roxabi/voiceCLI/commit/8e4df4ecdddc1ff0391a6f0aebafd5609f77d8d4))
* **nats:** use roxabi-contracts TtsResponse/SttResponse in adapters ([#120](https://github.com/Roxabi/voiceCLI/issues/120)) ([082a97b](https://github.com/Roxabi/voiceCLI/commit/082a97b2f79fa4d73e0f55395332209501844e87))
* **nkeys:** relocate NATS seed paths to ~/.voicecli/nkeys/ (ADR-055 D4) ([466aeb5](https://github.com/Roxabi/voiceCLI/commit/466aeb58e30e365b251232c4401626222179e76e))
* **nkeys:** relocate NATS seed paths to ~/.voicecli/nkeys/ (ADR-055 D4) ([7d60bae](https://github.com/Roxabi/voiceCLI/commit/7d60bae4b0e68358e137612a6657ecaf871414a2))
* **quadlet:** add missing NATS_NKEY_SEED_PATH and secret uid/gid for voicecli-tts and voicecli-stt ([abb8a7d](https://github.com/Roxabi/voiceCLI/commit/abb8a7df5ffeb665d8360afcfea0e92bda33d2a9))
* **quadlet:** align Secret= names with ADR-055 D4 convention ([a8e7ed9](https://github.com/Roxabi/voiceCLI/commit/a8e7ed902384557d04031d7f9eb1cfc0f128f728))
* **quadlet:** fix healthcheck quoted-string syntax error ([7b6cd0d](https://github.com/Roxabi/voiceCLI/commit/7b6cd0db378f370fc341854a5e36797dab975c96))
* **quadlet:** replace Device= with AddDevice= for CDI GPU ([2197d7a](https://github.com/Roxabi/voiceCLI/commit/2197d7ac9592a8d5230a139552e20eff0d9e7878))
* **quadlet:** simplify HealthCmd to avoid shell quoting issues ([b0fee57](https://github.com/Roxabi/voiceCLI/commit/b0fee57302f4553a9e5085c01cd160a3d52ebacc))
* **qwen:** normalize ISO language codes to full names before TTS synthesis ([bb6207d](https://github.com/Roxabi/voiceCLI/commit/bb6207de4d0405779bc5f1be1e6c76dd5ebb9f22))
* **supervisor:** remove engine env vars, add exitcodes for VRAM safety ([da4ea27](https://github.com/Roxabi/voiceCLI/commit/da4ea2717f005d71d7d0b45bccf78711aae4842f))


### Documentation

* extras topology for torch-as-extra split ([2ec2da4](https://github.com/Roxabi/voiceCLI/commit/2ec2da4f8f9e15791c1ed926d223121fae7a8d9e)), closes [#116](https://github.com/Roxabi/voiceCLI/issues/116)
* **nats:** document per-request engine switching and model_registry LRU cache ([247aeed](https://github.com/Roxabi/voiceCLI/commit/247aeedcd3dfe9bf25d34dd406f189246387f361))
* **quadlet:** replace deploy phases with auto-update section ([d95d65b](https://github.com/Roxabi/voiceCLI/commit/d95d65b8afdd1379f2262aaf0b15c4a1b3316038))
* **spec:** add spec for [#116](https://github.com/Roxabi/voiceCLI/issues/116) ml-base migration ([e6169ee](https://github.com/Roxabi/voiceCLI/commit/e6169ee84f836a56d0500866ff1a712d9d8b6b1a))


### Code Refactoring

* **deps:** split torch/ML libs into [tts]/[stt] extras for ml-base ([e75ef72](https://github.com/Roxabi/voiceCLI/commit/e75ef729eb025f3bbb1b9a725d2c4073d3f14e0a)), closes [#116](https://github.com/Roxabi/voiceCLI/issues/116)

## [0.3.0](https://github.com/Roxabi/voiceCLI/compare/voicecli/v0.2.1...voicecli/v0.3.0) (2026-04-27)

### Features

- feat(deploy): add container form factor for Quadlet deployment (#101)
- feat(model-registry): add LRU eviction for VRAM management (#100)
- feat(quadlet): voiceCLI Phase 2 Quadlet infrastructure — ADR-055 (#105)
- feat(quadlet): repoint workers to lyra-nats on roxabi.network (#107)
- feat(ops): adopt cross-project container publishing pattern (#112)
- feat(infra): migrate to shared roxabi-ml-base image + torch as extra (#118)
- feat(docker): split production image into TTS and STT variants (#122)

### Bug Fixes

- fix(nats): tighten /tmp/voicecli-nats file perms to 0o600 (#103)
- fix(nkeys): relocate NATS seed paths to ~/.voicecli/nkeys/ — ADR-055 D4 (#104)
- fix(docker): fully qualify base images to docker.io/* (#108)
- fix(docker): correct nvidia/cuda base tag (#109)
- fix(docker): fix uv tarball extraction path (#110)
- fix(docker): drop uv run from entrypoint (#113)
- fix(docker): drop --extra voxtral from default image (#114)
- fix(nats): use roxabi-contracts TtsResponse/SttResponse in adapters (#120)

### Refactors

- refactor: split oversized modules to meet 300-line file-length gate (#130)
- chore: drain-and-swap docs + post-merge review fixes (#119)

---


## [0.2.1](https://github.com/Roxabi/voiceCLI/compare/voicecli/v0.2.0...voicecli/v0.2.1) (2026-04-22)


### Bug Fixes

* **api:** honor daemon-absent fallback + skip socket probe on NATS path ([c0d85c9](https://github.com/Roxabi/voiceCLI/commit/c0d85c9a74fcc7f77b3f311e803e7d44bdfdf363))
* **api:** honor daemon-absent fallback + skip socket probe on NATS path ([13cf5ef](https://github.com/Roxabi/voiceCLI/commit/13cf5ef008bffc1ce370398b536d4ca45b7dee55))
* **e2e:** make engine registry robust to missing torch ([#92](https://github.com/Roxabi/voiceCLI/issues/92)) ([4fba1c4](https://github.com/Roxabi/voiceCLI/commit/4fba1c453eaf1fe46b1939cd3839808bdc7042a9))
* **nats:** scope nats-py reply inbox per ADR-051 for voice-tts/voice-stt ([04ebc61](https://github.com/Roxabi/voiceCLI/commit/04ebc61be06cf11b0a01edbd3e4d49427eb1ede1))
* **nats:** scope nats-py reply inbox per ADR-051 for voice-tts/voice-stt ([125053e](https://github.com/Roxabi/voiceCLI/commit/125053e794f97175e50e6b13c2a6a2c425a4ea4c))


### Documentation

* **frame:** add e2e engine_unavailable failure frame ([caacb82](https://github.com/Roxabi/voiceCLI/commit/caacb82e363cb27a126ae624de9694db4db0ccef))
* **plan:** add e2e engine_unavailable failure plan ([060d052](https://github.com/Roxabi/voiceCLI/commit/060d0524f6a6ccb11ea36c69cf0f01ada3cd0763))
* **spec:** add e2e engine_unavailable failure spec ([47c8f94](https://github.com/Roxabi/voiceCLI/commit/47c8f948727ded451cd9cab7f86c2f99e319ea5c))

## [0.2.0](https://github.com/Roxabi/voiceCLI/compare/voicecli/v0.1.0...voicecli/v0.2.0) (2026-04-21)


### Features

* add --plain flag, .txt input, and plain/chunked/chunk_size toml defaults ([7bc2ae8](https://github.com/Roxabi/voiceCLI/commit/7bc2ae856f1ba7e5c5a61907be02f4e981a2eef9))
* add --version flag and PATH install to `voicecli init` ([2b45ede](https://github.com/Roxabi/voiceCLI/commit/2b45ede3f56e4c4309c4c02ad9899f14ce40a572))
* add `voiceme init` command to scaffold voiceme.toml ([a8d9c73](https://github.com/Roxabi/voiceCLI/commit/a8d9c73c5c0e3bad452697c1ab13140eaac35339))
* add Chatterbox Turbo engine and universal document translator ([ee33979](https://github.com/Roxabi/voiceCLI/commit/ee3397902e6c22f05e3b76c38f1a0286b69b89a7))
* add first-use experience (doctor command, CUDA guard, download warnings) ([0ed96ef](https://github.com/Roxabi/voiceCLI/commit/0ed96ef64fae6d7d5a2f6a9792b5c498d4a9ba31))
* add qwen-fast engine backend using faster-qwen3-tts ([0f5793e](https://github.com/Roxabi/voiceCLI/commit/0f5793ea38cb36f5944b9cc10ceb27c7ba0dd690))
* add speech-to-text with Faster Whisper and Kyutai STT ([9524a9a](https://github.com/Roxabi/voiceCLI/commit/9524a9a7710e64be8172054607eb36e8920c1f2c))
* add user config (voiceme.toml), per-section directives, and segment transitions ([6087342](https://github.com/Roxabi/voiceCLI/commit/608734261bcd2c38e717dc9ddc3a8053b1e17af9))
* add voicecli serve daemon for warm Qwen model generation ([f41ca45](https://github.com/Roxabi/voiceCLI/commit/f41ca450da616d5f86f7775100fcbd621aead96a))
* **api:** add input validation for TTS parameters ([#35](https://github.com/Roxabi/voiceCLI/issues/35)) ([60c4e2e](https://github.com/Roxabi/voiceCLI/commit/60c4e2ebd304e17ba06fae54f8a52b2dc4db76df))
* **api:** expose voiceCLI as importable Python library ([#14](https://github.com/Roxabi/voiceCLI/issues/14)) ([8c19691](https://github.com/Roxabi/voiceCLI/commit/8c19691da489bdd90ae669e549647aa36d9d310f))
* Chatterbox chunking, MP3 support, voiceme skill ([4cc6192](https://github.com/Roxabi/voiceCLI/commit/4cc61922da54086f7778bec1a1ce8c4defc653cb))
* chunked output for progressive audio sending ([d74284d](https://github.com/Roxabi/voiceCLI/commit/d74284d3db5088278e39b1dd3e499d195aaf9267))
* **config:** add --config flag to generate and clone commands ([e862756](https://github.com/Roxabi/voiceCLI/commit/e862756da9cc87d06bc053808dcb1a722d0016c3))
* **config:** walk up from CWD to \$HOME to find voicecli.toml ([c28ca20](https://github.com/Roxabi/voiceCLI/commit/c28ca20ab417290966e16b3c1e7daa386f1846a1)), closes [#3](https://github.com/Roxabi/voiceCLI/issues/3)
* **daemon:** VRAM guard before loading new engine ([2100d53](https://github.com/Roxabi/voiceCLI/commit/2100d53b239230cce06d36a12d6508bdc3fa3423))
* **dictate:** --setup wizard + smarter clipboard error ([f980f9c](https://github.com/Roxabi/voiceCLI/commit/f980f9c43e86bf456b1aae5f95135a916c55793e))
* **dictate:** add dictate toggle command + global hotkey listener ([#7](https://github.com/Roxabi/voiceCLI/issues/7)) ([#24](https://github.com/Roxabi/voiceCLI/issues/24)) ([ed8802a](https://github.com/Roxabi/voiceCLI/commit/ed8802a43d0d09e28312c09e1eba80c50cbf8bcb))
* **dictate:** change default hotkey from Alt+Shift+Space to Ctrl+Space ([2d4a1f9](https://github.com/Roxabi/voiceCLI/commit/2d4a1f977b40525de6018a1ac4c058ff079bc45a))
* **dictate:** modes system + transcription history ([#8](https://github.com/Roxabi/voiceCLI/issues/8)) ([c658117](https://github.com/Roxabi/voiceCLI/commit/c658117a7c677dec55b79dc1070665baae7f799a))
* **dictate:** overlay keyboard grab + 7 STT modes + default_mode from toml ([0ada7d2](https://github.com/Roxabi/voiceCLI/commit/0ada7d27bedec2faf371cf5d6c23ffde71c8131b))
* **dictate:** Tab mode cycling, overlay position fix, next-mode command ([3d025f1](https://github.com/Roxabi/voiceCLI/commit/3d025f19566b4a35f97f48a394f46cb19f3fc943))
* **dictate:** waveform overlay, cancel, clipboard fix, smart language detection, save recordings ([3573ccf](https://github.com/Roxabi/voiceCLI/commit/3573ccff02c6b20954ccb7a193863705b0a628d5))
* **engine:** add Voxtral 4B TTS engine (int4 HQQ quantization) ([10ea68f](https://github.com/Roxabi/voiceCLI/commit/10ea68f12ceb1ebbbe892c493c700f7bb6f9c693))
* **engine:** add VRAM pre-check before model load in standalone mode ([292f907](https://github.com/Roxabi/voiceCLI/commit/292f90750ca9ce534df1ff9cc8dad502e6bdb772))
* **engine:** expose sampling params (temperature, top_p, min_p, repetition_penalty) ([56dae69](https://github.com/Roxabi/voiceCLI/commit/56dae69602bc1844296bcf9f5bcc0730d6f9018f))
* French tag-to-instruct, descriptive output filenames, default voice Ono_Anna ([f1ee0c8](https://github.com/Roxabi/voiceCLI/commit/f1ee0c8a6e90f336f5706f86a881155604b7b814))
* initial VoiceMe CLI with sample management, markdown input, and emotion controls ([1ef7067](https://github.com/Roxabi/voiceCLI/commit/1ef7067279d37170b72ede34ac51898e08d8a22d))
* make `voiceme init` interactive with step-by-step prompts ([f3306e4](https://github.com/Roxabi/voiceCLI/commit/f3306e413fcd14c9fdc92d50c7e07160f2e8822e))
* make voiceCLI a self-contained Claude Code marketplace plugin ([455b2c4](https://github.com/Roxabi/voiceCLI/commit/455b2c42cdce75fde1f8efc25df7b61b676a6c93))
* multi-segment instruct for per-section emotion control ([42de24b](https://github.com/Roxabi/voiceCLI/commit/42de24b4223b5d4c518a4e3426f66b6c1e8b0ffa))
* **nats:** add voicecli nats-serve tts entry point ([#43](https://github.com/Roxabi/voiceCLI/issues/43)) ([adbf37d](https://github.com/Roxabi/voiceCLI/commit/adbf37d2fc61dc942e39628e182ff07197096524))
* **nats:** docker-compose E2E + stub-hub CI job (V3b-V3d) ([#62](https://github.com/Roxabi/voiceCLI/issues/62)) ([0165cb8](https://github.com/Roxabi/voiceCLI/commit/0165cb868ad4a7e3199ce21593c12ae119ff9b95))
* **nats:** forward full ADR-044 TTS field set ([#66](https://github.com/Roxabi/voiceCLI/issues/66)) ([42c1898](https://github.com/Roxabi/voiceCLI/commit/42c189858f9a396645e007ec7c5343e3a5b56843)), closes [#47](https://github.com/Roxabi/voiceCLI/issues/47)
* **nats:** mock engine + env gate (slice V3a of [#45](https://github.com/Roxabi/voiceCLI/issues/45)) ([#52](https://github.com/Roxabi/voiceCLI/issues/52)) ([556551f](https://github.com/Roxabi/voiceCLI/commit/556551f22a67e32d473d4466666e8624a40f06a0))
* **nats:** subscribe to per-worker subject for targeted hub routing ([e561ffa](https://github.com/Roxabi/voiceCLI/commit/e561ffa5cc4f4160937043f55568e869fc9d2832))
* **nats:** voicecli nats-serve stt entry point (slice V2) ([#51](https://github.com/Roxabi/voiceCLI/issues/51)) ([883ba5e](https://github.com/Roxabi/voiceCLI/commit/883ba5eb565edcda4e71fb618363fe2f8b57bd70))
* **overlay:** add start/stop UI sounds (mic tap + slow cut) ([4711242](https://github.com/Roxabi/voiceCLI/commit/471124206b2a9bacbe882397670f2aa4a4f6bd01))
* **overlay:** rewrite overlay with GTK3 + layer-shell for native Wayland ([60c1dc4](https://github.com/Roxabi/voiceCLI/commit/60c1dc40279560b025cd786f411da975e393c265))
* **overlay:** waveform bars + auto-paste (AHK trigger) + real audio levels ([9e0995b](https://github.com/Roxabi/voiceCLI/commit/9e0995b53db247adee80eb863fce4e39d2bf24c5))
* **sample-pick:** clean-segment picker + yt-clone v1.1 auto-pick ([85b19c5](https://github.com/Roxabi/voiceCLI/commit/85b19c5a3666d3673123f416c254dd5e99953ea0))
* **samples:** add from-url command for YouTube voice sampling ([#22](https://github.com/Roxabi/voiceCLI/issues/22)) ([70b0513](https://github.com/Roxabi/voiceCLI/commit/70b0513f0b3cefcd0d70289f2b53cb14f15beec7))
* smooth tag transitions with onomatopoeia for Qwen TTS ([bfce07a](https://github.com/Roxabi/voiceCLI/commit/bfce07a9f10ad5f2a61f0c89af37ea83e2f4dd9a))
* structured instruct composition (accent, personality, speed, emotion) ([fd93a9a](https://github.com/Roxabi/voiceCLI/commit/fd93a9a1290eaf142aa98177845c09f7d622e1c9))
* **stt:** add stt-serve daemon with pyaudio recording and clipboard ([#9](https://github.com/Roxabi/voiceCLI/issues/9)) ([39f2b01](https://github.com/Roxabi/voiceCLI/commit/39f2b01d5d2b34fdaed96142a3577e4ac05e0245))
* **stt:** personal vocabulary support + supervisor scripts ([7bb42da](https://github.com/Roxabi/voiceCLI/commit/7bb42dacb64359320fed9bfa882bf24d7e6f8db8))
* **stt:** route file transcription through STT daemon for warm-model reuse ([387477d](https://github.com/Roxabi/voiceCLI/commit/387477d921d2ddba0b1e701dd1c092ebf141dda1))
* **supervisor:** own NATS-mode wrapper + configs (migrate from lyra) ([a96f838](https://github.com/Roxabi/voiceCLI/commit/a96f8381eaaed859f1b6d4a8bd85bcc08eaf5c90))
* **tts:** add FIFO request queue to TTS daemon ([#32](https://github.com/Roxabi/voiceCLI/issues/32)) ([8afeb88](https://github.com/Roxabi/voiceCLI/commit/8afeb889b87c98ecdc36537169966087cf640b34))
* upgrade Chatterbox to Multilingual model (23 languages) ([3053870](https://github.com/Roxabi/voiceCLI/commit/3053870fa229082fb220d3edeeba07cc34d41c27))
* **voice-design:** autonomous voice personality design skill + tg.py helper ([9fdddbc](https://github.com/Roxabi/voiceCLI/commit/9fdddbc6fced41546987c489afa2c67884774a4e))
* **voxtral:** make flow_steps/cfg_alpha configurable via CLI, frontmatter, and toml ([6ea815b](https://github.com/Roxabi/voiceCLI/commit/6ea815bf1d943870d4f6698a6e0fceca3d510646))
* **vram:** VRAM lifecycle management for TTS/STT daemons ([#37](https://github.com/Roxabi/voiceCLI/issues/37)) ([9388356](https://github.com/Roxabi/voiceCLI/commit/93883563543dc72b8912df4d33c87703bd3bce8b))


### Bug Fixes

* **api:** compose instruct from API kwargs, not just config ([4812550](https://github.com/Roxabi/voiceCLI/commit/4812550f8b2a295f976d9f260a61b2b48df491e8))
* **api:** log daemon errors instead of silently swallowing them ([0198285](https://github.com/Roxabi/voiceCLI/commit/01982852fd0035b2a537c7f85e9a1d6e3996feb8))
* **api:** remove in-process fallback for QWEN engines — wait for daemon instead ([10ce157](https://github.com/Roxabi/voiceCLI/commit/10ce157b72557ba393d7abea4bc0a022407ac73f))
* **ci:** add GTK3/Cairo system deps for pycairo and PyGObject ([25d25e1](https://github.com/Roxabi/voiceCLI/commit/25d25e1f8fa342e6a409ba9d1ff72d936b7cc16f))
* **ci:** install portaudio19-dev for pyaudio build ([75d6ab9](https://github.com/Roxabi/voiceCLI/commit/75d6ab918f9f17962190d736f6742ea2c32bfcd2))
* **ci:** pin CI actions to commit SHAs, harden supervisor config ([5c36cc5](https://github.com/Roxabi/voiceCLI/commit/5c36cc575bdd89ff63f57ca29b4813495a9b3fc3))
* **ci:** use girepository-2.0-dev for PyGObject on Ubuntu 24.04 ([833973c](https://github.com/Roxabi/voiceCLI/commit/833973cb8ca09277e9760818e905d49c14d284e5))
* **config:** frontmatter voice field overrides voicecli.toml default ([#12](https://github.com/Roxabi/voiceCLI/issues/12)) ([3b5936a](https://github.com/Roxabi/voiceCLI/commit/3b5936af1a614f36d95ed09671a95dde09b3e086))
* correct language ISO codes in output filenames, add tag-map key parity guard ([619cc63](https://github.com/Roxabi/voiceCLI/commit/619cc63f39806d216bc63d6cb51b27ea4b113c6f))
* **deps:** pin transformers&lt;5 to fix qwen_tts import error ([32c8e86](https://github.com/Roxabi/voiceCLI/commit/32c8e868c88e9f1adb931f7f0f0409af6f0745fa))
* **dictate:** add cancel command + fix overlay shortcuts + lower sound volume ([fe1b70d](https://github.com/Roxabi/voiceCLI/commit/fe1b70d008b2669276a8aae150aed368027f6e1b))
* **dictate:** use Ctrl+Shift+V for paste-without-formatting ([4f73f80](https://github.com/Roxabi/voiceCLI/commit/4f73f8073f22379522bdb74051d61ead68219298))
* **engine:** CLI -e flag now overrides frontmatter engine, fix qwen-fast clone without ref_text ([ac1813a](https://github.com/Roxabi/voiceCLI/commit/ac1813af36f55f8b1af084d9a386d150ead54c63))
* **nats:** concatenate chunked WAV output in TTS adapter ([#71](https://github.com/Roxabi/voiceCLI/issues/71)) ([fa11282](https://github.com/Roxabi/voiceCLI/commit/fa112826f99b95ec99cdae0218206b6b9b27e4c5))
* **nats:** restore VOICECLI_ENABLE_MOCK_ENGINE registry gate ([b98c7ed](https://github.com/Roxabi/voiceCLI/commit/b98c7ed56803bb02e89d44de6dccb8d26588cb2b))
* **nats:** restore VOICECLI_ENABLE_MOCK_ENGINE registry gate ([da4dca9](https://github.com/Roxabi/voiceCLI/commit/da4dca958c5d739e798f981c0706246491d01553))
* **overlay:** move to top of screen, add test-overlay command ([0e67ed5](https://github.com/Roxabi/voiceCLI/commit/0e67ed5e81c8ce8e190798e473f33c92e0a69927))
* **overlay:** prevent freeze from I/O blocking and poll pile-up ([#34](https://github.com/Roxabi/voiceCLI/issues/34)) ([886a061](https://github.com/Roxabi/voiceCLI/commit/886a0611cfa52c39a3152776eee725cdc6921da4))
* **overlay:** remove duplicate chimes from stt_daemon ([8a51478](https://github.com/Roxabi/voiceCLI/commit/8a514788b7041c636ed15497d7a0bbc0423a30fc))
* **overlay:** remove global X11 grab and close on any non-recording state ([7c76aef](https://github.com/Roxabi/voiceCLI/commit/7c76aef1b411afd4abd5df2717b2ce77a62d6d74))
* **overlay:** remove unused stt_client imports ([2414fdc](https://github.com/Roxabi/voiceCLI/commit/2414fdc0ae4b087195b60e3fb9a85c08aee4bb73))
* **overlay:** use cursor position to detect active monitor on multi-monitor setups ([ee24ce3](https://github.com/Roxabi/voiceCLI/commit/ee24ce37918b947cdaa56411baf8647965e691ad))
* **overlay:** use xrandr to detect primary monitor on multi-monitor setups ([5e971a1](https://github.com/Roxabi/voiceCLI/commit/5e971a13c375d03899f96fcff304ebf18c23b7b5))
* propagate voiceme.toml instruct parts to segments and preserve base_instruct in post-tag transitions ([577cb77](https://github.com/Roxabi/voiceCLI/commit/577cb7719249090d666ac278f8c749ce56223e1c))
* PyTorch cu128 for RTX 5070 Ti (Blackwell) and Qwen clone x-vector fallback ([20f3fe5](https://github.com/Roxabi/voiceCLI/commit/20f3fe599a8ab85a947216f88028e6205a755879))
* **qwen-fast:** guard instruct kwarg on clone path ([#86](https://github.com/Roxabi/voiceCLI/issues/86)) ([#87](https://github.com/Roxabi/voiceCLI/issues/87)) ([d736dcd](https://github.com/Roxabi/voiceCLI/commit/d736dcdb049b8dba2cfca5ec2161901fe7074cd8))
* **sample_pick:** preserve segments when file ends mid-silence (H1) ([c3655de](https://github.com/Roxabi/voiceCLI/commit/c3655de313425273fd40fdb5e765a64db06f2764))
* **sample_pick:** surface ffmpeg stderr tail on non-zero exit (M3) ([1f3a3fb](https://github.com/Roxabi/voiceCLI/commit/1f3a3fbf091b5f8d2a0b7fa9b758fd6a96c2e78f))
* **supervisor:** remove WSL2-specific env from stt conf ([85e1a32](https://github.com/Roxabi/voiceCLI/commit/85e1a32b77ba06c5ecea442cc01be690e6cea890))
* **tests:** add pytest pythonpath for src layout ([#4](https://github.com/Roxabi/voiceCLI/issues/4)) ([d896947](https://github.com/Roxabi/voiceCLI/commit/d8969470b99563bea698c51a8b6a3744fd6fc668))
* **tests:** decouple unit tests from Qwen daemon requirement ([58fb94a](https://github.com/Roxabi/voiceCLI/commit/58fb94ad6af71cd148b552aafb76aeb3a1b5c05a))
* **test:** update stt_daemon mocks for _play_ui_sound/_spawn_overlay + fix CI pytest invocation ([52df1ef](https://github.com/Roxabi/voiceCLI/commit/52df1ef78e09384357fe8155829e7a503390e1ee))
* use huggingface_hub constant for cache dir, clean up types and regex ([8d89a47](https://github.com/Roxabi/voiceCLI/commit/8d89a4784a9d7ea81a322bac9d7496eefed54b9f))
* **voicecli-dir:** drop . and .. from discovery fallback (H2) ([1e39dc6](https://github.com/Roxabi/voiceCLI/commit/1e39dc6f26f118d188ec49fd34ff2e237f5ec9b2))
* **yt-clone:** ~/projects/lyra-stack ghost path → ~/projects (H3) ([21dcb5c](https://github.com/Roxabi/voiceCLI/commit/21dcb5c2693ee93577e00ac915f0262002ced973))
* **yt-clone:** require --start ∧ --duration together; --blind exclusive (B3, H4) ([64373bf](https://github.com/Roxabi/voiceCLI/commit/64373bf82ed07fb6bda4ea3cba4bec33d97ed96c))


### Performance Improvements

* **overlay:** play start sound from daemon for zero-latency feedback ([7c1d117](https://github.com/Roxabi/voiceCLI/commit/7c1d11750bb2d45de2b01b3735f8421d94a52220))
* TF32 precision, clone prompt reuse, --fast flag for 0.6B model ([f6b94c7](https://github.com/Roxabi/voiceCLI/commit/f6b94c71013548c4457c1fbab8ab5b43bf156e12))


### Documentation

* add approved spec for [#6](https://github.com/Roxabi/voiceCLI/issues/6) stt-serve daemon ([2aafe80](https://github.com/Roxabi/voiceCLI/commit/2aafe809396d6786af5b6e400c6e7d93f7c59f26))
* add contributing invitation to README ([61aa24f](https://github.com/Roxabi/voiceCLI/commit/61aa24f058ba2f35b7b8bd7dbd5e69f228fda081))
* add implementation plan for [#6](https://github.com/Roxabi/voiceCLI/issues/6) stt-serve daemon ([56ec389](https://github.com/Roxabi/voiceCLI/commit/56ec389f5fdde274018ce6b53dfb6c705229d4b4))
* add supervisor gotchas + VRAM contention notes + yt-clone skill ([9769075](https://github.com/Roxabi/voiceCLI/commit/9769075a3ed5125b8df852e1c2a89a56c902d338))
* add TL;DR summary, git conventions, and gotchas section to CLAUDE.md ([1cf9078](https://github.com/Roxabi/voiceCLI/commit/1cf9078c893da18b78b139070f9a3113f037e6f4))
* align README and CLI docstrings with new TTS/STT directory paths ([f5a3b8b](https://github.com/Roxabi/voiceCLI/commit/f5a3b8bd34f7a009237e64325f9ef9f8b5532fef))
* **architecture:** add pattern catalog, glossary, and init retro ([fd6fb1d](https://github.com/Roxabi/voiceCLI/commit/fd6fb1d194ee273c6819a9c83bb152b10d21a5e5))
* **claude:** drop AskUserQuestion mention from decision protocol ([066f305](https://github.com/Roxabi/voiceCLI/commit/066f305f5bf08e1191e6c657b50c585fb849c5b2))
* **claude:** replace AskUserQuestion with Decision Protocol ([2a5956f](https://github.com/Roxabi/voiceCLI/commit/2a5956f2def8258e6b5a444258058886039e0879))
* **consensus:** video+voice code-review shipping strategy ([3db489b](https://github.com/Roxabi/voiceCLI/commit/3db489bae43be5f18b96a53bd27aaa73d835288f))
* **context:** clean CLAUDE.md, extract Code Pipeline ([d760766](https://github.com/Roxabi/voiceCLI/commit/d760766b7a82968c064cbb990748356d0cf629bf))
* document voicecli serve daemon in CLAUDE.md and README ([1d05c15](https://github.com/Roxabi/voiceCLI/commit/1d05c1562667642dc2a209ba0b22cb59dcc15619))
* document voicecli.toml walk-up discovery + add .env.example ([731345c](https://github.com/Roxabi/voiceCLI/commit/731345c78f6c6b0bd9a4cd401f3d95de9b8a6f49))
* document voiceme.toml segment propagation and post-tag base_instruct preservation ([a3ec97a](https://github.com/Roxabi/voiceCLI/commit/a3ec97a6c613ee29e1af299e3084b527c7791fa4))
* drop commit/push asking rule, keep destructive-ops guard ([ae8f283](https://github.com/Roxabi/voiceCLI/commit/ae8f283c9c8564a4c3239d5f14d0a2b9349cd532))
* expand standards and architecture documentation ([56b9363](https://github.com/Roxabi/voiceCLI/commit/56b9363eafc9aa93829f235519c6ee192e49c02a))
* **frame:** add approved frame for [#28](https://github.com/Roxabi/voiceCLI/issues/28) centralize shortcuts ([d3971a8](https://github.com/Roxabi/voiceCLI/commit/d3971a8e9830d92ae224293cd92a179d6f5880b8))
* **frame:** add frame for [#44](https://github.com/Roxabi/voiceCLI/issues/44) nats-serve stt entry point ([ae7addc](https://github.com/Roxabi/voiceCLI/commit/ae7addcbf6f939bc47dd3c96deab390a8c341a8c))
* **frame:** add frame for [#45](https://github.com/Roxabi/voiceCLI/issues/45) nats mock engine + stub-hub E2E ([4a55b84](https://github.com/Roxabi/voiceCLI/commit/4a55b845912dabcaab2b698562f09487a758c302))
* full doc audit — dictate section, LICENSE, config/stt ref ([40ad89c](https://github.com/Roxabi/voiceCLI/commit/40ad89cab44b866e7f1589fc0591a3b9f1086ed4))
* improve readme and contributing quality ([b165e3c](https://github.com/Roxabi/voiceCLI/commit/b165e3c086137f5b8cd4eda7c81fa13f00ad753f))
* **nats:** slice V4 polish — SKILL.md + CLAUDE.md + real-hub compose ([#63](https://github.com/Roxabi/voiceCLI/issues/63)) ([dcadae1](https://github.com/Roxabi/voiceCLI/commit/dcadae1e57c838b047d69d287a96d5c9cdd90e04))
* **plan:** add frame/analysis/spec/plan for [#42](https://github.com/Roxabi/voiceCLI/issues/42) nats-serve ([27e5ded](https://github.com/Roxabi/voiceCLI/commit/27e5dede3cb781ec9e8afccd2d8dc408fac21640))
* **plan:** add implementation plan for [#28](https://github.com/Roxabi/voiceCLI/issues/28) centralize shortcuts ([1875652](https://github.com/Roxabi/voiceCLI/commit/187565217fc88cfa9b9fd5dc52c86915a4a8da1e))
* **plan:** add implementation plan for VRAM lifecycle ([#36](https://github.com/Roxabi/voiceCLI/issues/36)) ([66b5e66](https://github.com/Roxabi/voiceCLI/commit/66b5e667aea0305ff63a6e819e80347d32d1a3e4))
* **plan:** add plan for [#44](https://github.com/Roxabi/voiceCLI/issues/44) nats-serve stt slice V2 ([cb73bcb](https://github.com/Roxabi/voiceCLI/commit/cb73bcb842a056b71a2a80671bd1b8e14f45057d))
* **plan:** add plan for [#45](https://github.com/Roxabi/voiceCLI/issues/45) slice V3a (mock engine + env gate) ([78ee9d6](https://github.com/Roxabi/voiceCLI/commit/78ee9d675711eeefa109fe1ba4c40fef82ec42b9))
* **readme:** add Library API section — Python-first paradigm ([#165](https://github.com/Roxabi/voiceCLI/issues/165)) ([ef217bc](https://github.com/Roxabi/voiceCLI/commit/ef217bc7dc8a650f3cfa594948aed29d5e9b7a26))
* **spec:** add approved spec for [#28](https://github.com/Roxabi/voiceCLI/issues/28) centralize shortcuts ([000c0a8](https://github.com/Roxabi/voiceCLI/commit/000c0a8b0bb7627171aff0dac563a37cd6ab4030))
* **spec:** add approved spec for VRAM lifecycle management ([#36](https://github.com/Roxabi/voiceCLI/issues/36)) ([916f53a](https://github.com/Roxabi/voiceCLI/commit/916f53a3951942c0b5b3e86e42996d5f37fc7f6c))
* **spec:** add spec for [#44](https://github.com/Roxabi/voiceCLI/issues/44) nats-serve stt slice V2 ([d69a9ce](https://github.com/Roxabi/voiceCLI/commit/d69a9ce95e682723ec785fd10b31a002ca59bc0e))
* **spec:** add spec for [#45](https://github.com/Roxabi/voiceCLI/issues/45) nats mock engine + stub-hub E2E ([f0b9af8](https://github.com/Roxabi/voiceCLI/commit/f0b9af8198cdec6c1fc2913de43fe442517b3fa5))
* **stt:** add STT setup guide ([7560652](https://github.com/Roxabi/voiceCLI/commit/7560652d540b2122163ba548973efe9b3b68cf86))
* sync dictation-setup + CLAUDE.md with overlay/AHK/auto-paste changes ([83add62](https://github.com/Roxabi/voiceCLI/commit/83add6243a5872dcc7c1ba33e621c6d6572fcdfe))
* update all references from tkinter to GTK3 + gtk-layer-shell ([457ca2f](https://github.com/Roxabi/voiceCLI/commit/457ca2f456c9df6934c1b1169e32d0d243c96fcc))
* update config discovery and data dirs to reflect ~/.voicecli/ canonical location ([4e5cbc8](https://github.com/Roxabi/voiceCLI/commit/4e5cbc8b48be4c45fbc45957937db2935c3f4429))
* update directive examples to show multi-key syntax ([d96a3b1](https://github.com/Roxabi/voiceCLI/commit/d96a3b1ef732a31a19a882ecd352039a0e36a6cb))
* update README and CLAUDE.md for voiceme.toml → example pattern ([a4c4011](https://github.com/Roxabi/voiceCLI/commit/a4c401130283cd6182e85671222e7b4fb789a491))
* update README, doctor, and emotions for new features ([90524b2](https://github.com/Roxabi/voiceCLI/commit/90524b2e67b5ee6392964b1e69aaa70300face24))
* update SKILL.md with chunked output and remove --mp3 default ([c2cb1dd](https://github.com/Roxabi/voiceCLI/commit/c2cb1ddede19ec7a290593876a301a266bea7cd1))

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
