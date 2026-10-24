package app.orchard.tv.system

import android.content.Context
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log

/**
 * Panel backlight on OPPO / OnePlus ColorOS TV (MediaTek TV SoC, CVTE board — `OPPOCNM632` and the
 * K9 `OPPOCNM652` are both this).
 *
 * On these boards `Settings.System.SCREEN_BRIGHTNESS` is written and read back happily, yet the
 * panel does not move: the backlight belongs to the firmware's picture engine, not to Android's
 * lights HAL. Control Center therefore showed a slider that changed nothing, which reads as broken.
 * The level actually lives in a vendor setting written by the TV's own picture menu
 * (`com.android.tv.settings`, OPPO's OptvSettings), 0–100 like the slider on screen.
 *
 * Nothing documents the setting's name, and it differs between ColorOS TV builds, so it is found
 * rather than assumed: the names below are tried first, then every setting whose name looks like a
 * brightness and whose value is 1–100. The winner is remembered in preferences and logged, and
 * [level] stays null when none is found — Control Center then opens the TV's own picture settings
 * instead of offering a slider that would do nothing (the behaviour TCL and Sony get).
 */
internal object OppoPicture {
    private const val TAG = "AerialOppo"
    private const val PREFS = "aerial"
    private const val KEY_SETTING = "oppo_backlight_setting_v2"
    private const val KEY_LEVEL = "oppo_backlight_level"

    /** Where one build keeps the panel level: a settings table and the key inside it. */
    private class Slot(val key: String, val global: Boolean)

    /**
     * Names the firmware's picture engine is known to use, best first.
     *
     * `tv_picture_backlight` is the one on `OPPOCNM632` (Android 9, MediaTek + CVTE board): it is
     * what the TV's own "屏幕亮度" row reads and writes, and the picture engine follows it. The
     * similarly named `tv_picture_brightness` is *not* the panel — it is the black level of the
     * picture menu, and writing it leaves the backlight where it was. That mix-up is exactly why
     * the first build of this file kept a slider that did nothing, so brightness names come last.
     */
    private val NAMES = listOf(
        "tv_picture_backlight", "picture_backlight", "panel_backlight",
        "tv_backlight", "backlight",
        "tv_picture_brightness", "picture_brightness", "tv_brightness", "brightness",
    )

    /** Words that mark a "brightness" setting that is not the panel's own level. */
    private val NOT_PANEL = listOf(
        "auto", "adaptive", "sensor", "mode", "curve", "min", "max", "step",
        "offset", "ratio", "dim", "duration", "timeout", "level_", "night",
        // Android's own screen brightness, which this board's panel ignores.
        "screen_brightness",
    )

    private var probed = false
    private var slot: Slot? = null

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Whether this is an OPPO / OnePlus ColorOS TV at all, before any setting is probed. */
    private fun isOppo(context: Context): Boolean {
        val brand = (Build.BRAND + " " + Build.MANUFACTURER + " " + Build.MODEL).lowercase()
        if (brand.contains("oppo") || brand.contains("oneplus") || brand.contains("oplus") || brand.contains("heytap")) return true
        return runCatching { context.packageManager.getPackageInfo("com.heytap.tv.launcher", 0) }.isSuccess ||
            runCatching { context.packageManager.getPackageInfo("com.oplus.tv.launcher", 0) }.isSuccess
    }

    /** The setting this build keeps the level in, once per process. Null on any other TV. */
    private fun slot(context: Context): Slot? {
        if (probed) return slot
        probed = true
        if (!isOppo(context)) return null
        // A setting found on an earlier run, while it still reads as a level.
        prefs(context).getString(KEY_SETTING, null)?.split('|')?.takeIf { it.size == 2 }?.let {
            val saved = Slot(it[0], it[1] == "g")
            if (read(context, saved) != null) {
                slot = saved
                return saved
            }
            prefs(context).edit().remove(KEY_SETTING).apply()
        }
        val named = NAMES.flatMap { listOf(Slot(it, true), Slot(it, false)) }
        // Global first: the picture engine's own table, where OPPO keeps it. Read through the
        // settings provider rather than Settings.*.getAll, which is not public API.
        val scanned = scan(context, Settings.Global.CONTENT_URI, true) +
            scan(context, Settings.System.CONTENT_URI, false)
        for (candidate in named + scanned) {
            if (read(context, candidate) == null) continue
            slot = candidate
            prefs(context).edit()
                .putString(KEY_SETTING, candidate.key + "|" + if (candidate.global) "g" else "s")
                .apply()
            Log.i(TAG, "panel level kept in ${if (candidate.global) "Global" else "System"}/${candidate.key}")
            return candidate
        }
        Log.i(TAG, "no vendor backlight setting here; Control Center opens the TV's own picture settings")
        return null
    }

    /**
     * Every setting in [uri] whose name looks like a panel brightness and whose value is a 1–100
     * level. Queried rather than read through `Settings.Global.getAll`, which is not public API.
     */
    private fun scan(context: Context, uri: Uri, global: Boolean): List<Slot> {
        val found = mutableListOf<Slot>()
        runCatching {
            context.contentResolver.query(uri, arrayOf("name", "value"), null, null, null)?.use { cursor ->
                while (cursor.moveToNext()) {
                    val key = cursor.getString(0) ?: continue
                    val name = key.lowercase()
                    if (!name.contains("bright") && !name.contains("backlight")) continue
                    if (NOT_PANEL.any { name.contains(it) }) continue
                    val level = cursor.getString(1)?.toIntOrNull() ?: continue
                    if (level in 1..100) found += Slot(key, global)
                }
            }
        }
        return found
    }

    /** The level [slot] holds, 1–100, or null when it is missing or not a level any more. */
    private fun read(context: Context, slot: Slot): Int? {
        val cr = context.contentResolver
        val value = runCatching {
            if (slot.global) Settings.Global.getInt(cr, slot.key, -1) else Settings.System.getInt(cr, slot.key, -1)
        }.getOrNull() ?: return null
        return value.takeIf { it in 1..100 }
    }

    private fun write(context: Context, slot: Slot, percent: Int): Boolean {
        val cr = context.contentResolver
        return runCatching {
            if (slot.global) Settings.Global.putInt(cr, slot.key, percent)
            else Settings.System.putInt(cr, slot.key, percent)
        }.getOrDefault(false)
    }

    /** Current level as 0..1, or null when this TV's panel cannot be reached from an app. */
    fun level(context: Context): Float? {
        val s = slot(context) ?: return null
        val percent = read(context, s) ?: prefs(context).getInt(KEY_LEVEL, 50)
        return percent / 100f
    }

    /**
     * One 5% notch up or down. Stops at 5 rather than 1: the firmware's own slider does, and a
     * panel at 1 on a TV is a black screen with Control Center still open on it.
     */
    fun step(context: Context, direction: Int): Float? {
        val s = slot(context) ?: return null
        val percent = read(context, s) ?: prefs(context).getInt(KEY_LEVEL, 50)
        val next = (if (direction > 0) (percent / 5 + 1) * 5 else ((percent + 4) / 5 - 1) * 5).coerceIn(5, 100)
        if (!write(context, s, next)) {
            Log.w(TAG, "the firmware refused a write to ${s.key}")
            return null
        }
        prefs(context).edit().putInt(KEY_LEVEL, next).apply()
        return next / 100f
    }
}
