package com.oneplus.tv.launcher2.guard

/** Filters firmware input placeholders without disabling protection for real gamepads. */
internal object GamepadDevices {
    private const val AZFP0_EVENTSERVER_NAME = "eventserver-Joystick"
    private const val AZFP0_EVENTSERVER_VENDOR_ID = 0xdead
    private const val AZFP0_EVENTSERVER_PRODUCT_ID = 0xbeef

    // The built-in remote receiver of Amlogic TVs: not external, vendor and product 0x0001, yet
    // its sources include SOURCE_GAMEPAD. Counted as a gamepad, it made the guard stop filtering
    // keys in other apps (issue #774's Android 11 rule), so Home went to PatchWall first and Aerial
    // came back seconds later (MiTV-AZFP0, Android 9, 2026-10-30).
    private const val AMLOGIC_KEYPAD_NAME = "aml_keypad"
    private const val AMLOGIC_KEYPAD_VENDOR_ID = 0x0001
    private const val AMLOGIC_KEYPAD_PRODUCT_ID = 0x0001

    fun isPhysicalGamepad(
        name: String?,
        isVirtual: Boolean,
        supportsGamepad: Boolean,
        vendorId: Int,
        productId: Int,
    ): Boolean {
        if (isVirtual || name.orEmpty().contains("virtual", ignoreCase = true) || !supportsGamepad) return false

        // MiTV-AZFP0 (Android 9) reports these software eventserver endpoints as gamepads.
        if (name == AZFP0_EVENTSERVER_NAME &&
            vendorId == AZFP0_EVENTSERVER_VENDOR_ID && productId == AZFP0_EVENTSERVER_PRODUCT_ID
        ) return false
        if (name == AMLOGIC_KEYPAD_NAME &&
            vendorId == AMLOGIC_KEYPAD_VENDOR_ID && productId == AMLOGIC_KEYPAD_PRODUCT_ID
        ) return false

        return true
    }
}
