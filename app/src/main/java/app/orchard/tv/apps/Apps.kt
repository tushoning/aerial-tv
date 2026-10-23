package app.orchard.tv.apps

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.ActivityInfo
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import app.orchard.tv.system.TvSource
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext

data class AppEntry(
    val packageName: String,
    val label: String,
    val component: ComponentName,
    /** Launched with CATEGORY_LEANBACK_LAUNCHER when the app has a TV entry, else CATEGORY_LAUNCHER. */
    val leanback: Boolean,
    /** False for apps on the system image, which can be disabled but not uninstalled. */
    val removable: Boolean,
    val art: TileArt,
    /** Set for a TV input pinned to Home (HDMI, tuner...) rather than an app. */
    val source: TvSource? = null,
    /** Set for a folder's tile; [packageName] is then the folder's key. */
    val folder: Folder? = null,
) {
    val key: String get() = packageName

    fun launchIntent(): Intent = source?.let { Intent(it.intent) } ?: Intent(Intent.ACTION_MAIN)
        .addCategory(if (leanback) Intent.CATEGORY_LEANBACK_LAUNCHER else Intent.CATEGORY_LAUNCHER)
        .setComponent(component)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
}

/**
 * A Home tile for a TV input. Its key never collides with a package name. A device or name chosen
 * for the input titles its caption; a device gives the tile its mark alone, a typed name titles the
 * tile with the port underneath. A picture from the phone page covers it.
 */
fun sourceEntry(context: Context, source: TvSource): AppEntry {
    val key = SOURCE_KEY_PREFIX + source.id
    val look = SourceLooks.of(source.id)
    val name = look?.name ?: look?.device?.let { context.getString(it.label) }
    // Home arranges its tiles on every move and hide; the art is made again only when it changes.
    val made = SourceArt(look, source.label, source.deviceName, CustomIcons.stamp(key), TileArtFactory.W)
    val art = synchronized(sourceArt) { sourceArt[key]?.takeIf { it.first == made }?.second } ?: run {
        val custom = CustomIcons.load(key)
        (if (custom != null) TileArtFactory.fromBanner(android.graphics.drawable.BitmapDrawable(android.content.res.Resources.getSystem(), custom))
        // A chosen device's mark goes on alone, its name on the caption; a typed name still shows.
        else if (look?.device != null && look.name == null) TileArtFactory.forSource(source.kind, null, null, look.device)
        else if (name != null) TileArtFactory.forSource(source.kind, name, source.label, look?.device)
        else TileArtFactory.forSource(source.kind, source.label, source.deviceName))
            .also { synchronized(sourceArt) { sourceArt[key] = made to it } }
    }
    return AppEntry(
        packageName = key,
        label = name ?: source.label,
        component = ComponentName("input", source.id),
        leanback = false,
        removable = false,
        art = art,
        source = source,
    )
}

private data class SourceArt(val look: SourceLook?, val label: String, val device: String?, val stamp: Long, val width: Int)
private val sourceArt = HashMap<String, Pair<SourceArt, TileArt>>()

const val SOURCE_KEY_PREFIX = "input:"

/** A Home tile for [folder], with [art] made from the apps in it. */
fun folderEntry(folder: Folder, art: TileArt): AppEntry = AppEntry(
    packageName = folder.key,
    label = folder.name,
    component = ComponentName("folder", folder.id),
    leanback = false,
    removable = false,
    art = art,
    folder = folder,
)

object AppCatalog {
    /** Xiaomi's app store declares only this category, not CATEGORY_LAUNCHER (MiTV-MFTR0). */
    private const val CATEGORY_APPSTORE_LAUNCHER = "android.intent.category.APPSTORE_LAUNCHER"

    /**
     * Firmware apps that the maker's own launcher shows but that declare no launcher category at
     * all, so no query finds them. Verified on Xiaomi MiTV-MFTR0.
     */
    private val UNLISTED_ENTRIES = listOf(
        ComponentName("com.xiaomi.mitv.mediaexplorer", "com.xiaomi.mitv.mediaexplorer.NewScraperMainEntryActivity"), // 高清播放器
        ComponentName("com.xiaomi.smarthome.tv", "com.xiaomi.smarthome.tv.ui.MainActivity"), // 米家
    )

    /**
     * Firmware apps drawn as Aerial's own tiles (see [TileArtFactory.forSystem]). None of them
     * declares a standard app category, so they are listed by package, each checked on its TV.
     */
    private val SYSTEM_TILES = mapOf(
        // Xiaomi MiTV-MFTR0
        "com.xiaomi.mitv.settings" to SystemTile.Settings,
        "com.mitv.gallery" to SystemTile.Photos,
        "com.xiaomi.mitv.calendar" to SystemTile.Calendar,
        "com.xiaomi.tweather" to SystemTile.Weather,
        "com.xiaomi.mitv.smartshare" to SystemTile.Cast,
        "com.xiaomi.mitv.tvmanager" to SystemTile.Security,
        "com.xiaomi.mitv.appstore" to SystemTile.Store,
        "com.xiaomi.mitv.mediaexplorer" to SystemTile.Media,
        // TCL MT5879 (China)
        "com.tcl.settings" to SystemTile.Settings,
        "com.tcl.tvsmartalbum" to SystemTile.Photos,
        "com.tcl.common.weather" to SystemTile.Weather,
        "com.tcl.MultiScreenInteraction_TV" to SystemTile.Cast,
        "com.tcl.guard" to SystemTile.Security,
        "com.tcl.appmarket2" to SystemTile.Store,
        "com.tcl.ui_mediaCenter" to SystemTile.Media,
        "com.tcl.browser" to SystemTile.Browser,
    )

    /**
     * Firmwares that do declare the standard categories. Only for system apps without a TV
     * banner: a banner is artwork made for the TV (Google TV's own apps have them) and wins.
     */
    private val SYSTEM_CATEGORIES = listOf(
        Intent.CATEGORY_APP_GALLERY to SystemTile.Photos,
        Intent.CATEGORY_APP_CALENDAR to SystemTile.Calendar,
        Intent.CATEGORY_APP_BROWSER to SystemTile.Browser,
        Intent.CATEGORY_APP_MARKET to SystemTile.Store,
        CATEGORY_APPSTORE_LAUNCHER to SystemTile.Store,
    )

    private class Cached(val stamp: Long, val width: Int, val label: String, val art: TileArt)
    private val cache = HashMap<String, Cached>()

    /** The apps of the last load, for the phone page. */
    @Volatile var loaded: List<AppEntry> = emptyList(); private set

    /** The TV inputs pinned to Home as Home last arranged them, for the phone page. */
    @Volatile var pinnedSources: List<AppEntry> = emptyList()

    /**
     * Every app with a TV entry, plus apps that only have a phone-style entry. China-market firmwares
     * (Xiaomi's settings, gallery, weather...) expose many of their own apps with CATEGORY_LAUNCHER only,
     * and a few with no launcher category at all (see [UNLISTED_ENTRIES]).
     * One tile per package; the TV entry wins.
     */
    suspend fun load(context: Context): List<AppEntry> = withContext(Dispatchers.Default) {
        TileDiskCache.init(context)
        val pm = context.packageManager
        val found = LinkedHashMap<String, Pair<ActivityInfo, Boolean>>()
        pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LEANBACK_LAUNCHER), 0)
            .forEach { found.putIfAbsent(it.activityInfo.packageName, it.activityInfo to true) }
        pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0)
            .forEach { found.putIfAbsent(it.activityInfo.packageName, it.activityInfo to false) }
        pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(CATEGORY_APPSTORE_LAUNCHER), 0)
            .forEach { found.putIfAbsent(it.activityInfo.packageName, it.activityInfo to false) }
        for (component in UNLISTED_ENTRIES) {
            val info = runCatching { pm.getActivityInfo(component, 0) }.getOrNull() ?: continue
            if (info.exported && info.isEnabled && info.applicationInfo.enabled) found.putIfAbsent(info.packageName, info to false)
        }
        found.remove(context.packageName)

        val byCategory = HashMap<String, SystemTile>()
        for ((category, tile) in SYSTEM_CATEGORIES) {
            pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(category), 0)
                .forEach { byCategory.putIfAbsent(it.activityInfo.packageName, tile) }
        }
        pm.resolveActivity(Intent(android.provider.Settings.ACTION_SETTINGS), 0)
            ?.let { byCategory.putIfAbsent(it.activityInfo.packageName, SystemTile.Settings) }

        coroutineScope {
            found.values.map { (info, leanback) ->
                async { runCatching { entry(pm, info, leanback, byCategory) }.getOrNull() }
            }.awaitAll().filterNotNull()
        }.also {
            loaded = it
            TileDiskCache.keepOnly(it.mapTo(HashSet()) { app -> app.packageName })
        }
    }

    /**
     * loadIcon and loadBanner pick the density bucket of the screen: a 96 px icon at 320 dpi, 72 px
     * at TCL's 240 dpi, stretched to a 198 px tile it looks soft (Xiaomi MiTV4-ANSM0, issue #17).
     * Ask for the densest bucket the app ships instead; the bitmap layers of adaptive icons too.
     */
    private fun densest(pm: PackageManager, info: ActivityInfo, res: Int): android.graphics.drawable.Drawable? =
        if (res == 0) null else runCatching {
            pm.getResourcesForApplication(info.applicationInfo)
                .getDrawableForDensity(res, android.util.DisplayMetrics.DENSITY_XXXHIGH, null)
        }.getOrNull()

    /**
     * The tile an APK file that is not installed would get, for [TileSheet]: apps from another TV
     * can be tried here without installing them. Its TV banner if it has one, else its icon.
     */
    fun archiveArt(pm: PackageManager, path: String): Pair<String, TileArt>? = runCatching {
        val pi = pm.getPackageArchiveInfo(path, PackageManager.GET_ACTIVITIES) ?: return null
        val ai = pi.applicationInfo ?: return null
        ai.sourceDir = path
        ai.publicSourceDir = path
        val res = pm.getResourcesForApplication(ai)
        fun drawable(id: Int) = if (id == 0) null else runCatching {
            res.getDrawableForDensity(id, android.util.DisplayMetrics.DENSITY_XXXHIGH, null)
        }.getOrNull()
        val label = ai.loadLabel(pm).toString().trim().ifEmpty { pi.packageName }
        val bannerId = ai.banner.takeIf { it != 0 } ?: pi.activities?.firstOrNull { it.bannerResource != 0 }?.bannerResource ?: 0
        val banner = drawable(bannerId)?.takeIf { it.intrinsicWidth >= it.intrinsicHeight * 4 / 3 }
        val recipe = TileRecipes.of(pi.packageName)
        val drawn = recipe?.glyph?.let { TileArtFactory.glyphTile(recipe.top ?: 0, recipe.bottom ?: 0, it) }
        val art = drawn ?: if (banner != null && recipe?.useIcon != true) TileArtFactory.fromBanner(banner)
            else TileArtFactory.fromIcon(drawable(ai.icon) ?: return null, recipe)
        label to art
    }.getOrNull()

    private fun entry(pm: PackageManager, info: ActivityInfo, leanback: Boolean, byCategory: Map<String, SystemTile>): AppEntry {
        val pkg = info.packageName
        // A picture chosen on the phone page changes the tile as much as an update does.
        val stamp = runCatching { pm.getPackageInfo(pkg, 0).lastUpdateTime }.getOrDefault(0L) + CustomIcons.stamp(pkg)
        val cached = synchronized(cache) { cache[pkg] }?.takeIf { it.stamp == stamp && it.width == TileArtFactory.W }
            ?: TileDiskCache.read(pkg, TileDiskCache.key(stamp))?.let { (label, art) ->
                Cached(stamp, TileArtFactory.W, label, art).also { synchronized(cache) { cache[pkg] = it } }
            }
        val system = (info.applicationInfo.flags and ApplicationInfo.FLAG_SYSTEM) != 0
        val label: String
        val art: TileArt
        if (cached != null) {
            label = cached.label
            art = cached.art
        } else {
            label = info.loadLabel(pm).toString().trim().ifEmpty { pkg }
            // Banner first: it is the artwork the app designed for a TV. ActivityInfo falls back
            // to the application's banner by itself.
            // A "banner" that is not wide is the app's square icon declared again (遥遥电视助手 on
            // Xiaomi MiTV-MFTR0); covering the tile with it blows the logo up past the edges.
            val banner = (if (leanback) densest(pm, info, info.bannerResource) ?: runCatching { info.loadBanner(pm) }.getOrNull() else null)
                ?.takeIf { it.intrinsicWidth >= it.intrinsicHeight * 4 / 3 }
            val own = if (system) SYSTEM_TILES[pkg] ?: byCategory[pkg]?.takeIf { banner == null } else null
            val custom = CustomIcons.load(pkg)
            val recipe = TileRecipes.of(pkg)
            art = when {
                custom != null -> TileArtFactory.fromBanner(android.graphics.drawable.BitmapDrawable(android.content.res.Resources.getSystem(), custom))
                own != null -> TileArtFactory.forSystem(own)
                recipe?.glyph != null -> TileArtFactory.glyphTile(recipe.top ?: 0, recipe.bottom ?: 0, recipe.glyph)
                banner != null && recipe?.useIcon != true -> TileArtFactory.fromBanner(banner)
                else -> TileArtFactory.fromIcon(densest(pm, info, info.iconResource) ?: info.loadIcon(pm), recipe)
            }
            synchronized(cache) { cache[pkg] = Cached(stamp, TileArtFactory.W, label, art) }
            TileDiskCache.write(pkg, TileDiskCache.key(stamp), label, art)
        }
        val removable = !system
        return AppEntry(pkg, label, ComponentName(pkg, info.name), leanback, removable, art)
    }
}
