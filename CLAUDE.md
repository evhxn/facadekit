# FacadeKit
Plan-driven repo. Source of truth: docs/04-repo-structure.md, docs/05-interfaces.md, tasks/.
- Implement only what a ticket specifies. Never change docs/05-interfaces.md without flagging it.
- A ticket is done only when its acceptance criteria pass.
- One commit per ticket: "T-XXX: <goal>".
- If a ticket conflicts with existing code or the interfaces, stop and report; don't improvise.
- Hardware spikes S-1, S-2, S-8 have NOT been run. Never hard-code the FPP media directory
  name, playlist entry fields, or output resolution/bitrate: put them in config with the
  plan's values as defaults.
