# Feature freezes

The maintainer has frozen new features in these areas:

- `brain_model/` and the local AI subsystem, including model loading, inference, prompt processing, assistant tools, and their web and CLI integrations.
- `tuch_controller/` and the touch-panel subsystem, including firmware, host previews, and panel-specific server integrations.

Limit changes in these areas to fixes for existing bugs, their regression tests, and verification. Leave enhancement proposals in the backlog until the maintainer explicitly lifts the relevant freeze.

# Web dashboard direction

When proposing GitHub issues, design for one household overview with the most important states, quick controls, and anything that needs attention. Give each device a dedicated view for everyday controls and advanced settings. Keep the overview usable on a phone and support English and Farsi.

Look for capabilities of the installed models that the dashboard has yet to expose. Check device adapters, manufacturer documentation, and local firmware support. Describe the user task, priority, acceptance criteria, and verification for each proposal. Prefer cross-device workflows with a clear household benefit. Research complementary integrations, explain their setup and behavior when a dependency is offline, and link related issues before opening a new one. The subsystem freezes above also apply to these proposals' implementation.
