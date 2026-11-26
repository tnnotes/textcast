# TextCast in your browser

The easiest way to use TextCast on a computer: a web page that talks to the
LoRa USB stick directly. Nothing to install.

**Open: https://tnnotes.github.io/textcast/**

## What you need

- A computer with **Windows, macOS, Linux or ChromeOS**.
- **Chrome**, **Edge** or **Firefox 151 or newer**. (Safari and iPhones/iPads
  can't talk to USB radios. On Android phones, use the
  [Android app](ANDROID.md).)
- A LoRa USB stick: the Dragino **LA66 USB** with the P2P firmware is tested
  (see [HARDWARE.md](HARDWARE.md)).

## First start

1. Plug the stick into the computer.
2. Open the link above.
3. Type **your name** (1–16 characters, no spaces).
   On a shared computer, tick **Protect with a passphrase**.
4. Press **Start** and pick the stick from the list the browser shows.
   The first time, TextCast sets the stick up (about 8 seconds).
5. Type a message and press Enter. Everyone in range with the same radio
   settings sees it.

Next time you only press **Start**: the browser remembers you and your stick.
If you unplug the stick, TextCast notices; plug it back in and it reconnects
by itself.

## Radio region

TextCast picks the region from the computer's time zone (Vietnam and
Indonesia: AS923-2, Europe: EU868, USA: US915, ...). **Everyone must use the
same settings.** Check or change them under **more options** on the start
screen, or with 📶 in the app (frequency, spreading factor, power, ...). The
preview shows how many bytes fit in one message.

Region presets are starting points, not legal advice: check your local rules.

## Using it

- **Channels** along the top: *All*, *Public* (everyone in range), your
  groups 🔒, your streams 📢 and streams you follow 📻.
- **"to"** under the messages chooses where your message goes. The counter
  shows the bytes used (100 per message by default; Vietnamese letters with
  marks use 2–3 bytes).
- Each message shows the time, the signal strength (dBm and SNR) and, for
  yours, *sent ✓* or *not sent ✗*.
- 👥 **Groups, streams and contacts:**
  - your identity string (`lid1:…`) to give to people, and your fingerprint
    to compare in person;
  - **New group**: type a name, then send the `ltg2:…` string to the members
    (in person, by chat, by email);
  - **Add pasted string**: join a group (`ltg2:`), follow a stream (`lts3:`) or
    save a contact (`lid1:`).
- 🔒 **Lock** (with a passphrase): hides private messages until you unlock;
  private messages that arrive meanwhile open when you unlock.
- ⋯ **More:** export your identity, notifications and sound, show your tag
  in public (`Lan#K7QX`), clear history, log out.

## Works offline, installs like an app

After the first visit, TextCast works **with no internet at all**: open the
same link and it loads from the copy saved in the browser. When you are online
it updates itself quietly; the new version is used the next time you open it.

In **Chrome** or **Edge**, press **Install app** on the start screen (or the
install icon in the address bar). TextCast gets its own icon and window, like a
normal program. In Firefox, bookmark the link; it works offline the same way.

**No internet even once?** Download [`web/index.html`](../web/index.html)
(the download button on that page) on any computer, copy it with a USB drive,
and open it in the browser. It is the whole app in one file and works fully
offline. It can't be installed as an app, and it keeps its own identity
(move it with a `.ltid` file).

## Keep your identity safe

Your identity and messages are stored **in this browser only**.

- Press **Back up now** (or ⋯ → Export identity) and keep the `.ltid` file
  somewhere safe. It is encrypted with the passphrase you choose for it.
- The same file opens in the Android app and the desktop app, and in another
  browser (*more options* → Import a .ltid file). **Move, don't clone:** stop
  using the identity on the old device.
- Clearing *browsing history* or *cached images and files* doesn't touch
  TextCast. Clearing **cookies and site data** deletes the identity and the
  messages.
- A forgotten passphrase cannot be recovered.

## Troubleshooting

| Problem | Try |
|---|---|
| "This browser can't talk to a USB radio" | Use Chrome, Edge or Firefox 151+ on a computer |
| The stick isn't in the list | Unplug and replug it. On Windows, install the driver for the stick's USB chip |
| "Could not open the radio" | Another program or browser tab is using the stick: close it |
| Nobody hears me | Same region and settings on every device (📶); check *sent ✓* |
| Messages don't arrive | Antennas attached, same settings; try a few metres apart first |
| "Too long for one radio message" | Shorten it; Vietnamese marks use 2–3 bytes per letter |
| Lost the identity after clearing browser data | Import your `.ltid` backup |
