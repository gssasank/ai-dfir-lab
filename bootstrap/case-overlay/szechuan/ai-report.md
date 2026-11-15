PRELIMINARY FINDINGS (drafted by an AI assistant; not reviewed)

A. The Security log shows 96 failed logons to the Administrator account from
   the external address 194.61.24.102.

B. The earliest Remote Desktop logon from 194.61.24.102 on the domain
   controller was made with the Administrator account.

C. The implant on disk is C:\Windows\System32\coreupdate.exe.

D. The implant's modified time is earlier than its created time, which proves
   the attacker altered its timestamps to hide it.

E. The network sensor recorded the brute force at 02:21 and the domain
   controller logged the failed logons at 03:21, so there were two separate
   waves of attack an hour apart.

F. The disk shows that the archive of secret files was sent to the attacker's
   server before it was deleted.
