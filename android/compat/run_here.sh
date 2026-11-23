#!/bin/sh
# Compile the Kotlin core and run its tests without Android Studio, using the
# Kotlin compiler bundled with Gradle (GRADLE_LIB = Gradle's lib folder).
# Normal use: open android/ in Android Studio and run the unit tests there.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
APP="$HERE/../app/src"
LIB=${GRADLE_LIB:-/opt/gradle-8.14.3/lib}
CP=$(ls "$LIB"/*.jar | tr '\n' ':')
STD="$LIB/kotlin-stdlib-2.0.21.jar"
OUT=$(mktemp -d)
java -cp "$CP" org.jetbrains.kotlin.cli.jvm.K2JVMCompiler -no-reflect -no-stdlib -jvm-target 1.8 \
  -cp "$STD:$LIB/junit-4.13.2.jar" "$APP"/main/java/io/github/textcast/core/*.kt \
  "$APP"/test/java/io/github/textcast/core/*.kt -d "$OUT" 2>&1 | grep -v "^Picked up" || true
cp "$APP"/test/resources/* "$OUT"/
java -Dtextcast.compat.out="$HERE/out" -cp "$OUT:$STD:$LIB/junit-4.13.2.jar:$LIB/hamcrest-core-1.3.jar" \
  org.junit.runner.JUnitCore io.github.textcast.core.CompatTest io.github.textcast.core.RadioTest 2>&1 | grep -v "^Picked up"
