package com.oneplus.tv.launcher2.system

import android.os.IBinder
import android.util.Log

/**
 * 固件服务 `tvapisvc`（接口 `com.oneplus.tvapi.ITvApi`）的唯一入口。
 *
 * 这台电视的背光、待机、关机都只能由它执行 —— Settings 里的那些键是影子数据，写得进去但面板
 * 和电源不动。它放行前会查调用方**进程名**，要求包含它白名单里的某一串（`com.heytap.tv.remote`、
 * `com.heytap.speechassist.tv` …）。本应用的主进程已经声明成
 * `android:process=":com.heytap.tv.remote"`，所以在这里直接调即可，不需要另开进程、也不需要 AIDL。
 *
 * `ServiceManager` 和 `ITvApi` 都是 @hide，只能反射。代理和两个方法句柄各查一次就存着：
 * 亮度每按一下键都会走 [dispatch]，反复 `Class.forName` 纯属浪费。
 */
object TvApi {

    private const val TAG = "TvApi"

    /** 服务代理。连的是系统服务，不会断，所以只存不清。 */
    @Volatile private var api: Any? = null

    @Volatile private var dispatchMethod: java.lang.reflect.Method? = null
    @Volatile private var queryMethod: java.lang.reflect.Method? = null

    private fun proxy(): Any? {
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

    /**
     * `disPatchCommand(module, cmdType, params, false)`，第四个参数固件恒传 false。
     * 返回 false 表示没能发出去（服务不在、反射失败），调用方可以据此决定要不要提示。
     */
    fun dispatch(module: String, cmdType: Int, params: Array<String>): Boolean {
        val target = proxy() ?: run {
            Log.w(TAG, "tvapisvc not reachable; $module/$cmdType dropped")
            return false
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
        }.getOrNull()?.also { dispatchMethod = it } ?: return false
        return runCatching {
            method.invoke(target, module, cmdType, params, false)
            true
        }.onFailure {
            Log.w(TAG, "disPatchCommand($module, $cmdType) failed", it)
        }.getOrDefault(false)
    }

    /**
     * `query(module, cmdType, out)`。返回值恒为 0（-1 只表示模块名不认识），真值写回传进去的数组。
     * AIDL 的 out 数组回程要求两边长度相等（`Parcel.readStringArray` 长度不符直接抛），长度由固件
     * 那边决定、看不出来，所以由调用方试探（见 [OppoBacklight]）。返回 null 表示这次调用失败。
     */
    fun query(module: String, cmdType: Int, out: Array<String>): Array<String>? {
        val target = proxy() ?: return null
        val method = queryMethod ?: runCatching {
            Class.forName("com.oneplus.tvapi.ITvApi")
                .getMethod(
                    "query",
                    String::class.java,
                    Int::class.javaPrimitiveType,
                    Array<String>::class.java
                )
        }.getOrNull()?.also { queryMethod = it } ?: return null
        return runCatching {
            method.invoke(target, module, cmdType, out)
            out
        }.getOrNull()
    }
}
