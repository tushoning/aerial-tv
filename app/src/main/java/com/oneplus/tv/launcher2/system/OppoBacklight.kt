package com.oneplus.tv.launcher2.system

import android.app.Service
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.database.ContentObserver
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Message
import android.os.Messenger
import android.os.SystemClock
import android.provider.Settings

/**
 * OPPO ColorOS TV 的屏幕亮度。
 *
 * 这台电视的面板背光有两套"真相"，而且互不相通：
 *
 * - 设置里「图像与亮度 › 屏幕亮度」那个条，写的是 `PictureManagerImpl.setBacklight()`，直接下到
 *   `g_disp__disp_back_light`，面板立刻变；它不写任何 Settings。
 * - Settings.Global 的 `tv_picture_backlight`：**两个方向要分开看**。
 *   - 我们写它 → 面板动不动，取决于设置应用 `com.android.tv.settings` 在不在跑。它常年活着并
 *     盯着这个键，收到变化就用 `PictureManagerImpl.setBacklight()` 推给面板（实测写 42，屏幕明显
 *     变亮，随后固件 query 也回报 42）；它不在跑时，写它就只是一份惰性快照。这个进程**没有**
 *     声明 persistent，所以「能推动面板」是现状，不是契约 —— **不能拿它当控制通道**。
 *   - 电视自己改 → **会**写这个键（实测 1 → 2 跟着变，且没有任何广播）。这是目前唯一能告诉我
 *     们「外界改过亮度」的信号，[watcher] 就是靠它。
 *   结论：这个键只用于**感知**（读）和**快照**，控制一律走下面的固件通道。
 *
 * 所以普通的 Settings 写入在这台机器上完全无效，这也是上游在这里显示「在图像设置里调」的原因。
 *
 * 真正能用的通道是固件的 `tvapisvc` 系统服务（接口 `com.oneplus.tvapi.ITvApi`），小布助手就是这么
 * 调的：`disPatchCommand("ModulePicture", 1, [亮度], false)`。
 *
 * 但该服务放行前会查调用方**进程名**，要求包含它白名单里的某一串（`com.heytap.tv.remote`、
 * `com.heytap.speechassist.tv` …）。所以本文件里的服务在清单中声明了
 * `android:process=":com.heytap.tv.remote"`——进程名因此是
 * `com.oneplus.tv.launcher2:com.heytap.tv.remote`，包含白名单串，调用才会被接受。
 *
 * 主界面进程不在白名单里，所以它只负责记账和转发，真正的调用由那个进程里的
 * [OppoBrightnessService] 完成，两者用 [Messenger] 通信（不需要写 AIDL）。
 *
 * 读这条腿走的是同一个服务：`query("ModulePicture", 1, ret)`，返回值恒为 0，真值写回 `ret`。
 * 只有固件知道面板当下的真实电平——用户用小布或设置页改过之后，记账值就是错的，所以 [level]
 * 在给出记账值的同时顺手要一次真值（[calibrate]），对不上就改记账并回调 [onLevel]。
 *
 * 但有两个时刻的回读不能信：`set` 刚刚发出去时面板还在渐变，MTK 的 getConfigValue 会读到中间
 * 电平；以及在途的 query 是 `set` 之前发出去的，读的是旧电平。前者用 [lastSetAt] 的时间窗挡，
 * 后者用 [version] 挡。
 */
object OppoBacklight {

    private const val SERVICE_PKG = "com.heytap.tvapiservice"
    private const val GLOBAL_KEY = "tv_picture_backlight"
    private const val PREFS = "aerial"
    private const val KEY = "oppo_backlight"

    /** 面板渐变期间读到的值不算：设完之后这段时间内的回读一律丢弃。 */
    private const val SETTLE_MS = 800L

    /** query 迟迟不回（tvapisvc 没起、Binder 卡住）时，多久之后允许再发一次。 */
    private const val QUERY_TIMEOUT_MS = 2_000L

    const val MSG_SET = 1
    const val MSG_QUERY = 2
    const val MSG_LEVEL = 3

    @Volatile private var messenger: Messenger? = null
    @Volatile private var binding = false
    @Volatile private var pending: Int? = null
    @Volatile private var last = 0
    @Volatile private var appContext: Context? = null

    /** 每次 set 加一。query 带着发起时的号回来，号变了说明中途 set 过，那次回读作废。 */
    @Volatile private var version = 0
    @Volatile private var querying = false
    @Volatile private var queriedAt = 0L
    @Volatile private var lastSetAt = 0L
    @Volatile private var pendingQuery = false

    /** 校准时发现面板真值和记账值不一样，把真值报出来（0..100），在主线程调用。 */
    @Volatile var onLevel: ((Int) -> Unit)? = null

    private val reply = Messenger(Handler(Looper.getMainLooper()) { msg ->
        if (msg.what == MSG_LEVEL) onRead(msg.arg1, msg.arg2)
        true
    })

    /**
     * 电视自己的「图像与亮度」页改亮度时会写 Settings.Global 的 tv_picture_backlight
     * （实测 1 → 2 这个键跟着变，而它没有广播），所以这里盯住它：一改就把值接过来，
     * 不用等下一次按键或打开控制中心才对齐。小布若走同一条路也会触发。
     */
    @Volatile private var watching = false

    private val watcher = object : ContentObserver(Handler(Looper.getMainLooper())) {
        override fun onChange(selfChange: Boolean) {
            val ctx = appContext ?: return
            // 我们自己 set 时也写这个键（给设置页冷启动留的快照），那次回调要忽略：
            // 面板还在渐变，而且记账值已经是最新。
            if (SystemClock.uptimeMillis() - lastSetAt < SETTLE_MS) return
            val value = runCatching {
                Settings.Global.getInt(ctx.contentResolver, GLOBAL_KEY, -1)
            }.getOrDefault(-1)
            if (value !in 0..100 || value == last) return
            last = value
            runCatching {
                ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putInt(KEY, value).apply()
            }
            onLevel?.invoke(value)
        }
    }

    /** 第一次拿到 context 时挂上，只挂一次。 */
    private fun watch(context: Context) {
        if (watching) return
        watching = true
        appContext = context.applicationContext
        runCatching {
            appContext?.contentResolver?.registerContentObserver(
                Settings.Global.getUriFor(GLOBAL_KEY), false, watcher,
            )
        }
    }

    /** 这台电视有没有那套固件接口。没有就走上游原来的分支。 */
    fun usable(context: Context): Boolean =
        runCatching { context.packageManager.getPackageInfo(SERVICE_PKG, 0) }.isSuccess

    /** 当前亮度，0..100。先给记账值，真值由 [calibrate] 稍后校正。 */
    fun level(context: Context): Int {
        watch(context)
        val value = remembered(context)
        calibrate(context)
        return value
    }

    private fun remembered(context: Context): Int {
        if (last in 0..100) return last
        val saved = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt(KEY, -1)
        val db = runCatching {
            Settings.Global.getInt(context.contentResolver, GLOBAL_KEY, -1)
        }.getOrDefault(-1)
        last = when {
            saved in 0..100 -> saved
            db in 0..100 -> db
            else -> 50
        }
        return last
    }

    /**
     * 调到 [percent]（0..100）。范围和电视系统那条一致：0 是最低（画面几乎全黑），100 是最高。
     * 0 能不能真的全黑看固件，这台机器上系统那条自己也允许拖到 0。
     */
    fun set(context: Context, percent: Int) {
        watch(context)
        val value = percent.coerceIn(0, 100)
        // 记下这一笔：在途的 query 读的是旧电平，之后 SETTLE_MS 内的回读也是渐变中的值。
        version++
        lastSetAt = SystemClock.uptimeMillis()
        last = value
        // 顺手把快照也写一份：设置应用在跑时会把它推给面板（就是刚设的那个值，无副作用），
        // 它不在跑时就只是冷启动快照，不同步的话重启后亮度会跳回旧值。
        // 没有 WRITE_SECURE_SETTINGS 时静默失败。
        runCatching { Settings.Global.putInt(context.contentResolver, GLOBAL_KEY, value) }
        runCatching {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().putInt(KEY, value).apply()
        }
        pending = value
        val ready = messenger
        if (ready != null) {
            flush(ready)
            return
        }
        bind(context)
    }

    /**
     * 要一次面板真值。结果不在这里等——[level] 已经先把记账值交出去了，回来再改。
     * 一次没回完不叠第二次，免得连按左右键时排一队 Binder。
     */
    private fun calibrate(context: Context) {
        appContext = context.applicationContext
        val now = SystemClock.uptimeMillis()
        if (querying && now - queriedAt < QUERY_TIMEOUT_MS) return
        val ready = messenger
        if (ready == null) {
            // 还没连上：先连，连上之后 onServiceConnected 补发。
            pendingQuery = true
            bind(context)
            return
        }
        querying = true
        queriedAt = now
        val sent = runCatching {
            ready.send(Message.obtain(null, MSG_QUERY).apply {
                arg1 = version
                replyTo = reply
            })
        }.isSuccess
        if (!sent) querying = false
    }

    /** 面板真值回来了。对不上就改记账，并让界面跟上。 */
    private fun onRead(value: Int, asked: Int) {
        querying = false
        if (asked != version) return
        if (SystemClock.uptimeMillis() - lastSetAt < SETTLE_MS) return
        if (value !in 0..100) return
        if (value == last) return
        last = value
        val ctx = appContext ?: return
        // 只记到本地，进程重启后不必再猜一个 50。
        //
        // 这里**不再回写 tv_picture_backlight**：那个键在设置应用活着时能推动面板（见文件头），
        // 而校准读到的可能是渐变中途的电平，回写等于把面板钉在一个我们并不想要的值上。它是
        // 「外界改过亮度的信号」，我们只读不写 —— 要写也只在 [set] 里写我们自己刚设的那个值。
        runCatching {
            ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().putInt(KEY, value).apply()
        }
        onLevel?.invoke(value)
    }

    private fun flush(m: Messenger) {
        val value = pending ?: return
        pending = null
        runCatching { m.send(Message.obtain(null, MSG_SET, value, 0)) }
    }

    private fun bind(context: Context) {
        if (binding || messenger != null) return
        val app = context.applicationContext
        binding = true
        val ok = runCatching {
            app.bindService(
                Intent(app, OppoBrightnessService::class.java),
                connection,
                Context.BIND_AUTO_CREATE
            )
        }.getOrDefault(false)
        if (!ok) binding = false
    }

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            binding = false
            val m = Messenger(binder)
            messenger = m
            flush(m)
            if (pendingQuery) {
                pendingQuery = false
                appContext?.let { calibrate(it) }
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            messenger = null
        }
    }
}

/**
 * 跑在 `:com.heytap.tv.remote` 进程里的那一半。它的唯一工作是把亮度值交给固件的 `tvapisvc`，
 * 以及把面板的真实电平读回来（`query`）。
 */
class OppoBrightnessService : Service() {

    private var api: Any? = null
    private var queryMethod: java.lang.reflect.Method? = null

    /** 写命令的方法，第一次用到时查一次就存着。 */
    private var dispatchMethod: java.lang.reflect.Method? = null

    /** 回读数组的槽位数：固件只写它认的那一格，长度对不上 AIDL 会抛，见 [reading]。 */
    private var slots = 1

    private val handler = object : Handler(Looper.getMainLooper()) {
        override fun handleMessage(msg: Message) {
            when (msg.what) {
                OppoBacklight.MSG_SET -> push(msg.arg1)
                OppoBacklight.MSG_QUERY -> read(msg.arg1, msg.replyTo)
            }
        }
    }

    private val bridge = Messenger(handler)

    override fun onBind(intent: Intent?): IBinder = bridge.binder

    override fun onCreate() {
        super.onCreate()
        connect()
    }

    /** `ServiceManager` 是 @hide，只能反射；接口类在 boot classpath 上，能直接加载。 */
    private fun connect() {
        if (api != null) return
        runCatching {
            val binder = Class.forName("android.os.ServiceManager")
                .getMethod("getService", String::class.java)
                .invoke(null, "tvapisvc") as? IBinder ?: return
            api = Class.forName("com.oneplus.tvapi.ITvApi\$Stub")
                .getMethod("asInterface", IBinder::class.java)
                .invoke(null, binder)
        }
    }

    /**
     * 写亮度这条路每次按键都走，所以反射查方法只做一次（`query` 那边早就是这样了）。
     * `Class.forName` + `getMethod` 都在主线程上，按一次键查一次纯属浪费。
     */
    private fun push(percent: Int) {
        connect()
        val target = api ?: return
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
        runCatching { method.invoke(target, "ModulePicture", 1, arrayOf(percent.toString()), false) }
    }

    private fun read(version: Int, caller: Messenger?) {
        connect()
        val target = api
        val level = if (target == null) -1 else reading(target)
        if (caller == null) return
        runCatching {
            caller.send(Message.obtain(null, OppoBacklight.MSG_LEVEL).apply {
                arg1 = level
                arg2 = version
            })
        }
    }

    /**
     * `query` 的返回值恒为 0（-1 只表示模块名不认识），真值写回传进去的数组。而 AIDL 的 out 数组
     * 回程要求两边长度相等（Parcel.readStringArray 长度不符直接抛），长度由固件那边决定，看不出来，
     * 只能试：先用上次成功的槽位数，不对再换。读到的第一格合法值（0..100）就是背光。
     */
    private fun reading(target: Any): Int {
        val method = queryMethod ?: runCatching {
            Class.forName("com.oneplus.tvapi.ITvApi")
                .getMethod(
                    "query",
                    String::class.java,
                    Int::class.javaPrimitiveType,
                    Array<String>::class.java
                )
        }.getOrNull()?.also { queryMethod = it } ?: return -1
        for (n in intArrayOf(slots, 1, 2, 4, 8)) {
            val back = Array(n) { "" }
            if (runCatching { method.invoke(target, "ModulePicture", 1, back) }.isFailure) continue
            val value = back.asSequence()
                .mapNotNull { it.trim().toIntOrNull() }
                .firstOrNull { it in 0..100 }
            if (value != null) {
                slots = n
                return value
            }
        }
        return -1
    }
}
