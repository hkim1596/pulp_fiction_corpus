# Backup web server on the Mac Studio

The main server (rtx6000) will be down for about a week. During that
time the website keeps running from a Mac Studio. The Studio has no
GPU, so no pipeline runs there — it only serves the site: browsing,
accounts, and annotation all work, because the site needs nothing but
Python and the copied `data/` folder. Annotations made during the week
are copied back to the main server afterward.

The one rule for the whole exercise: ONLY ONE SERVER IS LIVE AT A TIME.
Annotation events are per-issue log files; if people annotated on both
servers at once, the files would have to be merged by hand. Switch the
public address to the Studio when the main server goes down, switch it
back when the main server returns, and treat the Studio's data as the
master copy in between.

## Before the outage (main server still up)

1. On the Mac Studio, install the command line tools if needed
   (`xcode-select --install`) and make sure it can reach the main
   server with ssh (same account you use from the MacBook; if the
   Studio has no ssh key yet, `ssh-keygen` and add the public key to
   the server's `~/.ssh/authorized_keys`).

2. Copy this repository folder onto the Studio (any way you like —
   AirDrop, Dropbox, or git clone), open Terminal in it, and run:

       bash scripts/backup_server_setup.sh

   It asks for the server's ssh address, copies the whole project
   including `data/` (several GB — the page scans are the bulk), copies
   the three account/secret files into `~/pulp_backup/secrets/`, and
   checks the site code compiles. Rerunning it later refreshes the
   copy: run it again on the last day before the outage so the backup
   carries the newest annotations.

3. Install cloudflared on the Studio: `brew install cloudflared`
   (or download the mac build from Cloudflare). Then, one time:

       cloudflared tunnel login
       cloudflared tunnel create pulp-backup

   The login step opens a browser — choose the digihumeng.org zone.
   Then write the config file at `~/.cloudflared/config.yml`:

       tunnel: pulp-backup
       credentials-file: /Users/YOURUSER/.cloudflared/<the-id-shown-by-create>.json
       ingress:
         - hostname: pulp.digihumeng.org
           service: http://127.0.0.1:8092
         - service: http_status:404

   (Replace the credentials path with the actual file that
   `tunnel create` printed — it shows the exact path.)

## The day the main server goes down

On the Studio, two Terminal windows:

    Window 1:  bash ~/pulp_backup/pulp_fiction_corpus/scripts/serve_backup.sh
    Window 2:  cloudflared tunnel run pulp-backup

Then point the public name at the backup tunnel (this is the actual
switch; it takes effect within a minute or two):

    cloudflared tunnel route dns --overwrite-dns pulp-backup pulp.digihumeng.org

Check from any browser: https://pulp.digihumeng.org should show the
site with the version number in the footer. causal.digihumeng.org will
be down for the week — that is expected; it lives on the main server.

Keep both Terminal windows open for the week (the serve script restarts
the site by itself if it crashes; System Settings → prevent the Studio
from sleeping).

## The day the main server comes back

1. Stop new annotation for half an hour (tell the team).
2. Copy the week's work from the Studio back to the main server —
   run ON THE STUDIO (same ssh address as during setup):

       rsync -a ~/pulp_backup/pulp_fiction_corpus/data/annotations/ SRV:~/shared/khj/pulp_fiction_corpus/data/annotations/
       rsync -a ~/pulp_backup/pulp_fiction_corpus/data/feedback.jsonl SRV:~/shared/khj/pulp_fiction_corpus/data/feedback.jsonl
       rsync -a ~/pulp_backup/secrets/.pulp_users.json SRV:~/shared/khj/.pulp_users.json

   (Replace SRV with the server address. This is safe because the main
   server was off all week — nothing there changed. The annotations
   folder only ever grows; the users file is copied whole so accounts
   approved during the week survive.)
3. On the main server, make sure the site and tunnel sessions are up
   (handbook, "site process" and "tunnel" sections).
4. Point the public name back at the main tunnel — from the Studio or
   any machine logged in to the Cloudflare account:

       cloudflared tunnel route dns --overwrite-dns cihd-site pulp.digihumeng.org

5. On the Studio, Ctrl-C both windows. Done. Keep the Studio copy — it
   is now a cold backup; refresh it now and then with
   `bash scripts/backup_server_setup.sh`.

## If something goes wrong

Site shows error 1033 after the switch: the tunnel window on the
Studio is not running, or DNS has not switched yet — wait two minutes,
then rerun the route dns command. Site asks for a passcode nobody
knows: the secrets copy failed — rerun the setup script and check the
three "ok" lines. Accounts missing on the backup: same cause, same
fix. Site up but page previews slow the first time: the Studio is
building previews on demand; run
`python3 scripts/make_thumbs.py` in the project folder once
(or just let it warm up as people browse).
