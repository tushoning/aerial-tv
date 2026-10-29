package com.oneplus.tv.launcher2.system

import android.content.Context
import android.database.ContentObserver
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
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
 * `com.heytap.speechassist.tv` …）。本应用的清单把主进程直接声明成了
 * `android:process=":com.heytap.tv.remote"`，进程名因此是
 * `com.oneplus.tv.launcher2:com.heytap.tv.remote`，包含白名单串，在**本进程内**直接调用即可
 * （反射那一层收在 [TvApi] 里，和 [OppoPower] 共用）。
 *
 * 读这条腿走同一个服务：`query("ModulePicture", 1, ret)`，返回值恒为 0，真值写回 `ret`。
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

    /**
     * 记账值。-1 = 还不知道，别用 0 当"未知"：0 是合法的亮度（最低档），用它当哨兵会让
     * 进程刚起来时 [remembered] 直接返回 0，控制中心先显示 0 再被回读纠正过来。
     */
    @Volatile private var last = -1
    @Volatile private var appContext: Context? = null

    /** 每次 set 加一。query 带着发起时的号回来，号变了说明中途 set 过，那次回读作废。 */
    @Volatile private var version = 0
    @Volatile private var querying = false
    @Volatile private var queriedAt = 0L
    @Volatile private var lastSetAt = 0L

    /** 校准时发现面板真值和记账值不一样，把真值报出来（0..100），在主线程调用。 */
    @Volatile var onLevel: ((Int) -> Unit)? = null

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
            remember(ctx, value)
            onLevel?.invoke(value)
        }
    }

    /**
     * 回读面板真值的那条线程。`query` 是阻塞的 Binder 调用，放在主线程上会在打开控制中心时
     * 顶掉一帧；以前它绕道一个同进程的 Service，其实还是回主线程执行，等于没躲开。
     */
    private val worker by lazy {
        HandlerThread("OppoBacklight").apply { start() }
    }

    private val workerHandler by lazy { Handler(worker.looper) }

    private val mainHandler by lazy { Handler(Looper.getMainLooper()) }

    /** [usable] 的答案，查过一次就记住。 */
    @Volatile private var usableKnown: Boolean? = null

    /** 回读数组的槽位数：固件只写它认的那一格，长度对不上 AIDL 会抛，见 [read]。 */
    @Volatile private var slots = 1

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

    /**
     * 这台电视有没有那套固件接口。没有就走上游原来的分支。
     *
     * 每按一下亮度键都会问一次，所以查过一次就记住 —— `getPackageInfo` 要走一次
     * PackageManager，没必要按着键反复问。
     */
    fun usable(context: Context): Boolean {
        usableKnown?.let { return it }
        val known = runCatching { context.packageManager.getPackageInfo(SERVICE_PKG, 0) }.isSuccess
        usableKnown = known
        return known
    }

    /** 当前亮度，0..100。先给记账值，真值由 [calibrate] 稍后校正。 */
    fun level(context: Context): Int {
        watch(context)
        val value = remembered(context)
        calibrate(context)
        return value
    }

    /**
     * 现在就要一个准的值：向固件查一次（阻塞几毫秒），查不到才退回记账值。
     *
     * 只在「打开控制中心」这种必须一次就对的路径用 —— [level] 是先给记账值、回读异步纠正，
     * 面板刚画出来的那一两帧还是旧值。调用方必须不在主线程上（HomeViewModel 走 Default）。
     */
    fun levelNow(context: Context): Int {
        watch(context)
        // 刚设过就别问：面板还在渐变，读回来的是中间电平，此时记账值才是对的。
        if (SystemClock.uptimeMillis() - lastSetAt < SETTLE_MS) return remembered(context)
        // 作废在途的异步回读，免得它回来把刚读到的值改回旧的。
        version++
        val panel = read()
        if (panel !in 0..100) return remembered(context)
        remember(context, panel)
        return panel
    }

    /** 记下这个值：内存 + 本地，进程重启后不必再猜一个 50。 */
    private fun remember(context: Context, value: Int) {
        last = value
        runCatching {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putInt(KEY, value).apply()
        }
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
        remember(context, value)
        // 顺手把快照也写一份：设置应用在跑时会把它推给面板（就是刚设的那个值，无副作用），
        // 它不在跑时就只是冷启动快照，不同步的话重启后亮度会跳回旧值。
        // 没有 WRITE_SECURE_SETTINGS 时静默失败。
        runCatching { Settings.Global.putInt(context.contentResolver, GLOBAL_KEY, value) }
        push(value)
    }

    /**
     * 要一次面板真值。结果不在这里等——[level] 已经先把记账值交出去了，回来再改。
     * 一次没回完不叠第二次，免得连按左右键时排一队 Binder。
     */
    private fun calibrate(context: Context) {
        appContext = context.applicationContext
        val now = SystemClock.uptimeMillis()
        if (querying && now - queriedAt < QUERY_TIMEOUT_MS) return
        querying = true
        queriedAt = now
        val asked = version
        workerHandler.post {
            val value = read()
            mainHandler.post { onRead(value, asked) }
        }
    }

    /** 面板真值回来了。对不上就改记账，并让界面跟上。 */
    private fun onRead(value: Int, asked: Int) {
        querying = false
        if (asked != version) return
        if (SystemClock.uptimeMillis() - lastSetAt < SETTLE_MS) return
        if (value !in 0..100) return
        if (value == last) return
        val ctx = appContext ?: return
        // 只记到本地，进程重启后不必再猜一个 50。
        //
        // 这里**不再回写 tv_picture_backlight**：那个键在设置应用活着时能推动面板（见文件头），
        // 而校准读到的可能是渐变中途的电平，回写等于把面板钉在一个我们并不想要的值上。它是
        // 「外界改过亮度的信号」，我们只读不写 —— 要写也只在 [set] 里写我们自己刚设的那个值。
        remember(ctx, value)
        onLevel?.invoke(value)
    }

    private fun push(percent: Int) {
        TvApi.dispatch("ModulePicture", 1, arrayOf(percent.toString()))
    }

    /**
     * 面板真值。`query` 的返回值恒为 0（-1 只表示模块名不认识），真值写回传进去的数组；AIDL 的
     * out 数组回程要求两边长度相等（`Parcel.readStringArray` 长度不符直接抛），长度由固件那边
     * 决定、看不出来，只能试：先用上次成功的槽位数，不对再换。第一格合法值（0..100）就是背光。
     */
    private fun read(): Int {
        for (n in intArrayOf(slots, 1, 2, 4, 8)) {
            val back = TvApi.query("ModulePicture", 1, Array(n) { "" }) ?: continue
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
