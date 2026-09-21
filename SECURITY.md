# Security

pc-ai-assistant reads local system information (process names and file paths, public addresses your programs connect
to, disk and temperature history) into a database on your own machine. The tool itself sends nothing anywhere: its only
network use is the local Ollama server you run yourself. (Windows may contact certificate servers on its own
when it verifies a program's signature.)

**Reporting a problem.** If you find a security issue (for example a way to make it run a command from data it reads,
or to leak the local database), please do not open a public issue. Use GitHub's *Report a vulnerability* button on
the Security tab of this repository, or open an issue that says only that you have a security report, and the details
will be moved to a private channel.

**Things worth knowing**
- `data/` holds your history, including the list of public addresses your PC talked to. It is git-ignored; do not
  commit or share it, and delete it (or run `pulse prune --days 0`) if you want the history gone.
- The tool never claims a program is malicious or safe. Treat its findings as hints and verify them yourself
  (Task Manager -> Open file location, a Windows Defender scan).
