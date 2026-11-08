package app.orchard.tv.lite

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import org.json.JSONObject
import java.io.File

/**
 * The full app's icon library (apps/IconLibrary.kt): ready-made 16:9 tile pictures for popular
 * apps from the website, keyed by package. The index is read at most twice a day, and only the
 * pictures of apps installed here are downloaded, into files/library. Off the main thread.
 */
object IconLibrary {
    private const val TAG = "AerialLite"
    private const val BASE = "https://aerial.qust.me/icons/"
    private const val CHECK_EVERY_MS = 12 * 60 * 60 * 1000L
    private const val RETRY_MS = 10 * 60 * 1000L

    private lateinit var dir: File
    private lateinit var context: Context
    @Volatile private var index: Map<String, String> = emptyMap()
    @Volatile private var lastAttempt = 0L
    private val failedAt = HashMap<String, Long>()

    fun init(context: Context) {
        if (::dir.isInitialized) return
        this.context = context.applicationContext
        dir = File(context.filesDir, "library").apply { mkdirs() }
        index = parse(File(dir, "index.json").takeIf { it.exists() }?.readText())
    }

    private fun fileFor(pkg: String): File? = index[pkg]?.let { File(dir, it.substringAfterLast('/')) }

    /** The library's picture of [pkg] is on this TV (whether or not its tile uses it). */
    fun has(pkg: String): Boolean = ::dir.isInitialized && fileFor(pkg)?.exists() == true

    /** The library's picture of [pkg] as stored, for the phone page; null without one. */
    fun file(pkg: String): File? = if (::dir.isInitialized) fileFor(pkg)?.takeIf { it.exists() } else null

    /** Whether [pkg]'s tile uses the library: unless the phone page picked the app's own art. */
    fun uses(pkg: String): Boolean = pick(pkg) != Pick.Own

    private fun usable(pkg: String): File? = if (uses(pkg)) file(pkg) else null

    enum class Pick { Library, Own }

    private val picks get() = context.getSharedPreferences("icon_library", Context.MODE_PRIVATE)

    /** What the phone page picked for [pkg]; null for the library. */
    fun pick(pkg: String): Pick? = when {
        !::dir.isInitialized -> null
        pkg in picks.getStringSet("use_library", emptySet())!! -> Pick.Library
        pkg in picks.getStringSet("use_own", emptySet())!! -> Pick.Own
        else -> null
    }

    /** From the phone page, as the full app's IconLibrary.choose. */
    fun choose(pkg: String, pick: Pick?) {
        val library = picks.getStringSet("use_library", emptySet())!! - pkg
        val own = picks.getStringSet("use_own", emptySet())!! - pkg
        picks.edit()
            .putStringSet("use_library", if (pick == Pick.Library) library + pkg else library)
            .putStringSet("use_own", if (pick == Pick.Own) own + pkg else own)
            .apply()
    }

    /** When the app's picture arrived, or the pick changed; 0 without one. Part of the tile cache's key. */
    fun stamp(pkg: String): Long = (usable(pkg)?.lastModified() ?: 0L) + when (pick(pkg)) { Pick.Own -> 1L; Pick.Library -> 2L; null -> 0L }

    fun load(pkg: String): Bitmap? = usable(pkg)?.let { BitmapFactory.decodeFile(it.path) }

    /** Reads the index when due and fetches pictures for [installed] apps; true when a tile changed. */
    @Synchronized
    fun refresh(installed: Set<String>): Boolean {
        if (!::dir.isInitialized) return false
        val prefs = context.getSharedPreferences("icon_library", Context.MODE_PRIVATE)
        val now = System.currentTimeMillis()
        var changed = false
        if (now - prefs.getLong("checked_at", 0) > CHECK_EVERY_MS && now - lastAttempt > RETRY_MS) {
            lastAttempt = now
            runCatching { Net.text(BASE + "index.json") }
                .onFailure { Log.w(TAG, "could not read the icon index", it) }
                .getOrNull()
                ?.let { text ->
                    val fresh = parse(text)
                    if (fresh.isNotEmpty() || text.contains("\"icons\"")) {
                        File(dir, "index.json").writeText(text)
                        changed = fresh != index
                        index = fresh
                        prefs.edit().putLong("checked_at", now).apply()
                    }
                }
        }
        val keep = HashSet<String>()
        for (pkg in installed) {
            val path = index[pkg] ?: continue
            val file = fileFor(pkg) ?: continue
            keep += file.name
            if (file.exists() || now - (failedAt[pkg] ?: 0L) < RETRY_MS) continue
            runCatching {
                Net.download(BASE + path, file)
                val ok = BitmapFactory.Options().apply { inJustDecodeBounds = true }.also { BitmapFactory.decodeFile(file.path, it) }.outWidth > 0
                if (!ok) { file.delete(); error("not a picture") }
            }
                .onSuccess { changed = true; failedAt.remove(pkg) }
                .onFailure { failedAt[pkg] = now; Log.w(TAG, "could not fetch the picture of $pkg", it) }
        }
        // Pictures replaced by newer ones, or of apps no longer installed.
        dir.listFiles()?.forEach { if (it.name != "index.json" && it.name !in keep && !it.name.endsWith(".part")) it.delete() }
        return changed
    }

    private fun parse(text: String?): Map<String, String> = runCatching {
        val icons = JSONObject(text ?: return emptyMap()).getJSONObject("icons")
        val out = HashMap<String, String>()
        val keys = icons.keys()
        while (keys.hasNext()) {
            val pkg = keys.next()
            if (pkg.matches(Regex("[A-Za-z0-9_.]+"))) out[pkg] = icons.getString(pkg)
        }
        out
    }.getOrDefault(emptyMap())
}
