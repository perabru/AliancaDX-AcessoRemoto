plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val controllerExcludedSources = listOf(
    "**/HostCaptureService.kt",
    "**/HostRuntime.kt",
    "**/RemoteAccessibilityService.kt",
    "**/AndroidTlsIdentity.kt"
)

android {
    namespace = "br.com.perabru.minianydesk"
    compileSdk = 36
    buildToolsVersion = "35.0.0"
    sourceSets.getByName("main").java.exclude(controllerExcludedSources)

    defaultConfig {
        applicationId = "br.com.perabru.minianydesk"
        minSdk = 26
        targetSdk = 36
        versionCode = 64000
        versionName = "6.4.0"
        testInstrumentationRunner = "br.com.perabru.minianydesk.PortInteropInstrumentation"
    }

    flavorDimensions += "role"
    productFlavors {
        create("controlador") {
            dimension = "role"
            applicationIdSuffix = ".controlador"
            buildConfigField("String", "DEFAULT_ROLE", "\"controller\"")
            resValue("string", "app_name", "AliançaDX Acesso Remoto")
            resValue("color", "launcher_background", "#007A50")
        }
    }

    buildTypes {
        getByName("debug") {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-dev"
        }
        getByName("release") {
            isMinifyEnabled = false
            isDebuggable = false
            isJniDebuggable = false
            // The release pipeline signs externally with the protected owner
            // keystore and a verified rotation lineage. No private key belongs
            // in the project or in this Gradle file.
            signingConfig = null
        }
    }

    packaging {
        jniLibs {
            useLegacyPackaging = false
            // Bundle the already audited native release binaries byte-for-byte.
            // They do not need another NDK strip step during APK packaging.
            keepDebugSymbols.add("**/*.so")
        }
    }

    buildFeatures {
        buildConfig = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

kotlin {
    sourceSets.getByName("main").kotlin.exclude(controllerExcludedSources)
}

dependencies {
    implementation("org.jetbrains.kotlin:kotlin-stdlib:2.1.20")
}
