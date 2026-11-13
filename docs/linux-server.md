# Running GroundedOps on a Linux server

A stopgap until IT sets up AWS: turn a spare PC into a Linux server that runs
GroundedOps on the office network. Everything here is chosen so the same
steps work on an AWS machine later. The last section lists the few things
that change.

You do steps 1–4 by hand, once. Step 5 is one script,
[`deploy/linux/setup.sh`](../deploy/linux/setup.sh), which you can run again
at any time to update the app.

---

## 1. What you need

**Linux version: Ubuntu Server 24.04 LTS.** It gets security updates until
2029, has Python 3.12 built in (GroundedOps needs 3.11 or newer), and is the
same system as Amazon's standard Ubuntu image. Nothing in the repo asks for
a different one.

**The PC.** GroundedOps runs its own search and checking models on the
processor (no graphics card needed). The AI writing the answers is a cloud
service reached with an API key, so it does not need a big machine.

| | Minimum | Better |
|---|---|---|
| Processor | 64-bit Intel or AMD, 4 cores, made after about 2015 | 8 cores |
| Memory | 16 GB (8 GB starts, but uploads of big manuals get slow) | 16 GB or more |
| Disk | 128 GB SSD | 256 GB SSD |
| Network | Wired Ethernet | Wired Ethernet |

The disk holds about 6 GB of Python libraries, 2 GB of models, the manuals,
and the search index. **The whole disk is wiped** when Ubuntu installs.

You also need a **USB stick of 8 GB or more** (it will be wiped too), and a
screen and keyboard for the PC during the install. After that it runs
without them.

---

## 2. Make the install USB (on your Windows PC)

1. Download the **Ubuntu Server 24.04 LTS** ISO file from
   <https://ubuntu.com/download/server> (about 3 GB).
2. Optional but wise: check the download isn't damaged. In PowerShell:
   ```powershell
   Get-FileHash $HOME\Downloads\ubuntu-24.04*-live-server-amd64.iso
   ```
   Compare the long number with the `SHA256SUMS` file linked on the same
   download page.
3. Download **Rufus** from <https://rufus.ie> (the portable version needs no
   install) and open it with the USB stick plugged in.
4. Settings:
   - **Device:** your USB stick. Check the size, so you don't wipe the wrong drive.
   - **Boot selection:** click **SELECT** and pick the ISO.
   - **Partition scheme:** GPT
   - **Target system:** UEFI (non CSM)
   - **File system:** FAT32 (the default). Leave the other options as they are.
5. Click **START**. If asked, choose **Write in ISO Image mode (Recommended)**.
   Click OK on the "all data will be destroyed" warning, and wait for READY.

Rather not use Rufus? **balenaEtcher** (<https://etcher.balena.io>) is
simpler: choose the file, choose the stick, then click Flash. It has no settings.

---

## 3. Install Ubuntu on the spare PC

1. Plug in the USB stick and Ethernet, then switch on. Open the boot menu
   (usually **F12**, **F11** or **Esc** at the logo) and pick the USB stick.
2. While you are in the BIOS/UEFI setup, find **"Restore on AC power loss"**
   (or similar) and set it to **Power On**. Then the server comes back by
   itself after a power cut.
3. In the installer, take the defaults except:

| Screen | Choose |
|---|---|
| Type of install | **Ubuntu Server** (not minimized) |
| Network | DHCP is fine. **Write down the MAC address and IP shown.** See "Fixed address" below. |
| Proxy | Blank, unless the office uses one |
| Storage | **Use an entire disk**, with "Set up this disk as an LVM group" ticked. **Leave encryption off**: an encrypted disk needs a passphrase typed at every boot, so the server can't restart unattended. |
| Storage summary | Ubuntu only uses about 100 GB at first. Select `ubuntu-lv`, choose **Edit**, and set the size to the maximum. |
| Profile | Your name, server name `groundedops`, a username such as `goadmin`, a strong password (you'll need it for `sudo`) |
| Ubuntu Pro | Skip |
| SSH | **Tick "Install OpenSSH server".** Leave "Allow password authentication" on for now; step 4 turns it off. |
| Featured snaps | None |

4. When it says **Reboot Now**, remove the USB stick and press Enter.

**Fixed address.** The server needs an address that doesn't change. The
best way is a **DHCP reservation**: ask IT (or use the router's admin page)
to always give the MAC address you wrote down the same IP. The other way is
to set it in the installer's Network screen: Edit IPv4, then Manual, then
fill in the subnet (e.g. `192.168.1.0/24`), address, gateway and DNS. Only do
this with an address IT has set aside for you, or two machines could end up
with the same address.

---

## 4. First boot: lock it down

From now on, work from your Windows PC over SSH. Replace `192.168.1.50` with
the server's address.

**SSH key instead of a password.** In PowerShell on Windows:

```powershell
ssh-keygen -t ed25519            # press Enter at each question, unless you want a passphrase
type $HOME\.ssh\id_ed25519.pub | ssh goadmin@192.168.1.50 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
ssh goadmin@192.168.1.50         # should log in without a password now
```

(Skip `ssh-keygen` if you already have a key at `$HOME\.ssh\id_ed25519.pub`.)

Everything below runs **on the server**, in that SSH session.

**Updates first:**

```bash
sudo apt update && sudo apt full-upgrade -y
```

**Turn off password logins.** Only do this after the key login above
worked:

```bash
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\n' | sudo tee /etc/ssh/sshd_config.d/10-keys-only.conf
sudo systemctl reload ssh
```

Keep this window open and check in a **new** PowerShell window that
`ssh goadmin@192.168.1.50` still works. If it doesn't, the open window is
your way back in: delete that file and reload again.

**Firewall (ufw).** Allow SSH, and GroundedOps only from the office
network.

**Find the right range first, and don't assume it's the one your own PC is
on.** Office networks often translate addresses between subnets, so the
server may see your PC as something else entirely. Ask the server what it
sees:

```bash
echo "$SSH_CLIENT"      # the address the server sees you arriving from
ip -4 route show scope link proto kernel | awk '{print $1}'   # its own range
```

On the first install here, the server's own range was `10.10.8.0/21`, but a
PC on the office Wi-Fi (`192.168.1.42`) arrived as `10.242.2.216`. Allowing
the Wi-Fi range did nothing; the translated range had to be allowed instead.
Use both:

```bash
sudo ufw allow OpenSSH
sudo ufw allow from 10.10.8.0/21 to any port 8000 proto tcp
sudo ufw allow from 10.242.0.0/16 to any port 8000 proto tcp
sudo ufw enable
sudo ufw status
```

Check it from your own PC afterwards — `curl http://<server>:8000/health`
should answer `200`, not hang.

**Automatic security updates.** These are installed and on by default.
Check that, and let the server restart itself at 3am when an update needs
it (GroundedOps starts again on its own):

```bash
sudo dpkg-reconfigure -plow unattended-upgrades     # answer Yes
printf 'Unattended-Upgrade::Automatic-Reboot "true";\nUnattended-Upgrade::Automatic-Reboot-Time "03:00";\n' | sudo tee /etc/apt/apt.conf.d/52groundedops-reboot
```

---

## 5. Install GroundedOps

The script installs the version of `experimental/v17.3` that is **on
GitHub**. Local commits you haven't pushed won't be there, so push first if
you want them.

**Copy the script over.** In PowerShell, from the repo folder:

```powershell
scp deploy\linux\setup.sh goadmin@192.168.1.50:
```

Then choose **A** to move your current data across, or **B** to start
empty.

### A. Bring over what you have now (recommended)

This uses the app's own backup: documents, search index, FAQ, catalogue,
widget settings, accounts and enquiries in one encrypted file.

1. On the server, install everything but don't start it yet:
   ```bash
   sudo bash setup.sh
   ```
   The first run takes 20–40 minutes, mostly downloads. It ends with "Not
   started: no Owner account yet". That is expected.
2. On Windows, make the backup. It asks for a passphrase, which you'll need
   in step 3:
   ```powershell
   cd C:\Users\hrizvi\Downloads\Git\groundedops\src
   ..\.venv\Scripts\python manage_backup.py export --out $HOME\go.gobk
   scp $HOME\go.gobk goadmin@192.168.1.50:
   ```
3. On the server, restore it with accounts, then start the app.

   `/srv/groundedops` belongs to the service account, so **your own user
   can't `cd` into it** — "Permission denied" looks like the folder is
   missing. Every command below therefore runs as `groundedops`:
   ```bash
   sudo install -o groundedops -g groundedops -m 600 ~/go.gobk /srv/groundedops/ && rm ~/go.gobk
   sudo -u groundedops -H bash -c 'cd /srv/groundedops/app/src && ../.venv/bin/python manage_backup.py restore /srv/groundedops/go.gobk --accounts'
   sudo systemctl start groundedops
   sudo rm /srv/groundedops/go.gobk     # it holds password hashes; delete your Windows copy too
   ```
   The restore asks for the passphrase, and nothing appears as you type.
   **If `BACKUP_PASSPHRASE` is set in `src/.env`** (on both machines — copy
   the line across) neither the export nor the restore prompts at all, and
   `--yes` skips the confirmation too, so the whole move can run unattended.

### B. Start empty

```bash
sudo bash setup.sh you@innovative-technology.com
```

It asks you to choose the Owner account's password. Then add manuals
from the console's Documents page.

### Either way, then

- Open `http://192.168.1.50:8000/admin` **in a browser on your own PC** and
  sign in. You never need to touch the server's own screen and keyboard
  again after step 3.
- **API keys page: paste the provider key.** Keys saved in the Windows
  console are locked to that PC and don't come across with the backup.
- Check it can answer. This must say `"ready": true` (allow about a
  minute after start):
  ```bash
  curl -s 'http://127.0.0.1:8000/health?deep=1'
  ```
- If the WordPress site will talk to this server, copy `WIDGET_TOKEN_SECRET`
  from the Windows `src\.env` into the server's
  `/srv/groundedops/app/src/.env` (`sudo nano` it), then
  `sudo systemctl restart groundedops`. The script made a new random one.

### What the script set up

- A `groundedops` service account. Everything is under `/srv/groundedops`:
  the code in `app/` (with its `.venv`, `src/.env`, index and `documents/`),
  and the models in `hf-cache/`, at the versions pinned in `src/model_pins.py`.
- `src/.env` from `.env.example`, with fresh `SESSION_SECRET` and
  `WIDGET_TOKEN_SECRET`, no provider keys, and `TRUST_PROXY=0` (nothing sits
  in front of this server yet).
- A `groundedops` service that starts at boot, runs the models offline,
  restarts if the app crashes, and a once-a-minute check that restarts it
  if `/health` stops answering.

---

## 6. Day to day

| To | Run on the server |
|---|---|
| See if it's running | `systemctl status groundedops` |
| Watch the log | `journalctl -u groundedops -f` |
| Check it can answer | `curl -s 'http://127.0.0.1:8000/health?deep=1'` |
| Restart | `sudo systemctl restart groundedops` |
| Update to the latest on GitHub | `sudo bash /srv/groundedops/app/deploy/linux/setup.sh` (once this script is merged into the branch; until then, `sudo bash ~/setup.sh`) |

### Backups

A `groundedops-backup` timer runs every night at 02:30 and writes an
encrypted archive to `/srv/groundedops/backups/go-<date>.gobk`, keeping 14
days. It needs `BACKUP_PASSPHRASE` set in `src/.env`; without it the run
fails rather than writing a readable archive.

| To | Run on the server |
|---|---|
| Back up now | `sudo systemctl start groundedops-backup` |
| See the last run | `journalctl -u groundedops-backup -n 20` |
| List archives | `sudo ls -lh /srv/groundedops/backups/` |
| Check one really opens | `sudo -u groundedops -H bash -c 'cd /srv/groundedops/app/src && ../.venv/bin/python manage_backup.py inspect /srv/groundedops/backups/<file>'` |
| Restore one | as the restore command in 5A, pointing at the archive |

**These sit on the same machine as the thing they protect.** That covers a
bad restore, a wrong deletion or a corrupt index; it does not cover losing
the machine. Copy them somewhere else — a share, another box, object
storage — before this is relied on. On AWS, EBS snapshots cover the volume
as well.

Still not set up: public access through a Cloudflare named tunnel. The
steps are in `TUNNEL_SETUP.md`, written for Windows; on Linux use
`cloudflared service install`.

### Who can reach it

The firewall decides who can open port 8000, and that is the only thing
standing in front of the chatbot. **Questions are not authenticated**: any
address allowed through can `POST /query` and spend provider credit, with
only the per-IP quota (200/day by default) in the way. The admin console is
separate and does require a sign-in — its data endpoints answer 401/404
without one.

So the ufw ranges are the access-control decision, not a formality. Keep
them as narrow as the people testing actually need.

---

## 7. Moving to AWS later

`setup.sh` runs on AWS unchanged. What changes:

| Here (spare PC) | On AWS |
|---|---|
| The PC | An EC2 instance from Canonical's **Ubuntu Server 24.04 LTS** image (x86-64). Start with **m7i.xlarge** (4 vCPU, 16 GiB). Use **c7i.2xlarge** (8 vCPU) if uploading big manuals is too slow. No GPU. |
| `ufw` firewall | A **security group**: SSH (22) from the office IP only; port 8000 only from the load balancer's security group, or nothing at all if a Cloudflare tunnel is used. Leave `ufw` off on the instance. |
| The PC's own disk | An **EBS gp3 volume** (100 GB+) mounted at `/srv/groundedops` **before** running `setup.sh`, so the index, documents and model cache live on it. Add it to `/etc/fstab` by UUID with `nofail`. Back it up with EBS snapshots (Data Lifecycle Manager), as well as `manage_backup.py`. |
| DHCP reservation | An **Elastic IP**, or a DNS name pointing at the load balancer |
| Steps 2–4 (USB, installer, SSH key) | Not needed. You choose an AWS key pair at launch, and Ubuntu's AWS image already allows key logins only for the `ubuntu` user. Do the unattended-upgrades step only. |
| `TRUST_PROXY=0` | `TRUST_PROXY=1` and `WIDGET_ALLOWED_ORIGINS=https://<your site>` once a load balancer or tunnel is in front, as `STAGING.md` describes |
| Moving the data | The same as 5A: `manage_backup.py export` here, `restore --accounts` there, then paste the provider key again (stored keys are locked to the machine). |
