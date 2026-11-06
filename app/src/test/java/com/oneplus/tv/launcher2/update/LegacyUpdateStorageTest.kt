package com.oneplus.tv.launcher2.update

import android.Manifest
import android.app.Activity
import android.app.Application
import android.content.ContextWrapper
import java.io.File
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [23])
class LegacyUpdateStorageTest {
    @get:Rule val temporary = TemporaryFolder()
    private val application: Application = RuntimeEnvironment.getApplication()

    @Test
    fun blockedExternalCacheNeedsPermissionOnAndroid6() {
        denyStorage()
        assertTrue(LegacyUpdateStorage.needsPermission(cacheAt(temporary.newFile())))
    }

    @Test
    fun writableAppCacheDoesNotPromptForBroadStorageAccess() {
        denyStorage()
        assertFalse(LegacyUpdateStorage.needsPermission(cacheAt(temporary.newFolder())))
    }

    @Test
    fun grantedPermissionDoesNotCauseAnotherPrompt() {
        shadowOf(application).grantPermissions(Manifest.permission.READ_EXTERNAL_STORAGE, Manifest.permission.WRITE_EXTERNAL_STORAGE)
        assertFalse(LegacyUpdateStorage.needsPermission(cacheAt(temporary.newFile())))
    }

    @Test
    fun writePermissionAloneDoesNotSkipTheRequestForBlockedCache() {
        denyStorage()
        shadowOf(application).grantPermissions(Manifest.permission.WRITE_EXTERNAL_STORAGE)
        assertTrue(LegacyUpdateStorage.needsPermission(cacheAt(temporary.newFile())))
    }

    @Test
    fun readPermissionAloneDoesNotSkipTheRequestForBlockedCache() {
        denyStorage()
        shadowOf(application).grantPermissions(Manifest.permission.READ_EXTERNAL_STORAGE)
        assertTrue(LegacyUpdateStorage.needsPermission(cacheAt(temporary.newFile())))
    }

    @Test
    fun missingExternalCacheCanRequestPermission() {
        denyStorage()
        assertTrue(LegacyUpdateStorage.needsPermission(object : ContextWrapper(application) {
            override fun getExternalCacheDir(): File? = null
        }))
    }

    @Test
    @Config(sdk = [28])
    fun modernAndroidNeverRequestsLegacyStorageAccess() {
        assertFalse(LegacyUpdateStorage.needsPermission(object : ContextWrapper(application) {
            override fun getExternalCacheDir(): File? = error("Modern updates use internal cache")
        }))
    }

    @Test
    fun permissionRequestFindsAnActivityInsideAContextWrapper() {
        val controller = Robolectric.buildActivity(Activity::class.java).setup()
        try {
            val activity = controller.get()
            LegacyUpdateStorage.requestPermission(ContextWrapper(activity))
            assertArrayEquals(
                arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE, Manifest.permission.WRITE_EXTERNAL_STORAGE),
                shadowOf(activity).lastRequestedPermission.requestedPermissions,
            )
        } finally {
            controller.pause().stop().destroy()
        }
    }

    private fun cacheAt(directory: File) = object : ContextWrapper(application) {
        override fun getExternalCacheDir(): File = directory
    }

    private fun denyStorage() {
        shadowOf(application).denyPermissions(Manifest.permission.READ_EXTERNAL_STORAGE, Manifest.permission.WRITE_EXTERNAL_STORAGE)
    }
}
