# Backup web server on the Mac Studio — setup and operation

The Mac Studio is a warm standby for the website. It keeps a fresh copy
of everything by pulling from the main server every night, and during a
main-server outage it becomes the live site at the same public address.
It has no GPU, so the pipeline never runs there — only the website,
which needs nothing but Python and the copied data. (A
project-independent version of this whole design, reusable for other
sites, is saved one level above this repository in the Dropbox share
as `warm-standby-recipe.md`.)

## Why there can be no conflict

Only three kinds of files ever change on a live site: annotation logs,
the feedback log, and the accounts file. Everything else (scans, texts,
articles) is produced by the pipeline on the main server only. The
design keeps the two machines from ever writing the same files in the
same period:

The Studio has a MODE, written in the file `~/pulp_backup/MODE`:
"standby" or "live". In standby (normal life), the Studio only PULLS —
a nightly one-way copy from the main server at 3:00 am — and nobody
uses its site, because the public address points at the main server.
When you make it live (one script), the nightly pull PAUSES ITSELF, so
a stale copy from the returning main server can never overwrite the
outage period's work. Handing back (one script) copies the outage
period's annotations, feedback, and accounts to the main server FIRST,
then returns the public address, then resumes the nightly pull. One
machine writes at a time; the sync always flows away from whichever
machine has been writing. That is the whole trick.

## One-time setup (about 30 minutes, most of it copying)

Do this while the main server is still up. Steps 1–3 happen ON THE MAC
STUDIO, in Terminal.

STEP 1 — tools. Install the command line tools and cloudflared:

    xcode-select --install
    brew install cloudflared

(If the Studio has no Homebrew: install it first from https://brew.sh,
or download the mac build of cloudflared from Cloudflare's site.)

STEP 2 — ssh access to the main server. The main server is
tailab@155.230.137.46, port 52345 (the laptop's "rtx6000" shortcut).
Give the Studio the same shortcut and a key of its own — run on the
Studio:

    mkdir -p ~/.ssh
    printf '%s\n' "Host rtx6000" "  HostName 155.230.137.46" "  User tailab" "  Port 52345" >> ~/.ssh/config
    chmod 600 ~/.ssh/config
    test -f ~/.ssh/id_ed25519 || ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
    ssh-copy-id rtx6000
    ssh rtx6000 hostname

ssh-copy-id asks for the tailab password one time; after that the last
line must print the server's name with no password prompt. The key is
made without a passphrase on purpose — the 3:00 am job must log in
with nobody at the keyboard.

STEP 3 — get the project and run setup. Copy the repository folder
onto the Studio any way you like (Dropbox is easiest), open Terminal
in it, and run:

    bash scripts/backup_server_setup.sh

It asks for the server's ssh address — answer exactly: rtx6000 — then: copies the whole project
(code and data, several GB), copies the five secret files into
`~/pulp_backup/secrets/`, records the address, sets MODE to standby,
checks the site code, and installs the 3:00 am nightly pull. From this
moment the Studio keeps itself current with no further attention.

STEP 4 — the public-address tunnel, one time:

    cloudflared tunnel login
    cloudflared tunnel create pulp-backup

The login opens a browser — choose the digihumeng.org zone. Then write
`~/.cloudflared/config.yml` (replace the credentials path with the one
`tunnel create` printed):

    tunnel: pulp-backup
    credentials-file: /Users/YOURUSER/.cloudflared/THE-ID.json
    ingress:
      - hostname: pulp.digihumeng.org
        service: http://127.0.0.1:8092
      - service: http_status:404

STEP 5 — keep the Studio awake: System Settings → Energy → prevent
automatic sleeping. Done. The Studio is now a warm standby.

## Every night (automatic)

At 3:00 am the Studio pulls the whole project and the secret files
from the main server. If the main server is off, the pull fails
harmlessly and notes it in the log. If the Studio is live, the pull
skips itself. Check the history any time:

    tail -20 ~/pulp_backup/sync.log

## The day the main server goes down

Before a PLANNED shutdown, run one manual pull first, so the backup
carries the very latest work (skip this if the server died on its own):

    bash ~/pulp_backup/pulp_fiction_corpus/scripts/backup_sync.sh

Then, on the Studio, open two Terminal windows and a third for the
switch:

    window 1:  bash ~/pulp_backup/pulp_fiction_corpus/scripts/serve_backup.sh
    window 2:  cloudflared tunnel run pulp-backup
    window 3:  bash ~/pulp_backup/pulp_fiction_corpus/scripts/go_live.sh

go_live.sh checks the site is running, sets MODE to live (pausing the
nightly pull), and points pulp.digihumeng.org at the Studio. Within a
minute or two the site is public again, with all accounts and
annotations as of the last pull. Annotators work normally all week;
their work lands in the Studio's copy.

(causal.digihumeng.org stays down during the outage — it lives on the
main server. That is expected.)

## The day the main server comes back

Tell the team to pause annotation for ten minutes, then on the Studio:

    bash ~/pulp_backup/pulp_fiction_corpus/scripts/go_standby.sh

It confirms the main server is reachable, copies the outage period's
annotations, feedback log, and accounts file back, returns the public
address to the main tunnel, sets MODE to standby, and resumes the
nightly pull. Close the two Terminal windows (Ctrl-C in each). On the
main server, confirm the `pulpsite` and `tunnel` tmux sessions are up
(handbook, "site process" and "tunnel" sections).

## Troubleshooting

Public site shows error 1033 after go_live: the tunnel window is not
running, or DNS has not flipped yet — wait two minutes; if it
persists, rerun go_live.sh. Site asks for a passcode nobody knows, or
accounts are missing: the secrets copy is stale — run backup_sync.sh
once and restart serve_backup.sh. Nightly log says PROJECT SYNC FAILED
while the main server is up: the Studio lost ssh access — repeat step
2's test. Page previews slow on first views during an outage: run
`python3 scripts/make_thumbs.py` inside `~/pulp_backup/pulp_fiction_corpus`
once, or let them build as people browse. Unplanned crash of the main
server: work done on the main server after the last 3:00 am pull (less
than a day's worth) is not on the Studio; it is still on the main
server's disk and comes back with it — nothing is lost, the two
periods just stitch together at hand-back.
