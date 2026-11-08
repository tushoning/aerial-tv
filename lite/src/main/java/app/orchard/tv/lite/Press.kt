package app.orchard.tv.lite

import android.view.KeyEvent

/**
 * The OK key as the full app's Pressable handles it: a click fires on release only for a press that
 * began on the same control, so the release of the long press that opened a menu does not choose
 * the menu's first row. A held key (key repeats) is a long press.
 */
class Press {
    private var down = false
    private var long = false

    /** Handles [e] when it is an OK key; true when it was one. */
    fun onKey(e: KeyEvent, click: () -> Unit, longPress: (() -> Unit)? = null): Boolean {
        if (!isOk(e.keyCode)) return false
        if (e.action == KeyEvent.ACTION_DOWN) {
            if (e.repeatCount == 0) {
                down = true
                long = false
            } else if (down && !long && longPress != null) {
                long = true
                longPress()
            }
        } else if (e.action == KeyEvent.ACTION_UP) {
            val fire = down && !long
            down = false
            long = false
            if (fire) click()
        }
        return true
    }

    fun reset() {
        down = false
        long = false
    }

    companion object {
        fun isOk(code: Int) = code == KeyEvent.KEYCODE_DPAD_CENTER || code == KeyEvent.KEYCODE_ENTER ||
            code == KeyEvent.KEYCODE_NUMPAD_ENTER || code == KeyEvent.KEYCODE_BUTTON_A
    }
}
