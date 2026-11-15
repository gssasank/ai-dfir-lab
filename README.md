# CYBERSEC 510 AI-assisted DFIR lab

This repository holds the setup script and lab tools for the CYBERSEC 510 AI-assisted digital forensics and incident response (DFIR) lab.
Follow this page before class to turn a SANS SIFT Workstation virtual machine (VM) into a ready lab machine.

## Links and help

- Course site on Canvas: https://canvas.duke.edu/courses/82519
- Technical problems with the setup: message Sasank at sg765@duke.edu

## Before you start

- An Intel or AMD computer. The VM does not run on Apple Silicon Macs or Windows on ARM. If that is your computer, request a virtual machine from Duke OIT at https://vcm.duke.edu and set up there, or pair up with a classmate who can run the VM.
- About 50 GB of free disk space and at least 8 GB of memory.
- VMware: Workstation Pro on Windows or Linux, Fusion on an Intel Mac. It is free but needs a Broadcom account to download: https://knowledge.broadcom.com/external/article/368667/
  VirtualBox is an option but untested.
- A Duke AI Gateway key: go to https://dashboard.ai.duke.edu, open the AI Gateway tab, click Create Api Key, and leave the fund code blank.
- The Duke VPN if you are off campus: https://oit.duke.edu/service/vpn/ (server `vpn.duke.edu`, group `-Default-`).

## Setup steps

1. Download the SIFT Workstation OVA from https://www.sans.org/tools/sift-workstation (no SANS account needed). The file is `sift-2026-04-22.ova`, 9.46 GB, SHA-256 `69960210f92f2329ea69c648c971c23d6bd42568de66586ee4b8273797b9c860`.
2. In VMware, choose File > Open, select the OVA, and keep the defaults.
3. Start the VM and log in: user `sansforensics`, password `forensics`.
4. Inside the VM, open Chromium (Firefox does not work in this VM) and log in to Canvas.
5. Download the disk image `DC01-E01.zip` (4.84 GB) from https://canvas.duke.edu/courses/82519/files/5803259 into the Downloads folder. This is the only file you download: the setup command fetches everything else.
   Download the disk image inside the VM, not on your own computer: it is evidence from a real intrusion and contains malware.
6. Open a terminal in the VM and run the setup command below. Wait until it prints `SETUP COMPLETE`. When it asks whether to delete `DC01-E01.zip` to free space, you can answer `y`: the lab uses the unpacked copy.
7. Double-click "Set up Duke AI access" on the VM desktop and paste your key.
8. Double-click "Check my setup". When it says `READY`, your setup is done. Do this before class.
9. On lab day: if you are off campus, connect the Duke VPN on your own computer. Then start the VM and double-click "Start DFIR Lab".

## The setup command

<!-- BEGIN STUDENT COMMAND -->
```
curl -fsSLO https://raw.githubusercontent.com/gssasank/ai-dfir-lab/v1.1.0/dist/bootstrap.sh && echo "0df711e126ec48503df430c0dca81861661981c4d9390d870367ceb884ee9e3f  bootstrap.sh" | sha256sum -c && bash bootstrap.sh
```
<!-- END STUDENT COMMAND -->

The command downloads the setup script, checks its SHA-256, and runs it. The script downloads Claude Code, Protocol SIFT and the log kit itself, and unpacks the disk image from your Downloads folder; it needs internet access, usually takes a few minutes, and is safe to run again.
If copy and paste from your own computer into the VM does not work, open this page in Chromium inside the VM and copy the command there.

## Optional: use your own Claude subscription instead of a Duke key

If you already pay for Claude (Pro, Max or higher), the lab's AI assistant can use your subscription instead of a Duke AI Gateway key. It needs no Duke key and no VPN, and long conversations are not slowed by the Duke key's per-minute limit. It uses your own subscription's allowance. If you have no subscription, skip this section: the Duke key is the normal way.

1. Do setup steps 1 to 6 above.
2. Double-click "Use my Claude subscription" on the VM desktop. Chromium opens a Claude sign-in page: log in with the account that has your subscription and click Authorize. The window then says that Claude Code is signed in.
3. Double-click "Check my setup" (checks 6 to 9 show N/A on this route), then "Start DFIR Lab" as usual.

To go back to the Duke key, run `dfir-lab use duke` in a terminal; `dfir-lab use --status` shows which one is in use. Both your key and your sign-in stay on the VM. To sign the VM out of your Claude account, run `claude auth logout`.

## If something goes wrong

- Run the setup command again. It skips the steps that are already done and resumes.
- Run `dfir-lab report-problem` in a terminal and send the file it creates to Sasank at sg765@duke.edu. The file does not contain your key.
- If the check says it cannot reach the gateway, connect the Duke VPN on your own computer and run the check again.
- Never send your key to anyone. Nobody running this lab will ever ask for it.

## Keep your key safe

Your key is stored only inside the VM. It is not part of the problem report or of anything you submit. The same holds for your Claude sign-in if you use the subscription option.

## Credits

- The case is "The Stolen Szechuan Sauce" from DFIR Madness (https://dfirmadness.com/the-stolen-szechuan-sauce/), used for teaching with credit to its author.
- SIFT Workstation and Protocol SIFT are from SANS (https://github.com/teamdfir/protocol-sift). This lab installs Protocol SIFT from its own repository.
