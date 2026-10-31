package com.oneplus.tv.launcher2.guard

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class GamepadDevicesTest {
    @Test fun filtersAzfp0EventserverJoystickPlaceholder() {
        assertFalse(GamepadDevices.isPhysicalGamepad("eventserver-Joystick", false, true, 0xdead, 0xbeef))
    }

    @Test fun keepsSameNamedDeviceWithDifferentHardwareIds() {
        assertTrue(GamepadDevices.isPhysicalGamepad("eventserver-Joystick", false, true, 0x1234, 0x5678))
        assertTrue(GamepadDevices.isPhysicalGamepad("eventserver-Joystick", false, true, 0xdead, 0x5678))
        assertTrue(GamepadDevices.isPhysicalGamepad("eventserver-Joystick", false, true, 0x1234, 0xbeef))
        assertTrue(GamepadDevices.isPhysicalGamepad("eventserver-Joystick", false, true, 0, 0))
    }

    @Test fun doesNotExcludeOtherEventserverNames() {
        assertTrue(GamepadDevices.isPhysicalGamepad("eventserver-Gamepad", false, true, 0xdead, 0xbeef))
        assertTrue(GamepadDevices.isPhysicalGamepad("eventserver-Joystick Pro", false, true, 0xdead, 0xbeef))
    }

    @Test fun keepsOtherPhysicalGamepads() {
        assertTrue(GamepadDevices.isPhysicalGamepad("USB Gamepad", false, true, 0x1234, 0x5678))
    }

    @Test fun rejectsExistingVirtualDeviceCases() {
        assertFalse(GamepadDevices.isPhysicalGamepad("USB Gamepad", true, true, 0x1234, 0x5678))
        assertFalse(GamepadDevices.isPhysicalGamepad("virtual-search", false, true, 0, 0))
    }

    @Test fun filtersAmlogicBuiltInRemoteReceiver() {
        assertFalse(GamepadDevices.isPhysicalGamepad("aml_keypad", false, true, 0x0001, 0x0001))
    }

    @Test fun keepsSameNamedKeypadWithOtherIds() {
        assertTrue(GamepadDevices.isPhysicalGamepad("aml_keypad", false, true, 0x045e, 0x028e))
        assertTrue(GamepadDevices.isPhysicalGamepad("aml_keypad pro", false, true, 0x0001, 0x0001))
    }

    @Test fun rejectsDevicesWithoutGamepadSource() {
        assertFalse(GamepadDevices.isPhysicalGamepad("Keyboard", false, false, 0x1234, 0x5678))
    }
}
