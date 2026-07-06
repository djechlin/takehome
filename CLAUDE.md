Commit after every change.

To verify code compiles/formats, run `make build` (black + compileall over the
tree). Don't hand-roll inline `python3 -c "import ast..."` or one-off compile
scripts — use the Makefile target.

