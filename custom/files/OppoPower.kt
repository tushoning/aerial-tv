package com.oneplus.tv.launcher2.system

import android.os.IBinder
import android.os.SystemClock
import android.util.Log

/**
 * ColorOS TV 的待机 / 关机走固件服务 `tvapisvc`（`ITvApi.disPatchCommand`）。
 *
 * 实测到的映射（OPPOCNM632，Android 9，从 logcat 抓的）：
 *
 * ```
 * GlobalActionsDialog: is 9632 machine mDialog.isShowing so set to QUICK mode
 * TvPowerManagerImpl:  setPowerMode QUICK
 * TvApiManager##:      disPatchCommand module = ModulePower, type = 4
 * ModulePower:         Enter disPatchCommand cmdType 4 params QUICK
 * ModulePower:         doSetStandbyMode QUICK …          ← 快捷待机（黑屏，Android 不睡）
 * ```
 *
 * 参数换成 `"DEPTH"` 就是真关机 —— SystemUI 自己的字符串写着
 * `setpowermode DEPTH, do not need to do something to wifi`（真关机后不用再收拾 wifi）。
 *
 * 该服务按**进程名**放行（要求包含 `com.heytap.tv.remote` 之类白名单串）。本应用的主进程
 * 已经挂在 `:com.heytap.tv.remote` 上（manifest 的 `android:process`），所以这里直接调即可，
 * 不像背光那样要再跨一次进程。
 */
object OppoPower {

    private const val TAG = "OppoPower"

    /** `CMD_STANDBY_MODE`：实测 cmdType = 4。 */
    private const val CMD_STANDBY_MODE = 4

    /**
     * 电源菜单显示后还认账多久。
     *
     * **这个数有上界，不能随便加大**：菜单自己带一个 15 秒倒计时，到点会执行「息屏」
     * （实测：菜单 15:51:27 弹出，15:51:42 自动 QUICK，中间没有任何按键）。倒计时那条得
     * 保持息屏，所以窗口必须明显小于 15 秒。12 秒是「手慢也来得及」和「不碰倒计时」之间的折中。
     */
    const val WATCH_MS = 12_000L

    private const val ARM_MS = WATCH_MS

    /** 电源菜单刚显示过的截止时刻（uptime ms）。只有这段里的 QUICK 才是菜单那个「息屏」。 */
    @Volatile
    private var armedUntil = 0L

    /**
     * 用户在菜单里按过键 = 这次「息屏」是他自己选的，保持息屏。
     *
     * 判据用**按键**而不是窗口焦点：实测两条路的焦点都在 QUICK 之后约 30ms 才回来，分不开；
     * 而菜单里按方向键 / OK 会产生按键事件（本应用的无障碍服务开了按键过滤），按电源键不会。
     */
    fun cancel() {
        armedUntil = 0
    }

    /** 电源菜单（那个 Dialog）显示过了：接下来一小段时间里的 QUICK 待机是它触发的。 */
    fun arm() {
        armedUntil = SystemClock.uptimeMillis() + ARM_MS
    }

    fun armed(): Boolean = SystemClock.uptimeMillis() < armedUntil

    /** 服务代理，第一次用到时连一次就存着（连的是固件服务，不会断）。 */
    @Volatile
    private var api: Any? = null

    /** `disPatchCommand` 的方法句柄。反射查一次就够，别每次关机都查。 */
    @Volatile
    private var dispatchMethod: java.lang.reflect.Method? = null

    private fun connect(): Any? {
        api?.let { return it }
        val made = runCatching {
            val binder = Class.forName("android.os.ServiceManager")
                .getMethod("getService", String::class.java)
                .invoke(null, "tvapisvc") as? IBinder ?: return null
            Class.forName("com.oneplus.tvapi.ITvApi\$Stub")
                .getMethod("asInterface", IBinder::class.java)
                .invoke(null, binder)
        }.getOrNull() ?: return null
        api = made
        return made
    }

    /** 待机模式：`"QUICK"` 快捷待机（黑屏不睡），`"DEPTH"` 真关机。 */
    fun setStandbyMode(mode: String) {
        val target = connect() ?: run {
            Log.w(TAG, "tvapisvc not reachable; $mode not sent")
            return
        }
        val method = dispatchMethod ?: runCatching {
            Class.forName("com.oneplus.tvapi.ITvApi")
                .getMethod(
                    "disPatchCommand",
                    String::class.java,
                    Int::class.javaPrimitiveType,
                    Array<String>::class.java,
                    Boolean::class.javaPrimitiveType
                )
        }.getOrNull()?.also { dispatchMethod = it } ?: return
        runCatching {
            method.invoke(target, "ModulePower", CMD_STANDBY_MODE, arrayOf(mode), false)
            Log.i(TAG, "setStandbyMode($mode) sent to tvapisvc")
        }.onFailure { Log.w(TAG, "setStandbyMode($mode) failed", it) }
    }

    /** 真关机。 */
    fun shutdown() = setStandbyMode("DEPTH")
}
