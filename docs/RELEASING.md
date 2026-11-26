# Releasing

How to publish the web app on GitHub Pages, and how to build the Android APK
and publish it on GitHub so people can download it.

## The web app (GitHub Pages)

The web app is served from this repository at
`https://tnnotes.github.io/textcast/`. A GitHub workflow builds `web/` and
publishes it every time a push changes `web/`.

**Once:**

1. Put the workflow in place (it is kept in `web/deploy/`):

   ```bash
   mkdir -p .github/workflows
   cp web/deploy/web-pages.yml .github/workflows/web-pages.yml
   ```

2. Commit and push it.
3. On GitHub: **Settings → Pages → Build and deployment → Source: GitHub
   Actions**.
4. Watch **Actions**: when the run is green, the app is live.

**Each update:** edit `web/src/`, run `python3 web/build.py`, check it
(`node --test web/tests/compat.test.js`, then try it in a browser), commit
and push. Browsers that opened TextCast before pick up the new version by
themselves (the next time it opens after an online visit). Set `VERSION` in
`web/build.py` for a release.

# The Android app


## Quick route

1. **Set the version** in `android/app/build.gradle.kts`: `versionName` (shown
   to users, e.g. `0.5.0`) and `versionCode` (a whole number that must
   **increase** with every APK you publish, or phones refuse the update). Add the
   release notes to `CHANGELOG.md`.

2. **Build the APK:**

   ```bash
   cd android
   ./gradlew assembleRelease
   ```

   The build needs to know where the Android SDK is. Opening the `android`
   folder once in Android Studio writes that into `android/local.properties`
   (a personal file, not committed). Without Android Studio, create it yourself
   with one line, `sdk.dir=/Users/<you>/Library/Android/sdk` (Windows:
   `sdk.dir=C\:\\Users\\<you>\\AppData\\Local\\Android\\Sdk`), or set
   the `ANDROID_HOME` environment variable.

   or in Android Studio: **Build → Generate App Bundles or APKs → Generate APKs**.
   The file is `android/app/build/outputs/apk/release/app-release.apk`.

3. **Rename** it, e.g. `TextCast-0.5.0.apk`, and note its checksum so people can
   check their download:

   ```bash
   shasum -a 256 TextCast-0.5.0.apk        # Windows: certutil -hashfile TextCast-0.5.0.apk SHA256
   ```

4. **Test it once** on a phone: install, log in, send, receive, lock and unlock.

5. **Publish on GitHub:**
   1. Commit and push the code, then tag it: `git tag v0.5.0 && git push --tags`.
   2. On GitHub: **Releases → Draft a new release**, choose the tag, paste the
      changelog entry and the checksum, and **attach `TextCast-0.5.0.apk`**.
   3. Publish.

Attach APKs to releases; don't commit them to the repository (`.gitignore`
already excludes them).

### About signing (read once)

Android only installs signed apps. Without extra setup, the build signs the APK
with Android Studio's **debug key**, a file created automatically on your
computer: `~/.android/debug.keystore` (Windows:
`C:\Users\<you>\.android\debug.keystore`). That is fine for a GitHub download.

What the key controls is **updates**: a phone installs a new version over the
old one only if both are signed with the same key. So:

- **Back up `~/.android/debug.keystore`** (USB drive, cloud). With it you can
  publish updates from any computer: copy it to the same place there.
- If the file is lost, people must uninstall TextCast before installing your
  next version, and lose their identity unless they exported it first.

## Optional: your own signing key

Use your own key instead of the debug key if you publish in an app store, or if
several people should build official releases. Create it once:

```bash
cd android
keytool -genkeypair -v -keystore textcast-release.jks -alias textcast \
        -keyalg RSA -keysize 4096 -validity 10000
```

Then create `android/keystore.properties`:

```
storeFile=textcast-release.jks
storePassword=<the keystore password>
keyAlias=textcast
keyPassword=<the key password>
```

From then on, `./gradlew assembleRelease` signs with this key. Both files are in
`android/.gitignore`: **never commit them**, and back them up.

Switching keys counts as a different app to Android: phones with a
debug-signed version must uninstall it before installing one signed with the
new key. Decide early, before many people install it.

## For developers: check the code before a release

```bash
python3 -m unittest discover -s tests              # desktop core: ends with OK
python3 android/compat/make_vectors.py             # only if the protocol changed
cd android && ./gradlew testDebugUnitTest && cd .. # Kotlin core: byte-exact vs Python
python3 android/compat/check_kotlin.py             # Python opens what Kotlin made
node --test web/tests/compat.test.js               # web core: byte-exact vs Python
python3 web/tests/e2e_browser.py                   # web app in headless Chrome (needs Playwright)
```

Run `check_kotlin.py` within a few minutes of the Kotlin tests (message counters
are time-based).
