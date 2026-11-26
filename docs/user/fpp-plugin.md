# Projectors on the Pi: the FacadeKit FPP plugin

The FacadeKit plugin for Falcon Player (FPP) switches your projectors on and off from the Pi,
by itself or from your FPP playlists and schedule, and lists the shows you sent from FacadeKit.
It is optional: without it you can still send and play shows.

You need FPP 10.4 or newer and projectors that support **PJLink** (most network projectors
do, including Panasonic laser models). The projectors must be reachable from the Pi, for
example on a network cable from the Pi's Ethernet port.

## 1. Install

Until the plugin is listed in FPP's Plugin Manager, install it from your computer:
see [docs/20 §6](../20-fpp-plugin.md#6-sandbox-pi-dev-loop). It takes four commands.

## 2. Add the projectors

1. In FPP, open **Status/Control > FacadeKit Projectors**.
2. Press **Add projector**. Type a name (for example *Left*), the projector's address (for
   example `192.168.50.10`) and port `4352`.
3. If PJLink authentication is on in the projector's network menu, type its PJLink password.
   Leave it empty otherwise. The password is stored on the Pi only and is never shown again.
4. Input: the PJLink code of the projector input the Pi is connected to, usually `31`
   (HDMI 1). Leave it empty to keep the projector's own choice.
5. Timings: leave the defaults for a laser projector (ready wait 5 s, cooling wait 30 s, off
   delay 0 s). They only matter while a projector itself says it is warming up or cooling
   down. For a lamp projector, set the ready wait to about 60 s and the cooling wait to 120 s.
6. Press **Save**, then **Test**. The status table should show the projector as *Off
   (standby)* or *On*, with its model.

## 3. Use it in your shows

- **All on / All off** on the plugin page switch every projector at once.
- In a playlist's **lead-in**, add the command **FacadeKit Projectors On** and tick **Wait
  until ready**: the show starts once the projectors are on.
- In the **lead-out** (or as a scheduled command later at night), add **FacadeKit Projectors
  Off**.
- Or let the plugin do it: under **Automatic power**, turn on *Switch projectors on when a
  show starts* and *off when a show stops*. Choose *Only shows FacadeKit published* so your
  other light shows do not turn the projectors on.
- **Fault guard**: watches the projectors while a show plays and shows a red *Fault* line if
  one reports an error or switches off. Tick *Stop the playlist* only if you would rather
  stop the show than play it with a projector missing.
- **Temperature warnings** are written to the plugin log once per projector (first warning,
  error, back to normal) and the status table shows each projector's temperature and the time
  of its last good reading. A warning alone never stops anything.
- **Thermal recovery (optional, off by default)**: after a projector reports a temperature
  *error* the plugin stops sending it power-on requests and lets it cool. After the minimum
  cooldown and a few good readings in a row it switches the projector on **once** per show
  (or just confirms it if it is already on), while the other projector keeps playing. If that
  fails, or the error returns, the status table says **Needs attention** and the log says why.
  Switching a projector off, stopping the show or turning the option off cancels it. It never
  restarts a playlist, and it does nothing if *Stop the playlist* is ticked.

## 4. Pair FacadeKit

1. On the plugin page, press **Create pairing token** and copy the token (it is shown once).
2. In FacadeKit, go to **Send to Falcon Player**, connect to the Pi, and paste the token in
   the **Projectors** card. Press **Pair**.
3. The card now shows each projector's state, any fault, and how many FacadeKit shows are on
   the Pi. When you send a show, tick **Switch the projectors on** to have them on before it
   starts. The show also appears in the plugin's **Shows published by FacadeKit** list, with
   **Play** and **Stop** buttons.

Lost the token, or want to lock FacadeKit out? Press **Revoke token** (or create a new one).

## 5. Troubleshooting

| You see | Do this |
|---|---|
| "the projector asks for a PJLink password" | Type the projector's PJLink password in its row and Save. |
| "the projector refused the password" | Check the password in the projector's network menu. |
| "refused the connection" | Turn PJLink (network control) on in the projector's menu. |
| "no route to …" or "did not answer in time" | Check the network cable and the projector's address. |
| "reports cooling down; not switched on" | Wait; the plugin never switches a cooling projector back on. |
| FacadeKit: "The FacadeKit plugin is not installed on this FPP" | Install it (§1), or ignore: sending works without it. |
| FacadeKit: "did not accept FacadeKit's pairing token" | Create a new token on the plugin page and pair again. |

The plugin's log is at the bottom of its page. Passwords and tokens are removed from it.

## 6. Remove

FPP's **Content Setup > Plugin Manager**, FacadeKit Projectors, **Uninstall**. This deletes
the projector list, the token and the plugin's log from the Pi.
