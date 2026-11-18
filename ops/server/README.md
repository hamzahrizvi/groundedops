# Office access to the Linux server (10.10.15.170)

The office logs in as `hamzahrizvi` with this PC's SSH key. Root is needed only
for `groundedops-office`, and the sudoers rule allows exactly its three commands
with no password: `status`, `reindex` and `export-index`. See the script's header.

## Install (once, by the CEO; asks for the sudo password)

From the repo on Windows:

    scp ops/server/groundedops-office hamzahrizvi@10.10.15.170:

On the server:

    sudo install -o root -g root -m 755 ~/groundedops-office /usr/local/sbin/groundedops-office
    echo 'hamzahrizvi ALL=(root) NOPASSWD: /usr/local/sbin/groundedops-office status, /usr/local/sbin/groundedops-office reindex, /usr/local/sbin/groundedops-office export-index' | sudo tee /etc/sudoers.d/groundedops-office
    sudo chmod 440 /etc/sudoers.d/groundedops-office
    sudo visudo -cf /etc/sudoers.d/groundedops-office

The script must stay root-owned and not writable by `hamzahrizvi`; if that login
could edit it, the rule would be root for anyone holding the key. To update it,
repeat the `scp` and `install` lines.

To remove the access: `sudo rm /etc/sudoers.d/groundedops-office`.

## Use (the office, or anyone with the key)

    ssh hamzahrizvi@10.10.15.170 'sudo -n /usr/local/sbin/groundedops-office status'

`reindex` runs 20-60 minutes with the service down. Start it detached, then wait
for its last line:

    ssh hamzahrizvi@10.10.15.170 'nohup sudo -n /usr/local/sbin/groundedops-office reindex > ~/reindex.out 2>&1 &'
    ssh hamzahrizvi@10.10.15.170 'tail -3 ~/reindex.out'

The last line is one of:
- `REINDEXED`: done, and the backup is kept.
- `RESTORED`: a document lost passages, so the old index is back, with the list.
- `REFUSED`: nothing was changed.
- `BUSY`: another job is running.

`export-index` stops the service for the length of one tar (about a minute). It
prints `EXPORTED /var/tmp/groundedops-index.XXXX/chroma_db.tar.gz`, which `scp`
can then fetch. Copy that index into Windows only if:
- `status` on the server shows the same document checksums as Windows, and
- it shows the same chromadb and sentence-transformers versions as Windows.

Stop the Windows backend while you swap it in.

Not covered, on purpose: a release (`sudo bash ~/setup.sh`). That script lives
in a folder this login can write to, so allowing it would mean full root. A
release stays a CEO job. `reindex` refuses to run until the server's code
includes the fix it needs.
