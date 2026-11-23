import java.util.Properties

// Release signing: put your key's details in android/keystore.properties
// (never commit it). Without that file, release builds use the debug key.
val keyFile: File = rootProject.file("keystore.properties")
val keyProps = Properties().apply { if (keyFile.exists()) keyFile.inputStream().use { load(it) } }

plugins {
    id("com.android.application")      // AGP 9 compiles Kotlin itself (built-in Kotlin)
}

android {
    namespace = "io.github.textcast"
    compileSdk = 36

    defaultConfig {
        applicationId = "io.github.textcast"
        minSdk = 21            // Android 5.0: nearly every phone still in use
        targetSdk = 35
        versionCode = 1
        versionName = "0.5.0"
    }

    signingConfigs {
        if (keyFile.exists()) create("release") {
            storeFile = rootProject.file(keyProps.getProperty("storeFile"))
            storePassword = keyProps.getProperty("storePassword")
            keyAlias = keyProps.getProperty("keyAlias")
            keyPassword = keyProps.getProperty("keyPassword")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    testOptions { unitTests.isReturnDefaultValues = true }

    lint {
        abortOnError = false          // report lint problems without failing the build
        checkReleaseBuilds = false
    }
}

dependencies {
    implementation("com.github.mik3y:usb-serial-for-android:3.8.1")
    testImplementation("junit:junit:4.13.2")
}

// Unit tests also write Kotlin-made files for android/compat/check_kotlin.py
tasks.withType<Test>().configureEach {
    systemProperty("textcast.compat.out", rootProject.file("compat/out").absolutePath)
}
