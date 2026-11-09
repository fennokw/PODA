# PODA Desktop security

The packaged app opens an authenticated loopback backend on an OS-assigned port. The renderer uses context isolation and runs in a sandbox. The process accepts the desktop shell's per-launch token and validates sessions and origins. Closing the parent pipe stops the backend. Normal startup requires SQLCipher and uses the platform's OS credential store.

Report a suspected vulnerability privately using this repository's Security tab. Report shared runtime issues through [PODA Web](https://github.com/fennokw/PODA-Web/security).
