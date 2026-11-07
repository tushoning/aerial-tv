package app.orchard.tv.lite

import android.content.Context
import android.media.AudioManager
import android.media.SoundPool
import android.os.Build
import android.provider.Settings
import android.view.KeyEvent

/**
 * Interface sounds, as the full app's system/Sounds.kt: the TV's own sound effects (each TV sounds
 * as its own menus, and its key sound switch silences them), Aerial's own set (for TVs that ship
 * with key sounds off), or none; chosen in Personalize. Lite plays one per key press: a tick for
 * the arrows, a click for OK, one for Back.
 */
object Sounds {
    private var audio: AudioManager? = null
    private lateinit var app: Context
    private var pool: SoundPool? = null
    private var move = 0
    private var select = 0
    private var back = 0

    fun init(context: Context) {
        app = context.applicationContext
        audio = app.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
        prepare()
    }

    /** Loads Aerial's set ahead of the first press when it is the one chosen; loading takes a moment. */
    fun prepare() {
        if (LiteSettings.uiSounds == UiSounds.Aerial) ownPool()
    }

    @Suppress("DEPRECATION")
    private fun ownPool(): SoundPool {
        pool?.let { return it }
        // SoundPool.Builder is Android 5.0; the old constructor works everywhere.
        return SoundPool(3, AudioManager.STREAM_SYSTEM, 0).also {
            move = it.load(app, R.raw.ui_move, 1)
            select = it.load(app, R.raw.ui_select, 1)
            back = it.load(app, R.raw.ui_back, 1)
            pool = it
        }
    }

    fun key(code: Int) {
        // The TV's effect, and Aerial's sound for it: 0 a move, 1 select, 2 back.
        val (effect, own) = when (code) {
            KeyEvent.KEYCODE_DPAD_UP -> AudioManager.FX_FOCUS_NAVIGATION_UP to 0
            KeyEvent.KEYCODE_DPAD_DOWN -> AudioManager.FX_FOCUS_NAVIGATION_DOWN to 0
            KeyEvent.KEYCODE_DPAD_LEFT -> AudioManager.FX_FOCUS_NAVIGATION_LEFT to 0
            KeyEvent.KEYCODE_DPAD_RIGHT -> AudioManager.FX_FOCUS_NAVIGATION_RIGHT to 0
            KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER -> AudioManager.FX_KEY_CLICK to 1
            KeyEvent.KEYCODE_BACK -> (if (Build.VERSION.SDK_INT >= 33) AudioManager.FX_BACK else AudioManager.FX_KEY_CLICK) to 2
            else -> return
        }
        when (LiteSettings.uiSounds) {
            UiSounds.Off -> Unit
            // The volume form, with the key sound switch checked here: Hisense changed the
            // one-argument form to take only its own effects (the full app's issue #262).
            UiSounds.Tv -> {
                val on = runCatching { Settings.System.getInt(app.contentResolver, Settings.System.SOUND_EFFECTS_ENABLED, 1) != 0 }.getOrDefault(true)
                if (on) audio?.playSoundEffect(effect, -1f)
            }
            UiSounds.Aerial -> {
                val p = ownPool()
                val (id, volume) = when (own) { 0 -> move to 0.7f; 1 -> select to 1f; else -> back to 0.9f }
                if (id != 0) p.play(id, volume, volume, 1, 0, 1f)
            }
        }
    }
}
