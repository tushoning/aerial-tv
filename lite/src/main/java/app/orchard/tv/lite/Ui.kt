package app.orchard.tv.lite

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Typeface
import android.os.Build
import android.util.DisplayMetrics
import android.util.TypedValue
import android.view.WindowManager
import android.widget.TextView
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * The 960×540 design canvas, as in the full app, but scaled by hand: Views take pixels, so every
 * size goes through [px]. 4.x TVs report 720p or 1080p UIs at 160-320 dpi, and this keeps the
 * layout the same on all of them.
 */
object Ui {
    /** Pixels per canvas unit. */
    var u = 1f
        private set
    var screenW = 960
        private set
    var screenH = 540
        private set
    /** The screen's height in canvas units: 540 on 16:9, more on a 4:3 box. */
    val canvasH get() = screenH / u

    private lateinit var assets: android.content.res.AssetManager

    fun init(context: Context) {
        // The whole screen, not the part left by a navigation bar (Android-x86, a few boxes).
        val dm = if (Build.VERSION.SDK_INT >= 17) {
            DisplayMetrics().also { (context.getSystemService(Context.WINDOW_SERVICE) as WindowManager).defaultDisplay.getRealMetrics(it) }
        } else {
            context.resources.displayMetrics
        }
        screenW = max(dm.widthPixels, dm.heightPixels)
        screenH = minOf(dm.widthPixels, dm.heightPixels)
        u = screenW / 960f
        assets = context.assets
    }

    fun px(v: Float): Float = v * u
    fun pxi(v: Float): Int = (v * u).roundToInt()

    // The full app's Palette (tvOS dark, elevated).
    const val WHITE = 0xFFFFFFFF.toInt()
    const val SECONDARY = 0x99EBEBF5.toInt()
    const val TERTIARY = 0x4DEBEBF5
    const val FOCUS = 0xFFF5F5F7.toInt()
    const val ON_FOCUS = 0xFF1D1D1F.toInt()
    const val FILL = 0x2E787880
    const val FILL_STRONG = 0x52787880
    const val HAIRLINE = 0x1AFFFFFF
    const val GREEN = 0xFF30D158.toInt()
    const val RED = 0xFFFF453A.toInt()
    /** A panel's color where there is no frosted wallpaper to make glass from. */
    const val GLASS = 0xE6202022.toInt()

    const val DIM = 0xB3FFFFFF.toInt()
    const val FAINT = 0x80FFFFFF.toInt()
    const val INK = ON_FOCUS
    const val PANEL = 0xF2232328.toInt()
    val bold: Typeface get() = font(600)
    val regular: Typeface get() = font(500)

    private val faces = HashMap<String, Typeface>()

    /**
     * Inter at [weight], with its optical size axis at [opticalSize] (14 for text, 32 for display),
     * as the full app's Theme. Font variations need Android 8.0; before that the full app uses the
     * system's sans-serif, and so does Lite: Roboto, bold from 600.
     */
    fun font(weight: Int, opticalSize: Float = 14f): Typeface = synchronized(faces) {
        val key = "$weight/$opticalSize"
        faces[key] ?: run {
            val face = if (Build.VERSION.SDK_INT >= 26 && ::assets.isInitialized) {
                runCatching {
                    Typeface.Builder(assets, "inter.ttf")
                        .setFontVariationSettings("'wght' $weight, 'opsz' $opticalSize")
                        .build()
                }.getOrNull()
            } else null
            (face ?: if (weight >= 600) Typeface.DEFAULT_BOLD else Typeface.DEFAULT).also { faces[key] = it }
        }
    }

    /** A paint for text in one of the full app's Type styles. */
    fun text(size: Float, weight: Int, color: Int = WHITE, display: Boolean = false): Paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.SUBPIXEL_TEXT_FLAG).apply {
        textSize = px(size)
        typeface = font(weight, if (display) 32f else 14f)
        this.color = color
    }

    /** Letter spacing in ems: [Paint.setLetterSpacing] from Android 5.0, by hand before. */
    fun width(text: String, paint: Paint, spacingEm: Float = 0f): Float {
        if (spacingEm == 0f) return paint.measureText(text)
        if (Build.VERSION.SDK_INT >= 21) { paint.letterSpacing = spacingEm; return paint.measureText(text).also { paint.letterSpacing = 0f } }
        return paint.measureText(text) + spacingEm * paint.textSize * (text.length - 1).coerceAtLeast(0)
    }

    fun draw(canvas: Canvas, text: String, x: Float, baseline: Float, paint: Paint, spacingEm: Float = 0f) {
        if (spacingEm == 0f) { canvas.drawText(text, x, baseline, paint); return }
        if (Build.VERSION.SDK_INT >= 21) {
            paint.letterSpacing = spacingEm
            canvas.drawText(text, x, baseline, paint)
            paint.letterSpacing = 0f
            return
        }
        var at = x
        val extra = spacingEm * paint.textSize
        for (i in text.indices) {
            val ch = text.substring(i, i + 1)
            canvas.drawText(ch, at, baseline, paint)
            at += paint.measureText(ch) + extra
        }
    }

    /** The cap height of Inter (and near enough Roboto's), as a share of the text size: the full app's capBox. */
    const val CAP_HEIGHT = 0.727f

    fun TextView.textPx(units: Float) = setTextSize(TypedValue.COMPLEX_UNIT_PX, px(units))
}
