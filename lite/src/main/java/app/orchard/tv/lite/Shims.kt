package app.orchard.tv.lite

import android.content.SharedPreferences
import kotlin.reflect.KProperty

// Just enough of Compose's state and AndroidX's prefs.edit {} for the full app's data files that
// Lite compiles as they are (LayoutStore, AppNames, CustomIcons; see lite/build.gradle.kts). In
// those files the state only counts changes; Lite reloads Home itself when they happen.

class MutableState<T>(var value: T)

fun <T> mutableStateOf(value: T) = MutableState(value)

fun mutableIntStateOf(value: Int) = MutableState(value)

operator fun <T> MutableState<T>.getValue(thisRef: Any?, property: KProperty<*>): T = value

operator fun <T> MutableState<T>.setValue(thisRef: Any?, property: KProperty<*>, v: T) {
    value = v
}

inline fun SharedPreferences.edit(commit: Boolean = false, action: SharedPreferences.Editor.() -> Unit) {
    val e = edit()
    e.action()
    if (commit) e.commit() else e.apply()
}
