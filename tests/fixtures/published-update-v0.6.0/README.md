# Published update fixture

These files are the public v0.6.0 update manifest and its detached signature.
They are retained as immutable regression fixtures so bootstrap trust tests do
not depend on generated files in the ignored `dist` directory.

The published manifest was signed without a trailing line ending. The test
loader removes the single line ending added by source control before verifying
the detached signature.
