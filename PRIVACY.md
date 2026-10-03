# Privacy Policy — Calendar Agent

_Last updated: 2026-10-03_

Calendar Agent is a personal, open-source command-line tool that runs entirely on the user's own
computer. It is operated by its user for their own Google account. There is no hosted service,
no company behind it, and no server operated by the author receives any data.

## What the tool accesses

When you sign in, the tool requests these Google OAuth scopes:

- `openid`, `email` — to display which Google account is signed in.
- `https://www.googleapis.com/auth/calendar.events` — to read, create, update and delete events
  on your Google Calendar, only when you run a command that does so.

## How data is handled

- Calendar data is fetched from the Google Calendar API over HTTPS and shown in your terminal.
  It is not transmitted anywhere else.
- OAuth tokens are stored only on your computer, in a file readable by your user account alone
  (`~/.local/state/outlook-calendar-agent/`, mode 600).
- Drafts of proposed calendar changes and an audit log of confirmed changes are stored in the same
  local directory. They never leave your machine.
- The tool never stores or logs your Google password, access tokens or refresh tokens in any
  log file.
- No analytics, telemetry or third-party services are used.

## Changes to your calendar

The tool never modifies your calendar on its own. Every create, update or delete operation prints
a full preview and requires you to type `yes` in the terminal before anything is sent to Google.

## Revoking access

Run `uv run outlook-calendar logout` to revoke the token and delete the local token file, or
remove "Calendar Agent" at https://myaccount.google.com/permissions.

## Contact

Questions about this tool can be raised through the repository's issue tracker.
