INCIDENT TICKET  IR-2020-0919                      Priority: HIGH

Reported by: Network operations
Affected host: CITADEL-DC01 (domain controller, 10.42.85.10)

What we know
  Our network sensor flagged about 29,000 Remote Desktop connection attempts
  from 194.61.24.102 to the domain controller, starting 2020-09-19 02:19 UTC.
  The sensor's clock is known to be correct. Nobody has checked the server's.

What you have
  - A forensic image of the domain controller's disk (E01), mounted read-only
    at /mnt/dc01.
  - A log kit in /cases/szechuan: event logs from the domain controller (DC01)
    and from one desktop (DESKTOP-SDN1RPT), the same logs as CSV, a little
    registry output, and a summary from the network sensor.
  - An AI assistant that can run the forensic tools for you.

What we need to know
  1. Is the evidence sound?
  2. Did they get in?
  3. What did they leave behind, and how did it get there?
  4. What happened to the files on the file share (C:\FileShare)?
  5. Can we trust the server's clock?

The rule for today
  Anything the AI tells you is a hypothesis until you have opened the evidence
  it cites and seen it for yourself. You sign the findings. The AI does not.

How to work
  - Ask the AI one question at a time. Ask it to show the exact record, file,
    or command output behind every claim.
  - Check each answer:   dfir-lab checkpoint <number> <answer>
  - Stuck?               dfir-lab hint <number>
  - See your progress:   dfir-lab progress
  - Type /clear in the AI assistant before you start each new checkpoint.
    Short conversations are fast; long ones get slow.
  - Write your answers and evidence in the worksheet as you go.
