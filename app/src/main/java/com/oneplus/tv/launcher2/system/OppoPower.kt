package com.oneplus.tv.launcher2.system

import android.os.SystemClock
import android.util.Log

/**
 * ColorOS TV 的待机 / 关机走固件服务 `tvapisvc`（`ITvApi.disPatchCommand`，见 [TvApi]）。
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
 * 这个 object 只负责「上膛」这笔账：电源菜单出现过之后的一小段时间里，随后到来的 QUICK 才被
 * 认作菜单那个「息屏」（改写由 HomeGuardService 里的观察者执行）。
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
        armedUntil = SystemClock.uptimeMillis() + WATCH_MS
    }

    fun armed(): Boolean = SystemClock.uptimeMillis() < armedUntil

    /** 待机模式：`"QUICK"` 快捷待机（黑屏不睡），`"DEPTH"` 真关机。 */
    fun setStandbyMode(mode: String) {
        if (TvApi.dispatch("ModulePower", CMD_STANDBY_MODE, arrayOf(mode))) {
            Log.i(TAG, "setStandbyMode($mode) sent to tvapisvc")
        }
    }

    /**
     * 真关机。发出去就把窗口关掉：真关机过程中若 12 秒内又来一个 QUICK（比如用户又按了电源），
     * 不该再补一发 DEPTH。
     */
    fun shutdown() {
        armedUntil = 0
        setStandbyMode("DEPTH")
    }
}
