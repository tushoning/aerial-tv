package com.oneplus.tv.launcher2.update

import android.app.PendingIntent
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import androidx.annotation.RequiresApi
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.FileProvider
import androidx.core.content.edit
import com.oneplus.tv.launcher2.BuildConfig
import com.oneplus.tv.launcher2.guard.GuardSetup
import com.oneplus.tv.launcher2.system.DiagLog
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Whether a newer release is out (issues #24, #76). Most owners stay on the version they installed
 * and report bugs fixed long ago. Aerial reads the latest release's tag from the
 * website (reachable in China, unlike GitHub); Control Center then names the new version and says
 * how to update. Nothing is downloaded or installed in the background: only when the owner picks
 * "Download and Install" does Aerial fetch the new APK and hand it to the system installer
 * ([install]).
 *
 * Home and network events check at most every three hours; opening Control Center shortens that
 * to ten minutes. Failed checks retry while Home is in front (#912).
 */
object Updates {
    private const val TAG = "AerialUpdates"
    private const val PREFS = "updates"
    // This fork publishes this file with every release; three routes to it, as for the APK
    // below. One mirror going down must not read as "no new version".
    private val VERSION_URLS = listOf(
        "https://ghfast.top/https://github.com/tushoning/aerial-tv/releases/latest/download/version.txt",
        "https://gh-proxy.com/https://github.com/tushoning/aerial-tv/releases/latest/download/version.txt",
        "https://github.com/tushoning/aerial-tv/releases/latest/download/version.txt",
    )

    /**
     * The version the first source that answers with one reports; null when none did. A mirror
     * can answer with an error page rather than fail outright, so a reply that is not a version
     * number moves on to the next source. Blocking: both callers already run it on IO, and this
     * is the only place the list is read.
     */
    private fun fetchLatest(): String? = VERSION_URLS.firstNotNullOfOrNull { url ->
        runCatching { read(url).trim().removePrefix("v") }
            .onFailure { Log.w(TAG, "could not check for a new version at $url", it) }
            .getOrNull()
            ?.takeIf { it.matches(Regex("\\d+(\\.\\d+)*")) }
    }

    /**
     * A source that delivers less than [STALL_BYTES] in [STALL_MS] is given up for the next one
     * (issue #390). A slow but moving download (about 8 KB/s and up) still finishes.
     */
    private const val STALL_MS = 30_000L
    private const val STALL_BYTES = 256 * 1024L
    const val CONTROL_CENTER_MAX_AGE_MS = UpdateCheck.PANEL_MAX_AGE_MS

    /** This build's version, e.g. "1.17" (the guard build's "-guard" left off). */
    val current: String = BuildConfig.VERSION_NAME.substringBefore('-')

    /** The newer release's version, or null when this is the latest (or nothing is known yet). */
    var available by mutableStateOf<String?>(null); private set

    /** Where an install started from Control Center has got to. */
    var install by mutableStateOf<Install>(Install.Idle); private set

    private lateinit var prefs: SharedPreferences
    private lateinit var appContext: Context
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var started = false
    private var checker: UpdateCheck? = null

    fun init(context: Context) {
        if (started) return
        started = true
        appContext = context.applicationContext
        prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        available = prefs.getString("latest", null)?.takeIf { newer(it, current) }
        checker = UpdateCheck(
            scope = scope,
            lastSuccess = { prefs.getLong("checked_at", 0) },
            read = {
                withContext(Dispatchers.IO) {
                    fetchLatest() ?: throw IllegalStateException("no source answered with a version")
                }
            },
            save = { latest, checkedAt ->
                prefs.edit { putString("latest", latest); putLong("checked_at", checkedAt) }
                available = latest.takeIf { newer(it, current) }
            },
            log = { Log.i(TAG, it); DiagLog.i("update check: $it") },
        )
        // A download left from an install that has since finished or been given up.
        scope.launch(Dispatchers.IO) {
            apkFile(context).takeIf { System.currentTimeMillis() - it.lastModified() > 60 * 60 * 1000L }?.delete()
        }
    }

    /**
     * [force]: now, however recently it last looked (ConfigReceiver's check_update). Opening Control
     * Center asks with a shorter [maxAgeMs]: that is where the viewer looks for a new version, and a
     * release out an hour ago was not shown there for hours more (issue #301).
     */
    fun checkIfStale(
        force: Boolean = false,
        maxAgeMs: Long = UpdateCheck.MAX_AGE_MS,
        networkCameUp: Boolean = false,
    ) {
        checker?.check(force, maxAgeMs, networkCameUp, when {
            force -> "forced"
            networkCameUp -> "network"
            maxAgeMs == CONTROL_CENTER_MAX_AGE_MS -> "control center"
            else -> "home"
        })
    }

    fun diagnostics(): String = if (!started) "not initialized" else
        "known=${prefs.getString("latest", null) ?: "unknown"}; ${checker?.diagnostics}"

    /**
     * Asks once, now, and waits for the answer. Control Center's Check for Updates row has to say
     * what came of the check, which [checkIfStale] cannot tell it: that one launches and returns.
     * Null means either that no source answered or that this build is already the latest.
     */
    suspend fun checkNow(): String? {
        if (!started) return null
        val latest = withContext(Dispatchers.IO) { fetchLatest() } ?: return null
        prefs.edit { putString("latest", latest); putLong("checked_at", System.currentTimeMillis()) }
        available = latest.takeIf { newer(it, current) }
        return available
    }

    /** Whether version [a] comes after [b], comparing dotted numbers ("1.10" after "1.9"). */
    fun newer(a: String, b: String): Boolean {
        val x = a.split('.').map { it.toIntOrNull() ?: 0 }
        val y = b.split('.').map { it.toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(x.size, y.size)) {
            val d = x.getOrElse(i) { 0 } - y.getOrElse(i) { 0 }
            if (d != 0) return d > 0
        }
        return false
    }

    private fun read(url: String): String {
        val c = URL(url).openConnection() as HttpURLConnection
        LegacyUpdateTls.configure(appContext, c)
        c.connectTimeout = 8_000
        c.readTimeout = 10_000
        return try {
            c.inputStream.use { it.readBytes().decodeToString() }
        } finally {
            c.disconnect()
        }
    }

    // ---- Installing the new version from Control Center ----

    sealed interface Install {
        data object Idle : Install
        data class Downloading(val fraction: Float) : Install
        /** The system installer is open; it asks the owner and replaces Aerial, which restarts. */
        data object Opened : Install
        /** Aerial may not install apps yet; the TV's setting for it is open. */
        data object NeedsPermission : Install
        /** Android 6 firmware requires storage access before the APK can be saved. */
        data object NeedsStoragePermission : Install
        data class Failed(val reason: Failure) : Install
    }

    enum class Failure {
        /** Neither the website nor GitHub gave a complete APK. */
        Download,
        /** The TV lets no app hand an APK to its installer, or has no installer to hand it to. */
        Blocked,
        /** This copy is not the official package or not signed with the official key. */
        NotOfficial,
        /** The downloaded APK is no newer than this one (the website not yet updated). */
        NotNewer,
    }

    /** The official package. Copies renamed for one firmware (OPPO, see docs/devices) cannot update from the release. */
    private const val PACKAGE = "com.oneplus.tv.launcher2"

    /** Whether this copy can update itself from the release at all. */
    fun canInstall(context: Context): Boolean = context.packageName == PACKAGE

    /**
     * Downloads this flavor's APK of the latest release and installs it. Android 9 and later, a
     * PackageInstaller session first (installBySession), as only a session can carry the dex
     * metadata that has the system compile Aerial as it installs. Where the session fails, the system
     * installer by ACTION_VIEW, as before Android 9: TCL's verifier (docs/devices/tcl-mt5879-cn.md)
     * passes only installs that go through com.android.packageinstaller, as Dangbei's do, and an
     * ACTION_VIEW on the APK makes the installer itself the one installing. The APK must be this package, signed
     * with this copy's key and no older, or the installer would only fail with a less clear message.
     */
    fun install(context: Context) {
        if (install is Install.Downloading) return
        val app = context.applicationContext
        if (!canInstall(app)) { install = Install.Failed(Failure.NotOfficial); return }
        if (LegacyUpdateStorage.needsPermission(app)) {
            install = Install.NeedsStoragePermission
            LegacyUpdateStorage.requestPermission(context)
            return
        }
        if (Build.VERSION.SDK_INT >= 26 && !app.packageManager.canRequestPackageInstalls()) {
            // Xiaomi MiTV-MFTR0 has this page; TCL MT5879 does not (tools/install.sh grants it there).
            val settings = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${app.packageName}"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            if (runCatching { context.startActivity(settings) }.isSuccess) {
                install = Install.NeedsPermission
                return
            }
            // No page to allow it on (Xiaomi MiTV-MFTP0, Android 9, has only Xiaomi's own settings:
            // "this TV does not let apps install apps", issue #284). Firmware installers often let
            // it through or ask themselves, so the download goes ahead and the installer decides.
            Log.i(TAG, "not allowed to install and no page to allow it on; trying the installer anyway")
        }
        install = Install.Downloading(0f)
        scope.launch {
            val file = apkFile(app)
            val ready = withContext(Dispatchers.IO) {
                // A download kept from a moment ago, when the owner backed out of the installer.
                verify(app, file)?.takeIf { testApk == null && it.versionName?.substringBefore('-') == available }
                    ?: downloadFrom(app, file)
            }
            val failure = when {
                ready == null -> Failure.Download
                !sameSigner(app, ready) -> Failure.NotOfficial
                versionCode(ready) < BuildConfig.VERSION_CODE -> Failure.NotNewer
                else -> null
            }
            if (failure != null) {
                Log.w(TAG, "not installing: $failure")
                if (failure != Failure.Download) file.delete()
                install = Install.Failed(failure)
                return@launch
            }
            val opened = if (Build.VERSION.SDK_INT >= 28) installBySession(app, file) else openInstaller(context, app, file)
            install = if (opened) Install.Opened else Install.Failed(Failure.Blocked)
        }
    }

    /**
     * From Android 13 the installer's ACTION_VIEW records the APK as a local file (packageSource 3),
     * and Android then restricts the app's settings (ACCESS_RESTRICTED_SETTINGS deny; seen on the
     * Android 13 TV emulator). On TCL T966D5 (Android 14) Home Guard was off after every update made
     * this way, and stayed on after one pushed by 多屏互动 (issue #723). A session that says where
     * the APK came from is not restricted; the system still asks the owner to confirm. Where the
     * firmware turns the session down, the installer's ACTION_VIEW as before (UpdateResult).
     *
     * The session also carries the new APK's dex metadata (assets/aerial-dm, see app/build.gradle.kts)
     * as aerial.dm, from Android 9: the system then compiles Aerial with its Baseline Profile as it
     * installs (Xiaomi MiTV-MFTR0: status speed-profile, reason install-dm). Without it the update ran
     * uncompiled until the next one, its main thread about twice as slow (docs/PERF-2026-10-30.md).
     * Should a firmware refuse the metadata, the APK alone once more ([withDm]).
     */
    @RequiresApi(28)
    private fun installBySession(app: Context, file: File, withDm: Boolean = true): Boolean = runCatching {
        val installer = app.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(app.packageName)
            if (Build.VERSION.SDK_INT >= 33) setPackageSource(PackageInstaller.PACKAGE_SOURCE_OTHER)
            // Android 12 and later let an app update itself without the confirmation page, which some
            // firmwares do not have (Xiaomi MiTV-MFTR0: no CONFIRM_INSTALL activity, so the session
            // gave way to the installer's ACTION_VIEW and its dex metadata was lost). The owner has
            // just picked "Download and Install". Where the system still wants a confirmation, it
            // answers STATUS_PENDING_USER_ACTION as before.
            if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val dm = if (withDm) dexMetadata(file) else null
        val id = installer.createSession(params)
        try {
            installer.openSession(id).use { session ->
                file.inputStream().use { input ->
                    session.openWrite("aerial.apk", 0, file.length()).use { out ->
                        input.copyTo(out)
                        session.fsync(out)
                    }
                }
                if (dm != null) session.openWrite("aerial.dm", 0, dm.size.toLong()).use { out ->
                    out.write(dm)
                    session.fsync(out)
                }
                val result = Intent(app, UpdateResult::class.java).putExtra(EXTRA_WITH_DM, dm != null)
                val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
                GuardSetup.noteInstallerOpened(app)
                session.commit(PendingIntent.getBroadcast(app, id, result, flags).intentSender)
            }
        } catch (e: Exception) {
            runCatching { installer.abandonSession(id) }
            throw e
        }
        Log.i(TAG, "install session $id committed for ${file.length()} bytes, dex metadata ${dm?.size ?: "none"}")
        DiagLog.i("update: session $id, dex metadata ${dm?.size ?: "none"}")
        true
    }.getOrElse {
        if (withDm) {
            Log.w(TAG, "install session with dex metadata failed; without it", it)
            installBySession(app, file, withDm = false)
        } else {
            Log.w(TAG, "no install session; opening the installer", it)
            openInstaller(app, app, file)
        }
    }

    /**
     * The dex metadata the build put in [apk] for this Android (28.dm for 9 to 11, 31.dm from 12),
     * or null: an APK from before 1.72, or one that cannot be read.
     */
    private fun dexMetadata(apk: File): ByteArray? = runCatching {
        val name = if (Build.VERSION.SDK_INT >= 31) "assets/aerial-dm/31.dm" else "assets/aerial-dm/28.dm"
        java.util.zip.ZipFile(apk).use { zip -> zip.getEntry(name)?.let { e -> zip.getInputStream(e).use { it.readBytes() } } }
    }.getOrNull()

    private const val EXTRA_WITH_DM = "com.oneplus.tv.launcher2.update.WITH_DM"

    /** The session's answer (UpdateResult): the owner's confirmation, or the installer instead. */
    internal fun onSessionResult(context: Context, intent: Intent) {
        val app = context.applicationContext
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                runCatching { app.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }.onFailure {
                    // Some Android 13+ firmware installers have no CONFIRM_INSTALL page but still
                    // take the APK by ACTION_VIEW (issue #827, model not known yet).
                    Log.w(TAG, "could not ask to confirm the update; opening the installer", it)
                    val id = intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1)
                    if (id >= 0) runCatching { app.packageManager.packageInstaller.abandonSession(id) }
                    val file = apkFile(app)
                    if (!file.isFile || !openInstaller(app, app, file)) install = Install.Failed(Failure.Blocked)
                }
            }
            PackageInstaller.STATUS_SUCCESS -> Unit // this process is replaced
            PackageInstaller.STATUS_FAILURE_ABORTED -> {
                Log.i(TAG, "update not confirmed")
                install = Install.Idle
            }
            else -> {
                val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
                val file = apkFile(app)
                // A firmware that turns the dex metadata down (INSTALL_FAILED_BAD_DEX_METADATA):
                // the APK alone in a new session.
                if (intent.getBooleanExtra(EXTRA_WITH_DM, false) && message.orEmpty().contains("DEX_METADATA", ignoreCase = true) &&
                    Build.VERSION.SDK_INT >= 28 && file.isFile
                ) {
                    Log.w(TAG, "install session refused the dex metadata ($message); without it")
                    if (installBySession(app, file, withDm = false)) return
                }
                Log.w(TAG, "install session failed ($status, $message); opening the installer")
                DiagLog.i("update: session failed ($status, $message); installer")
                if (!file.isFile || !openInstaller(app, app, file)) install = Install.Failed(Failure.Blocked)
            }
        }
    }

    /** Resume failed checks; if the installer closed without replacing Aerial, reset its button. */
    fun onHomeResumed() {
        checker?.setActive(true)
        if (install == Install.Opened) install = Install.Idle
    }

    fun onHomePaused() { checker?.setActive(false) }

    /** Back to the button, when the update page is left. */
    fun resetInstall() {
        if (install !is Install.Downloading) install = Install.Idle
    }

    private fun apkFile(context: Context): File {
        // Android 5 and 6 installers read a file:// URI, so there the APK sits where they can read it.
        val dir = if (Build.VERSION.SDK_INT >= 24) context.cacheDir else context.externalCacheDir ?: context.cacheDir
        return File(File(dir, "updates"), "aerial.apk")
    }

    /**
     * For testing the whole update on a TV before a release (ConfigReceiver's update_test): the APK
     * pushed to Android/data/com.oneplus.tv.launcher2/files/update-test.apk stands in for the download.
     */
    @Volatile var testApk: File? = null

    /** The website first (reachable in China), then GitHub. Returns the APK's info once it parses. */
    private suspend fun downloadFrom(context: Context, file: File): PackageInfo? {
        testApk?.takeIf { it.isFile }?.let { test ->
            file.parentFile?.mkdirs()
            test.copyTo(file, overwrite = true)
            if (Build.VERSION.SDK_INT < 24) file.setReadable(true, false)
            return verify(context, file)
        }
        val name = "aerial-${BuildConfig.FLAVOR}.apk"
        // This build's updates come from its own fork's release of the same version, through
        // domestic mirrors: the upstream APK is another package signed with another key, so
        // verify() would turn it away anyway.
        val tag = "v" + (available ?: current)
        val sources = listOf(
            "https://ghfast.top/https://github.com/tushoning/aerial-tv/releases/download/$tag/$name",
            "https://gh-proxy.com/https://github.com/tushoning/aerial-tv/releases/download/$tag/$name",
            "https://github.com/tushoning/aerial-tv/releases/download/$tag/$name",
        )
        for (url in sources) {
            val ok = runCatching { download(url, file) }
                .onFailure { Log.w(TAG, "could not download $url", it) }
                .isSuccess
            if (ok) verify(context, file)?.let { return it }
            file.delete()
        }
        return null
    }

    private suspend fun download(url: String, file: File) {
        val directory = checkNotNull(file.parentFile)
        check(directory.isDirectory || directory.mkdirs()) { "Cannot create update directory: $directory" }
        check(directory.canWrite()) { "Cannot write update directory: $directory" }
        val part = File(directory, file.name + ".part")
        val c = URL(url).openConnection() as HttpURLConnection
        LegacyUpdateTls.configure(appContext, c)
        c.connectTimeout = 10_000
        c.readTimeout = 20_000
        // A throttled route can keep the TLS handshake up and deliver a byte now and then, so no
        // read() ever blocks long enough to time out and the progress bar sits at 0% for good
        // (issue #390, FFALCON/TCL on China Unicom). So the bytes are counted, not each read timed.
        var windowAt = SystemClock.elapsedRealtime()
        var windowFrom = 0L
        try {
            if (c.responseCode != HttpURLConnection.HTTP_OK) error("HTTP ${c.responseCode}")
            val total = c.contentLength.toLong()
            var done = 0L
            var shown = -1
            c.inputStream.use { input ->
                part.outputStream().use { out ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        done += n
                        val now = SystemClock.elapsedRealtime()
                        if (now - windowAt >= STALL_MS) {
                            if (done - windowFrom < STALL_BYTES) error("stalled: ${done - windowFrom} bytes in ${(now - windowAt) / 1000} s")
                            windowAt = now
                            windowFrom = done
                        }
                        val percent = if (total > 0) (done * 100 / total).toInt() else 0
                        if (percent != shown) {
                            shown = percent
                            withContext(Dispatchers.Main) { install = Install.Downloading(percent / 100f) }
                        }
                    }
                }
            }
            if (total > 0 && done != total) error("got $done of $total bytes")
            if (!part.renameTo(file)) error("could not keep the download")
            // The installer on Android 5 and 6 opens the file itself.
            if (Build.VERSION.SDK_INT < 24) file.setReadable(true, false)
        } finally {
            c.disconnect()
            part.delete()
        }
    }

    /** The APK's package info when it parses as this package, else null. */
    @Suppress("DEPRECATION")
    private fun verify(context: Context, file: File): PackageInfo? {
        if (!file.isFile) return null
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
        val info = runCatching { context.packageManager.getPackageArchiveInfo(file.path, flags) }.getOrNull() ?: return null
        return info.takeIf { it.packageName == context.packageName }
    }

    /** Whether [apk] is signed like this copy. If the archive's signers cannot be read, the installer decides. */
    @Suppress("DEPRECATION")
    private fun sameSigner(context: Context, apk: PackageInfo): Boolean {
        val pm = context.packageManager
        fun signers(info: PackageInfo?): Set<String>? {
            val sigs = if (Build.VERSION.SDK_INT >= 28) info?.signingInfo?.apkContentsSigners else info?.signatures
            return sigs?.takeIf { it.isNotEmpty() }?.map { it.toCharsString() }?.toSet()
        }
        val theirs = signers(apk) ?: return true
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
        val ours = signers(runCatching { pm.getPackageInfo(context.packageName, flags) }.getOrNull()) ?: return true
        return theirs == ours
    }

    @Suppress("DEPRECATION")
    private fun versionCode(info: PackageInfo): Long =
        if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else info.versionCode.toLong()

    private fun openInstaller(context: Context, app: Context, file: File): Boolean {
        val uri = if (Build.VERSION.SDK_INT >= 24) FileProvider.getUriForFile(app, "${app.packageName}.files", file)
        else Uri.fromFile(file)
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            GuardSetup.noteInstallerOpened(app)
            context.startActivity(intent)
            Log.i(TAG, "installer opened for ${file.length()} bytes")
            true
        } catch (e: ActivityNotFoundException) {
            Log.w(TAG, "no installer", e)
            false
        } catch (e: SecurityException) {
            Log.w(TAG, "installer refused", e)
            false
        }
    }
}
