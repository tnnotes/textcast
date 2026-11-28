# TextCast for Android

The TextCast app for Android phones and tablets, **Android 5.0 and newer**.
It talks to the same LoRa modules over a USB OTG cable and speaks the same
protocol as the web and terminal apps: phones and computers chat with each
other, and identity files (with their TextCast number) move between them. It
looks like the web app: a chat list with a round **+** to add a person by
number, and one chat at a time.

## What you need

- A phone with **USB host (OTG)** support (most phones; some cheap ones lack it).
- A **USB OTG adapter or cable** (USB-C or micro-USB to USB-A) for the LA66 USB stick.
- A module with a known profile: Dragino LA66 (tested), RAK3172 or Wio-E5
  (untested), or your own profile (.json, same format as the desktop app).

## Build the APK (Android Studio)

1. Open Android Studio → **Open** → choose the `android` folder (this folder).
2. Wait for "Gradle sync" to finish. The first time, it downloads Gradle and
   the libraries (a few minutes). If it asks to install a missing SDK platform,
   accept.
3. Plug your phone in by USB with **USB debugging** on, choose it in the
   device list at the top and press **Run ▶**. The app installs and starts.

To get an `.apk` file to share: **Build → Build App Bundle(s) / APK(s) → Build
APK(s)**. Android Studio shows a link to the file
(`app/build/outputs/apk/debug/app-debug.apk`). Copy it to a phone and open it
(allow "install unknown apps" when asked).

From a terminal instead (needs the Android SDK, e.g. installed by Android Studio):

```
cd android
./gradlew assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
./gradlew testDebugUnitTest      # protocol compatibility tests
```

**If sync fails with a Java version error**: Settings → Build, Execution,
Deployment → Build Tools → Gradle → *Gradle JDK* → choose a JDK 17 or 21
(Android Studio's bundled "jbr-21" works).

**Release builds** (`./gradlew assembleRelease`) are signed with your own key
when `android/keystore.properties` exists, otherwise with the debug key. How to
create the key and publish the APK on GitHub: `docs/RELEASING.md` in the
GitHub version of the project.

## Using it

1. Plug the module into the phone (OTG), now or later. Android may ask whether
   to open TextCast: tick "always" so it doesn't ask again.
2. **Login**: choose your identity (or "+ New identity"), type the passphrase,
   choose the radio (or *Connect later*), **Start**, and allow USB access when
   asked.
3. **Settings** (on the login screen, or ⋮ → Radio settings in the chat): radio
   module, region preset and manual radio values, log size. Every node in a
   network must use the same radio values.
4. The **chat list** opens, like a phone messenger: Public first, then people
   and groups with their last message and unread count. Tap one to open it.
   ⋮ has *My number*, *Let people find me by number*, groups and channels, radio
   settings, the radio log, lock/unlock, change passphrase, back up, log out.

**Private messages to one person:** tap the round **+** and type their
**TextCast number** (*My number* in ⋮ shows yours); the phone asks nearby
devices who has it, then *Add and chat*. Both of you add each other.
(*More…* pastes an identity string or a group invite, or starts a group.)
Your message shows ✓, then **✓✓** when their device confirms it, or *not
delivered · tap to resend* after 30 s. The notification for a private message
says who wrote, not what.

**No module yet?** Choose *Connect later* on the login screen. Plug the
module in whenever you want to send: TextCast connects by itself (tap the bar
at the top if it doesn't). **Unplugged it?** Plug it back in: same thing,
no need to log out (Android may ask again for USB access).

Key strings (`ltg2:`, `lts3:`, `lid1:`) are shared with Android's share menu
or the clipboard; **+ → More… → Paste** pre-fills a string you copied.

The app keeps listening when the screen is off (a notification shows it is
running). Log out to stop it.

### Moving an identity between computer and phone

- Desktop → phone: `python3 identity.py export --nick Tuan` makes `Tuan.ltid`.
  Copy it to the phone, then on the login screen choose **Import from file…**.
- Phone → desktop: ⋮ → **Back up identity (.ltid)…**, copy the file to the
  computer, `python3 identity.py import Tuan.ltid`.

**Move, don't clone**: use an identity on one device at a time. Two devices
sending with the same identity can reuse encryption counters.

## How the code is organised

```
app/src/main/java/io/github/textcast/
  core/      the protocol, crypto and drivers - plain Kotlin, no Android code
    Protocol.kt   air profile, airtime, frames              (protocol.py)
    Crypto.kt     rolling tags, ChaCha20                    (crypto.py)
    Ed25519.kt    identity keys                             (ed25519.py)
    Vault.kt      scrypt, passphrase files, log encryption  (vault.py)
    Keyring.kt    identity, private chats, groups, contacts (keyring.py)
    Numbers.kt    TextCast numbers and number lookups       (numbers.py)
    Radio.kt      LA66 driver + generic AT driver           (la66.py, atradio.py)
    Hardware.kt   module profiles                           (hardware.py)
    Settings.kt   region presets, config file               (regions.py, config.py)
    Node.kt       channels, send/receive, history           (node.py)
    MessageLog.kt message log (JSON lines; private text encrypted)
  app/       Android screens (ChatsActivity = chat list, ChatActivity = one chat),
             USB serial (plug in / unplug at any time), background service
app/src/test/   CompatTest (byte-exact against Python), RadioTest (simulated modules)
compat/         make_vectors.py, check_kotlin.py, run_here.sh
```

USB serial uses [usb-serial-for-android](https://github.com/mik3y/usb-serial-for-android)
(CH340/CH9102, CP210x, FTDI, PL2303, CDC ACM).

## Compatibility tests

The Kotlin core must produce exactly the same bytes as the Python code. After
changing the protocol on either side:

```
python3 android/compat/make_vectors.py        # Python → vectors.json
cd android && ./gradlew testDebugUnitTest     # Kotlin reproduces them, writes compat/out/
cd .. && python3 android/compat/check_kotlin.py   # Python opens what Kotlin made
```

Run `check_kotlin.py` within a few minutes of the Kotlin tests: message
counters are time-based and only accepted within ±5 minutes.

## Status

- Core protocol: tested against the Python code (both directions).
- App: written for Android 5.0+ and tested on a phone with an LA66 stick
  (public and private chats with a laptop). Please report what works and what
  doesn't on other phones.
- Not yet: QR codes for key strings.
