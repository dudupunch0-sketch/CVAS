# Output Backups

This directory stores previous checked-in generated sample outputs when the
current `docs/test_examples_output_*` files are refreshed.

Backups are intentionally separated from the live output files:

- live examples stay at `docs/test_examples_output_fast.*` and
  `docs/test_examples_output_full.*`
- older generated examples live in dated subdirectories here

Do not edit backup files by hand. If a backup is no longer useful for review or
history, remove the whole dated backup folder in a dedicated cleanup PR.
