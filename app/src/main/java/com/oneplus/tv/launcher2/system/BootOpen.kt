package com.oneplus.tv.launcher2.system

import android.content.Context
import android.os.SystemClock
import androidx.core.content.edit
import kotlin.math.abs

/**
 * Whether the input or app the TV starts on (Personalize) is due: once per power-on and once per
 * wake, never again while the TV stays on. Coocaa stops Aerial in the background; coming back to
 * it from the app it had opened started Aerial cold, which took that for a boot and opened the
 * app again, round and round (issue #766). Kept on disk, since that is a new process each time.
 */
object BootOpen {
    private const val PREFS = "system"
    private const val OPENED_ELAPSED = "boot_open_elapsed"
    private const val OPENED_BOOT = "boot_open_boot"
    private const val SCREEN_OFF_ELAPSED = "screen_off_elapsed"
    private const val SCREEN_OFF_BOOT = "screen_off_boot"

    /** When this boot began by the wall clock, to tell a stored time of this boot from one before. */
    private fun bootStamp(): Long = System.currentTimeMillis() - SystemClock.elapsedRealtime()

    /** Whether a time saved as [elapsed] with [stamp] was in this boot; the clock may be set after it. */
    private fun thisBoot(elapsed: Long, stamp: Long): Boolean =
        elapsed in 0..SystemClock.elapsedRealtime() && abs(stamp - bootStamp()) < SAME_BOOT_SLACK_MS

    /** The screen went off: the next time it lights is a wake, and the target is due again. */
    fun noteScreenOff(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit {
            putLong(SCREEN_OFF_ELAPSED, SystemClock.elapsedRealtime()).putLong(SCREEN_OFF_BOOT, bootStamp())
        }
    }

    fun noteOpened(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit {
            putLong(OPENED_ELAPSED, SystemClock.elapsedRealtime()).putLong(OPENED_BOOT, bootStamp())
        }
    }

    /** False when the target was opened in this boot and the screen has not gone off since. */
    fun due(context: Context): Boolean {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val opened = prefs.getLong(OPENED_ELAPSED, -1L)
        if (opened < 0 || !thisBoot(opened, prefs.getLong(OPENED_BOOT, 0L))) return true
        val off = prefs.getLong(SCREEN_OFF_ELAPSED, -1L)
        return off >= 0 && thisBoot(off, prefs.getLong(SCREEN_OFF_BOOT, 0L)) && off > opened
    }

    private const val SAME_BOOT_SLACK_MS = 2 * 60 * 1000L
}
