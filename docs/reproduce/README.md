# Reproductions

One page per bug: the fault the proxy makes, the steps, and what a build with the bug and a fixed
build each do - so a review comment or a ticket can link the page instead of retelling it.

| Page | Bug | Rule | Script |
|---|---|---|---|
| [lost-trickle-after-ice-restart.md](lost-trickle-after-ice-restart.md) | WT-2071: a candidate request lost with the signaling after an ICE restart ends the call | `close-trickle-after-update` | [`android-ice-restart-on-wifi-off.sh`](../../scripts/android-ice-restart-on-wifi-off.sh) |

## Adding one

1. Find the frame the bug turns on in the proxy's log of a normal run (`--journal`): every frame is
   there with its direction, kind, name and transaction.
2. Make the fault with a generic rule if you can (`close-on` / `drop-on` with `from`, `name`,
   `after`, `times` - see [rules](../rules.md)); write a rule of your own only when matching frames is
   not enough, and load it with `--rules`.
3. Run it on a build with the bug and on a fixed one. Keep the proxy's lines and the app's lines that
   show the difference.
4. Write the page with these sections: **Bug** (what goes wrong, why it is rare without the proxy),
   **Fault** (the rule and what causes the rest), **Setup**, **Steps**, **Expected** (the proxy, the
   build with the bug, the fixed build), **Seen** (date, device, result of both builds), **Notes**.
   Put no host names, numbers or credentials of a real installation in it.
5. Add a script under `scripts/` when the steps are more than a couple of commands, and a row to the
   table above.
